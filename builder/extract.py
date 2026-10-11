"""Brief (+ tool API) -> rules.json: the rule sheet the generator reads. Claude extracts, code validates."""
import asyncio, datetime, json, os, re, time

import api_spec

MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")


# ---------------------------------------------------------------- schema (strict: every key required, no extras)

def S(d=""): return {"type": "string", "description": d}
def I(d=""): return {"type": "integer", "description": d}
def N(d=""): return {"type": "number", "description": d}
def B(d=""): return {"type": "boolean", "description": d}
def E(vals, d=""): return {"type": "string", "enum": vals, "description": d}
def A(items, d=""): return {"type": "array", "items": items, "description": d}
def O(d="", **props): return {"type": "object", "description": d, "properties": props, "required": list(props), "additionalProperties": False}
def OR(d=""): return O(d, outcome=S("close_call outcome, exactly as in the outcome table"), reason=S("close_call reason, exactly as in the table; '' when the table lists none"))
def TEXTS(d): return A(S(), d)

def EXPR(ref, ops, extra):
    return O("A typed expression; use only the listed operations.", op=E(ops), value=N(), field=S(), unit=E(["business_days", "calendar_days", ""]), args=A({"$ref": ref}), **extra)

STATES = ["transferred", "promise_recorded", "promise_service_error", "callback_booked", "identity_failed", "all_notes_paid", "first_non_paid_note"]
DATE_RULES = ["today", "next_business_day", "end_of_week", "deadline", "business_days_from_today", "calendar_days_from_today", "other"]
AMOUNT_RULES = ["full", "floor", "full_or_floor_if_too_much", "percent_of_full", "other"]

