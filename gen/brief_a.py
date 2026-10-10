"""Generates out/brief-a.workflow.json for Brief A (Freya Bank early-stage collections)."""
import json, pathlib

BASE = "https://freya-bank-mock.freyavoice.workers.dev/v1"
ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- python snippets (no double braces allowed)

COMMON = r'''
import datetime, json, re
HOL = ['2026-10-26', '2026-11-11', '2026-11-26', '2026-12-25', '2027-01-01']
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
def isbd(d):
    return d.weekday() < 5 and d.isoformat() not in HOL
def nextbd(d):
    d = d + datetime.timedelta(days=1)
    while not isbd(d):
        d = d + datetime.timedelta(days=1)
    return d
def say(d):
    return DAYS[d.weekday()] + ' ' + str(d.day) + ' ' + d.strftime('%B') + ' ' + str(d.year) + ' (' + d.isoformat() + ')'
def get_now():
    try:
        return datetime.datetime.fromisoformat(str(vars.get('now') or '').strip()[:16])
    except Exception:
        return datetime.datetime(2026, 10, 14, 11, 0)
'''

SETUP_CODE = COMMON + r'''
try:
    now = get_now()
    today = now.date()
    nb = nextbd(today)
    hm = now.strftime('%H:%M')
    days = []
    if isbd(today) and hm < '19:00':
        days.append(today.isoformat())
        t1 = 'today, ' + say(today) + ', at a time later than ' + hm + ' and no later than 19:00'
    else:
        t1 = 'today is not possible'
    days.append(nb.isoformat())
    vars['cb_days'] = ','.join(days)
    vars['cb_today'] = today.isoformat()
    vars['now_hm'] = hm
    vars['cb_fail_n'] = 0
    vars['callback_facts'] = ('Current date and time: ' + say(today) + ', ' + hm + '. A callback can be booked only for: ' + t1 +
        '; or the next business day, ' + say(nb) + ', between 10:00 and 19:00. "Tomorrow" means ' + say(today + datetime.timedelta(days=1)) + '.')
    vars['setup_state'] = 'done'
except Exception as e:
    vars['callback_facts'] = 'A callback can be booked for today (later than now) or the next business day, between 10:00 and 19:00.'
    vars['setup_state'] = 'error'
'''

