Development brief D (synthetic). Fennick Business Bank is fictional. Raw material forwarded as received.

---------------------------------------------------------------------------
From: Ruth Okafor <r.okafor@fennick.example>
To: Collections Automation
Date: 2 November 2026
Subject: SME arrears calls – voice assistant scope

Hi team,

As agreed, the assistant will phone the business contact of SME customers who are behind on up to three facilities. Attached is the policy extract (CP-7, v3). Please follow it to the letter, but note the memo from Compliance further down – it changes a few things and it wins where the two disagree.

The system gives you `customer_id`, `contact_name` (the person we ask for, e.g. "Peter Lindqvist"), `company_name` (e.g. "Lindqvist Joinery Ltd") and `timestamp` (local time of the call, e.g. 2026-11-09T10:15). Use `timestamp` for every date; the server clock is wrong.

The assistant should introduce itself as "Fennick's virtual assistant".

Thanks,
Ruth

---------------------------------------------------------------------------
POLICY EXTRACT CP-7 v3 — Outbound SME arrears contact

7.1 Greeting. The assistant says: "Hello, this is Fennick's virtual assistant calling from Fennick Business Bank. Calls are recorded." It then asks: "Am I speaking with {contact_name} from {company_name}?" Nothing about arrears, facilities, sums or the bank's reasons may be said to anyone before verification.

7.2 Only an explicit yes or the contact's own name confirms. Ask twice at most; then end with code NR-3.

7.3 Third parties (receptionist, colleague): do not explain the call. Ask when the contact is available. Callback: today (after the current time) or the next business day, 08:30 to 18:00 inclusive. Book it (code NR-1). Outside the window: ask once more; then end with NR-2. Wrong company/number: NR-4. Business closed down / contact deceased: say "Thank you for letting us know." and end with NR-5.

7.4 Verification. Ask, one at a time: the company registration number, then the year the company was founded. Registration numbers are 8 digits. Three wrong answers in total fail the check: refer the caller to their relationship manager, end with NV-1. A caller who refuses after one explanation that this protects the business: NV-2.

7.5 Disclosure. After verification call get_account. Say you are calling about overdue payments on the business's facilities, then for each facility: its name ("business overdraft", "term loan", "asset finance agreement"), the arrears amount and days in arrears.

7.6 Calendar. Business days Monday–Friday. Bank holidays: 2026-11-11, 2026-12-25, 2026-12-28, 2027-01-01.

7.7 Deadlines by arrears:
   - 1–14 days: 10 business days after today, 1 attempt
   - 15–90 days: 3 business days after today, 2 attempts

7.8 Minimum acceptable amount: `minimum_due` on every facility. The assistant always proposes the amount; never ask the caller to name one.

7.9 First ask: "Can the business make this payment today?" (several facilities: "Can the business clear all of these today?").

7.10 Persuasion. A refusal uses an attempt. Attempt 1: the full arrears on the next business day. Attempt 2: the minimum on the deadline. Exhausted: PR-2.

7.11 Read-back: "Can you confirm that?" Record only after a yes. On a failed recording try once more, then apologise and end with PR-5.

7.12 Special cases: already paid → "Payments can take up to two business days to reach us." PD-1. Disputes the debt → PR-3. Cash-flow trouble or insolvency → offer the business support unit; yes → transfer queue `business_support`, end TR-2; no → PR-4. Restructuring / new terms → transfer `restructure`, TR-1. Asks for a human → ask once if you can help, then transfer `relationship`, TR-3.

7.13 Several facilities: present all, then work through them in order. Each facility without a promise gets add_note with reason code. No notes on transferred calls. Closing: any recorded promise → PR-1, unless the call was transferred.

---------------------------------------------------------------------------
MEMO — Compliance, 5 November 2026 (supersedes CP-7 v3 where different)

a) The second identity question is now the year the company was founded ONLY for companies; we keep it. But three wrong answers is reduced to TWO wrong answers in total.
b) 2026-12-24 is added as a bank holiday.
c) The 15–90 days band now gets 5 business days, not 3.
d) After a recorded promise the assistant must say: "A confirmation will be emailed to the company's registered address."
e) Customer support asked: do not book callbacks for today any more; the next business day only.

---------------------------------------------------------------------------
CODE LIST — collections outcomes (system values in brackets)

PR-1 promise recorded            [outcome=commitment]
PD-1 already paid                [outcome=settled]
TR-1 transferred, restructuring  [outcome=escalated, reason=restructure]
TR-2 transferred, support        [outcome=escalated, reason=support]
TR-3 transferred, human          [outcome=escalated, reason=human]
PR-2 refused                     [outcome=no_commitment, reason=refused]
PR-3 disputed                    [outcome=no_commitment, reason=disputed]
PR-4 cash-flow hardship          [outcome=no_commitment, reason=hardship]
PR-5 system error                [outcome=no_commitment, reason=system_error]
NV-1 verification failed         [outcome=unverified, reason=failed]
NV-2 verification refused        [outcome=unverified, reason=refused]
NR-1 callback booked             [outcome=unreached, reason=callback]
NR-2 no callback time            [outcome=unreached, reason=no_time]
NR-3 identity unclear            [outcome=unreached, reason=unclear]
NR-4 wrong number                [outcome=unreached, reason=wrong_number]
NR-5 closed down / deceased      [outcome=unreached, reason=gone]

Note reasons for add_note: refused, disputed, hardship, settled, system_error.
