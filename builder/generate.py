"""rules.json + tool API -> Freya workflow dict. Pure Python, no model call. Graph shape is the one that passed on Brief A:
setup code -> opening -> security_check -> load_account -> prepare_deal code -> negotiate -> end."""
import json, re

import api_spec, snippets as SN, policy


def nobraces(s):
    """Text that goes into Python must not hold {{var}} templates."""
    return re.sub(r"\{\{(\w+)\}\}", r"\1", s) if isinstance(s, str) else s


def bullets(items, prefix="- "):
    return "\n".join(prefix + x for x in items if x and x.strip())


def orr(o):
    return f"outcome {o['outcome']}" + (f", reason {o['reason']}" if o.get("reason") else "")


def config(r, api):
    """The constant every snippet reads. Only what code needs, never a {{template}}."""
    r = policy.legacy(r)
    f = lambda *a, **k: api_spec.find_field(api, *a, **k)
    T = api["tools"]
    no_reason = sorted({o["outcome"] for o in r["outcomes"]} - {o["outcome"] for o in r["outcomes"] if o["reason"]})
    promise_outcome = next((p["outcome"] for p in r["close_precedence"] if p["state"] == "promise_recorded"), "promise")
    cfg = {
        "now_var": r["call_vars"]["now"],
        "now_example": next((v["example"] for v in r["call_vars"]["examples"] if v["name"] == r["call_vars"]["now"]), ""),
        "holidays": r["calendar"]["holidays"], "business_weekdays": r["calendar"]["business_weekdays"] or [0, 1, 2, 3, 4],
        "next_weekday": r["calendar"]["next_weekday"], "week_starts": r["calendar"]["week_starts"],
        "earliest": r["calendar"].get("earliest_promise_business_days", 0),
        "callback": {"allow_today": r["third_party"]["callback"]["allow_today"], "ahead": r["third_party"]["callback"]["business_days_ahead"],
                     "start": r["third_party"]["callback"]["window_start"], "end": r["third_party"]["callback"]["window_end"],
                     "extra_asks": r["third_party"]["callback"]["extra_asks_when_outside"],
                     "booked": r["third_party"]["callback"]["booked"], "not_booked": r["third_party"]["callback"]["not_booked"]},
        "bands": [{"name": b["name"], "min": b["min_days_late"], "max": b["max_days_late"], "offset": b["deadline_offset"], "unit": b["deadline_unit"], "attempts": b["attempts"]} for b in r["bands"]],
        "attempts": r["persuasion"]["attempts"],
        "products": [{k: p[k] for k in ("type", "spoken", "floor_field", "floor_percent")} for p in r["products"]],
        "debt_intro": r["debt"]["intro"], "debt_per_product": r["debt"]["per_product"],
        "first_ask": r["first_ask"], "multi_first_ask": r["multi_first_ask"],
        "questions": [{k: q[k] for k in ("field", "ask", "kind", "digits")} for q in r["identity"]["questions"]],
        "date_order": r["identity"]["numeric_date_order"],
        "max_wrong": r["identity"]["max_wrong_total"],
        "minimum_offer": r["execution"]["minimum_offer"],
        "intent_actions": r["execution"]["intent_actions"],
        "readback": r["confirmation"]["readback_question"],
        "hints": {h["value"]: {"instruction": h["instruction"], "wrong": h["counts_as_wrong"]} for h in r["identity"]["hints"]},
        "failed": {k: r["identity"]["failed"][k] for k in ("say", "outcome", "reason")},
        "confirm": {"retries": r["confirmation"]["retries_on_failure"], "success": r["confirmation"]["success_sentence"],
                    "failure_say": r["confirmation"]["failure_say"], "failure": r["confirmation"]["failure"]},
        "promise_outcome": promise_outcome,
        "queue_close": {i["transfer_queue"]: {"outcome": i["close_outcome"] or "transferred", "reason": i["close_reason"]} for i in r["intents"] if i["transfer_queue"]},
        "notes_on_transfer": r["notes"]["on_transfer"], "notes_single": r["notes"]["single_product"],
        "note_reasons": api_spec.enum(api, "add_note", "reason"),
        "refused_reason": r["persuasion"]["exhausted"]["reason"],
        "refused_note_reason": r["execution"]["refused_note_reason"] or next((i["note_reason"] for i in r["intents"] if i["close_reason"] == r["persuasion"]["exhausted"]["reason"] and i["note_reason"]), r["persuasion"]["exhausted"]["reason"]),
        "precedence": r["close_precedence"],
        "no_reason_outcomes": no_reason,
        "paths": {k: T[k]["path"] for k in T},
        "fields": {
            "id_field": f("verify_identity", "field"), "id_answer": f("verify_identity", "answer"),
            "cb_date": f("schedule_callback", "date", fmt="date"), "cb_time": f("schedule_callback", "time"),
            "p_pid": f("create_promise", "product_id", "product"), "p_date": f("create_promise", "date", fmt="date"),
            "p_amount": f("create_promise", "amount", type_="number"),
            "n_pid": f("add_note", "product_id", "product"), "n_reason": f("add_note", "reason"),
            "queue": f("transfer_to_agent", "queue"), "outcome": f("close_call", "outcome"), "reason": f("close_call", "reason"),
        },
    }

    def clean(x):
        if isinstance(x, dict): return {k: clean(v) for k, v in x.items()}
        if isinstance(x, list): return [clean(v) for v in x]
        return nobraces(x)
    return clean(cfg)


