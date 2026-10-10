"""Python run inside the workflow (code nodes and tool hooks). Every value comes from the CFG constant the generator
prepends; nothing here knows a brief. No double braces anywhere: the platform may template them."""

BASE = r'''
import datetime, json, re, math
def jl(k, dflt):
    try:
        x = vars.get(k)
        return json.loads(x) if isinstance(x, str) and x else (x if x else dflt)
    except Exception:
        return dflt
def money(x):
    return '{:,.2f}'.format(x)
def num(x):
    try:
        return float(str(x).replace(',', ''))
    except Exception:
        return 0.0
'''

DATES = r'''
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
HOL = set(C['holidays'])
BW = set(C['business_weekdays'])
def isbd(d):
    return d.weekday() in BW and d.isoformat() not in HOL
def nextbd(d):
    d = d + datetime.timedelta(days=1)
    for i in range(len(HOL) + 8):
        if isbd(d):
            return d
        d = d + datetime.timedelta(days=1)
    raise ValueError("no business day found")
def addbd(d, n):
    for i in range(int(n)):
        d = nextbd(d)
    return d
def say(d):
    return DAYS[d.weekday()] + ' ' + str(d.day) + ' ' + d.strftime('%B') + ' ' + str(d.year) + ' (' + d.isoformat() + ')'
def get_now():
    return datetime.datetime.fromisoformat(str(vars.get(C['now_var']) or '').strip().replace('Z', '+00:00'))
'''

SETUP = r'''
try:
    now = get_now()
    today = now.date()
    hm = now.strftime('%H:%M')
    cb = C['callback']
    days = []
    parts = []
    if cb['allow_today'] and isbd(today) and hm < cb['end']:
        days.append(today)
        parts.append('today, ' + say(today) + ', at a time later than ' + hm + ' and from ' + cb['start'] + ' to ' + cb['end'])
    d = today
    for i in range(max(0, int(cb['ahead']))):
        d = nextbd(d)
        days.append(d)
        parts.append(say(d) + ', from ' + cb['start'] + ' to ' + cb['end'])
    vars['cb_days'] = ','.join(x.isoformat() for x in days)
    vars['cb_today'] = today.isoformat()
    vars['now_hm'] = hm
    vars['callback_facts'] = ('Current date and time: ' + say(today) + ', ' + hm + '. A callback can be booked only for: ' + '; or '.join(parts) +
        ' (both times included). "Tomorrow" means ' + say(today + datetime.timedelta(days=1)) + '.')
    vars['setup_state'] = 'done'
    vars.setdefault('id_state', 'pending')
    vars.setdefault('x_wrong', 0)
    vars.setdefault('x_policy', '{}')
    vars.setdefault('x_confirmed', '{}')
    vars.setdefault('policy_pending', 'no')
except Exception as e:
    vars['setup_state'] = 'error'
    vars['setup_error'] = str(e)[:200]
    vars['callback_facts'] = 'Call time unavailable. Do not book callbacks or promise dates.'
'''