ACCOUNT_CODE = COMMON + r'''
def load_products():
    p = vars.get('products')
    if isinstance(p, str):
        s = p.strip()
        try:
            p = json.loads(s)
        except Exception:
            try:
                import ast
                p = ast.literal_eval(s)
            except Exception:
                p = None
    if isinstance(p, dict):
        p = [p]
    if isinstance(p, list) and p and isinstance(p[0], dict):
        return p
    item = dict()
    for k, v in list(vars.items()):
        m = re.search(r'products\W*0\W*([a-z_0-9]+)$', str(k))
        if m:
            item[m.group(1)] = v
    if item:
        return [item]
    return []
def num(x):
    try:
        return float(x)
    except Exception:
        return 0.0
def money(x):
    return '{:,.2f}'.format(x)
try:
    prods = load_products()
    pr = prods[0]
    now = get_now()
    today = now.date()
    typ = str(pr.get('type') or '')
    full = num(pr.get('past_due'))
    dl = int(num(pr.get('days_late')))
    if typ == 'credit_card':
        phrase = 'credit card ending ' + str(pr.get('card_last4') or '')
        floor = num(pr.get('minimum_payment'))
        fname = 'minimum payment'
    elif typ == 'car_loan':
        phrase = 'car loan'
        floor = num(pr.get('instalment_amount'))
        fname = 'instalment amount'
    else:
        phrase = 'personal loan'
        floor = num(pr.get('instalment_amount'))
        fname = 'instalment amount'
    if floor <= 0 or floor > full:
        floor = full
    early = dl <= 7
    n = 12 if early else 7
    d = today
    for i in range(n):
        d = nextbd(d)
    deadline = d
    # attempt 1: last business day of this week, or of next week if today is that day
    def last_bd_of_week(start):
        mon = start - datetime.timedelta(days=start.weekday())
        x = mon + datetime.timedelta(days=4)
        while x >= mon and not isbd(x):
            x = x - datetime.timedelta(days=1)
        return x
    a1 = last_bd_of_week(today)
    if a1 <= today:
        a1 = last_bd_of_week(today + datetime.timedelta(days=7))
    if a1 > deadline:
        a1 = deadline
    valid = []
    rows = []
    x = today
    end = deadline + datetime.timedelta(days=3)
    while x <= end:
        label = DAYS[x.weekday()][:3] + ' ' + str(x.day) + ' ' + x.strftime('%b') + ' = ' + x.isoformat() + ': '
        if x > deadline:
            label += 'AFTER the deadline -> say the latest date you can record is ' + say(deadline) + ' and offer it'
        elif isbd(x):
            label += 'allowed' + (' (today)' if x == today else '')
            valid.append(x.isoformat())
        else:
            why = 'bank holiday' if x.weekday() < 5 else 'weekend'
            label += 'NOT allowed (' + why + ') -> offer the next business day, ' + say(nextbd(x))
        rows.append(label)
        x = x + datetime.timedelta(days=1)
    rows.append('Any date after ' + end.isoformat() + ': AFTER the deadline -> offer ' + say(deadline))
    rows.append('Any date before today: not allowed.')
    mon = today - datetime.timedelta(days=today.weekday())
    wk = []
    for i in range(7):
        t = mon + datetime.timedelta(days=i)
        wk.append('"this ' + DAYS[i] + '" = ' + say(t) + (' (in the past)' if t < today else '') + '; "next ' + DAYS[i] + '" = ' + say(t + datetime.timedelta(days=7)))
    attempts = 1 if early else 2
    vars['product_id'] = str(pr.get('product_id') or 'p1')
    vars['product_phrase'] = phrase
    vars['full_amount'] = round(full, 2)
    vars['floor_amount'] = round(floor, 2)
    vars['full_text'] = money(full)
    vars['floor_text'] = money(floor)
    vars['days_late_n'] = dl
    vars['deadline_iso'] = deadline.isoformat()
    vars['valid_dates'] = ','.join(valid)
    vars['promise_state'] = 'none'
    vars['promise_fail_n'] = 0
    facts = []
    facts.append('Today: ' + say(today) + '. Time now: ' + now.strftime('%H:%M') + '.')
    facts.append('Product: ' + phrase + ' (product_id ' + vars['product_id'] + ').')
    facts.append('FULL past-due amount: ' + money(full) + '. Days late: ' + str(dl) + '.')
    facts.append('FLOOR (' + fname + '): ' + money(floor) + '. Never record less than this, never more than the full amount.')
    facts.append('Band: ' + ('early' if early else 'late') + '. DEADLINE (latest date you may record): ' + say(deadline) + '.')
    facts.append('Persuasion attempts allowed in this call: ' + str(attempts) + '.')
    facts.append('Attempt 1 = propose the full amount ' + money(full) + ' (or the floor ' + money(floor) + ' if the customer already said the full amount is too much) on ' + say(a1) + '.')
    if attempts == 2:
        facts.append('Attempt 2 = propose the floor ' + money(floor) + ' on the deadline, ' + say(deadline) + '.')
    else:
        facts.append('There is NO attempt 2 in this call. After attempt 1 is refused, close the call as refused.')
    vars['deal_facts'] = '\n'.join(facts)
    vars['date_table'] = '\n'.join(rows) + '\nWeekday words:\n' + '\n'.join(wk)
    vars['acct_state'] = 'ok' if full > 0 else 'error'
except Exception as e:
    vars['acct_state'] = 'error'
    vars['deal_facts'] = 'Account details are not available.'
    vars['date_table'] = ''
    vars['product_phrase'] = 'account'
    vars['full_text'] = ''
    vars['days_late_n'] = ''
    vars['acct_error'] = str(e)[:200]
'''

DOB_PRE = r'''
try:
    import re, datetime
    b = config.get('body')
    if isinstance(b, dict):
        f = str(b.get('field') or '')
        a = str(b.get('answer') or '').strip()
        if f == 'phone_last4':
            dg = re.sub(r'\D', '', a)
            if len(dg) >= 4:
                b['answer'] = dg[-4:]
        elif f == 'date_of_birth' and not re.match(r'^\d{4}-\d{2}-\d{2}$', a):
            MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
            low = a.lower()
            y = re.search(r'(19|20)\d\d', low)
            mon = None
            for i, m in enumerate(MON):
                if re.search(r'\b' + m, low):
                    mon = i + 1
                    break
            out = ''
            if y and mon:
                rest = low.replace(y.group(0), ' ')
                dd = re.search(r'\b(\d{1,2})', rest)
                if dd:
                    out = '%s-%02d-%02d' % (y.group(0), mon, int(dd.group(1)))
            else:
                nums = re.findall(r'\d+', low)
                if len(nums) == 3:
                    if len(nums[0]) == 4:
                        yy, mm, dd2 = nums[0], int(nums[1]), int(nums[2])
                    else:
                        dd2, mm, yy = int(nums[0]), int(nums[1]), nums[2]
                        if len(yy) == 2:
                            yy = ('19' if int(yy) > 30 else '20') + yy
                    out = '%s-%02d-%02d' % (yy, mm, dd2)
            if out:
                b['answer'] = out
except Exception:
    pass
'''

