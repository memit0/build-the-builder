"""Generalisation check: extract -> validate -> generate -> lint -> run the generated code locally, on every brief.

    python3 dev/check.py            # reuses dev/out/<name>.rules.json when present
    python3 dev/check.py --fresh    # re-extracts (Claude calls, ~2-3 min and ~$0.6 per brief, run in parallel)
"""
import concurrent.futures, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "builder"), str(ROOT)]
import api_spec, extract, generate, lint  # noqa: E402

SETS = {
    "a": (["briefs/brief-a.md"], "tools/openapi.json"),
    "ab": (["briefs/brief-a.md", "briefs/brief-b.md"], "tools/openapi.json"),
    "c": (["dev/briefs/brief-c.md"], "dev/briefs/brief-c.openapi.json"),
    "d": (["dev/briefs/brief-d.md"], "dev/briefs/brief-d.openapi.json"),
}
OUT = ROOT / "dev" / "out"


def rules_for(name, fresh):
    briefs, tool_api = SETS[name]
    p = OUT / f"{name}.rules.json"
    if p.exists() and not fresh:
        return json.loads(p.read_text())
    api = api_spec.load(ROOT / tool_api)
    r = extract.extract("\n\n".join((ROOT / b).read_text() for b in briefs), api, log=lambda e: print(name, json.dumps(e)[:200], flush=True))
    p.write_text(json.dumps(r, indent=1))
    return r


def src(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def selftest(wf, r, api):
    """Run the generated code like the platform would, on synthetic accounts. Returns problems."""
    errs = []
    N = wf["nodes"]
    item = api["tools"]["get_account"]["response"]["products"]["items"]["properties"]
    now = next(v["example"] for v in r["call_vars"]["examples"] if v["name"] == r["call_vars"]["now"])
    for band in r["bands"]:
        prods = []
        for i, t in enumerate(item["type"].get("enum", ["x"])[:3]):
            p = {"product_id": f"p{i + 1}", "type": t, "past_due": 900.0, "days_late": band["min_days_late"]}
            for k, s in item.items():
                p.setdefault(k, 300.0 if s.get("type") == "number" else "1234")
            prods.append(p)
        V = {r["call_vars"]["now"]: now}
        exec(src(N["setup"]), {"vars": V})
        if V.get("setup_state") != "done":
            errs.append("setup code failed")
        verify = N["security_check"]["tools"][0]
        for q in r["identity"]["questions"]:
            V["x_id_field"] = q["field"]
            exec(verify["post_process_code"], {"vars": V, "result": {"data": {"verified": True}}})
        V["products"] = json.dumps(prods)
        exec(src(N["prepare_deal"]), {"vars": V})
        if V.get("acct_state") != "ok":
            errs.append(f"account code failed for band {band['name']}: {V.get('acct_error')}")
            continue
        for k in ("deal_facts", "date_table", "debt_text", "first_ask_text"):
            if not V.get(k) or "{{" in str(V[k]):
                errs.append(f"{k} empty or templated for band {band['name']}")
        tools = {t["function"]["name"]: t for t in N["negotiate"]["tools"]}
        P = json.loads(V["x_prod"])
        F = generate.config(r, api)["fields"]
        cfg = {"url": "https://x" + api["tools"]["create_promise"]["path"],
               "body": {F["p_pid"]: P[0]["pid"], F["p_amount"]: P[0]["full"], F["p_date"]: P[0]["valid"][-1] if P[0]["valid"] else ""}}
        step = tools["policy_step"]["function"]["parameters"]["properties"]["code"]["default"]
        def policy_step(action, pid, **kwargs):
            for key in ("action", "product_id", "amount", "date", "too_much", "intent"):
                V["policy_step_args." + key] = dict(action=action, product_id=pid, **kwargs).get(key)
            exec(step, {"vars": V})
        policy_step("stage", P[0]["pid"], amount=P[0]["full"], date=cfg["body"][F["p_date"]])
        policy_step("readback", P[0]["pid"])
        policy_step("confirm", P[0]["pid"])
        exec(tools["create_promise"]["pre_process_code"], {"vars": V, "config": cfg})
        if V.get("promise_block"):
            errs.append(f"a valid promise was blocked for band {band['name']}: {V['promise_block']}")
        cfg["body"][F["p_amount"]] = P[0]["floor"] - 1
        exec(tools["create_promise"]["pre_process_code"], {"vars": V, "config": cfg})
        if P[0]["floor"] > 1 and not V.get("promise_block"):
            errs.append("a promise below the floor was not blocked")
        if "add_note" in tools and len(P) > 1 and P[1]["atts"]:
            note = lambda n: exec(tools["add_note"]["pre_process_code"], {"vars": V, "config": {"url": "https://x" + api["tools"]["add_note"]["path"],
                                  "body": {F["n_pid"]: P[1]["pid"], F["n_reason"]: r["persuasion"]["exhausted"]["reason"], "attempts_made": n}}})
            note(0)
            if not V.get("note_block"):
                errs.append(f"a refusal note with 0 attempts made was not blocked for band {band['name']}")
            for _ in P[1]["atts"]:
                policy_step("offer", P[1]["pid"], too_much=False)
                policy_step("refuse", P[1]["pid"])
            note(len(P[1]["atts"]))
            if V.get("note_block"):
                errs.append(f"a refusal note after every attempt was blocked for band {band['name']}: {V['note_block']}")
        V["x_recorded"] = P[0]["pid"]
        body = {F["outcome"]: "whatever"}
        exec(tools["close_call"]["pre_process_code"], {"vars": V, "config": {"url": "https://x/close_call", "body": body}})
        want = next((p["outcome"] for p in r["close_precedence"] if p["state"] == "promise_recorded"), None)
        if want and body[F["outcome"]] != want:
            errs.append(f"close with a recorded promise sent {body} instead of {want}")
    return errs


def main():
    fresh = "--fresh" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or list(SETS)
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        sheets = dict(zip(names, ex.map(lambda n: rules_for(n, fresh), names)))
    bad = 0
    for name, r in sheets.items():
        api = api_spec.load(ROOT / SETS[name][1])
        wf = generate.generate(r, api)
        (OUT / f"{name}.workflow.json").write_text(json.dumps(wf, indent=1))
        probs = [("validate", e) for e in extract.validate(r, api)] + [("lint", e) for e in lint.lint(wf, r, api)] + [("selftest", e) for e in selftest(wf, r, api)]
        bad += bool(probs)
        print(f"{'OK  ' if not probs else 'FAIL'} {name}: {len(json.dumps(wf)) // 1000} KB")
        for k, e in probs:
            print(f"     {k}: {e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
