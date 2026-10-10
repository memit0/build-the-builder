"""Compiler policy support and evidence-based coverage. No bank-specific constants."""
import copy
import re
import math


def schema_errors(value, schema, path="rules"):
    """Small stdlib validator for the subset used by the extraction schema."""
    errors = []
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool}
    if kind in types and (not isinstance(value, types[kind]) or
                         kind in ("number", "integer") and isinstance(value, bool)):
        return [f"{path}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: unsupported value {value!r}")
    if kind == "number" and not math.isfinite(value):
        errors.append(f"{path}: expected a finite number")
    if kind == "object":
        props = schema["properties"]
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        for key, item in value.items():
            if key in props:
                errors += schema_errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unknown field")
    if kind == "array":
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}.{i}")
    return errors


def legacy(rules):
    """Keep old caches usable without pretending to have extracted new policy."""
    r = copy.deepcopy(rules)
    r.setdefault("execution", {
        "refused_note_reason": "",
        "minimum_offer": {"limit": -1, "scope": "per_product", "repeat_on_attempts": [], "source": ""},
        "intent_actions": [],
    })
    r.setdefault("requirements", [])
    return r


def lookup(rules, path):
    value = rules
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def validate(rules):
    errors = []
    offer = rules["execution"]["minimum_offer"]
    if offer["limit"] < -1:
        errors.append("minimum offer limit must be -1 (unspecified) or nonnegative")
    if offer["limit"] >= 0 and not offer["source"].strip():
        errors.append("minimum offer policy needs a source quote")
    numbers = {a["number"] for a in rules["persuasion"]["attempts"]}
    if not set(offer["repeat_on_attempts"]) <= numbers:
        errors.append("minimum offer exception names an unknown persuasion attempt")
    intent_names = {i["name"] for i in rules["intents"]}
    for action in rules["execution"]["intent_actions"]:
        if action["intent"] not in intent_names:
            errors.append(f"unsupported intent action: {action['intent']}")
    ids = set()
    for req in rules["requirements"]:
        if req["id"] in ids:
            errors.append(f"duplicate requirement {req['id']}")
        ids.add(req["id"])
        if req["status"] == "superseded":
            continue
        if not req["paths"]:
            errors.append(f"requirement {req['id']} is unmapped")
        if req["critical"] and any(p.startswith("unmapped_rules") for p in req["paths"]):
            errors.append(f"critical requirement {req['id']} has no supported representation")
        for path in req["paths"]:
            try:
                lookup(rules, path)
            except (KeyError, IndexError, ValueError, TypeError):
                errors.append(f"requirement {req['id']}: unknown path {path}")
    return errors


def coverage(rules, workflow):
    """Evidence is found in emitted code/routing; text-only rules stay visibly weak."""
    nodes = workflow["nodes"]
    tools = {t["function"]["name"]: t for n in nodes.values() for t in n.get("tools", [])}
    evidence = {
        "calendar.holidays": ("code", "prepare_deal.code", "HOL = set(C['holidays'])"),
        "calendar.business_weekdays": ("code", "prepare_deal.code", "BW = set(C['business_weekdays'])"),
        "calendar.earliest_promise_business_days": ("code", "create_promise.pre_process_code", "p['valid']"),
        "bands.*.deadline_offset": ("code", "create_promise.pre_process_code", "p['valid']"),
        "bands.*.deadline_unit": ("code", "create_promise.pre_process_code", "p['valid']"),
        "products.*.floor_field": ("code", "create_promise.pre_process_code", "p['floor']"),
        "products.*.floor_percent": ("code", "create_promise.pre_process_code", "p['floor']"),
        "identity.max_wrong_total": ("code", "verify_identity.post_process_code", "x_wrong"),
        "identity.questions.*.field": ("code", "verify_identity.pre_process_code", "remaining[0]['field']"),
        "confirmation.retries_on_failure": ("code", "create_promise.pre_process_code", "x_pfailed"),
        "confirmation.readback_question": ("code+prompt", "create_promise.pre_process_code", "x_confirmed"),
        "execution.minimum_offer": ("code+prompt", "policy_step.code", "minimum_count"),
        "execution.intent_actions": ("code+prompt", "policy_step.code", "intent_actions"),
        "execution.refused_note_reason": ("code", "add_note.pre_process_code", "C['refused_note_reason']"),
        "third_party.callback": ("code", "schedule_callback.pre_process_code", "cb_days"),
        "close_precedence": ("code", "close_call.pre_process_code", "C['precedence']"),
    }
    def source(loc):
        name, field = loc.split(".", 1)
        if name == "prepare_deal":
            return nodes[name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        if name == "security_check":
            return str(nodes[name]["transitions"])
        tool = tools.get(name, {})
        if field == "code":
            return tool.get("function", {}).get("parameters", {}).get("properties", {}).get("code", {}).get("default", "")
        return tool.get(field, "")
    inventory = rules.get("requirements", [])
    legacy_inventory = not inventory
    if legacy_inventory:
        def leaves(value, path=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k != "requirements":
                        yield from leaves(v, path + "." + k if path else k)
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    yield from leaves(v, path + "." + str(i))
            elif value not in ("", None):
                yield path
        inventory = [dict(id="field:" + p, source="", critical=False, status="active", paths=[p]) for p in leaves(rules)]
    rows = []
    for req in inventory:
        mechanisms = []
        for path in req["paths"]:
            normalized = re.sub(r"\.\d+(?=\.|$)", ".*", path)
            match = next((v for k, v in sorted(evidence.items(), key=lambda x: -len(x[0]))
                          if normalized == k or normalized.startswith(k + ".")), None)
            if match and match[2] in source(match[1]):
                mechanisms.append({"path": path, "mechanism": match[0], "location": match[1]})
            else:
                mechanisms.append({"path": path, "mechanism": "prompt", "location": "generated prompts"})
        strong = bool(mechanisms) and all(m["mechanism"] in ("code", "workflow") for m in mechanisms)
        status = "superseded" if req["status"] == "superseded" else (
            "covered" if strong else "weak" if mechanisms else "unsupported")
        rows.append({**req, "coverage": status, "evidence": mechanisms})
    return {"inventory_origin": "rule_fields_only" if legacy_inventory else "source_requirements", "requirements": rows, "warnings":
            (["Legacy rule sheet: no independent requirement inventory. Re-extract before final submission."] if legacy_inventory else []) +
            (["Minimum-offer limit is unspecified; no offer-count guarantee."] if rules["execution"]["minimum_offer"]["limit"] < 0 else []) +
            ["Customer intent/confirmation classification and free speech remain model-dependent.",
             "Freya code_exec dialogue tool argument handling needs a live platform regression."]}