VERIFY_POST = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            left = d.get('wrong_answers_left')
            if d.get('all_verified') is True:
                V['id_state'] = 'verified'
                d['what_to_do_next'] = 'Both answers are correct. The customer is verified.'
            elif d.get('hint') == 'day_month_swapped':
                d['what_to_do_next'] = 'Day and month were the other way round. This is NOT a wrong answer. Ask the customer to say the date of birth again with the month as a word, then call verify_identity again.'
            elif d.get('ok') is False and left is None:
                d['what_to_do_next'] = 'The answer could not be read. This is NOT a wrong answer. Ask the same question again (for a date: day, month as a word, and year), then call verify_identity again.'
            elif d.get('verified') is True:
                d['what_to_do_next'] = 'Correct. Now ask the second security question: the last four digits of the phone number on file. Then call verify_identity with field phone_last4.'
            elif isinstance(left, int) and left <= 0 or d.get('error'):
                V['id_state'] = 'failed'
                d['what_to_do_next'] = 'That answer is wrong and the identity check has FAILED (three wrong answers). Do not ask anything more and say nothing about any account, product, amount or why you called. Tell the customer to use the Freya Bank mobile app or visit a branch. Thank the customer, say goodbye, and in the same message call close_call with outcome not_verified and reason failed.'
            else:
                d['what_to_do_next'] = 'That answer is wrong. Wrong answers left: ' + str(left) + '. Tell the customer it did not match and ask the same question again, then call verify_identity again.'
except Exception:
    pass
'''

CB_PRE = r'''
try:
    import re
    V = vars if isinstance(vars, dict) else dict()
    b = config.get('body')
    if isinstance(b, dict):
        t = str(b.get('time') or '').strip().lower()
        m = re.search(r'(\d{1,2})(?::|\.|h)?(\d{2})?', t)
        hh = None
        if m:
            hh = int(m.group(1)); mm = int(m.group(2) or 0)
            if ('pm' in t or 'afternoon' in t or 'evening' in t) and hh < 12:
                hh += 12
            b['time'] = '%02d:%02d' % (hh, mm)
        tm = str(b.get('time') or '')
        dt = str(b.get('date') or '')[:10]
        days = str(V.get('cb_days') or '').split(',')
        ok = bool(re.match(r'^\d{2}:\d{2}$', tm)) and '10:00' <= tm <= '19:00'
        if V.get('cb_days') and dt not in days:
            ok = False
        if dt == str(V.get('cb_today') or '') and tm <= str(V.get('now_hm') or ''):
            ok = False
        if not ok:
            config['url'] = config.get('url', '').replace('/schedule_callback', '/callback_outside_window')
            V['cb_block'] = 'yes'
        else:
            V['cb_block'] = ''
except Exception:
    pass
'''

CB_POST = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(); result['data'] = d
        if d.get('ok') is True:
            V['callback_state'] = 'booked'
            d['what_to_do_next'] = 'The callback is booked. Say you will call back then, thank the person, say goodbye, and in the same message call close_call with outcome not_reached and reason callback.'
        elif d.get('error') == 'not found' or V.get('cb_block') == 'yes':
            n = int(V.get('cb_fail_n') or 0) + 1
            V['cb_fail_n'] = n
            d['ok'] = False
            d['error'] = 'outside the callback window, NOT booked'
            if n >= 2:
                d['what_to_do_next'] = 'NOT booked: this time is also outside the callback window. Do not schedule. Thank the person, say goodbye, and in the same message call close_call with outcome not_reached and reason no_callback.'
            else:
                d['what_to_do_next'] = 'NOT booked: that day or time is outside the callback window. Ask once for another time within the window (see the callback rules). If the next answer is also outside, do not schedule.'
        else:
            d['what_to_do_next'] = 'The booking service failed. Call schedule_callback once more with the same date and time.'
except Exception:
    pass
'''

PROMISE_PRE = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    b = config.get('body')
    bad = ''
    if isinstance(b, dict):
        if str(V.get('promise_state') or '') == 'recorded':
            bad = 'a promise for this product is already recorded'
        else:
            d = str(b.get('date') or '')[:10]
            b['date'] = d
            valid = str(V.get('valid_dates') or '')
            if valid and d not in valid.split(','):
                bad = 'the date ' + d + ' is not allowed (weekend, holiday, past, or after the deadline ' + str(V.get('deadline_iso') or '') + ')'
            try:
                a = round(float(str(b.get('amount')).replace(',', '')), 2)
                b['amount'] = a
                lo = float(V.get('floor_amount')); hi = float(V.get('full_amount'))
                if a < lo - 0.005 or a > hi + 0.005:
                    bad = bad or ('the amount ' + str(a) + ' is outside ' + str(lo) + ' to ' + str(hi))
            except Exception:
                pass
    V['promise_block'] = bad
    if bad:
        config['url'] = config.get('url', '').replace('/create_promise', '/promise_not_allowed')
except Exception:
    pass
