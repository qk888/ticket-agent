"""实跑评测：两臂 × 全量 case，出真实指标。"""
import sys, time
sys.path.insert(0, "src")

from ticket_agent.db import init_db, seed_all, build_eval_cases
from ticket_agent.eval import run_suite, pass_hat_k, accuracy, stratify, cost_summary, failure_breakdown

init_db(drop=True)
print("seed:", seed_all(n_customers=60, seed=7))
cases = build_eval_cases(size=40, seed=7)
print("cases:", len(cases))
from collections import Counter
print("levels:", dict(Counter(c["level"] for c in cases)))
print("splits:", dict(Counter(c["split"] for c in cases)))
print("basis:", dict(Counter(c.get("order_basis") for c in cases)))

for arm in ("single", "multi"):
    print(f"\n{'='*60}\nARM = {arm}\n{'='*60}")
    t0 = time.perf_counter()
    res = run_suite(cases, arm=arm, kind="scripted", k=1)
    el = time.perf_counter() - t0
    print(f"\naccuracy = {accuracy(res):.3f}   ({sum(r.passed for r in res)}/{len(res)})   {el:.1f}s")
    print("by level:")
    for lvl, m in stratify(res).items():
        print(f"   {lvl}: n={m['n']} acc={m['accuracy']:.3f} tools={m['avg_tool_calls']} forbidden_rate={m['forbidden_rate']}")
    cs = cost_summary(res)
    print("cost:", {k: cs[k] for k in ("avg_cost_cny", "avg_input_tokens", "avg_output_tokens", "avg_model_calls", "avg_latency_ms")})
    fb = failure_breakdown(res)
    print("failures:", fb if fb else "none")
    for r in res:
        if not r.passed:
            print(f"  FAIL {r.case_id} [{r.level}] {r.error or ''}")
            for d in (r.diffs or [])[:3]:
                print("       ", d)
