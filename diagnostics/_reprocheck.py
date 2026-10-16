import sys, hashlib, json
sys.path.insert(0, "src")
from ticket_agent.db import build_eval_cases, init_db, seed_all

init_db(drop=True); seed_all(n_customers=60, seed=7)
cases = build_eval_cases(size=30, seed=7)
fp = hashlib.sha256(json.dumps(
    [(c["case_id"], c["level"], c["condition"], c["expected_state"]) for c in cases],
    sort_keys=True, default=str).encode()).hexdigest()[:16]
print("指纹:", fp)
lv = {}
for c in cases: lv[c["level"]] = lv.get(c["level"],0)+1
print("levels:", dict(sorted(lv.items())), "n=", len(cases))
