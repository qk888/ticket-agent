"""取证：test_writes_are_attributed_and_counted 为什么拿不到动作？

按 conftest 的方式用**测试库**跑一遍，把每一步打出来：
  - 筛出来的 case 是哪条、golden 期望什么
  - 跑之前 ticket.status 是什么
  - 跑之后 ticket.status 是什么
  - tool_calls / actions_taken / verifier verdict
这样能分清是"case 没选中"还是"跑了但没动作"还是"合并语义又错了"。
"""
import os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[0]
TEST_DB = _ROOT / "eval_out" / "pytest_probe.db"
for suf in ("", "-wal", "-shm"):
    p = Path(str(TEST_DB) + suf)
    if p.exists():
        p.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")

from ticket_agent.db import init_db, seed_all, build_eval_cases
from ticket_agent.service import run_once
from ticket_agent.graph.nodes import set_model_kind

init_db(drop=True)
print("seed:", seed_all(n_customers=60, seed=11))
cases = build_eval_cases(size=40, seed=11)
print("cases:", len(cases))

paid = [c for c in cases if (c["expected_state"].get("ticket") or {}).get("status") == "resolved"]
print(f"golden 期望 resolved 的 case：{len(paid)}")
if not paid:
    raise SystemExit("一条都没有 —— 筛选口径变了，不是合并语义的问题")

set_model_kind("scripted")
c = paid[0]
print(f"\n选中 {c['case_id']}  tenant={c['tenant_id']} ticket={c['ticket_id']}")
print("  golden ticket:", c["expected_state"]["ticket"])
print("  golden order :", c["expected_state"].get("order"))
print("  golden refund:", c["expected_state"].get("refunds"))

rec = run_once(c["tenant_id"], c["ticket_id"], arm="multi")
s = rec.summary()
print("\n--- run_once(multi) 结果 ---")
print("  status      :", rec.status)
print("  actions     :", s.get("actions_taken"))
print("  tool_calls  :", s.get("tool_calls"))
print("  verdict     :", s.get("verdict"))
print("  summary 全部键:", sorted(s.keys()))