ACCOUNT = r'''
def load_products():
    p = vars.get('products')
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            try:
                import ast
                p = ast.literal_eval(p)
            except Exception:
                p = None
    if isinstance(p, dict):
        p = [p]
    if isinstance(p, list) and p and isinstance(p[0], dict):
        return p
    items = dict()
    for k, v in list(vars.items()):
        m = re.search(r'products\W*(\d+)\W*([a-z_0-9]+)$', str(k))
        if m:
            items.setdefault(int(m.group(1)), dict())[m.group(2)] = v
    return [items[i] for i in sorted(items)]
def band_for(dl):
    bs = sorted(C['bands'], key=lambda b: b['min'])
    for b in bs:
        if b['min'] <= dl <= b['max']:
            return b
    raise ValueError('days_late outside supported bands')
def last_bd_of_week(start):
    first = start - datetime.timedelta(days=(start.weekday() + 1) % 7 if C['week_starts'] == 'sunday' else start.weekday())
    x = first + datetime.timedelta(days=6)
    while x >= first and not isbd(x):
        x = x - datetime.timedelta(days=1)
    return x
def attempt_date(rule, n, today, deadline):
    if rule == 'today':
        d = today if isbd(today) else nextbd(today)
    elif rule == 'next_business_day':
        d = nextbd(today)
    elif rule == 'end_of_week':
        d = last_bd_of_week(today)
        if d <= today:
            d = last_bd_of_week(today + datetime.timedelta(days=7))
    elif rule == 'deadline':
        d = deadline
    elif rule == 'business_days_from_today':
        d = addbd(today, n)
    elif rule == 'calendar_days_from_today':
        d = today + datetime.timedelta(days=int(n))
        if not isbd(d):
            d = nextbd(d)
    else:
        return None
    return min(d, deadline)
class Fmt(dict):
    def __missing__(self, k):
        return ''
try:
    prods = load_products()
    if not prods:
        raise ValueError('no products')
    now = get_now()
    today = now.date()
    earliest = addbd(today, C['earliest']) if C['earliest'] else today
    PT = dict((p['type'], p) for p in C['products'])
    out = []
    facts = ['Today: ' + say(today) + '. Time now: ' + now.strftime('%H:%M') + '.', 'Number of late products: ' + str(len(prods)) + '.']
    lines = []
    for i, pr in enumerate(prods):
        typ = str(pr.get('type') or '')
        spec = PT[typ]
        full = float(pr['past_due'])
        dl = int(pr['days_late'])
        floor = float(pr[spec['floor_field']]) if spec['floor_field'] else round(full * float(spec['floor_percent']) / 100.0, 2)
        if not math.isfinite(full) or not math.isfinite(floor) or full <= 0 or floor <= 0 or floor > full:
            raise ValueError('invalid account amount or floor')
        band = band_for(dl)
        deadline = addbd(today, band['offset']) if band['unit'] == 'business_days' else today + datetime.timedelta(days=int(band['offset']))
        if band['unit'] != 'business_days' and not isbd(deadline):
            while not isbd(deadline) and deadline > today:
                deadline = deadline - datetime.timedelta(days=1)
        f = Fmt((k, str(v)) for k, v in pr.items())
        try:
            phrase = spec['spoken'].format_map(f).strip()
        except Exception:
            phrase = typ.replace('_', ' ')
        pid = str(pr['product_id'])
        if not pid or pid in [x['pid'] for x in out]:
            raise ValueError('missing or duplicate product id')
        valid = []
        x = earliest
        while x <= deadline:
            if isbd(x):
                valid.append(x.isoformat())
            x = x + datetime.timedelta(days=1)
        atts = [a for a in sorted(C['attempts'], key=lambda a: a['number']) if not a['bands'] or band['name'] in a['bands']]
        atts = atts[:max(0, int(band['attempts']))]
        k = len(out) + 1
        facts.append('')
        facts.append('PRODUCT ' + str(k) + ': ' + phrase + ' (product_id ' + pid + ').')
        facts.append('  FULL past-due amount: ' + money(full) + '. Days late: ' + str(dl) + '.')
        facts.append('  FLOOR (lowest amount that can be recorded): ' + money(floor) + '. Never record less than the floor or more than the full amount.')
        facts.append('  Band: ' + band['name'] + '. DEADLINE (latest date you may record for this product): ' + say(deadline) + '.')
        facts.append('  Persuasion attempts allowed for this product: ' + str(len(atts)) + '.')
        alines = []
        options = []
        for j, a in enumerate(atts):
            ad = attempt_date(a['date_rule'], a['date_param'], today, deadline)
            on = (' on ' + say(ad)) if ad else ''
            r = a['amount_rule']
            if r == 'full':
                am = money(full)
            elif r == 'floor':
                am = money(floor)
            elif r == 'full_or_floor_if_too_much':
                am = money(full) + on + '. BUT if the customer has already said the full amount is too much (even at the first ask), attempt ' + str(j + 1) + ' is instead: propose the FLOOR ' + money(floor) + ' on that SAME date'
            elif r == 'percent_of_full':
                am = money(max(floor, round(full * float(a['amount_param']) / 100.0, 2)))
            else:
                am = 'as the rule says'
            alines.append('Attempt ' + str(j + 1) + ' = propose ' + am + ('' if r == 'full_or_floor_if_too_much' else on) + '.')
            facts.append('  ' + alines[-1] + ' (' + a['text'] + ')')
            options.append(dict(number=a['number'], date=ad.isoformat() if ad else '', amount_rule=r, amount=max(floor, round(full * float(a['amount_param']) / 100.0, 2)) if r == 'percent_of_full' else floor if r == 'floor' else full))
        if not atts:
            facts.append('  There is NO persuasion attempt for this product. After a flat refusal, stop.')
        tpl = C['debt_per_product'] or '{product}: {past_due}, {days_late} days late.'
        f2 = Fmt(f)
        f2.update(product=phrase, past_due=money(full), days_late=str(dl), floor=money(floor), deadline=say(deadline))
        try:
            lines.append(tpl.format_map(f2).strip())
        except Exception:
            lines.append(phrase + ': ' + money(full) + ', ' + str(dl) + ' days late.')
        out.append(dict(pid=pid, phrase=phrase, full=round(full, 2), floor=round(floor, 2), valid=valid, deadline=deadline.isoformat(), deadline_say=say(deadline), atts=alines, options=options))
    maxd = max(datetime.date.fromisoformat(o['deadline']) for o in out)
    rows = []
    x = today
    end = maxd + datetime.timedelta(days=3)
    while x <= end:
        label = DAYS[x.weekday()][:3] + ' ' + str(x.day) + ' ' + x.strftime('%b') + ' = ' + x.isoformat() + ': '
        if x < earliest:
            label += 'NOT allowed (too early) -> offer ' + say(earliest if isbd(earliest) else nextbd(earliest))
        elif not isbd(x):
            late = [o['phrase'] + ' (deadline ' + o['deadline_say'] + ')' for o in out if x > datetime.date.fromisoformat(o['deadline'])]
            if len(late) == len(out):
                label += 'NOT allowed, AFTER the deadline -> say the latest date you can record is the deadline and offer it'
            else:
                label += 'NOT allowed (' + ('bank holiday' if x.weekday() in BW else 'not a business day') + ') -> offer the next business day, ' + say(nextbd(x)) + ('; for ' + ', '.join(late) + ' it is AFTER the deadline -> offer its deadline' if late else '')
        else:
            ok = [o['phrase'] for o in out if x.isoformat() in o['valid']]
            late = [o['phrase'] + ' (deadline ' + o['deadline_say'] + ')' for o in out if x > datetime.date.fromisoformat(o['deadline'])]
            if len(out) == 1:
                label += 'allowed' + (' (today)' if x == today else '') if ok else 'AFTER the deadline -> say the latest date you can record is ' + out[0]['deadline_say'] + ' and offer it'
            else:
                label += ('allowed for: ' + ', '.join(ok) if ok else '') + ('; ' if ok and late else '') + ('AFTER the deadline for: ' + ', '.join(late) + ' -> for that product offer its deadline' if late else '')
        rows.append(label)
        x = x + datetime.timedelta(days=1)
    rows.append('Any date after ' + end.isoformat() + ': AFTER every deadline -> offer each product its deadline.')
    rows.append('Any date before today: not allowed.')
    if C['week_starts'] == 'sunday':
        first = today - datetime.timedelta(days=(today.weekday() + 1) % 7)
    else:
        first = today - datetime.timedelta(days=today.weekday())
    wk = []
    for i in range(7):
        t = first + datetime.timedelta(days=i)
        if C['next_weekday'] == 'following_week':
            nx = t + datetime.timedelta(days=7)
        else:
            nx = today + datetime.timedelta(days=((t.weekday() - today.weekday()) % 7) or 7)
        wk.append('"this ' + DAYS[t.weekday()] + '" = ' + say(t) + (' (in the past)' if t < today else '') + '; "next ' + DAYS[t.weekday()] + '" = ' + say(nx))
    vars['x_prod'] = json.dumps(out)
    vars['n_products'] = len(out)
    vars['deal_facts'] = '\n'.join(facts)
    vars['date_table'] = '\n'.join(rows) + '\nWeekday words:\n' + '\n'.join(wk)
    vars['debt_text'] = ((C['debt_intro'] + ' ') if C['debt_intro'] else '') + ' '.join(lines)
    vars['first_ask_text'] = C['multi_first_ask'] if len(out) > 1 and C['multi_first_ask'] else C['first_ask']
    vars['acct_state'] = 'ok'
except Exception as e:
    vars['acct_state'] = 'error'
    vars['acct_error'] = str(e)[:200]
'''