def code(cfg, keys, *parts):
    """A snippet with the config keys it reads. Compact JSON with spaced braces: no double brace ever reaches the platform."""
    js = json.dumps({k: cfg[k] for k in keys}, separators=(",", ":"), ensure_ascii=True)
    while "{{" in js or "}}" in js:
        js = js.replace("{{", "{ {").replace("}}", "} }")
    return "C = __import__('json').loads(" + repr(js) + ")\n" + "".join(parts)


# ---------------------------------------------------------------- tool builders

def api_tool(api, name, description, cust_var, fields=None, enums=None, pre=None, post=None, retries=0, mapping=None, tool_name=None):
    t = api["tools"][name]
    cust = api_spec.find_field(api, name, "customer_id", "customer")
    props = {}
    for k, s in t["fields"].items():
        if k == cust:
            props[k] = {"type": "string", "default": "{{" + cust_var + "}}"}
            continue
        p = {"type": s.get("type", "string"), "default": "{{" + k + "}}"}
        d = (fields or {}).get(k) or s.get("description", "")
        e = (enums or {}).get(k, s.get("enum"))
        if e:
            p["enum"] = list(e)
            d = (d + " " if d else "") + "One of: " + ", ".join(e) + "."
        if d:
            p["description"] = d
        props[k] = p
    tool = {
        "type": "function", "config": {"timeoutMs": 12000, "maxRetries": retries},
        "function": {"name": tool_name or name, "handler": "__function__:api_call", "description": description,
                     "parameters": {"type": "object", "properties": {
                         "url": {"type": "string", "default": api["base"] + t["path"]},
                         "method": {"type": "string", "default": "POST"},
                         "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                         "body": {"type": "object", "properties": props, "required": [k for k in t["required"] if k != cust]}}}},
    }
    tool["pre_process_code"] = (pre or "") + "\nif vars.get('call_closed') == 'yes':\n    config['url'] = ''\n"
    if post: tool["post_process_code"] = post
    if mapping: tool["response_mapping"] = mapping
    return tool


def bool_true(var):
    return [{"variable": var, "operator": "equals", "operand": "true"}, {"variable": var, "operator": "equals", "operand": "True"}]


CLOSED = {"target_node": "end", "description": "close_call succeeded",
          "condition": {"logic": "or", "conditions": bool_true("close_ok") + bool_true("close_call_response.data.ok")}}


def code_node(name, src, nxt, extra=()):
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                         "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": src}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": list(extra) + [{"target_node": nxt, "description": "always", "condition": {"operator": "always"}}],
    }


def conv(name, line, task, tools, transitions):
    return {"name": name, "node_type": "conversation", "on_enter": {"type": "tts", "text": line} if line else {"type": "llm"},
            "on_enter_first_entry_only": True, "task_message": task, "tools": tools, "transitions": transitions, "max_node_turns": 60}


# ---------------------------------------------------------------- prompts

def intent_lines(intents):
    out = []
    for i in intents:
        tail = []
        if i["phase"] == "any":  # its close differs by phase; the instructions say which
            pass
        elif i["transfer_queue"]:
            tail.append(f"call transfer_to_agent with queue {i['transfer_queue']}")
        if i["close_outcome"] and i["phase"] != "any":
            tail.append("close with " + orr({"outcome": i["close_outcome"], "reason": i["close_reason"]}))
        out.append(f"{i['name'].upper()}: when {i['trigger']} -> {i['instructions']}" + (f" (Tools: {'; '.join(tail)}.)" if tail else ""))
    return bullets(out)


def outcome_table(r):
    return bullets([f"{o['outcome']}" + (f" / {o['reason']}" if o["reason"] else "") + f": {o['when']}" for o in r["outcomes"]])


def prompts(r):
    cv, op, tp, idn = r["call_vars"], r["opening"], r["third_party"], r["identity"]
    who, addr = cv["full_name"], cv["address_as"]
    acts = lambda i: bool(i["transfer_queue"] or i["close_outcome"])
    glob = [i for i in r["intents"] if i["phase"] == "any" and not acts(i)]
    before = [i for i in r["intents"] if i["phase"] == "before_verification" or (i["phase"] == "any" and acts(i))]
    after = [i for i in r["intents"] if i["phase"] == "after_verification" or (i["phase"] == "any" and acts(i))]
    cb = tp["callback"]
    days = (["today (later than the current time)"] if cb["allow_today"] else []) + ([f"the next {cb['business_days_ahead']} business day(s)"] if cb["business_days_ahead"] else [])
    window = f"A callback can be for {' or '.join(days)}, from {cb['window_start']} to {cb['window_end']}, both included."

    system = f"""You are {r['agent_identity']}, making an outbound phone call to {who}. Speak English. Keep every turn to one to three short sentences. Address the customer as {addr}.

Current date and time for this call: {{{{{cv['now']}}}}}. Use only this, never your own clock.

Rules that hold in every step:
{intent_lines(glob)}
- Never say anything the bank's rules do not allow. Never invent figures, dates, reasons or terms.
- Answer a direct customer question before continuing the current step, unless the brief says otherwise. Then resume from the exact current state: do not restart or repeat a question, explanation, offer, or proposal unless the brief explicitly allows another one.
- The required opening disclosure that the call is recorded is not a claim that a payment promise was recorded. Say a promise was recorded only after create_promise returns ok true.
- When a step says to call a tool, actually call it. Saying you did is not enough. Call one tool per message.
- When a tool result has a field what_to_do_next, follow it exactly. After every tool result, speak to the customer or call the next tool. Never end a turn with nothing.
- Never say something is recorded, booked or transferred unless the tool result says ok true.
- If a tool call returns an error, call the same tool once more with the same values. If it fails again, tell the customer honestly that it did not work.
- Every call ends with exactly one close_call, in the same message as your thank-you and goodbye. Never call any tool after close_call.

Exact sentences the bank requires, word for word, when their situation comes up:
{bullets([f'"{p["text"]}" ({p["when"]})' for p in r['verbatim_phrases']])}"""

    opening_line = f"{op['first_sentence']} {op['confirm_question']}".strip()
    opening = f"""You have just said: "{opening_line}"
Your only job in this step is to find out who is on the line.

STRICT: never say why you are calling. Before identity is verified never say any of these, to anyone (relatives included, and the customer too at this stage): {', '.join(op['forbidden_before_verification'])}; and nothing about money, amounts, products or the account. If asked what the call is about, say you can only discuss it with {addr} once their identity is confirmed.

The bank's rules for this step:
{bullets(op['rules'] + [op['confirmation_rule']])}

Decide from the person's answer:

1. CONFIRMED (the bank's rule above says what counts): call transition_to_security_check immediately and say nothing else.

2. NOT CLEAR: answer their question first if they asked one, then ask again: "{op['confirm_question']}". You may ask at most {op['max_asks']} times in total. If it is still not a clear confirmation, thank them, say goodbye and call close_call with {orr(op['unclear'])}.

3. SOMEONE ELSE ANSWERED (anyone who is not the customer):
{bullets(tp['rules'], '   - ')}
   Callback rules: {window} {{{{callback_facts}}}}
   As soon as they name a day and a time, convert it to date YYYY-MM-DD and 24-hour time HH:MM and call schedule_callback, even if you think it is outside the window: the system checks it. If they give only a day, or only "morning"/"afternoon" with no hour, ask for an exact time first. Then follow what_to_do_next.
   If they cannot say when the customer can be reached, thank them, say goodbye and call close_call with {orr(cb['not_booked'])}.

4. WRONG NUMBER (the number does not belong to the customer): {tp['wrong_number']['say']} Thank them, say goodbye and call close_call with {orr(tp['wrong_number'])}.

5. DECEASED (they say the customer has died): say "{tp['deceased']['say']}" Ask nothing else. Say goodbye and call close_call with {orr(tp['deceased'])}.

Other situations:
{intent_lines(before)}"""

    qs = idn["questions"]
    ident_line = f"Thank you. {idn['intro']} {qs[0]['ask']}".strip()
    kinds = {"date": "Convert whatever they said to {fmt}. " + {"DMY": "A date given only as numbers is day, then month, then year.", "MDY": "A date given only as numbers is month, then day, then year.", "YMD": "A date given only as numbers is year, month, day."}[idn["numeric_date_order"]] + " If they give it in parts, wait until you have it all.",
             "digits": "Collect all parts first, then send the digits only ({fmt}) by concatenating them in order. If the customer supplies only some of the remaining digits, ask only for the digits still missing. As soon as all digits are supplied, call verify_identity once; do not read back or ask the customer to confirm the completed number.",
             "year": "Send the four-digit year ({fmt}).", "postcode": "Send it as {fmt}, letters and digits exactly as said.", "text": "Send it as {fmt}."}
    qlines = [f"{n + 1}. \"{q['ask']}\" -> call verify_identity with field {q['field']} and the answer. {kinds[q['kind']].format(fmt=q['how_to_send'])}" for n, q in enumerate(qs)]
    identity = f"""The person said they are {addr}. You have asked the first security question. Nothing about the account may be said until every question passes.

STRICT: never say why you are calling and never mention: {', '.join(op['forbidden_before_verification'])}, amounts, or any product in this step. You do not know the account details yet.

Security questions, one at a time, in this order:
{chr(10).join(qlines)}
Call verify_identity once the customer has supplied a complete answer, including one you suspect is wrong. Never judge a complete answer yourself. For an answer given in parts, collect and concatenate the parts in order; ask only for the missing part, and do not repeat or confirm the whole answer before calling verify_identity. After each result, follow what_to_do_next.
Keep track of which security question is currently active. If the customer asks a side question or gives an answer in pieces, answer or collect the missing piece and resume that same question. Do not go back to an earlier question or ask the active question again unless the bank's decline or retry rule explicitly requires it. A question about the reason for the call counts as declining, as the rule below says.

The bank's rules for this step:
{bullets(idn['rules'] + [h['value'] + ': ' + h['instruction'] for h in idn['hints']])}

When the check has failed: {idn['failed']['say']} Then thank them, say goodbye and call close_call with {orr(idn['failed'])}. Do not state any amount or product, even if asked.

Declining: {idn['declined']['rule']} The first time, explain: "{idn['declined']['explain']}" and ask the question again. When the rule says it closes: thank them, say goodbye and call close_call with {orr(idn['declined'])}.

Other situations:
{intent_lines(before)}"""

    neg_line = "Thank you, your identity is confirmed. {{debt_text}} {{first_ask_text}}"
    multi = r["multi_product"]
    notes = r["notes"]
    negotiate = f"""The customer is verified. You have just said: "{{{{debt_text}}}} {{{{first_ask_text}}}}"

TOOLS: create_promise (only after a clear yes to the read-back), {'add_note, ' if notes['used'] else ''}transfer_to_agent, close_call. When a rule below says to call a tool, call it.

FACTS (computed by the bank's system; trust them, never compute dates or amounts yourself):
{{{{deal_facts}}}}

== A. CHECK THESE FIRST, on every customer turn ==
{intent_lines(after)}

== B. GETTING THE PROMISE ==
- Look every date up in the DATE TABLE below and do exactly what its row says.
- Combine date details given across turns before choosing a date. Once an exact date is clear, anchor it to the call date and use that exact date when it is allowed. If it is a weekend or holiday and is not after the deadline, offer the next business day shown in the DATE TABLE; do not skip ahead to the deadline. Offer a product's deadline only when the customer's requested date is after that product's deadline.
- For a named date, find that exact date's row in the DATE TABLE and follow its instruction. Do not call a date "after the deadline" unless that row says it is after the deadline. A holiday or weekend row that says to offer the next business day takes precedence over offering the deadline.
- Use the phrase "latest date I can record" only when the customer's completed requested date is strictly later than the product's deadline. A requested holiday or weekend before the deadline is not a late request: offer its next business day from the DATE TABLE.
- After asking once for an exact day to replace a vague period, let the customer finish giving the date in pieces without repeating the date question. Ask another clarification only if the completed date is still genuinely ambiguous.
- A refusal never finishes a product by itself: after each flat refusal, make the next persuasion attempt FACTS lists for that product. A product is finished without a promise only when all its attempts were made and refused (or a rule in section A ends it).
- Use only the amounts, floors, deadlines and attempts in FACTS. Count the persuasion attempts you already made{' for each product separately' if multi else ''}; never exceed the number allowed in FACTS. Do not repeat an offer the customer already refused.
- For each product, make only the numbered proposals in FACTS. One refusal advances to the next numbered attempt; it does not allow another proposal within the same attempt. Never treat a flat "no" as saying the full amount is too much. Offer the floor on attempt 1 only when the customer explicitly said the full amount is too much and the brief allows it, or when that numbered attempt specifically says to offer the floor.
- The first ask about paying today is not a numbered persuasion attempt. If the customer says the full amount is too much, do not immediately offer the floor for today: use the floor in the next numbered attempt that allows it (FACTS says so on that attempt; often attempt 1), on that attempt's own listed date, not a later attempt's date. If the customer accepts the floor amount but proposes another allowed date, keep the amount and work only with the new date; do not offer the floor again.
- A non-answer or unrelated response does not permit repeating the current proposal. It still uses the attempt if the brief says non-answers count; move to the next numbered attempt, or stop and close/note when none remain. Never repeat an exhausted offer while waiting for a response.
- When the attempts are used up and they still refuse: stop and close with {orr(r['persuasion']['exhausted'])}{' (or note the product, when there are several)' if multi else ''}.
The bank's rules:
{bullets([r['persuasion']['counts_as_attempt']] + r['amount_rules'] + r['calendar']['rules'] + r['persuasion']['rules'] + r['debt']['rules'])}
{('''
== SEVERAL PRODUCTS (FACTS says how many) ==
Process products in get_account order. For each product, finish its state before moving on: either record the customer's own allowed proposal, or make every numbered attempt FACTS allows and stop when they are refused. A refusal stated before you have made an attempt does not skip that product's required attempts. After a promise is agreed for one product, continue through every remaining product. Do not start the combined read-back until every product is either promised or has exhausted its attempts (or a bank rule ends the call). Then read back all agreed promises together once and ask for one confirmation.
''' + bullets(multi)) if multi else ''}

== C. CONFIRM AND RECORD ==
- Read back the product, the amount and the date (weekday, day, month), then ask exactly "{r['confirmation']['readback_question']}".
- Only after a clear yes, call create_promise with product_id (copied from FACTS), amount (a number, e.g. 637.5) and date (YYYY-MM-DD from the DATE TABLE). One call per product.
- The date sent to create_promise must be the same exact date you just read back and the customer confirmed. Copy its ISO date from the DATE TABLE; do not shift it by a day.
- After create_promise, follow what_to_do_next exactly. Only say "recorded" when the result says ok true.
The bank's rules:
{bullets(r['confirmation']['rules'])}
{('''
== D. NOTES ==
''' + bullets([notes['when']]) + f'''
- add_note takes the product_id from FACTS, one reason and attempts_made (the numbered attempts you proposed for that product and they refused). If it says the note was NOT added because attempts are missing, make the attempt it names. {"Never add a note on a transferred call." if not notes['on_transfer'] else ''}''') if notes['used'] else ''}

== E. CLOSING ==
{bullets(r['closing_rules'])}
Outcome table:
{outcome_table(r)}
After transfer_to_agent returns ok true: tell the customer you are connecting them now, thank them, say goodbye and in the same message call close_call as what_to_do_next says.

== OTHER RULES FROM THE BANK ==
{bullets(r['unmapped_rules'])}

DATE TABLE:
{{{{date_table}}}}"""

    account_error = f"""The customer is verified, but the account details could not be loaded. Do not state any amount. Apologise that you cannot access the details right now, thank the customer, say goodbye and in the same message call close_call with {orr(r['confirmation']['failure'])}."""
    return dict(system=system, opening_line=opening_line, opening=opening, ident_line=ident_line, identity=identity,
                neg_line=neg_line, negotiate=negotiate, account_error=account_error, before=before, after=after)


# ---------------------------------------------------------------- the workflow

CLOSE_KEYS = ["fields", "precedence", "queue_close", "note_reasons", "no_reason_outcomes"]


def generate(r, api):
    r = policy.legacy(r)
    import extract
    errors = extract.validate(r, api)
    if errors:
        raise ValueError("Invalid rule sheet: " + "; ".join(errors))
    cfg = config(r, api)
    P = prompts(r)
    cv = r["call_vars"]["customer_id"]
    outs = api_spec.enum(api, "close_call", "outcome") or sorted({o["outcome"] for o in r["outcomes"]})
    reasons = api_spec.enum(api, "close_call", "reason") or sorted({o["reason"] for o in r["outcomes"] if o["reason"]})
    F = cfg["fields"]
    H = SN.BASE + SN.PROD_HELPERS

    def close(name="close_call"):
        return api_tool(api, "close_call", "Ends the call in the bank's records. CALL it exactly once, as the very last action, in the same message in which you thank the customer and say goodbye.", cv,
                        fields={F["outcome"]: "The outcome, from the outcome table.", F["reason"]: "The reason, from the outcome table. Leave it out when the table lists none."},
                        enums={F["outcome"]: outs, F["reason"]: reasons}, pre=code(cfg, CLOSE_KEYS, H, SN.CLOSE_PRE), post=code(cfg, [], SN.CLOSE_POST),
                        mapping=[{"path": "data.ok", "variable": "close_ok"}], tool_name=name)

    def transfer(intents):
        qs = [i["transfer_queue"] for i in intents if i["transfer_queue"]]
        if not qs:
            return []
        when = "; ".join(f"queue {i['transfer_queue']} when {i['trigger']}" for i in intents if i["transfer_queue"])
        return [api_tool(api, "transfer_to_agent", "Hands the call to a person. CALL it only as the bank's rules say: " + when + ".", cv,
                         enums={F["queue"]: list(dict.fromkeys(qs))}, pre=code(cfg, ["fields"], SN.TRANSFER_PRE), post=code(cfg, ["fields", "queue_close"], SN.TRANSFER_POST))]

    verify = api_tool(api, "verify_identity", "Checks one security answer. CALL it every time the customer answers a security question, once per answer.", cv,
                      fields={F["id_answer"]: "The customer's answer in the format the question needs: " + "; ".join(f"{q['field']}: {q['how_to_send']}" for q in r["identity"]["questions"]) + "."},
                      pre=code(cfg, ["fields", "questions", "date_order"], SN.BASE, SN.VERIFY_PRE), post=code(cfg, ["questions", "hints", "failed", "max_wrong"], SN.BASE, SN.VERIFY_POST), mapping=[{"path": "data.all_verified", "variable": "all_verified"}])
    callback = api_tool(api, "schedule_callback", "Books a callback for the customer. CALL it as soon as the person who answered names a day and a time when the customer can be reached.", cv,
                        fields={F["cb_date"]: "YYYY-MM-DD.", F["cb_time"]: "HH:MM, 24-hour."}, pre=code(cfg, ["fields", "callback", "paths"], SN.BASE, SN.CB_PRE), post=code(cfg, ["callback"], SN.BASE, SN.CB_POST))
    promise = api_tool(api, "create_promise", f"Records one payment promise for one product. CALL it only after you read back product, amount and date and the customer clearly said yes to \"{r['confirmation']['readback_question']}\". If it fails, follow what_to_do_next.", cv,
                       fields={F["p_pid"]: "The product_id from FACTS, copied exactly (p1, p2, ...).", F["p_amount"]: "The agreed amount as a number, e.g. 637.5.", F["p_date"]: "The agreed date, YYYY-MM-DD."},
                       pre=code(cfg, ["fields", "paths"], H, SN.PROMISE_PRE), post=code(cfg, ["confirm", "promise_outcome"], H, SN.PROMISE_POST))
    policy_tool = {
        "type": "function", "config": {"timeoutMs": 10000, "maxRetries": 0},
        "function": {"name": "policy_step", "handler": "__function__:code_exec",
                     "description": "Advance the bank policy state before any offer, read-back, confirmation or intent. Never invent your own proposal. After this tool follow the routed policy instruction. Fill unused arguments with amount 0, date empty, too_much false, intent empty; do not carry arguments from a previous action.",
                     "parameters": {"type": "object", "properties": {
                         "code": {"type": "string", "default": code(cfg, ["minimum_offer", "intent_actions", "readback"], H, SN.POLICY_STEP)},
                         "timeout_ms": {"type": "number", "default": 10000},
                         "action": {"type": "string", "enum": ["offer", "minimum", "refuse", "stage", "change", "readback", "confirm", "intent"]},
                         "product_id": {"type": "string", "description": "Exact get_account product id"},
                         "amount": {"type": "number"}, "date": {"type": "string", "description": "Exact ISO date"},
                         "too_much": {"type": "boolean"}, "intent": {"type": "string"},
                     }, "required": ["action", "product_id", "amount", "date", "too_much", "intent"]}},
    }
    neg_tools = [policy_tool, promise]
    P["negotiate"] += """

MANDATORY POLICY STATE PROTOCOL (takes precedence over manual counting above):
- Before making any numbered persuasion offer call policy_step action offer with the product_id and too_much reflecting what the customer actually said. Speak only the permitted proposal returned by the policy route; never repeat it.
- A refusal to an issued numbered offer calls policy_step refuse once. A refusal to the initial ask goes straight to offer; do not count it as a refusal of an unissued numbered offer.
- For a discretionary minimum offer after a too-small customer proposal or 'too much', call policy_step minimum with its agreed allowed date. If a numbered attempt is due, use offer instead. Never verbally offer a minimum outside this protocol.
- For an agreed customer proposal call policy_step stage. On any changed value call change immediately to invalidate confirmation. For multiple products stage them all before a combined read-back.
- Call policy_step readback when all agreed proposals are staged and all other products are finished. Read ONLY the computed values and exact question, then wait for the customer's clear yes. Only that yes permits policy_step confirm; it confirms the combined staged set. Never call confirm on a question, silence or a refusal. Then create_promise copies the exact confirmed values.
- If a supported special intent applies, call policy_step intent BEFORE any other action. Do not use it for an unrelated side question.
- Neither the code tool nor the bank tools authorize you to assume customer agreement. Follow the actual customer's words.
"""
    if r["notes"]["used"]:
        note = api_tool(api, "add_note", "Records why one product ended without a promise. CALL it as the bank's note rules say: " + r["notes"]["when"], cv,
                        fields={F["n_pid"]: "The product_id from FACTS, copied exactly."}, pre=code(cfg, ["fields", "paths", "notes_on_transfer", "notes_single", "refused_reason", "refused_note_reason"], H, SN.NOTE_PRE), post=code(cfg, [], H, SN.NOTE_POST))
        # Not sent to the bank: NOTE_PRE pops it and blocks a refusal note until this reaches the product's allowed attempts.
        note["function"]["parameters"]["properties"]["body"]["properties"]["attempts_made"] = {
            "type": "integer", "default": "{{attempts_made}}",
            "description": "How many numbered persuasion attempts from FACTS you actually proposed for THIS product and the customer refused. The first ask about paying today does not count."}
        neg_tools.append(note)

    get_account = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 2},
                   "function": {"name": "get_account", "handler": "__function__:api_call", "description": "Get the late products.",
                                "parameters": {"type": "object", "properties": {
                                    "url": {"type": "string", "default": api["base"] + api["tools"]["get_account"]["path"]},
                                    "method": {"type": "string", "default": "POST"},
                                    "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                    "body": {"type": "object", "properties": {api_spec.find_field(api, "get_account", "customer_id", "customer"): {"type": "string", "default": "{{" + cv + "}}"}}, "required": []}}}}}

    get_account["pre_process_code"] = "if vars.get('id_state') != 'verified':\n    config['url'] = ''\n"
    verify["pre_process_code"] += "\nif vars.get('id_state') in ('failed', 'verified'):\n    config['url'] = ''\n"

    fallback = close("close_call_fallback")
    fallback["function"]["description"] = "Records how the call ended, if it was not closed during the call."
    fallback["pre_process_code"] = code(cfg, CLOSE_KEYS + ["paths"], H, SN.CLOSE_PRE, SN.FALLBACK_CLOSE_EXTRA)
    fallback.pop("post_process_code", None); fallback.pop("response_mapping", None)
    fallback["config"]["alwaysRunsAt"] = ["end_of_call"]

    dk = ["holidays", "business_weekdays", "now_var", "now_example"]
    setup = code(cfg, dk + ["callback"], SN.BASE, SN.DATES, SN.SETUP)
    account = code(cfg, dk + ["bands", "attempts", "products", "debt_intro", "debt_per_product", "first_ask", "multi_first_ask", "week_starts", "next_weekday", "earliest"], SN.BASE, SN.DATES, SN.ACCOUNT)
    ok = lambda v, val: {"logic": "or", "conditions": [{"variable": v, "operator": "equals", "operand": val}]}
    nodes = {
        "setup": code_node("setup", setup, "setup_retry", [{"target_node": "opening", "description": "setup done", "condition": ok("setup_state", "done")}]),
        "setup_retry": code_node("setup_retry", setup, "account_error", [{"target_node": "opening", "description": "setup done", "condition": ok("setup_state", "done")}]),
        "opening": conv("opening", P["opening_line"], P["opening"], [callback] + transfer(P["before"]) + [close()], [
            CLOSED,
            {"target_node": "security_check", "description": "The person on the line clearly confirmed they are the customer", "is_tool_call": True,
             "condition": {"operator": "matches_intent", "description": "The person clearly confirmed they are the customer"}},
        ]),
        "security_check": conv("security_check", P["ident_line"], P["identity"], [verify] + transfer(P["before"]) + [close()], [
            {"target_node": "load_account", "description": "Every security answer verified",
             "condition": ok("id_state", "verified")},
            CLOSED,
        ]),
        "load_account": {
            "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "prepare_deal",
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [get_account],
            "response_mapping": [{"path": "data.products", "variable": "products"}],
            "transitions": [{"target_node": "prepare_deal", "description": "always", "condition": {"operator": "always"}}],
        },
        "prepare_deal": code_node("prepare_deal", account, "prepare_retry", [{"target_node": "negotiate", "description": "account ready", "condition": ok("acct_state", "ok")}]),
        "prepare_retry": code_node("prepare_retry", account, "account_error", [{"target_node": "negotiate", "description": "account ready", "condition": ok("acct_state", "ok")}]),
        "negotiate": conv("negotiate", P["neg_line"], P["negotiate"], neg_tools + transfer(P["after"]) + [close()], [CLOSED, {"target_node": "policy_dispatch", "description": "policy action processed", "condition": ok("policy_pending", "yes")}]),
        "account_error": conv("account_error", "", P["account_error"], [close()], [CLOSED]),
        "end": {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
                "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                                                               "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}},
    }
    nodes["policy_dispatch"] = code_node("policy_dispatch", "vars['policy_pending'] = 'no'\n", "policy_result")
    nodes["policy_result"] = conv("policy_result", "", P["negotiate"] + "\nCURRENT POLICY INSTRUCTION:\n{{policy_instruction}}", nodes["negotiate"]["tools"], nodes["negotiate"]["transitions"])
    nodes["policy_result"]["on_enter_first_entry_only"] = False
    test_vars = {v["name"]: v["example"] for v in r["call_vars"]["examples"]}
    test_vars.setdefault(cv, "c_test")
    return {"initial_node": "setup", "system_prompt": P["system"], "nodes": nodes, "global_nodes": {}, "tools": [fallback], "test_variables": test_vars}
