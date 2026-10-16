"""冒烟测试：多角色 triage→resolver→verifier 是否跑通。"""
import sys, time
sys.path.insert(0, "src")

from ticket_agent.db import init_db, seed_all, build_eval_cases
from ticket_agent.db.session import session_scope, TenantRepo
from ticket_agent.eval.harness import run_case

init_db(drop=True)
print("seed:", seed_all(n_customers=60, seed=7))
cases = build_eval_cases(size=6, seed=7)
print("cases:", len(cases))

for c in cases[:3]:
    t0 = time.perf_counter()
    r = run_case(c, arm="multi", kind="scripted")
    ms = (time.perf_counter() - t0) * 1000
    print(f"\n=== {c['case_id']} [{c['level']}] passed={r.passed} {ms:.0f}ms")
    print("   expected_action:", r.expected_action, "observed:", r.observed_action)
    print("   tool_calls:", r.tool_calls, "write_calls:", r.write_calls)
    print("   forbidden:", r.forbidden_hits)
    if r.error:
        print("   ERROR:", r.error)
    for d in (r.diffs or [])[:4]:
        print("   diff:", d)