VERIFY_PRE = r'''
try:
    V = vars
    b = config.get('body')
    if isinstance(b, dict):
        F = C['fields']
        f = str(b.get(F['id_field']) or '')
        a = str(b.get(F['id_answer']) or '').strip()
        V['x_id_field'] = f
        V['id_block'] = ''
        remaining = [q for q in C['questions'] if q['field'] not in str(V.get('x_idok') or '').split(',')]
        if V.get('id_state') in ('failed', 'verified') or not remaining or f != remaining[0]['field']:
            raise ValueError('answer must be for the current security question')
        q = remaining[0]
        kind = q['kind']
        low = a.lower()
        out = ''
        if kind == 'digits':
            dg = re.sub(r'\D', '', a)
            n = int(q.get('digits') or 0)
            if n and len(dg) > n:
                dg = dg[-n:]
            out = dg
        elif kind == 'year':
            y = re.search(r'(1[89]|20)\d\d', low)
            out = y.group(0) if y else ''
        elif kind == 'postcode':
            out = re.sub(r'\s+', ' ', a).strip().upper()
        elif kind == 'date' and not re.match(r'^\d{4}-\d{2}-\d{2}$', a):
            MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
            y = re.search(r'(1[89]|20)\d\d', low)
            mon = None
            for i, m in enumerate(MON):
                if re.search(r'\b' + m, low):
                    mon = i + 1
                    break
            if y and mon:
                rest = low.replace(y.group(0), ' ')
                dd = re.search(r'\b(\d{1,2})', rest)
                if dd:
                    out = '%s-%02d-%02d' % (y.group(0), mon, int(dd.group(1)))
            else:
                ns = re.findall(r'\d+', low)
                if len(ns) == 3:
                    if len(ns[0]) == 4:
                        yy, mm, dd2 = ns[0], int(ns[1]), int(ns[2])
                    elif C['date_order'] == 'MDY':
                        mm, dd2, yy = int(ns[0]), int(ns[1]), ns[2]
                    else:
                        dd2, mm, yy = int(ns[0]), int(ns[1]), ns[2]
                    if len(yy) == 2:
                        yy = ('19' if int(yy) > 30 else '20') + yy
                    out = '%s-%02d-%02d' % (yy, mm, dd2)
        if out:
            b[F['id_answer']] = out
except Exception as e:
    vars['id_block'] = str(e)[:120]
    config['url'] = ''
'''