SCHEMA = O(
    "The complete rule sheet of one bank brief.",
    bank_name=S("The bank's name."),
    agent_identity=S("How the agent names itself when asked who/what it is, e.g. \"Acme Bank's digital assistant\"."),
    call_vars=O(
        "The call variables every call starts with.",
        customer_id=S("Name of the variable passed to every tool call as customer_id."),
        address_as=S("How to address the customer, as a template with {{variable}} placeholders, e.g. '{{title}} {{last_name}}'."),
        full_name=S("The full name of the person to ask for, as a template with {{variable}} placeholders."),
        now=S("Name of the variable that holds the current date and time."),
        examples=A(O(name=S(), example=S("The brief's example value, or a realistic one.")), "Every call variable with an example value."),
    ),
    customer_id_fields=A(O(tool=S("Exact tool name from the API."), field=S("Exact request field that receives the call's customer ID.")),
                         "For every tool requiring the customer's ID, map it to the exact request field name from the API."),
    opening=O(
        first_sentence=S("The exact first sentence the agent says, written from the brief's requirements (who is calling, disclosures such as recording). No reason for the call. May use {{variables}}."),
        confirm_question=S("The exact question that asks whether the right person is on the line, with {{variables}}."),
        forbidden_before_verification=TEXTS("Words/topics the agent must not say before identity is verified."),
        confirmation_rule=S("What counts as a confirmation of identity and what does not, and how many times to ask."),
        max_asks=I("How many times the agent may ask the confirmation question before closing as unclear."),
        unclear=OR("Close when it stays unclear."),
        rules=TEXTS("Every other rule of the opening, in the brief's words."),
    ),
    third_party=O(
        rules=TEXTS("Every rule about someone other than the customer answering, in the brief's words."),
        callback=O(
            allow_today=B("A callback may be today (later than now)."),
            business_days_ahead=I("How many following business days are allowed besides today (e.g. 'today or the next business day' -> 1)."),
            window_start=S("HH:MM, earliest allowed time."),
            window_end=S("HH:MM, latest allowed time, inclusive."),
            extra_asks_when_outside=I("How many times to ask for another time when the offered one is outside the window."),
            booked=OR("Close after a booked callback."),
            not_booked=OR("Close when no callback could be booked."),
        ),
        wrong_number=O(say=S("What to say, '' if nothing specific."), outcome=S(), reason=S()),
        deceased=O(say=S("The exact sentence to say."), outcome=S(), reason=S()),
    ),
    identity=O(
        intro=S("One sentence said before the first security question, e.g. 'Before we continue, I need to ask you two security questions.'"),
        questions=A(O(
            field=S("The verify_identity field value from the tool API enum."),
            ask=S("The exact spoken question."),
            kind=E(["date", "digits", "year", "postcode", "text"], "How the answer is normalised before sending."),
            digits=I("For kind digits: how many digits are sent (e.g. 4). Otherwise 0."),
            how_to_send=S("The format the tool expects, e.g. 'YYYY-MM-DD' or 'four digits'."),
        ), "The security questions, in the order they are asked."),
        numeric_date_order=E(["DMY", "MDY", "YMD"], "How to read a date given only as numbers."),
        max_wrong_total=I("Wrong answers in total that end the check."),
        hints=A(O(value=S("The hint value verify_identity can return."), instruction=S("What the agent does."), counts_as_wrong=B()), "Hints the verify tool can return."),
        failed=O(say=S("What to tell the customer after the check fails."), outcome=S(), reason=S()),
        declined=O(rule=S("When a refusal to answer closes the call, in the brief's words."), explain=S("What to explain the first time they decline."), outcome=S(), reason=S()),
        rules=TEXTS("Every other identity rule, in the brief's words."),
    ),
    account_fields=O(
        "Which get_account response field plays each role. Copy names exactly from the tool API's get_account response.",
        list=S("The response field that holds the array of late products, e.g. 'products'."),
        past_due=S("Item field with the full past-due (overdue) amount, e.g. 'past_due'. Not a minimum or instalment."),
        days_late=S("Item field with how many days the product is late, e.g. 'days_late'."),
        product_id=S("Item field with the product's id, e.g. 'product_id'."),
        type=S("Item field with the product type, e.g. 'type'."),
    ),
    products=A(O(
        type=S("Product type value as get_account returns it."),
        spoken=S("How to name it, with {field} placeholders for get_account fields, e.g. 'credit card ending {card_last4}'."),
        floor_rule={"$ref": "#/$defs/amount_expr"},
        deadline_rule={"$ref": "#/$defs/date_expr"},
    ), "One entry per product type."),
    debt=O(
        intro=S("The sentence said right after verification, before the products, e.g. 'I am calling from the collections team about a late payment.' Exactly what the brief requires, nothing more."),
        per_product=S("Template of the sentence stating one product, with placeholders {product} (its spoken name), {past_due}, {days_late}, {floor}, {deadline} or any get_account field in single braces. Say only what the brief requires."),
        rules=TEXTS("Every rule about stating the debt, in the brief's words."),
    ),
    calendar=O(
        business_weekdays=A({"type": "integer"}, "Business weekdays, 0=Monday .. 6=Sunday."),
        holidays=TEXTS("Every bank holiday, YYYY-MM-DD."),
        next_weekday=E(["following_week", "next_occurrence"], "Meaning of 'next <weekday>': the weekday in the following calendar week, or simply the next one to come."),
        week_starts=E(["monday", "sunday"]),
        earliest_promise_business_days=I("0 when a promise may be for today; N when it must be at least N business days ahead."),
        rules=TEXTS("Every date rule, in the brief's words (allowed promise dates, weekends/holidays, vague periods, dates after the deadline...)."),
    ),
    bands=A(O(
        name=S(), min_days_late=I(), max_days_late=I(),
        deadline_offset=I("N in 'the Nth business day after today' (0 = today)."),
        deadline_unit=E(["business_days", "calendar_days"]),
        attempts=I("Persuasion attempts allowed in this band."),
    ), "Lateness bands, covering every days_late value the brief mentions."),
    amount_rules=TEXTS("Every amount rule, in the brief's words."),
    first_ask=S("The exact first ask for payment (one product)."),
    multi_first_ask=S("The first ask when the customer has several products; '' if the brief does not say."),
    persuasion=O(
        counts_as_attempt=S("What uses a persuasion attempt and what never does, in the brief's words."),
        attempts=A(O(
            number=I(), bands=TEXTS("Band names this attempt applies to; empty = all bands."),
            date_rule=E(DATE_RULES, "end_of_week = the last business day of this week, or of next week when today is that day."),
            date_param=I("N for *_days_from_today, else 0."),
            amount_rule=E(AMOUNT_RULES), amount_param=N("Percent for percent_of_full, else 0."),
            text=S("The attempt in the brief's words."),
        )),
        exhausted=OR("Close when the attempts are used up."),
        rules=TEXTS("Every other promise-getting rule, in the brief's words."),
    ),
    confirmation=O(
        readback_question=S("The exact confirmation question after the read-back."),
        success_sentence=S("The exact sentence said after a promise is recorded; '' if none."),
        retries_on_failure=I("How many more times create_promise is tried after a failure."),
        failure_say=S("What to tell the customer when recording failed for good."),
        failure=OR("Close when recording failed for good."),
        rules=TEXTS("Every other confirming/recording rule, in the brief's words."),
    ),
    intents=A(O(
        name=S("snake_case name, e.g. already_paid, dispute, hardship, restructuring, human, robot, scam, unknown_question, refuses_to_talk."),
        trigger=S("What the customer says that triggers it."),
        instructions=S("Exactly what the agent does and says, step by step, in the brief's words, including precedence over other rules."),
        phase=E(["before_verification", "after_verification", "any"]),
        transfer_queue=S("transfer_to_agent queue used, '' if none."),
        close_outcome=S("Outcome it closes with, '' if it does not close."),
        close_reason=S("Reason it closes with, '' if none."),
        note_reason=S("add_note reason for the product when there are several products, '' if none."),
    ), "Every special situation the brief handles (paid, dispute, hardship, plan request, human, robot, scam, unanswerable question, refusing to talk...)."),
    notes=O(
        used=B("Whether add_note is ever used."),
        when=S("When to call add_note, in the brief's words; '' if never."),
        on_transfer=B("Whether notes are still added when the call is transferred."),
        single_product=B("Whether notes are also added when the customer has only one product."),
    ),
    multi_product=TEXTS("Every rule for customers with several products, in the brief's words. Empty if the brief has none."),
    outcomes=A(O(outcome=S(), reason=S("'' for an outcome without reason; one row per outcome/reason pair."), when=S()), "The full outcome table, one row per pair."),
    close_precedence=A(O(
        state=E(STATES, "A state the system tracks: transferred (a transfer succeeded), promise_recorded, promise_service_error (recording failed for good and nothing was recorded), callback_booked, identity_failed, all_notes_paid (several products, no promise, every note is paid), first_non_paid_note (several products, no promise)."),
        outcome=S(), reason=S("'' = take it from the transfer intent of the queue used (transferred) or from the note (first_non_paid_note), or none."),
    ), "Ordered: the first state that holds decides close_call. Only states the brief's closing rules support."),
    closing_rules=TEXTS("Every closing rule, in the brief's words."),
    verbatim_phrases=A(O(text=S("Exact quoted sentence."), when=S()), "Every sentence the brief quotes for the agent to say."),
    evidence=A(O(
        path=S("Rule-sheet path of the value, e.g. 'bands[1].attempts' or 'calendar.holidays[0]'."),
        quote=S("The brief's sentence or table row that states the value, copied character for character. When a memo overrides it, quote the memo."),
        derived=B("True only when the value is worked out from the quote rather than written in it, e.g. 'today or the next business day' -> 1."),
    ), "Proof for the key values, one entry each: every bands[i] min_days_late, max_days_late, deadline_offset and attempts; opening.max_asks; "
       "identity.max_wrong_total; third_party.callback window_start, window_end and business_days_ahead; confirmation.retries_on_failure; every calendar.holidays[i]."),
    overrides=TEXTS("Where a later instruction (memo, amendment, newer date) replaced an earlier one: what changed. Empty if none."),
    unmapped_rules=TEXTS("Every rule that fits nowhere above, in the brief's words. Nothing in the brief may be dropped."),
)