'''

PROMISE_POST = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(); result['data'] = d
        blk = str(V.get('promise_block') or '')
        if d.get('ok') is True:
            V['promise_state'] = 'recorded'
            d['what_to_do_next'] = 'RECORDED. Tell the customer the promise is recorded, then say exactly: "You will receive a text message confirming this promise." Then thank the customer, say goodbye, and in the same message call close_call with outcome promise.'
        elif blk or d.get('error') == 'not found':
            d['ok'] = False
            d['error'] = 'NOT recorded: ' + (blk or 'outside the rules')
            d['what_to_do_next'] = 'NOT recorded, because ' + (blk or 'the date or amount breaks the rules') + '. Do not say it is recorded. Check the DATE TABLE and the amount rules, offer a value that is allowed, read it back and ask "Do you confirm?" again.'
        else:
            n = int(V.get('promise_fail_n') or 0) + 1
            V['promise_fail_n'] = n
            d['ok'] = False
            if n >= 2:
                V['promise_state'] = 'service_error'
                d['what_to_do_next'] = 'NOT recorded, the service failed again. Tell the customer you could not record the promise. Thank the customer, say goodbye, and in the same message call close_call with outcome no_promise and reason service_error.'
            else:
                d['what_to_do_next'] = 'NOT recorded, the service failed. Do NOT say it is recorded. Tell the customer you need a moment and call create_promise again right now with the same amount and date.'
except Exception:
    pass
'''

TRANSFER_PRE = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    b = config.get('body')
    if isinstance(b, dict):
        V['transfer_pending'] = str(b.get('queue') or '')
except Exception:
    pass
'''

TRANSFER_POST = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if d.get('ok') is True:
                q = str(V.get('transfer_pending') or V.get('transfer_to_agent_args.queue') or '')
                V['transfer_done'] = q or 'yes'
                r = dict(restructuring='restructuring', general='human', support='support').get(q, q)
                d['what_to_do_next'] = 'The transfer is done. Tell the customer you are connecting them now, thank them, say goodbye, and in the same message call close_call with outcome transferred and reason ' + r + '.'
            else:
                d['what_to_do_next'] = 'The transfer failed. Call transfer_to_agent once more with the same queue.'
except Exception:
    pass
'''

CLOSE_PRE = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    b = config.get('body')
    if isinstance(b, dict):
        def g(k):
            return str(V.get(k) or '')
        tq = g('transfer_done')
        if tq:
            q = g('transfer_to_agent_args.queue') or tq
            b['outcome'] = 'transferred'
            r = dict(restructuring='restructuring', general='human', support='support').get(q)
            if r:
                b['reason'] = r
        elif g('promise_state') == 'recorded':
            b['outcome'] = 'promise'
        elif g('promise_state') == 'service_error':
            b['outcome'] = 'no_promise'
            b['reason'] = 'service_error'
        elif g('callback_state') == 'booked':
            b['outcome'] = 'not_reached'
            b['reason'] = 'callback'
        elif g('id_state') == 'failed':
            b['outcome'] = 'not_verified'
            b['reason'] = 'failed'
        if b.get('outcome') in ('promise', 'paid'):
            b.pop('reason', None)
        elif not b.get('reason') or '{' in str(b.get('reason')):
            b.pop('reason', None)
except Exception:
    pass
'''

CLOSE_POST = r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if d.get('ok') is True:
                V['call_closed'] = 'yes'
                d['what_to_do_next'] = 'The call is closed. Say nothing more.'
            else:
                d['what_to_do_next'] = 'close_call was not accepted: ' + str(d.get('error')) + '. Fix the outcome or reason and call close_call again.'
except Exception:
    pass
'''

FALLBACK_CLOSE_PRE = CLOSE_PRE + r'''
try:
    V = vars if isinstance(vars, dict) else dict()
    done = str(V.get('call_closed') or '') == 'yes' or str(V.get('close_call_response.data.ok') or '').lower() == 'true' or str(V.get('close_ok') or '').lower() == 'true'
    if done:
        config['url'] = config.get('url', '').replace('/close_call', '/already_closed')
except Exception:
    pass
'''

# ---------------------------------------------------------------- builders

def api_tool(name, path, description, body, required, pre=None, post=None, retries=0, mapping=None):
    props = {"customer_id": {"type": "string", "default": "{{customer_id}}"}}
    props.update(body)
    t = {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": BASE + path},
                    "method": {"type": "string", "default": "POST"},
                    "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                    "body": {"type": "object", "properties": props, "required": required},
                },
            },
        },
    }
    if pre: t["pre_process_code"] = pre
    if post: t["post_process_code"] = post
    if mapping: t["response_mapping"] = mapping
    return t


def close_tool(outcomes, reasons, when):
    return api_tool(
        "close_call", "/close_call",
        "Ends the call in the bank's records. CALL it exactly once, as the very last action, in the same message in which you thank the customer and say goodbye. " + when,
        {
            "outcome": {"type": "string", "enum": outcomes, "default": "{{outcome}}", "description": "One of: " + ", ".join(outcomes) + "."},
            "reason": {"type": "string", "enum": reasons, "default": "{{reason}}", "description": "One of: " + ", ".join(reasons) + ". Leave it out for outcome promise or paid."},
        },
        ["outcome"], pre=CLOSE_PRE,
        mapping=[{"path": "data.ok", "variable": "close_ok"}],
    )


