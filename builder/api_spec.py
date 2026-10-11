"""Parse the bank's tool API (OpenAPI 3) deterministically: base URL, per-tool request fields and enums."""
import json
import pathlib

import yaml


def load(path):
    text = pathlib.Path(path).read_text()
    spec = yaml.safe_load(text) if pathlib.Path(path).suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    base = spec["servers"][0]["url"].rstrip("/")

    def resolve(value):
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value and value["$ref"].startswith("#/"):
            target = spec
            for part in value["$ref"][2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return resolve(target)
        return {key: resolve(item) for key, item in value.items()}

    tools = {}
    for path_, ops in spec["paths"].items():
        op = ops.get("post") or next(iter(ops.values()))
        schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        responses = op.get("responses", {})
        response = next((v for k, v in responses.items() if str(k).startswith("2")), responses.get("default", {}))
        resp = response.get("content", {}).get("application/json", {}).get("schema", {})
        name = op.get("operationId") or path_.rstrip("/").rsplit("/", 1)[-1]
        tools[name] = {
            "path": path_,
            "summary": op.get("summary", ""),
            "fields": resolve(schema).get("properties", {}),
            "required": resolve(schema).get("required", []),
            "response": resolve(resp).get("properties", {}),
        }
    return {"base": base, "tools": tools, "info": spec.get("info", {}).get("description", "")}


def enum(api, tool, field):
    return list(api["tools"].get(tool, {}).get("fields", {}).get(field, {}).get("enum") or [])


def find_field(api, tool, *hints, type_=None, fmt=None):
    """Name of the request field of `tool` that plays a role, so hooks survive a renamed field."""
    fields = api["tools"].get(tool, {}).get("fields", {})
    if hints and hints[0] == "customer_id":
        mapped = api.get("customer_id_fields", {}).get(tool)
        if mapped in fields:
            return mapped
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
            lines.append("  returns:")
            for f, s in t["response"].items():
                lines.append(f"  - {f} ({s.get('type', '?')}){': ' + s['description'] if s.get('description') else ''}")
                for g, x in s.get("items", {}).get("properties", {}).items():
                    extra = (" enum=" + json.dumps(x["enum"])) if x.get("enum") else ""
                    lines.append(f"      - {g} ({x.get('type', '?')}){extra}{': ' + x['description'] if x.get('description') else ''}")
    return "\n".join(lines)