SCHEMA["$defs"] = {
    "amount_expr": EXPR("#/$defs/amount_expr", ["money", "percent_of", "account_field", "max", "min"], {}),
    "date_expr": EXPR("#/$defs/date_expr", ["today", "account_date", "band_deadline", "add_days"], {}),
}


# ---------------------------------------------------------------- the model call

SYS = """You turn a bank's collection-call brief into a precise JSON rule sheet. A program builds a phone agent from your sheet, so every value must come from the brief.
- Read every word. Most failures come from one skipped rule.
- floor_rule is an amount expression: money(value), percent_of(value, field='past_due'), account_field(field), or max/min(args=[expressions]).
- deadline_rule is a date expression: today, account_date(field), band_deadline, or add_days(args=[one date], value, unit). Use the product's own fields when the brief says product-specific; use band_deadline only when the band controls it.
- Every expression object has op, value, field, unit, args. Unused slots are 0, '', '', and []. money uses value; percent_of uses value as percent and field='past_due'; account_field/account_date use field; max/min use args; add_days uses one date arg plus value and unit.
- Preserve the brief's actual calculation in these expressions. If an expression cannot be represented, do not approximate it; explain it in unmapped_rules.
- The brief may be raw bank material: mails, a policy extract, memos and code lists. A later or dated instruction (a memo, an amendment) replaces an earlier one. Apply it and record the change in `overrides`.
- Map every outcome, reason, queue, identity field and product type to the exact values of the tool API enums. A code list in the brief tells you the mapping.
- Map every request field that carries the call's customer ID in `customer_id_fields`, using exact tool and field names from the API.
- The phone agent never sees the brief, so resolve cross-references: write "treat it as a flat refusal (one persuasion attempt)", not "rule 30" or "section 7".
- Copy quoted sentences word for word. Keep rules in the brief's own words where the schema asks for rules.
- Never invent a rule, value or phrase the brief does not give. When the brief is silent, use the neutral value ('' / 0 / false / []).
- When a rule fits no field, put it in `unmapped_rules`. Nothing may be dropped."""