def transfer_tool(queues, when):
    return api_tool(
        "transfer_to_agent", "/transfer_to_agent",
        "Hands the call to a person. CALL it " + when,
        {"queue": {"type": "string", "enum": queues, "default": "{{queue}}", "description": "One of: " + ", ".join(queues) + "."}},
        ["queue"], post=TRANSFER_POST,
    )


def bool_true(var):
    return [{"variable": var, "operator": "equals", "operand": "true"}, {"variable": var, "operator": "equals", "operand": "True"}]


CLOSED = {
    "target_node": "end",
    "description": "close_call succeeded",
    "condition": {"logic": "or", "conditions": bool_true("close_ok") + bool_true("close_call_response.data.ok")},
}


def code_node(name, code, nxt, extra_transitions=()):
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                          "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": list(extra_transitions) + [{"target_node": nxt, "description": "always", "condition": {"operator": "always"}}],
    }


SYSTEM = """You are Freya Bank's digital assistant, making an outbound phone call to {{title}} {{first_name}} {{last_name}}. Speak English. Keep every turn to one to three short sentences. Address the customer as {{title}} {{last_name}}.

Current date and time for this call: {{now}}. Use only this, never your own clock.

Rules that hold in every step:
- If asked whether you are a robot or an AI: say you are Freya Bank's digital assistant, then continue.
- If asked "is this a scam?": say they can verify this call by phoning Freya Bank's customer service number, then continue.
- If asked something the information you have does not answer: say you do not have that information. Never invent figures, dates, reasons or terms.
- Never offer a discount, a waiver, an instalment plan or any other term.
- When a step says to call a tool, actually call it. Saying you did is not enough. Call one tool per message.
- When a tool result has a field what_to_do_next, follow it exactly. After every tool result, speak to the customer or call the next tool. Never end a turn with nothing.
- Never say something is recorded, booked or transferred unless the tool result says ok true.
- If a tool call returns an error, call the same tool once more with the same values. If it fails again, tell the customer honestly that it did not work.
- Every call ends with exactly one close_call, in the same message as your thank-you and goodbye. Never call any tool after close_call."""

OPENING_LINE = "Hello, this is Freya Bank's digital assistant calling from Freya Bank. We are recording this call. Am I speaking with {{title}} {{last_name}}?"

OPENING_TASK = """You have just said: "Hello, this is Freya Bank's digital assistant calling from Freya Bank. We are recording this call. Am I speaking with {{title}} {{last_name}}?"
Your only job in this step is to find out who is on the line.

STRICT: never say why you are calling. Never say the words collections, debt, payment, amount, loan, card, account balance or anything about money, to anyone, including relatives and including the customer at this stage. If asked what the call is about, say you can only discuss it with {{title}} {{last_name}} once their identity is confirmed.

Decide from the person's answer:

1. CONFIRMED: they say "yes", "that's me", "speaking", "it's me" or give the customer's own name ({{first_name}} or {{last_name}}) as theirs. Then call transition_to_security_check immediately and say nothing else.
   "Hello", "go ahead", "who is this", "what is this about" are NOT a confirmation.

2. NOT CLEAR: answer their question first if they asked one (who you are: Freya Bank's digital assistant; robot: same; scam: they can verify the call by phoning Freya Bank's customer service number). Then ask again: "Am I speaking with {{title}} {{last_name}}?". This counts as the second ask. If after the second ask it is still not a clear confirmation, thank them, say goodbye and call close_call with outcome not_reached, reason unclear.

3. SOMEONE ELSE ANSWERED (a relative, colleague, anyone who is not the customer): do not say why you are calling, do not ask security questions, do not accept a payment offer or any information about the account. Ask when {{title}} {{last_name}} can be reached.
   Callback rules: {{callback_facts}}
   As soon as they name a day and a time, convert it to date YYYY-MM-DD and 24-hour time HH:MM and call schedule_callback, even if you think it is outside the window: the system checks it. If they give only a day, or only "morning"/"afternoon" with no hour, ask for an exact time first. Then follow what_to_do_next.
   If they do not know when the customer can be reached, ask once; if they still cannot say, thank them, say goodbye and call close_call with outcome not_reached, reason no_callback.

4. WRONG NUMBER: the number does not belong to the customer / they do not know the customer. Apologise, thank them, say goodbye and call close_call with outcome not_reached, reason wrong_number.

5. DECEASED: they say the customer has died. Say "I am sorry for your loss." Ask nothing else. Say goodbye and call close_call with outcome not_reached, reason deceased.

6. The customer says twice that they do not want to talk: thank them, say goodbye and call close_call with outcome not_verified, reason declined.

7. The person asks for a human: ask once whether you can help first. If they insist, call transfer_to_agent with queue general. When it returns ok true, say you are connecting them, say goodbye and call close_call with outcome transferred, reason human."""

IDENTITY_LINE = "Thank you. Before we continue, I need to ask you two security questions. What is your date of birth?"

