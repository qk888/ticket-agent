import datetime as dt, sys
sys.path.insert(0, "src")
from ticket_agent import rules as rules_mod
from ticket_agent.db import build_eval_cases, init_db, seed_all

class S:
    def __init__(s, d): s._d = d
    def __getattr__(s, n):
        if n == "datetime":
            d = s._d
            class D(dt.datetime):
                @classmethod
                def now(cls, tz=None): return dt.datetime.now(tz) + d
            return D
        return getattr(dt, n)

init_db(drop=True); seed_all(n_customers=60, seed=7)
base = {c["case_id"]: c for c in build_eval_cases(size=10000, seed=7)}
rules_mod.dt = S(dt.timedelta(hours=12))
cur = {c["case_id"]: c for c in build_eval_cases(size=10000, seed=7)}
rules_mod.dt = dt

same_id = sorted(set(base) & set(cur))
diff_cond = [k for k in same_id if base[k]["condition"] != cur[k]["condition"]]
diff_body = [k for k in same_id if base[k]["body"] != cur[k]["body"]]
diff_exp  = [k for k in same_id if base[k]["expected_state"] != cur[k]["expected_state"]]
diff_init = [k for k in same_id if base[k]["initial_state"] != cur[k]["initial_state"]]
print("相同 case_id 数:", len(same_id))
print("condition 不同:", len(diff_cond), diff_cond[:6])
print("body 不同     :", len(diff_body), diff_body[:6])
print("expected 不同 :", len(diff_exp), diff_exp[:6])
print("initial  不同 :", len(diff_init), diff_init[:6])
for k in diff_cond[:3]:
    print("---", k)
    print("  base body:", base[k]["body"][:70])
    print("  cur  body:", cur[k]["body"][:70])
    print("  base exp :", base[k]["expected_state"])
    print("  cur  exp :", cur[k]["expected_state"])