def _ask(prompt, purpose, log):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    async def run():
        o = ClaudeAgentOptions(model=MODEL, tools=[], max_turns=4, setting_sources=[], system_prompt=SYS,
                               output_format={"type": "json_schema", "schema": SCHEMA})
        out = None
        async for m in query(prompt=prompt, options=o):
            if isinstance(m, ResultMessage):
                out = m
        return out

    t = time.time()
    for attempt in range(2):
        m = asyncio.run(run())
        log({"event": "model_call", "model": MODEL, "purpose": purpose, "seconds": round(time.time() - t, 1),
             "cost_usd": getattr(m, "total_cost_usd", None), "ok": bool(m and m.structured_output)})
        if m and isinstance(m.structured_output, dict):
            return m.structured_output
    raise RuntimeError("extraction returned no structured output: " + str(getattr(m, "result", ""))[:500])


def extract(brief_text, api, log=print):
    def ask_or_keep(prompt, purpose, current):
        """Repair/audit calls are improvements: if one fails (limit, outage), keep the sheet in hand."""
        try:
            return _ask(prompt, purpose, log)
        except Exception as e:
            log({"event": "model_call_failed", "purpose": purpose, "error": str(e)[:300]})
            return current
    def check(r):
        return validate(r, api, brief_text)
    ctx = f"BRIEF:\n<<<\n{brief_text}\n>>>\n\nTOOL API:\n<<<\n{api_spec.compact(api)}\n>>>\n"
    rules = _ask(ctx + "\nFill the rule sheet from the brief.", "extract the rule sheet from the brief", log)
    for rnd in range(2):
        errs = check(rules)
        if not errs:
            break
        log({"event": "validate", "errors": errs})
        rules = ask_or_keep(ctx + "\nYour rule sheet:\n" + json.dumps(rules) + "\n\nIt has these errors:\n- " + "\n- ".join(errs) +
                     "\n\nReturn the corrected full rule sheet.", f"repair rule sheet (round {rnd + 1})", rules)
    audited = ask_or_keep(ctx + "\nA first reader produced this rule sheet:\n" + json.dumps(rules) +
                   "\n\nAudit it against the brief, rule by rule. Find every rule that is missing, wrong, contradicted, or not in the "
                   "brief's words, every override from a memo that was not applied, every enum value that does not match the tool API, "
                   "and every quoted sentence that is not exact. Return the corrected full rule sheet.", "audit the rule sheet against the brief", rules)
    keep = len(check(audited)) <= len(check(rules))
    old, new = _flat(rules), _flat(audited)
    log({"event": "audit_changes", "kept": keep,
         "changes": [{"path": p, "before": old.get(p), "after": new.get(p)} for p in sorted(old.keys() | new.keys()) if old.get(p) != new.get(p)]})
    if keep:
        rules = audited
    errs = check(rules)
    if errs:
        log({"event": "validate", "errors": errs})
        fixed = ask_or_keep(ctx + "\nYour rule sheet:\n" + json.dumps(rules) + "\n\nIt has these errors:\n- " + "\n- ".join(errs) +
                     "\n\nReturn the corrected full rule sheet.", "repair rule sheet after audit", rules)
        if len(check(fixed)) < len(errs):
            rules = fixed
    return rules