IDENTITY_TASK = """The person said they are {{title}} {{last_name}}. You have asked the first security question (date of birth). Nothing about the account may be said until both questions pass.

STRICT: never say why you are calling and never mention collections, debt, payment, amounts, a loan or a card in this step. You do not know the account details yet.

Security questions, one at a time:
1. Date of birth. When they answer, call verify_identity with field date_of_birth and the answer as YYYY-MM-DD. Convert whatever they said: "14 March 1988" -> 1988-03-14. A date given only as numbers is day, then month, then year: "05/04/1990" -> 1990-04-05. If they give the date in two parts, wait until you have day, month and year.
2. Then the last four digits of the phone number on file. Call verify_identity with field phone_last4 and the four digits.
Call verify_identity for every answer they give, including answers you suspect are wrong. Never judge an answer yourself.

Reading the verify_identity result:
- verified true, all_verified false: the date of birth passed. Ask for the last four digits of the phone number on file.
- hint "day_month_swapped": day and month were the other way round. This is NOT a wrong answer. Ask them to say the date of birth again with the month as a word, then call verify_identity again.
- ok false with no "verified" field: the answer could not be read. NOT a wrong answer. Ask again (day, month as a word, year).
- verified false and wrong_answers_left above 0: wrong answer. Say it did not match and ask the same question again.
- verified false and wrong_answers_left 0 (or an error saying the check is over): the check has FAILED. Ask nothing more. Do not state any amount or product, even if asked. Tell the customer to use the Freya Bank mobile app or visit a branch, thank them, say goodbye and in the same message call close_call with outcome not_verified, reason failed.

If they do not want to answer, or ask what the call is about instead of answering: the FIRST time, explain once that these questions protect their account and ask the question again. The SECOND time they refuse or ask that, thank them, say goodbye and call close_call with outcome not_verified, reason declined. Saying twice that they do not want to talk also closes as not_verified, declined.

If they ask for a human: ask once whether you can help first. If they insist, call transfer_to_agent with queue general. When it returns ok true, say you are connecting them, say goodbye and call close_call with outcome transferred, reason human.

When the check has failed (what_to_do_next says so), do not state any amount or product even if they ask how much they owe."""

NEGOTIATE_LINE = "Thank you, your identity is confirmed. I am calling from the collections team about a late payment on your {{product_phrase}}. The past-due amount is {{full_text}}, and it is {{days_late_n}} days late. Can you make this payment today?"

NEGOTIATE_TASK = """The customer is verified. You have just said: "I am calling from the collections team about a late payment on your {{product_phrase}}. The past-due amount is {{full_text}}, and it is {{days_late_n}} days late. Can you make this payment today?"

TOOLS: create_promise (only after a clear yes to the read-back), transfer_to_agent, close_call. When a rule below says to call a tool, call it.

FACTS (computed by the bank's system; trust them, never compute dates yourself):
{{deal_facts}}

== A. CHECK THESE FIRST, on every customer turn ==
A1. HARDSHIP: the customer mentions illness, a death in the family or losing their job. Say ONE short sentence of sympathy. Make NO persuasion attempt and propose no amount or date. If they ask for a discount, say you cannot offer one. Offer to connect them with the customer support team. If they accept: call transfer_to_agent with queue support. If they decline: thank them, say goodbye and call close_call with outcome no_promise, reason hardship. (Only if they themselves name a date or an amount, handle it under section B.)
A2. NEW PLAN: they ask for a new payment plan, lower instalments, smaller monthly payments, or to spread or restructure the debt. Do not describe any plan. Say you will connect them with the team that handles this and call transfer_to_agent with queue restructuring.
A3. ALREADY PAID: they say they already paid this past-due amount. Say that payments can take one business day to appear. Take no promise. Thank them, say goodbye and call close_call with outcome paid.
A4. DISPUTE: they say the debt is not theirs, they never had this product, or the product was closed or paid off in the past. Do not argue. Take no promise. Thank them, say goodbye and call close_call with outcome no_promise, reason dispute.
A5. HUMAN: they ask for a person. Ask once whether you can help first. If they insist, call transfer_to_agent with queue general.
A6. They say for the second time that they do not want to talk: thank them, say goodbye and call close_call with outcome no_promise, reason refused.

== B. GETTING THE PROMISE ==
B1. You propose every amount. NEVER ask "how much can you pay?" or anything like it. If they say "you tell me", propose an amount yourself.
B2. Yes to paying today: the promise is the full amount, today. Go to C.
B3. They name a DATE: look it up in the DATE TABLE below and do exactly what its row says. A date or amount the customer proposes is NEVER a refusal.
    - "allowed": accept it.
    - weekend or holiday: say that day is not possible and offer the next business day given in the row.
    - after the deadline: say the latest date you can record is the deadline, and offer it. If they refuse the deadline, that is a flat refusal (B6).
    - Vague period ("next week", "the week after next", "end of the month", "when my salary comes"): ask ONCE for the exact day. If they still give no exact day, propose the deadline.
B4. FULL AMOUNT TOO MUCH (and not asking for a plan): offer the floor ONCE, in one message: name the floor amount, say that the rest stays overdue, and ask on which day they can pay it (keep a date already agreed). From then on, the full amount is off the table: every later proposal in this call uses the floor amount, never the full amount. Do not name the floor again except in a persuasion attempt after a flat refusal. If they refuse the floor itself, that is a flat refusal (B6).
B5. They name an AMOUNT: from the floor up to the full amount (both included) -> accept it. Below the floor -> say that amount cannot be recorded and offer the floor once; if they do not accept, that is a flat refusal (B6). Above the full amount -> the most you can record is the full amount.
B6. FLAT REFUSAL or non-answer (also: "no" to paying today with no date and no amount): this uses ONE persuasion attempt. Use the attempts listed in FACTS, in order: Attempt 1, then (late band only) Attempt 2. Count the attempts you already made in this conversation. In Attempt 1 use the floor amount if the customer has said the full amount is too much at any point, otherwise the full amount.
    When the allowed attempts are used up and they still refuse: stop. Do not repeat an offer they refused. Thank them, say goodbye and call close_call with outcome no_promise, reason refused.

== C. CONFIRM AND RECORD ==
C1. Read back the product, the amount and the date (weekday, day, month), then ask exactly "Do you confirm?".
C2. Only after a clear yes, call create_promise with amount (a number, e.g. 637.5) and date (YYYY-MM-DD from the DATE TABLE).
C3. If they change the date or amount at this point, check it again under B and read it back again.
TRANSFERS: after transfer_to_agent returns ok true, tell the customer you are connecting them now, thank them, say goodbye and in the same message call close_call with outcome transferred and reason restructuring (queue restructuring), support (queue support) or human (queue general).
C4. After create_promise, follow what_to_do_next exactly. If it failed once, call create_promise again immediately. Only say "recorded" when the result says ok true. After a recorded promise say: "You will receive a text message confirming this promise." Then thank, goodbye, close_call with outcome promise.
C5. Only one promise per product. A recorded promise cannot be changed or cancelled in this call; if asked, say they can change it in the mobile app.

DATE TABLE:
{{date_table}}"""

