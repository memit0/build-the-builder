"""Parse the bank's tool API (OpenAPI 3) deterministically: base URL, per-tool request fields and enums."""
import json


def load(path):
    with open(path) as f:
        spec = json.load(f)
    base = spec["servers"][0]["url"].rstrip("/")
    tools = {}
    for path_, ops in spec["paths"].items():
        op = ops.get("post") or next(iter(ops.values()))
        schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        resp = (op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {}))
        tools[path_.strip("/")] = {
            "path": path_,
            "summary": op.get("summary", ""),
            "fields": schema.get("properties", {}),
            "required": schema.get("required", []),
            "response": resp.get("properties", {}),
        }
    return {"base": base, "tools": tools, "info": spec.get("info", {}).get("description", "")}


def enum(api, tool, field):
    return list(api["tools"].get(tool, {}).get("fields", {}).get(field, {}).get("enum") or [])


def find_field(api, tool, *hints, type_=None, fmt=None):
    """Name of the request field of `tool` that plays a role, so hooks survive a renamed field."""
    fields = api["tools"].get(tool, {}).get("fields", {})
    for h in hints:
        if h in fields:
            return h
    for name, f in fields.items():
        if name == "customer_id":
            continue
        if fmt and f.get("format") == fmt:
            return name
        if any(h in name for h in hints):
            return name
    if type_:
        for name, f in fields.items():
            if f.get("type") == type_ and name != "customer_id":
                return name
    return hints[0] if hints else ""


def compact(api):
    """A short text view of the API for the extraction prompt."""
    lines = ["Base URL: " + api["base"]]
    for name, t in api["tools"].items():
        lines.append(f"\n{name}: {t['summary']}")
        for f, s in t["fields"].items():
            extra = (" enum=" + json.dumps(s["enum"])) if s.get("enum") else ""
            lines.append(f"  - {f} ({s.get('type', '?')}{', ' + s['format'] if s.get('format') else ''}){' required' if f in t['required'] else ''}{extra}{': ' + s['description'] if s.get('description') else ''}")
        if t["response"]:
            lines.append("  returns: " + json.dumps(t["response"])[:600])
    return "\n".join(lines)