# ---------------------------------------------------------------- validation

def validate(r, api, brief=""):
    errs = ground(r, brief) if brief else []
    seen_customer_tools = set()
    for item in r["customer_id_fields"]:
        fields = api["tools"].get(item["tool"], {}).get("fields", {})
        if item["field"] not in fields:
            errs.append(f"customer_id_fields: {item['field']!r} is not a request field of {item['tool']!r}")
        if item["tool"] in seen_customer_tools:
            errs.append(f"customer_id_fields: duplicate tool {item['tool']!r}")
        seen_customer_tools.add(item["tool"])
    outs = api_spec.enum(api, "close_call", "outcome")
    reasons = api_spec.enum(api, "close_call", "reason")
    table = {(o["outcome"], o["reason"]) for o in r["outcomes"]}
    for o, why in table:
        if outs and o not in outs:
            errs.append(f"outcome {o!r} is not in the close_call outcome enum {outs}")
        if reasons and why and why not in reasons:
            errs.append(f"reason {why!r} is not in the close_call reason enum {reasons}")

    def pair(where, outcome, reason, allow_empty_reason=False):
        if not outcome:
            return
        if (outcome, reason) not in table and not (allow_empty_reason and not reason and any(x == outcome for x, _ in table)):
            errs.append(f"{where}: {outcome}/{reason or '(no reason)'} is not a row of the outcome table")

    pair("opening.unclear", **r["opening"]["unclear"])
    cb = r["third_party"]["callback"]
    pair("callback.booked", **cb["booked"]); pair("callback.not_booked", **cb["not_booked"])
    for k in ("wrong_number", "deceased"):
        pair("third_party." + k, r["third_party"][k]["outcome"], r["third_party"][k]["reason"])
    for k in ("failed", "declined"):
        pair("identity." + k, r["identity"][k]["outcome"], r["identity"][k]["reason"])
    pair("persuasion.exhausted", **r["persuasion"]["exhausted"]); pair("confirmation.failure", **r["confirmation"]["failure"])
    for it in r["intents"]:
        pair("intent " + it["name"], it["close_outcome"], it["close_reason"])
    for p in r["close_precedence"]:
        pair("close_precedence " + p["state"], p["outcome"], p["reason"], allow_empty_reason=True)
    for t in (cb["window_start"], cb["window_end"]):
        if not re.match(r"^\d{2}:\d{2}$", t):
            errs.append(f"callback window time {t!r} is not HH:MM")

    fields = api_spec.enum(api, "verify_identity", "field")
    if not r["identity"]["questions"]:
        errs.append("identity.questions is empty")
    for q in r["identity"]["questions"]:
        if fields and q["field"] not in fields:
            errs.append(f"identity field {q['field']!r} is not in the verify_identity enum {fields}")
    queues = api_spec.enum(api, "transfer_to_agent", "queue")
    for it in r["intents"]:
        if it["transfer_queue"] and queues and it["transfer_queue"] not in queues:
            errs.append(f"intent {it['name']}: queue {it['transfer_queue']!r} is not in {queues}")
        if it["note_reason"] and api_spec.enum(api, "add_note", "reason") and it["note_reason"] not in api_spec.enum(api, "add_note", "reason"):
            errs.append(f"intent {it['name']}: note reason {it['note_reason']!r} is not in the add_note enum")
    af = r["account_fields"]
    resp = api["tools"].get("get_account", {}).get("response", {})
    if resp.get(af["list"], {}).get("type") != "array":
        errs.append(f"account_fields.list {af['list']!r} is not an array in the get_account response {list(resp)}")
    items = resp.get(af["list"], {}).get("items", {}).get("properties", {})
    def expr_error(x, kind, where):
        ops = {"amount": {"money", "percent_of", "account_field", "max", "min"}, "date": {"today", "account_date", "band_deadline", "add_days"}}[kind]
        if not isinstance(x, dict) or x.get("op") not in ops:
            errs.append(f"{where}: invalid {kind} expression")
            return
        op, args, field = x["op"], x["args"], x["field"]
        wants = {"money": (0, 0), "percent_of": (0, 0), "account_field": (0, 0), "max": (2, 100), "min": (2, 100), "today": (0, 0), "account_date": (0, 0), "band_deadline": (0, 0), "add_days": (1, 1)}[op]
        if not wants[0] <= len(args) <= wants[1]: errs.append(f"{where}: {op} needs {wants[0]}..{wants[1]} arguments")
        if op in ("max", "min", "add_days"):
            for a in args: expr_error(a, kind, where + ".args")
        if op == "account_field" and (field not in items or items.get(field, {}).get("type") not in ("number", "integer")):
            errs.append(f"{where}: {field!r} is not a numeric get_account field {list(items)}")
        if op == "account_date" and (field not in items or items.get(field, {}).get("type") != "string"):
            errs.append(f"{where}: {field!r} is not a string account date field")
        if op == "percent_of" and field != "past_due": errs.append(f"{where}: percent_of field must be 'past_due'")
    for role in ("past_due", "days_late", "product_id", "type"):
        if af[role] not in items:
            errs.append(f"account_fields.{role} {af[role]!r} is not a get_account item field {list(items)}")
    for role in ("past_due", "days_late"):
        if items.get(af[role], {}).get("type") not in ("number", "integer"):
            errs.append(f"account_fields.{role} {af[role]!r} is not numeric")
    types = items.get(af["type"], {}).get("enum") or []
    for p in r["products"]:
        expr_error(p["floor_rule"], "amount", f"product {p['type']}.floor_rule")
        expr_error(p["deadline_rule"], "date", f"product {p['type']}.deadline_rule")
        if types and p["type"] not in types:
            errs.append(f"product type {p['type']!r} is not in the get_account enum {types}")
    for t in types:
        if t not in [p["type"] for p in r["products"]]:
            errs.append(f"product type {t!r} from get_account has no products entry")
    for h in r["calendar"]["holidays"]:
        try:
            datetime.date.fromisoformat(h)
        except ValueError:
            errs.append(f"holiday {h!r} is not YYYY-MM-DD")
    bands = sorted(r["bands"], key=lambda b: b["min_days_late"])
    if not bands:
        errs.append("bands is empty")
    for a, b in zip(bands, bands[1:]):
        if b["min_days_late"] != a["max_days_late"] + 1:
            errs.append(f"bands {a['name']} and {b['name']} leave a gap or overlap")
    names = {b["name"] for b in bands}
    for at in r["persuasion"]["attempts"]:
        for b in at["bands"]:
            if b not in names:
                errs.append(f"attempt {at['number']}: band {b!r} is not a band name {sorted(names)}")
    known = {v["name"] for v in r["call_vars"]["examples"]}
    for tpl in (r["call_vars"]["address_as"], r["call_vars"]["full_name"], r["opening"]["first_sentence"], r["opening"]["confirm_question"]):
        for v in re.findall(r"\{\{(\w+)\}\}", tpl):
            if v not in known:
                errs.append(f"template variable {{{{{v}}}}} is not a call variable {sorted(known)}")
    return errs