VERIFY_POST = r'''
try:
    V = vars
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            Q = C['questions']
            f = str(V.get('x_id_field') or '')
            okf = [x for x in str(V.get('x_idok') or '').split(',') if x]
            left = d.get('wrong_answers_left')
            hint = d.get('hint')
            if not V.get('id_block') and d.get('verified') is True and f and f not in okf:
                okf.append(f)
                V['x_idok'] = ','.join(okf)
            todo = [q for q in Q if q['field'] not in okf]
            H = C['hints']
            wrong = not V.get('id_block') and d.get('verified') is False and (not hint or (H.get(str(hint)) or dict()).get('wrong') is True)
            if wrong:
                V['x_wrong'] = int(V.get('x_wrong') or 0) + 1
            if V.get('id_state') == 'failed' or int(V.get('x_wrong') or 0) >= C['max_wrong']:
                V['id_state'] = 'failed'
                d['all_verified'] = False
                d['what_to_do_next'] = 'Identity failed. No more questions or account access. ' + C['failed']['say']
            elif V.get('id_block'):
                d['verified'] = False
                d['all_verified'] = False
                d['what_to_do_next'] = 'Verification call blocked: ' + V['id_block'] + '. Resume the current security question; do not access the account.'
            elif not todo and d.get('verified') is True:
                V['id_state'] = 'verified'
                d['all_verified'] = True
                d['what_to_do_next'] = 'All answers are correct. The customer is verified.'
            elif hint:
                h = H.get(str(hint))
                d['what_to_do_next'] = ('Hint ' + str(hint) + ': ' + h['instruction'] + (' This is NOT a wrong answer.' if not h['wrong'] else '')) if h else ('Hint ' + str(hint) + '. Ask the same question again and call verify_identity again.')
            elif d.get('ok') is False and left is None and d.get('verified') is None:
                d['what_to_do_next'] = 'The answer could not be read. This is NOT a wrong answer. Ask the same question again, then call verify_identity again.'
            elif d.get('verified') is True:
                nq = todo[0]
                d['what_to_do_next'] = 'Correct. Now ask the next security question: "' + nq['ask'] + '" Then call verify_identity with field ' + nq['field'] + '.'
            elif (isinstance(left, int) and left <= 0) or d.get('error'):
                V['id_state'] = 'failed'
                fl = C['failed']
                d['what_to_do_next'] = 'That answer is wrong and the identity check has FAILED. Ask nothing more and say nothing about any account, product, amount or why you called. ' + fl['say'] + ' Then thank the customer, say goodbye, and in the same message call close_call with outcome ' + fl['outcome'] + (' and reason ' + fl['reason'] if fl['reason'] else '') + '.'
            else:
                d['what_to_do_next'] = 'That answer is wrong. Wrong answers left: ' + str(left) + '. Tell the customer it did not match and ask the same question again, then call verify_identity again.'
except Exception:
    pass
'''

CB_PRE = r'''
try:
    V = vars
    F = C['fields']
    cb = C['callback']
    b = config.get('body')
    if isinstance(b, dict):
        t = str(b.get(F['cb_time']) or '').strip().lower()
        m = re.search(r'(\d{1,2})(?::|\.|h)?(\d{2})?', t)
        if m:
            hh = int(m.group(1)); mm = int(m.group(2) or 0)
            if ('pm' in t or 'afternoon' in t or 'evening' in t) and hh < 12:
                hh += 12
            b[F['cb_time']] = '%02d:%02d' % (hh, mm)
        tm = str(b.get(F['cb_time']) or '')
        dt = str(b.get(F['cb_date']) or '')
        b[F['cb_date']] = dt
        days = [x for x in str(V.get('cb_days') or '').split(',') if x]
        ok = bool(re.match(r'^(?:[01]\d|2[0-3]):[0-5]\d$', tm)) and cb['start'] <= tm <= cb['end'] and V.get('setup_state') == 'done'
        if dt not in days:
            ok = False
        if dt == str(V.get('cb_today') or '') and tm <= str(V.get('now_hm') or ''):
            ok = False
        V['cb_block'] = '' if ok else 'yes'
        if not ok:
            config['url'] = ''
except Exception:
    vars['cb_block'] = 'yes'
    config['url'] = ''
'''

