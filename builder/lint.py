"""Static checks on a generated workflow. Returns a list of problems; empty means clean."""
import json, re

import api_spec

CODE_KEYS = ("pre_process_code", "post_process_code")


def lint(wf, rules, api):
    errs = []
    nodes = wf["nodes"]
    text = json.dumps(wf)

    def walk(o, path=""):
        if o is None:
            errs.append(f"null value at {path}")
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in CODE_KEYS or (k == "default" and path.endswith(".code")):
                    if "{{" in v or "}}" in v:
                        errs.append(f"double brace in code at {path}.{k}")
                    try:
                        compile(v, path, "exec")
                    except SyntaxError as e:
                        errs.append(f"code does not compile at {path}.{k}: {e}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
    walk(wf)

    # graph: reachable, passthrough nodes end with always, every path can reach end_call
    edges = {k: [t["target_node"] for t in n.get("transitions", [])] + ([n["passthrough_default_transition"]] if n.get("passthrough_default_transition") else []) for k, n in nodes.items()}
    for k, ts in edges.items():
        for t in ts:
            if t not in nodes:
                errs.append(f"{k} -> unknown node {t}")
    seen, todo = set(), [wf["initial_node"]]
    while todo:
        k = todo.pop()
        if k in seen or k not in nodes:
            continue
        seen.add(k); todo += edges[k]
    for k in nodes:
        if k not in seen:
            errs.append(f"node {k} is unreachable")
        n = nodes[k]
        if n.get("passthrough") and not (n["transitions"] and n["transitions"][-1]["condition"].get("operator") == "always"):
            errs.append(f"passthrough node {k} does not end with an always transition")
        if n["node_type"] == "conversation" and not any(t["target_node"] == "end" or nodes.get(t["target_node"], {}).get("node_type") == "end_call" for t in n["transitions"]):
            errs.append(f"conversation node {k} has no route to end_call")
    if not any(n["node_type"] == "end_call" for n in nodes.values()):
        errs.append("no end_call node")

    # tools: fields and enums match the API
    for k, n in nodes.items():
        for t in n.get("tools", []) + n.get("pre_actions", []):
            fn = t["function"]
            if fn["handler"] != "__function__:api_call":
                continue
            url = fn["parameters"]["properties"]["url"]["default"]
            name = url.rsplit("/", 1)[-1]
            spec = api["tools"].get(name)
            if not spec:
                errs.append(f"{k}: tool {fn['name']} calls unknown path {url}")
                continue
            for f, p in fn["parameters"]["properties"]["body"]["properties"].items():
                if f not in spec["fields"] and f"b.pop('{f}'" not in t.get("pre_process_code", ""):
                    errs.append(f"{k}: {fn['name']} sends field {f} that the API does not have")
                api_enum = spec["fields"].get(f, {}).get("enum")
                if api_enum and p.get("enum") and not set(p["enum"]) <= set(api_enum):
                    errs.append(f"{k}: {fn['name']}.{f} enum {p['enum']} is not within the API enum {api_enum}")

    # every outcome/reason pair can be sent by close_call
    close_tools = [t for n in nodes.values() for t in n.get("tools", []) if t["function"]["name"] == "close_call"]
    F = api_spec.find_field(api, "close_call", "outcome"), api_spec.find_field(api, "close_call", "reason")
    body = lambda t: t["function"]["parameters"]["properties"]["body"]["properties"]
    for o in rules["outcomes"]:
        ok = any(F[0] in body(t) and o["outcome"] in body(t)[F[0]].get("enum", [o["outcome"]]) and
                 (not o["reason"] or o["reason"] in body(t).get(F[1], {}).get("enum", [o["reason"]]))
                 for t in close_tools)
        if not ok:
            errs.append(f"outcome {o['outcome']}/{o['reason']} cannot be sent by any close_call")

    # every quoted sentence of the brief reaches some prompt or line
    flat = re.sub(r"\s+", " ", text.replace("\\n", " ").replace('\\"', '"'))
    for p in rules["verbatim_phrases"]:
        if re.sub(r"\s+", " ", p["text"]) not in flat:
            errs.append(f"verbatim phrase missing: {p['text']!r}")
    for k, n in nodes.items():
        for fld in ("task_message",):
            for v in re.findall(r"\{\{([\w.]+)\}\}", n.get(fld, "")):
                if v not in SET_VARS and v not in wf.get("test_variables", {}):
                    errs.append(f"{k}.{fld} uses {{{{{v}}}}} that nothing sets")
    return errs


# variables the generated code always sets (plus call variables from test_variables)
SET_VARS = {"callback_facts", "deal_facts", "date_table", "debt_text", "first_ask_text"}