# ---------------------------------------------------------------- grounding: key values must be quoted from the brief

WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
WORDS |= {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth".split())}
WORDS |= {"once": 1, "twice": 2}


def _norm(s):
    """Lowercase, single spaces, no markdown/brace/quote-style noise: what counts as 'word for word'."""
    s = re.sub(r"[*`{}]", "", str(s)).translate(str.maketrans("‘’“”", "''\"\""))
    return " ".join(s.lower().split())


def _flat(x, p=""):
    """Rule sheet -> {path: leaf value}, paths in evidence style ('bands.1.attempts')."""
    # ponytail: positional list paths; an inserted item shows as every later index changing
    if not isinstance(x, (dict, list)):
        return {p: x}
    out = {}
    for k, v in (x.items() if isinstance(x, dict) else enumerate(x)):
        out |= _flat(v, f"{p}.{k}" if p else str(k))
    return out


def _key(path):
    return ".".join(re.findall(r"[^.\[\]\s]+", path))


def _at(r, path):
    try:
        for k in _key(path).split("."):
            r = r[int(k)] if isinstance(r, list) else r[k]
        return r
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _states(quote, v):
    """Whether a normalised quote states value v (digits, number words, ordinals, or the literal text)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        said = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", quote)} | {float(WORDS[w]) for w in re.findall(r"[a-z]+", quote) if w in WORDS}
        return float(v) in said
    return _norm(v) in quote


def ground(r, brief):
    """Code-side check that key values and phrases come from the brief, not from the model. Errors feed the repair loop."""
    b, errs = _norm(brief), []
    for p in r["verbatim_phrases"]:
        if _norm(p["text"]) not in b:
            errs.append(f"verbatim phrase is not in the brief word for word: {p['text']!r}")
    if "evidence" not in r:  # ponytail: sheet from before grounding; re-extract to check its values
        return errs
    have = set()
    for e in r["evidence"]:
        have.add(_key(e["path"]))
        q, v = _norm(e["quote"]), _at(r, e["path"])
        if not q or q not in b:
            errs.append(f"evidence {e['path']}: the quote is not in the brief word for word: {e['quote'][:150]!r}")
        elif v is None:
            errs.append(f"evidence path {e['path']!r} is not a value in the rule sheet")
        elif not e["derived"] and not _states(q, v):
            errs.append(f"{e['path']} is {v!r}, but its quote does not state {v!r}: {e['quote'][:150]!r}. Fix the value or quote the text that states it.")
    need = [f"bands[{i}].{k}" for i in range(len(r["bands"])) for k in ("min_days_late", "max_days_late", "deadline_offset", "attempts")]
    def expr_paths(e, path):
        op = e["op"]
        out = [path + ".op"]
        if op in ("money", "percent_of", "add_days"): out.append(path + ".value")
        if op in ("account_field", "account_date", "percent_of"): out.append(path + ".field")
        if op == "add_days": out.append(path + ".unit")
        for i, child in enumerate(e["args"]): out += expr_paths(child, f"{path}.args.{i}")
        return out
    for i, p in enumerate(r["products"]):
        need += expr_paths(p["floor_rule"], f"products.{i}.floor_rule")
        need += expr_paths(p["deadline_rule"], f"products.{i}.deadline_rule")
    need += [f"calendar.holidays[{i}]" for i in range(len(r["calendar"]["holidays"]))]
    need += ["opening.max_asks", "identity.max_wrong_total", "third_party.callback.window_start", "third_party.callback.window_end",
             "third_party.callback.business_days_ahead", "confirmation.retries_on_failure"]
    missing = [p for p in need if _key(p) not in have]
    if missing:
        errs.append("evidence is missing for: " + ", ".join(missing))
    return errs
