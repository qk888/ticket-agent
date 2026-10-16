import sys, time
sys.path.insert(0, "src")
from ticket_agent.db import init_db, seed_all, build_eval_cases

for n in (1000, 2000):
    t0 = time.time()
    init_db(drop=True)
    st = seed_all(n_customers=n, seed=7)
    cases = build_eval_cases(size=100000, seed=7)
    lv = {}
    for c in cases: lv[c["level"]] = lv.get(c["level"], 0) + 1
    cond = {}
    for c in cases: cond[c["condition"]] = cond.get(c["condition"], 0) + 1
    sty = {}
    for c in cases: sty[c["style"]] = sty.get(c["style"], 0) + 1
    print(f"=== n_customers={n} ({time.time()-t0:.1f}s) ===")
    print("seed:", st)
    print("cases:", len(cases), "levels:", lv)
    print("conditions:", len(cond), dict(sorted(cond.items(), key=lambda x: -x[1])))
    print("styles:", sty)
    print("source:", {k: v for k, v in st.items() if k.startswith("text_source")})
