"""The builder. Reads a bank brief and its tool API, creates the phone agent on Freya, leaves a trace.

    source env.sh
    python3 builder/build.py BRIEF.md [MORE_BRIEFS.md ...] TOOL_API.json [--slot NAME] [--rules RULES.json] [--no-save] [--test A]
    python3 builder/build.py CASE_FOLDER TOOL_BASE_URL [--slot NAME]   (folder: every .md/.txt/.pdf in it is read as the brief)

Steps: extract the rule sheet (Claude) -> validate -> generate the workflow (code) -> lint -> save + publish -> chat smoke
-> export. Every step, model call and Freya id is written to traces/<slot>-<time>.jsonl.
"""
import argparse, json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
try:
    import claude_agent_sdk  # noqa: F401
except ImportError:  # system python has no SDK: re-run inside the kit's venv
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists() and pathlib.Path(sys.executable).resolve() != venv.resolve():
        os.execv(str(venv), [str(venv)] + sys.argv)
sys.path[:0] = [str(ROOT / "builder"), str(ROOT)]
import api_spec, extract, generate, lint  # noqa: E402
import freya  # noqa: E402


class Trace:
    def __init__(self, slot):
        (ROOT / "traces").mkdir(exist_ok=True)
        self.path = ROOT / "traces" / f"{slot}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.f = self.path.open("w")

    def __call__(self, e):
        e = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **e}
        self.f.write(json.dumps(e, default=str) + "\n"); self.f.flush()
        print(json.dumps(e, default=str)[:400], flush=True)


def smoke(workflow_id, test_vars, log):
    """A chat turn on the published workflow: it must start, speak, and leak no placeholder."""
    out, call = [], None
    for msg in ("Hello?",):  # ponytail: one turn; the chat route is slow (often >60 s) and skips code steps
        try:
            body = {"workflowId": workflow_id, "message": msg, "customerVariables": test_vars}
            if call:
                body["callId"] = call
            r = freya.api("POST", "/api/v2/chat/message", body)
            call = r.get("callId") or call
            said = str(r.get("response") or "")
            out.append({"user": msg, "agent": said, "ok": bool(said.strip()) and "{{" not in said})
        except Exception as e:
            out.append({"user": msg, "error": str(e)[:300], "ok": False})
            break
    if call:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call})
        except Exception:
            pass
    log({"event": "smoke_test", "turns": out, "passed": all(t["ok"] for t in out)})
    return out


TEXT_TYPES = {".md", ".txt", ".pdf"}


def read_text(path):
    if path.suffix == ".pdf":  # ponytail: poppler's pdftotext; no pdf lib in the venv
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return path.read_text()


def brief_files(args, log):
    """Brief inputs as files: a folder contributes every text-like file under it, sorted; other types are skipped."""
    out = []
    for a in args:
        p = pathlib.Path(a)
        if not p.is_dir():
            out.append(p); continue
        for f in sorted(p.rglob("*")):
            if f.is_file():
                (out.append(f) if f.suffix.lower() in TEXT_TYPES else log({"event": "input_skipped", "path": str(f)}))
    return out


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("files", nargs="+", help="brief file(s) or a case folder, then the tool API json or the tools' base URL")
    ap.add_argument("--slot"); ap.add_argument("--rules", help="reuse a rule sheet instead of extracting")
    ap.add_argument("--no-save", action="store_true"); ap.add_argument("--test", help="practice brief letter for run_cases.py (A or B)")
    a = ap.parse_args()
    is_api = lambda f: f.endswith(".json") or f.startswith(("http://", "https://"))
    briefs = [f for f in a.files if not is_api(f)]
    tool_api = next(f for f in a.files if is_api(f))
    slot = a.slot or "-".join(pathlib.Path(b).stem for b in briefs)
    log = Trace(slot)
    t0 = time.time()
    log({"event": "start", "args": a.files, "tool_api": tool_api, "slot": slot, "builder_model": extract.MODEL})
    briefs = brief_files(briefs, log)
    log({"event": "inputs", "briefs": [str(b) for b in briefs]})

    api = api_spec.load(tool_api)
    out = ROOT / "out"; out.mkdir(exist_ok=True)
    if a.rules:
        rules = json.load(open(a.rules))
        log({"event": "rules_reused", "path": a.rules})
    else:
        text = "\n\n".join(f"=== {b} ===\n" + read_text(b) for b in briefs)
        rules = extract.extract(text, api, log=log)
    (out / f"{slot}.rules.json").write_text(json.dumps(rules, indent=1))
    errs = extract.validate(rules, api)
    log({"event": "rules", "path": str(out / f"{slot}.rules.json"), "validation_errors": errs})

    wf = generate.generate(rules, api)
    (out / f"{slot}.generated.json").write_text(json.dumps(wf, indent=1))
    problems = lint.lint(wf, rules, api)
    log({"event": "lint", "problems": problems})

    if not a.no_save:
        try:
            freya.validate_workflow(slot, wf)
            log({"event": "platform_dry_run", "ok": True})
        except Exception as e:
            log({"event": "platform_dry_run", "ok": False, "error": str(e)[:1500]})
        ids = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
        log({"event": "saved_on_freya", **ids})
        smoke(ids["workflow_id"], wf["test_variables"], log)
        path = freya.export_workflow(ids["workflow_id"], str(out / f"{slot}.workflow.json"))
        log({"event": "exported", "path": path})
        if a.test:
            cmd = [sys.executable, str(ROOT / "tools" / "run_cases.py"), ids["agent_id"], "--brief", a.test, "--out", str(ROOT / "results" / f"{slot}.json")]
            log({"event": "test_start", "cmd": " ".join(cmd)})
            r = subprocess.run(cmd, capture_output=True, text=True)
            log({"event": "test_result", "stdout_tail": r.stdout[-3000:], "returncode": r.returncode})
    log({"event": "end", "seconds": round(time.time() - t0)})
    print("trace:", log.path)


if __name__ == "__main__":
    main()