ACCOUNT_ERROR_TASK = """The customer is verified, but the account details could not be loaded. Do not state any amount. Apologise that you cannot access the details right now, thank the customer, say goodbye and in the same message call close_call with outcome no_promise, reason service_error."""


def conv(name, line, task, tools, transitions, first_only=True):
    return {
        "name": name, "node_type": "conversation",
        "on_enter": {"type": "tts", "text": line} if line else {"type": "llm"},
        "on_enter_first_entry_only": first_only,
        "task_message": task, "tools": tools, "transitions": transitions,
        "max_node_turns": 60,
    }


verify_tool = api_tool(
    "verify_identity", "/verify_identity",
    "Checks one security answer. CALL it every time the customer answers a security question, once per answer.",
    {
        "field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "default": "{{field}}", "description": "date_of_birth or phone_last4."},
        "answer": {"type": "string", "default": "{{answer}}", "description": "For date_of_birth: YYYY-MM-DD. For phone_last4: four digits."},
    },
    ["field", "answer"], post=VERIFY_POST,
    mapping=[{"path": "data.all_verified", "variable": "all_verified"}],
)

callback_tool = api_tool(
    "schedule_callback", "/schedule_callback",
    "Books a callback for the customer. CALL it as soon as the person who answered names a day and a time when the customer can be reached.",
    {
        "date": {"type": "string", "default": "{{date}}", "description": "YYYY-MM-DD."},
        "time": {"type": "string", "default": "{{time}}", "description": "HH:MM, 24-hour."},
    },
    ["date", "time"], pre=CB_PRE, post=CB_POST,
)

promise_tool = api_tool(
    "create_promise", "/create_promise",
    "Records the payment promise. CALL it only after you read back product, amount and date and the customer clearly said yes to \"Do you confirm?\". If it fails once, call it again.",
    {
        "product_id": {"type": "string", "default": "{{product_id}}"},
        "amount": {"type": "number", "default": "{{amount}}", "description": "The agreed amount as a number, e.g. 637.5."},
        "date": {"type": "string", "default": "{{date}}", "description": "The agreed date, YYYY-MM-DD."},
    },
    ["amount", "date"], pre=PROMISE_PRE, post=PROMISE_POST,
)