CB_POST = r'''
try:
    V = vars
    cb = C['callback']
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        if d.get('ok') is True and V.get('cb_block') != 'yes':
            V['x_cb'] = 'booked'
            o = cb['booked']
            d['what_to_do_next'] = 'The callback is booked. Say when you will call back, thank the person, say goodbye, and in the same message call close_call with outcome ' + o['outcome'] + (' and reason ' + o['reason'] if o['reason'] else '') + '.'
        elif d.get('error') == 'not found' or V.get('cb_block') == 'yes':
            n = int(V.get('cb_fail_n') or 0) + 1
            V['cb_fail_n'] = n
            d['ok'] = False
            d['error'] = 'outside the callback window, NOT booked'
            if n > int(cb['extra_asks']):
                o = cb['not_booked']
                d['what_to_do_next'] = 'NOT booked: this time is also outside the callback window. Do not schedule. Thank the person, say goodbye, and in the same message call close_call with outcome ' + o['outcome'] + (' and reason ' + o['reason'] if o['reason'] else '') + '.'
            else:
                d['what_to_do_next'] = 'NOT booked: that day or time is outside the callback window. Ask once for another time within the window.'
        else:
            d['what_to_do_next'] = 'The booking service failed. Call schedule_callback once more with the same date and time.'
except Exception:
    pass
'''

PROD_HELPERS = r'''
def prods():
    try:
        return json.loads(vars.get('x_prod') or '[]')
    except Exception:
        return []
def resolve(pid):
    P = prods()
    ids = [p['pid'] for p in P]
    s = str(pid or '').strip()
    return s

def lst(k):
    return [x for x in str(vars.get(k) or '').split(',') if x]
'''

PROMISE_PRE = r'''
try:
    V = vars
    F = C['fields']
    b = config.get('body')
    bad = ''
    if isinstance(b, dict):
        pid = resolve(b.get(F['p_pid']))
        b[F['p_pid']] = pid
        V['x_last_pid'] = pid
        P = dict((p['pid'], p) for p in prods())
        p = P.get(pid)
        if V.get('id_state') != 'verified':
            bad = 'identity is not verified'
        elif V.get('acct_state') != 'ok' or not p:
            bad = 'account or product is unavailable'
        elif pid in lst('x_pfailed'):
            bad = 'recording retries are exhausted for this product'
        elif pid in lst('x_recorded'):
            bad = 'a promise for this product is already recorded'
        elif P and not p:
            bad = 'unknown product_id ' + pid + '; use one of ' + ', '.join(P)
        elif p:
            d = str(b.get(F['p_date']) or '')
            b[F['p_date']] = d
            if d not in p['valid']:
                bad = 'the date ' + d + ' is not allowed for ' + p['phrase'] + ' (weekend, holiday, past, or after its deadline ' + p['deadline'] + ')'
            try:
                a = round(float(str(b.get(F['p_amount'])).replace(',', '')), 2)
                b[F['p_amount']] = a
                if not math.isfinite(a) or a < p['floor'] or a > p['full']:
                    bad = bad or ('the amount ' + str(a) + ' is outside ' + str(p['floor']) + ' to ' + str(p['full']) + ' for ' + p['phrase'])
            except Exception:
                bad = bad or 'amount is not a finite number'
            agreed = jl('x_confirmed', dict()).get(pid)
            if not agreed or agreed != dict(amount=b.get(F['p_amount']), date=b.get(F['p_date'])):
                bad = bad or 'these exact values have not been confirmed'
            ps = jl('x_policy', dict()).get(pid) or dict()
            if ps.get('forbid_promise'):
                bad = bad or 'the product intent prohibits a promise'
    else:
        bad = 'missing promise body'
    V['promise_block'] = bad
    if bad:
        config['url'] = ''
except Exception as e:
    vars['promise_block'] = 'promise validation failed: ' + str(e)[:120]
    config['url'] = ''
'''

