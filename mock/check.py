"""Replay every test-customers.json entry against the mock; status + body must match exactly.
usage: python mock/check.py [BASE_URL]   (default http://localhost:8787)"""
import json, sys, urllib.request, urllib.error, pathlib

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787").rstrip("/")
DB = json.loads((pathlib.Path(__file__).parent / "test-customers.json").read_text())
FULL = {"customerNo": "", "cardNumber": "540000******1234", "intAddressInfoType": "E", "currencyType": "TL",
        "statementDate": "05.10.2026", "emailAddress": "x@y.example", "statementDay": 5}

def post(svc, data):
    req = urllib.request.Request(f"{BASE}/voice/v1/{svc}", json.dumps({"data": data, "call": {"lang": "tr"}}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "mock-check"})  # workers.dev 403s Python-urllib
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

fails = n = 0
def check(svc, data, entry):
    global fails, n
    n += 1
    got = post(svc, {**FULL, **data})
    if got != (entry["http"], entry["response"]):
        fails += 1; print("FAIL", svc, data, got[0], entry["http"])

for c in DB["customers"]:
    for svc, v in c["services"].items():
        if "http" in v: check(svc, {"customerNo": int(c["customerNo"])}, v); continue  # number form on purpose
        for card, e in v.items():
            if "http" in e: check(svc, {"customerNo": c["customerNo"], "cardNumber": card}, e); continue
            for week, w in e.items(): check(svc, {"cardNumber": card, "newStatementWeek": int(week)}, w)

# not-found / empty paths
assert post("cards.list", {"customerNo": "11111111"})[1]["result"]["code"] == "FB_IVR_CHANNEL_102"
assert post("cards.balance", {"cardNumber": "000000******0000"})[0] == 404
assert post("cards.balance", {"cardNumber": ""})[1]["result"]["code"] == "INVALID_ARGUMENTS_ERROR"
print(f"{n - fails}/{n} entries match")
sys.exit(1 if fails else 0)
