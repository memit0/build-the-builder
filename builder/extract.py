"""Brief (+ tool API) -> rules.json: the rule sheet the generator reads. Claude extracts, code validates."""
import asyncio, datetime, json, os, re, time

import api_spec, policy

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
    products=A(O(
        type=S("Product type value as get_account returns it."),
        spoken=S("How to name it, with {field} placeholders for get_account fields, e.g. 'credit card ending {card_last4}'."),
        floor_field=S("The get_account field that holds the lowest amount that can be recorded; '' if a percentage is used."),
        floor_percent=N("Floor as a percent of the past-due amount when the brief uses one; 0 otherwise."),
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
    overrides=TEXTS("Where a later instruction (memo, amendment, newer date) replaced an earlier one: what changed. Empty if none."),
    unmapped_rules=TEXTS("Every rule that fits nowhere above, in the brief's words. Nothing in the brief may be dropped."),
)


# ---------------------------------------------------------------- the model call

EXECUTION = O(
    refused_note_reason=S("Exact add_note reason code for exhausted persuasion; empty if notes unused"),
    minimum_offer=O(limit=I("Maximum discretionary floor offers; -1 only if unspecified, 0 forbids."),
                    scope=E(["per_product", "per_call"]), repeat_on_attempts=A(I(), "Numbered attempts explicitly allowed to repeat the floor, even after the limit."),
                    source=S("Exact quote supporting the limit and exceptions; never infer a limit.")),
    intent_actions=A(O(intent=S("Existing intents[].name"), stop_negotiation=B("Disable persuasion after this intent"),
                       allow_customer_proposal=B("Allow a customer-initiated promise despite stopping persuasion"),
                       forbid_promise=B("Never record for affected product"), source=S("Exact source quote"))),
)
REQUIREMENT = O(id=S(), source=S("Exact quote from the brief; enumerate each obligation separately"),
                critical=B("Numbers, prerequisites, prohibitions, recording and closing are critical"),
                status=E(["active", "superseded"]), paths=A(S(), "Dot paths including array indices into this rule sheet; [] means unsupported"))
SCHEMA["properties"].update(execution=EXECUTION, requirements=A(REQUIREMENT))
SCHEMA["required"] += ["execution", "requirements"]
INVENTORY_SCHEMA = O(requirements=A(O(id=S(), source=S(), critical=B(), status=E(["active", "superseded"]))))
AUDIT_SCHEMA = O(requirements=A(REQUIREMENT), errors=TEXTS("Every missing, incorrect or contradictory requirement. Empty only after checking all of them."))

SYS = """You turn a bank's collection-call brief into a precise JSON rule sheet. A program builds a phone agent from your sheet, so every value must come from the brief.
- Read every word. Most failures come from one skipped rule.
- The brief may be raw bank material: mails, a policy extract, memos and code lists. A later or dated instruction (a memo, an amendment) replaces an earlier one. Apply it and record the change in `overrides`.
- Map every outcome, reason, queue, identity field and product type to the exact values of the tool API enums. A code list in the brief tells you the mapping.
- The phone agent never sees the brief, so resolve cross-references: write "treat it as a flat refusal (one persuasion attempt)", not "rule 30" or "section 7".
- Copy quoted sentences word for word. Keep rules in the brief's own words where the schema asks for rules.
- Never invent a rule, value or phrase the brief does not give. When the brief is silent, use the neutral value ('' / 0 / false / []).
- When a rule fits no field, put it in `unmapped_rules`. Nothing may be dropped."""


def _ask(prompt, purpose, log, schema=None):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    async def run():
        o = ClaudeAgentOptions(model=MODEL, tools=[], max_turns=4, setting_sources=[], system_prompt=SYS,
                               output_format={"type": "json_schema", "schema": schema or SCHEMA})
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
    ctx = f"BRIEF:\n<<<\n{brief_text}\n>>>\n\nTOOL API:\n<<<\n{api_spec.compact(api)}\n>>>\n"
    inventory = _ask(ctx + "\nIndependently enumerate EVERY atomic obligation, including conditions, exceptions and memo overrides. "
                     "Mark replaced requirements superseded. Do not extract a rule sheet yet.",
                     "independent source requirement inventory", log, INVENTORY_SCHEMA)
    rules = _ask(ctx + "\nSource inventory:\n" + json.dumps(inventory) +
                 "\nFill the rule sheet from the brief. Map every inventory ID to concrete paths. "
                 "Preserve all exceptions to minimum-offer limits in execution. Do not invent intent actions.", "extract the rule sheet from the brief", log)
    for rnd in range(2):
        errs = validate(rules, api)
        if not errs:
            break
        log({"event": "validate", "errors": errs})
        rules = ask_or_keep(ctx + "\nYour rule sheet:\n" + json.dumps(rules) + "\n\nIt has these errors:\n- " + "\n- ".join(errs) +
                     "\n\nReturn the corrected full rule sheet.", f"repair rule sheet (round {rnd + 1})", rules)
    audited = ask_or_keep(ctx + "\nA first reader produced this rule sheet:\n" + json.dumps(rules) +
                   "\n\nAudit it against the brief, rule by rule. Find every rule that is missing, wrong, contradicted, or not in the "
                   "brief's words, every override from a memo that was not applied, every enum value that does not match the tool API, "
                   "and every quoted sentence that is not exact. Return the corrected full rule sheet.", "audit the rule sheet against the brief", rules)
    if len(validate(audited, api)) <= len(validate(rules, api)):
        rules = audited
    errs = validate(rules, api)
    if errs:
        log({"event": "validate", "errors": errs})
        fixed = ask_or_keep(ctx + "\nYour rule sheet:\n" + json.dumps(rules) + "\n\nIt has these errors:\n- " + "\n- ".join(errs) +
                     "\n\nReturn the corrected full rule sheet.", "repair rule sheet after audit", rules)
        if len(validate(fixed, api)) < len(errs):
            rules = fixed
    audit = _ask(ctx + "\nIndependent inventory:\n" + json.dumps(inventory) + "\nCandidate sheet:\n" + json.dumps(rules) +
                 "\nCompare each inventory item with the actual values at its paths. Return the inventory IDs unchanged, "
                 "with paths into the sheet and an error for every missing or incorrectly implemented requirement. "
                 "Do not mark a requirement covered merely because similar words occur somewhere.",
                 "semantic completeness and value audit", log, AUDIT_SCHEMA)
    expected = {x["id"] for x in inventory["requirements"]}
    actual = {x["id"] for x in audit["requirements"]}
    if expected != actual or audit["errors"]:
        raise ValueError("Requirement audit failed: " + json.dumps({"missing_ids": sorted(expected - actual), "errors": audit["errors"]}))
    rules["requirements"] = audit["requirements"]
    originals = {x["id"]: x for x in inventory["requirements"]}
    for req in rules["requirements"]:
        original = originals[req["id"]]
        if any(req[k] != original[k] for k in ("source", "critical", "status")):
            raise ValueError(f"Requirement {req['id']} changed during audit")
        if req["source"] not in brief_text:
            raise ValueError(f"Requirement {req['id']} has no exact source quote")
    offer = rules["execution"]["minimum_offer"]
    if offer["limit"] >= 0 and offer["source"] not in brief_text:
        raise ValueError("Minimum-offer policy has no exact source quote")
    for action in rules["execution"]["intent_actions"]:
        if not action["source"] or action["source"] not in brief_text:
            raise ValueError(f"Intent action {action['intent']} has no exact source quote")
    return rules


# ---------------------------------------------------------------- validation

def validate(r, api):
    r = policy.legacy(r)
    structural = policy.schema_errors(r, SCHEMA)
    if structural:
        return structural
    errs = policy.validate(r)
    refused_note = r["execution"]["refused_note_reason"]
    if refused_note and refused_note not in api_spec.enum(api, "add_note", "reason"):
        errs.append("execution.refused_note_reason is not in the add_note reason enum")
    if r["requirements"] and r["notes"]["used"] and not refused_note:
        errs.append("executable refusal-note mapping is required when notes are used")
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
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", t):
            errs.append(f"callback window time {t!r} is not HH:MM")
    if cb["window_start"] > cb["window_end"]:
        errs.append("callback window start is after end")
    for name, value in (("identity.max_wrong_total", r["identity"]["max_wrong_total"]), ("opening.max_asks", r["opening"]["max_asks"])):
        if value <= 0:
            errs.append(f"{name} must be positive")
    for name, value in (("callback.business_days_ahead", cb["business_days_ahead"]), ("callback.extra_asks", cb["extra_asks_when_outside"]),
                        ("confirmation.retries", r["confirmation"]["retries_on_failure"]), ("calendar.earliest", r["calendar"]["earliest_promise_business_days"])):
        if value < 0:
            errs.append(f"{name} cannot be negative")
    weekdays = r["calendar"]["business_weekdays"]
    if not weekdays or len(set(weekdays)) != len(weekdays) or any(d < 0 or d > 6 for d in weekdays):
        errs.append("business_weekdays must be unique integers from 0 to 6")

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
    types = (api["tools"].get("get_account", {}).get("response", {}).get("products", {}).get("items", {}).get("properties", {}).get("type", {}).get("enum") or [])
    for p in r["products"]:
        if not 0 <= p["floor_percent"] <= 100:
            errs.append(f"product {p['type']}: floor percentage outside 0..100")
        if types and p["type"] not in types:
            errs.append(f"product type {p['type']!r} is not in the get_account enum {types}")
        if not p["floor_field"] and not p["floor_percent"]:
            errs.append(f"product {p['type']}: neither floor_field nor floor_percent")
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
    for b in bands:
        if b["min_days_late"] < 0 or b["max_days_late"] < b["min_days_late"] or b["deadline_offset"] < 0 or b["attempts"] < 0:
            errs.append(f"band {b['name']}: invalid bounds, deadline or attempt count")
    names = {b["name"] for b in bands}
    for at in r["persuasion"]["attempts"]:
        if at["date_rule"] == "other" or at["amount_rule"] == "other":
            errs.append(f"attempt {at['number']}: unsupported executable policy 'other'")
        if at["number"] <= 0 or at["date_param"] < 0 or not 0 <= at["amount_param"] <= 100:
            errs.append(f"attempt {at['number']}: invalid parameters")
        for b in at["bands"]:
            if b not in names:
                errs.append(f"attempt {at['number']}: band {b!r} is not a band name {sorted(names)}")
    for b in bands:
        attempts = sorted(a["number"] for a in r["persuasion"]["attempts"] if not a["bands"] or b["name"] in a["bands"])
        if attempts != list(range(1, b["attempts"] + 1)):
            errs.append(f"band {b['name']}: attempt ladder does not match declared count")
    known = {v["name"] for v in r["call_vars"]["examples"]}
    for tpl in (r["call_vars"]["address_as"], r["call_vars"]["full_name"], r["opening"]["first_sentence"], r["opening"]["confirm_question"]):
        for v in re.findall(r"\{\{(\w+)\}\}", tpl):
            if v not in known:
                errs.append(f"template variable {{{{{v}}}}} is not a call variable {sorted(known)}")
    return errs