PROMISE_POST = r'''
try:
    V = vars
    cf = C['confirm']
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        blk = str(V.get('promise_block') or '')
        pid = str(V.get('x_last_pid') or '')
        multi = len(prods()) > 1
        if d.get('ok') is True and not blk:
            rec = lst('x_recorded')
            if pid not in rec:
                rec.append(pid)
            V['x_recorded'] = ','.join(rec)
            ss = (' then say exactly: "' + cf['success'] + '"') if cf['success'] else ''
            if multi:
                d['what_to_do_next'] = 'RECORDED for product ' + pid + '. If another agreed promise is not recorded yet, call create_promise for it now. When every agreed promise is recorded, tell the customer they are recorded' + ss + '. Products without a promise are finished only after every persuasion attempt FACTS allows for them was made and refused; then make the add_note calls the rules require, thank the customer, say goodbye and call close_call.'
            else:
                d['what_to_do_next'] = 'RECORDED. Tell the customer the promise is recorded,' + ss + '. Then thank the customer, say goodbye, and in the same message call close_call with outcome ' + C['promise_outcome'] + '.'
        elif blk or d.get('error') == 'not found':
            d['ok'] = False
            d['error'] = 'NOT recorded: ' + (blk or 'outside the rules')
            d['what_to_do_next'] = 'NOT recorded, because ' + (blk or 'the date or amount breaks the rules') + '. Do not say it is recorded. Check the DATE TABLE and FACTS, offer a value that is allowed, read it back and ask for confirmation again.'
        else:
            fails = jl('x_pfail', dict())
            n = int(fails.get(pid, 0)) + 1
            fails[pid] = n
            V['x_pfail'] = json.dumps(fails)
            d['ok'] = False
            if n > int(cf['retries']):
                fl = lst('x_pfailed')
                if pid not in fl:
                    fl.append(pid)
                V['x_pfailed'] = ','.join(fl)
                o = cf['failure']
                tail = ('Then continue with the other products, and call add_note for this product with the reason for a failed recording if the rules require notes.' if multi else
                        'Then thank the customer, say goodbye, and in the same message call close_call with outcome ' + o['outcome'] + (' and reason ' + o['reason'] if o['reason'] else '') + '.')
                d['what_to_do_next'] = 'NOT recorded, the service failed again. Do NOT say it is recorded. ' + (cf['failure_say'] or 'Tell the customer you could not record the promise.') + ' ' + tail
            else:
                d['what_to_do_next'] = 'NOT recorded, the service failed. Do NOT say it is recorded. Tell the customer you need a moment and call create_promise again right now with the same values.'
except Exception:
    pass
'''

NOTE_PRE = r'''
try:
    V = vars
    F = C['fields']
    b = config.get('body')
    bad = ''
    if isinstance(b, dict):
        pid = resolve(b.get(F['n_pid']))
        b[F['n_pid']] = pid
        V['x_note_pid'] = pid
        V['x_note_reason'] = str(b.get(F['n_reason']) or '')
        b.pop('attempts_made', None)
        p = dict((x['pid'], x) for x in prods()).get(pid) or dict()
        need = p.get('atts') or []
        state = jl('x_policy', dict()).get(pid) or dict()
        made = int(state.get('refused', 0))
        if not p or V.get('id_state') != 'verified':
            bad = 'verified identity and known product required'
        elif len(prods()) == 1 and not C['notes_single']:
            bad = 'no notes are added when the customer has only one product'
        elif V.get('x_transfer') and not C['notes_on_transfer']:
            bad = 'no notes are added on a transferred call'
        elif pid in lst('x_recorded'):
            bad = 'this product has a recorded promise and needs no note'
        elif pid in [n[0] for n in jl('x_notes', [])]:
            bad = 'this product already has a note'
        elif V['x_note_reason'] == C['refused_note_reason'] and made < len(need):
            bad = ('a refusal note needs every persuasion attempt for ' + p['phrase'] + ' first (' + str(len(need)) + ' allowed, ' + str(made) + ' made; the first ask does not count). Make the next one now: '
                   + need[min(made, len(need) - 1)] + ' Call add_note with ' + C['refused_note_reason'] + ' only after the last attempt is refused')
    V['note_block'] = bad
    if bad:
        config['url'] = ''
except Exception as e:
    vars['note_block'] = 'note validation failed: ' + str(e)[:120]
    config['url'] = ''
'''

NOTE_POST = r'''
try:
    V = vars
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        blk = str(V.get('note_block') or '')
        if d.get('ok') is True and not blk:
            ns = jl('x_notes', [])
            ns.append([str(V.get('x_note_pid') or ''), str(V.get('x_note_reason') or '')])
            V['x_notes'] = json.dumps(ns)
            d['what_to_do_next'] = 'Note saved. Continue: the next product, or the next required note, or thank the customer, say goodbye and call close_call.'
        elif blk:
            d['ok'] = False
            d['what_to_do_next'] = 'Note NOT added: ' + blk + '.' + ('' if 'persuasion attempt' in blk else ' Do not add it. Continue.')
        else:
            d['what_to_do_next'] = 'The note service failed. Call add_note once more with the same values.'
except Exception:
    pass
'''

TRANSFER_PRE = r'''
try:
    b = config.get('body')
    if isinstance(b, dict):
        vars['transfer_pending'] = str(b.get(C['fields']['queue']) or '')
except Exception:
    pass
'''

TRANSFER_POST = r'''
try:
    V = vars
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if d.get('ok') is True:
                q = str(V.get('transfer_pending') or V.get('transfer_to_agent_args.' + C['fields']['queue']) or '')
                V['x_transfer'] = q or 'yes'
                o = C['queue_close'].get(q) or dict(outcome='transferred', reason=q)
                d['what_to_do_next'] = 'The transfer is done. Tell the customer you are connecting them now, thank them, say goodbye, and in the same message call close_call with outcome ' + o['outcome'] + (' and reason ' + o['reason'] if o['reason'] else '') + '.'
            else:
                d['what_to_do_next'] = 'The transfer failed. Call transfer_to_agent once more with the same queue.'
except Exception:
    pass
'''

