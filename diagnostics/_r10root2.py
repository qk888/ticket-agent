"""复现并定性 `after_sales` 重复行的根因。

三份诊断已经量到的形状：
  - `_r10dup3.py`：single 跑完 before=0→after=1；紧接着 multi 跑同一条，
    before=1（**上一轮写入的行没被 apply_snapshot 清掉**）但 after 仍=1
    → 写入侧幂等生效，重复来自**重置漏删**。
  - `_r10root.py` 第一轮：连续 3 次，before = 0 / 1 / 1，
    after 恒 1 → 同一形状。

本脚本直接查 after_sales 表**不经过 snapshot 口径**，
看第 1 轮写入的那行在表里到底还在不在，以及它的 idempotency_key
与 `apply_snapshot` 重建时用的 `snap-a-*` 键有什么差别。
若行还在 → `apply_snapshot` 的 `list_after_sales_of_order(order.id)`
漏了某个维度（极可能是**同一订单、另一张工单**写入的行，
按 order 删会全删；按 ticket 删会漏 —— 要看它实际删的是哪个）。

用法：
    python _r10root2.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10root2_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"] == "R10:compensate"]
c = r10[0]
cid = c["case_id"]
tid, tk = c["tenant_id"], c["ticket_id"]
print(f"目标 {cid}  tenant={tid} ticket_id={tk}")


def dump(label: str) -> None:
    with session_scope() as s:
        rows = s.execute(
            _sqltext(
                "SELECT id, order_id, ticket_id, kind, amount, status, idempotency_key "
                "FROM after_sales_actions ORDER BY id"
            ),
        ).mappings().all()
        same_ticket = [r for r in rows if r["ticket_id"] == tk]
    print(f"\n-- {label}：全表 {len(rows)} 行，其中 ticket_id={tk} 的 {len(same_ticket)} 行")
    for r in same_ticket:
        print(f"   id={r['id']} order={r['order_id']} kind={r['kind']} "
              f"amt={r['amount']} key={r['idempotency_key']}")


dump("造数后")
res = run_case(c, arm="single", kind="scripted", k_index=0)
print(f"\n第 1 轮 run_case(single)：passed={res.passed}")
dump("第 1 轮跑完")

with session_scope() as s:
    from ticket_agent.db.snapshot import apply_snapshot
    apply_snapshot(s, tid, tk, c.get("initial_state") or {})
dump("手动 apply_snapshot 之后")

print("\n== 结论判据 ==")
print("  若「手动 apply_snapshot 之后」仍有 ticket_id={tk} 的行 → 重置漏删")
print("  若没了 → run_case 内部的 reset 之外还有别处写入")
print(f"\n库：{DB_URL}")