get_account_action = {
    "type": "function", "config": {"timeoutMs": 12000, "maxRetries": 2},
    "function": {"name": "get_account", "handler": "__function__:api_call", "description": "Get the late products.",
                 "parameters": {"type": "object", "properties": {
                     "url": {"type": "string", "default": BASE + "/get_account"},
                     "method": {"type": "string", "default": "POST"},
                     "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                     "body": {"type": "object", "properties": {"customer_id": {"type": "string", "default": "{{customer_id}}"}}, "required": []}}}},
}

fallback_close = api_tool(
    "close_call_fallback", "/close_call", "Records how the call ended, if it was not closed during the call.",
    {
        "outcome": {"type": "string", "enum": ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"], "default": "{{outcome}}",
                    "description": "promise: a promise was recorded. paid: customer said they already paid. transferred: handed to a person. no_promise: verified, no promise. not_verified: identity check failed or declined. not_reached: the customer was not on the line."},
        "reason": {"type": "string", "default": "{{reason}}", "description": "transferred: restructuring, human or support. no_promise: refused, hardship, dispute or service_error. not_verified: failed or declined. not_reached: callback, no_callback, wrong_number, deceased or unclear. Leave out for promise and paid."},
    },
    ["outcome"], pre=FALLBACK_CLOSE_PRE,
)
fallback_close["config"]["alwaysRunsAt"] = ["end_of_call"]

nodes = {
    "setup": code_node("setup", SETUP_CODE, "opening"),
    "opening": conv("opening", OPENING_LINE, OPENING_TASK, [
        callback_tool,
        transfer_tool(["general"], "only when the person insists on a human after you asked once whether you can help. Queue general."),
        close_tool(["not_reached", "not_verified", "transferred"], ["callback", "no_callback", "wrong_number", "deceased", "unclear", "declined", "human"],
                   "not_reached: callback, no_callback, wrong_number, deceased or unclear. not_verified: declined."),
    ], [
        CLOSED,
        {"target_node": "security_check", "description": "The person on the line clearly confirmed they are the customer", "is_tool_call": True,
         "condition": {"operator": "matches_intent", "description": "The person clearly confirmed they are the customer (yes, that's me, speaking, or said the customer's name as their own)"}},
    ]),
    "security_check": conv("security_check", IDENTITY_LINE, IDENTITY_TASK, [
        verify_tool,
        transfer_tool(["general"], "only when the customer insists on a human after you asked once whether you can help. Queue general."),
        close_tool(["not_verified", "transferred"], ["failed", "declined", "human"], "not_verified: failed (three wrong answers) or declined (would not answer)."),
    ], [
        {"target_node": "load_account", "description": "Both security answers verified",
         "condition": {"logic": "or", "conditions": bool_true("all_verified") + bool_true("verify_identity_response.data.all_verified")}},
        CLOSED,
    ]),
    "load_account": {
        "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "prepare_deal",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [get_account_action],
        "response_mapping": [{"path": "data.products", "variable": "products"}],
        "transitions": [{"target_node": "prepare_deal", "description": "always", "condition": {"operator": "always"}}],
    },
    "prepare_deal": code_node("prepare_deal", ACCOUNT_CODE, "negotiate", [
        {"target_node": "account_error", "description": "account not loaded",
         "condition": {"logic": "or", "conditions": [{"variable": "acct_state", "operator": "equals", "operand": "error"}]}},
    ]),
    "negotiate": conv("negotiate", NEGOTIATE_LINE, NEGOTIATE_TASK, [
        promise_tool,
        transfer_tool(["restructuring", "general", "support"],
                      "with queue restructuring when the customer asks for a new payment plan or lower instalments; queue support when a customer in hardship accepts the support team; queue general when the customer insists on a human."),
        close_tool(["promise", "paid", "transferred", "no_promise"], ["refused", "hardship", "dispute", "service_error", "restructuring", "human", "support"],
                   "promise: a promise was recorded. paid: they already paid. no_promise: refused, hardship, dispute or service_error."),
    ], [CLOSED]),
    "account_error": conv("account_error", "", ACCOUNT_ERROR_TASK, [
        close_tool(["no_promise"], ["service_error"], ""),
    ], [CLOSED]),
    "end": {
        "name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                                                       "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    },
}

wf = {
    "initial_node": "setup",
    "system_prompt": SYSTEM,
    "nodes": nodes,
    "global_nodes": {},
    "tools": [fallback_close],
    "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
}

# safety: no double braces inside any python snippet
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("pre_process_code", "post_process_code", "code") or (k == "default" and path.endswith("code")):
                s = v if isinstance(v, str) else ""
                assert "{{" not in s and "}}" not in s, path + "." + k
            walk(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o): walk(v, path + f"[{i}]")
walk(wf)
for s in (SETUP_CODE, ACCOUNT_CODE, DOB_PRE, VERIFY_POST, CB_PRE, CB_POST, PROMISE_PRE, PROMISE_POST, TRANSFER_PRE, TRANSFER_POST, CLOSE_PRE, CLOSE_POST, FALLBACK_CLOSE_PRE):
    assert "{{" not in s and "}}" not in s
    compile(s, "snippet", "exec")

(ROOT / "out").mkdir(exist_ok=True)
(ROOT / "out" / "brief-a.workflow.json").write_text(json.dumps(wf, indent=1))
print("written")