CLOSE_PRE = r'''
try:
    V = vars
    F = C['fields']
    b = config.get('body')
    if isinstance(b, dict):
        rec = lst('x_recorded')
        notes = jl('x_notes', [])
        order = [p['pid'] for p in prods()]
        notes = sorted(notes, key=lambda n: order.index(n[0]) if n[0] in order else 99)
        def holds(st, rule):
            if st == 'transferred':
                return bool(V.get('x_transfer'))
            if st == 'promise_recorded':
                return bool(rec)
            if st == 'promise_service_error':
                return bool(lst('x_pfailed')) and not rec
            if st == 'callback_booked':
                return V.get('x_cb') == 'booked'
            if st == 'identity_failed':
                return V.get('id_state') == 'failed'
            if st == 'all_notes_paid':
                pr = rule['outcome'] if rule['outcome'] in C['note_reasons'] else 'paid'
                return bool(notes) and not rec and len(notes) >= len(order) and all(n[1] == pr for n in notes)
            if st == 'first_non_paid_note':
                return bool(notes) and not rec
            return False
        for rule in C['precedence']:
            if not holds(rule['state'], rule):
                continue
            b[F['outcome']] = rule['outcome']
            r = rule['reason']
            if not r and rule['state'] == 'transferred':
                q = str(V.get('x_transfer') or '')
                r = (C['queue_close'].get(q) or dict(reason='')).get('reason', '')
            if not r and rule['state'] == 'first_non_paid_note':
                np = [n for n in notes if n[1] != 'paid']
                r = np[0][1] if np else ''
            if r:
                b[F['reason']] = r
            else:
                b.pop(F['reason'], None)
            break
        if b.get(F['outcome']) in C['no_reason_outcomes']:
            b.pop(F['reason'], None)
        elif not b.get(F['reason']) or '{' in str(b.get(F['reason'])):
            b.pop(F['reason'], None)
except Exception:
    pass
'''

CLOSE_POST = r'''
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if d.get('ok') is True:
                vars['call_closed'] = 'yes'
                d['what_to_do_next'] = 'The call is closed. Say nothing more.'
            else:
                d['what_to_do_next'] = 'close_call was not accepted: ' + str(d.get('error')) + '. Fix the outcome or reason and call close_call again.'
except Exception:
    pass
'''

FALLBACK_CLOSE_EXTRA = r'''
try:
    done = str(vars.get('call_closed') or '') == 'yes' or str(vars.get('close_call_response.data.ok') or '').lower() == 'true' or str(vars.get('close_ok') or '').lower() == 'true'
    if done:
        config['url'] = ''
except Exception:
    pass
'''

# code_exec arguments are read through the platform's documented tool_args namespace.
# This tool performs no network calls. Dialogue classification is still probabilistic.
POLICY_STEP = r'''
try:
    args = dict((k, vars.get('policy_step_args.' + k)) for k in ('action', 'product_id', 'amount', 'date', 'too_much', 'intent'))
    action = args['action']
    pid = str(args['product_id'] or '')
    products = dict((p['pid'], p) for p in prods())
    p = products.get(pid)
    if vars.get('id_state') != 'verified' or vars.get('acct_state') != 'ok' or not p:
        raise ValueError('verified identity and known account product required')
    states = jl('x_policy', dict())
    st = states.setdefault(pid, dict(issued=0, refused=0, minimum_count=0, pending=False))
    confirmed = jl('x_confirmed', dict())
    instruction = ''
    if action == 'offer':
        if st.get('stop_negotiation') or pid in lst('x_recorded'):
            raise ValueError('negotiation is stopped for this product')
        if st['pending']:
            raise ValueError('an offer is already awaiting an answer; do not repeat it')
        options = p['options']
        i = st['issued']
        if i >= len(options):
            raise ValueError('all persuasion attempts are exhausted; make no further offer')
        option = options[i]
        if option['date'] not in p['valid']:
            raise ValueError('no permitted date for this attempt')
        amount = option['amount']
        if option['amount_rule'] == 'full_or_floor_if_too_much' and str(args['too_much']).lower() == 'true':
            amount = p['floor']
        if amount == p['floor'] and p['floor'] < p['full']:
            rule = C['minimum_offer']
            exception = option['number'] in rule['repeat_on_attempts']
            count = int(vars.get('minimum_count') or 0) if rule['scope'] == 'per_call' else st['minimum_count']
            if rule['limit'] >= 0 and count >= rule['limit'] and not exception:
                raise ValueError('minimum offer limit reached; do not repeat the offer')
            st['minimum_count'] += 1
            vars['minimum_count'] = int(vars.get('minimum_count') or 0) + 1
        st['issued'] += 1
        st['pending'] = True
        instruction = 'Make ONLY this proposal, once: ' + p['phrase'] + ', amount ' + money(amount) + ', on ' + option['date'] + '. ' + p['atts'][i]
    elif action == 'refuse':
        if not st['pending']:
            raise ValueError('no issued persuasion attempt to refuse')
        st['refused'] += 1
        st['pending'] = False
        instruction = 'Refusal recorded. Call policy_step offer for the next permitted attempt, or finish this product if exhausted.'
    elif action == 'minimum':
        if st.get('stop_negotiation') or st['pending'] or pid in lst('x_recorded'):
            raise ValueError('minimum offer is unavailable in the current state')
        rule = C['minimum_offer']
        count = int(vars.get('minimum_count') or 0) if rule['scope'] == 'per_call' else st['minimum_count']
        if rule['limit'] < 0:
            raise ValueError('no executable discretionary minimum-offer policy; re-extract the brief')
        if count >= rule['limit']:
            raise ValueError('minimum offer limit reached')
        date = str(args['date'] or '')
        if date not in p['valid']:
            raise ValueError('minimum offer date is not allowed')
        st['minimum_count'] += 1
        vars['minimum_count'] = int(vars.get('minimum_count') or 0) + 1
        instruction = 'Offer the minimum, ONCE: ' + p['phrase'] + ', ' + money(p['floor']) + ', on ' + date + '. Follow the bank instructions about the remaining overdue balance. Do not repeat this offer.'
    elif action == 'intent':
        rule = next((x for x in C['intent_actions'] if x['intent'] == args['intent']), None)
        if not rule:
            raise ValueError('intent has no supported executable action; follow its prompt instructions')
        st.update(stop_negotiation=rule['stop_negotiation'], forbid_promise=rule['forbid_promise'], allow_customer_proposal=rule['allow_customer_proposal'])
        st['pending'] = False
        confirmed = dict((k, v) for k, v in confirmed.items() if k in lst('x_recorded'))
        vars['x_readback'] = '{}'
        st.pop('staged', None)
        instruction = 'Intent state updated. Follow the bank intent instructions; do not negotiate if stopped.'
    elif action in ('stage', 'change'):
        vars['x_readback'] = '{}'
        confirmed = dict((k, v) for k, v in confirmed.items() if k in lst('x_recorded'))
        st.pop('staged', None)
        amount = round(float(str(args['amount']).replace(',', '')), 2)
        date = str(args['date'] or '')
        if st.get('forbid_promise') or (st.get('stop_negotiation') and not st.get('allow_customer_proposal')) or pid in lst('x_recorded'):
            raise ValueError('a promise is prohibited for this product')
        if not math.isfinite(amount) or not p['floor'] <= amount <= p['full']:
            raise ValueError('amount outside the permitted range')
        if date not in p['valid']:
            wanted = datetime.date.fromisoformat(date)
            valid = [datetime.date.fromisoformat(x) for x in p['valid']]
            if not valid:
                raise ValueError('no valid promise date')
            corrected = next((x for x in valid if x >= wanted), valid[-1])
            instruction = 'Requested date is not permitted. Offer ' + corrected.isoformat() + ' and wait for agreement; then stage again. Do not claim it is confirmed.'
        else:
            st['staged'] = dict(amount=amount, date=date)
            st['pending'] = False
            instruction = 'Proposal staged, NOT confirmed. Finish other products if any; then call policy_step readback for the combined proposals.'
    elif action == 'readback':
        staged = dict((k, v['staged']) for k, v in states.items() if v.get('staged') and k not in lst('x_recorded'))
        if not staged:
            raise ValueError('no staged values to read back')
        vars['x_readback'] = json.dumps(staged)
        instruction = 'Read back these exact proposals together: ' + '; '.join(products[k]['phrase'] + ', ' + money(v['amount']) + ', ' + v['date'] for k, v in staged.items()) + '. Ask exactly: ' + C['readback'] + ' Wait for a clear yes before policy_step confirm.'
    elif action == 'confirm':
        staged = dict((k, v['staged']) for k, v in states.items() if v.get('staged') and k not in lst('x_recorded'))
        if not staged or jl('x_readback', dict()) != staged:
            raise ValueError('a read-back of the current exact values is required')
        confirmed.update(staged)
        instruction = 'Confirmed exact values: ' + json.dumps(staged) + '. Call create_promise once per product with only these values.'
    else:
        raise ValueError('unknown policy action')
    vars['x_policy'] = json.dumps(states)
    vars['x_confirmed'] = json.dumps(confirmed)
    vars['policy_instruction'] = instruction
except Exception as e:
    if 'confirmed' in locals():
        vars['x_confirmed'] = json.dumps(confirmed)
    if 'states' in locals():
        vars['x_policy'] = json.dumps(states)
    vars['policy_instruction'] = 'Action blocked: ' + str(e)[:250] + '. Do not invent or repeat an offer. Follow the bank rules for the current state.'
vars['policy_pending'] = 'yes'
'''
