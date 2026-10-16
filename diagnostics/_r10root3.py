"""复现并定性 `after_sales` 重复行的根因。

已量到的形状（三份诊断交叉）：
  - `_r10root2.py`：跑完 after=1，手动 apply_snapshot 之后 → 0 行。
    **重置本身能删干净**，漏删假设在这里被证伪。
  - `_r10dup3.py`：single 跑完 before=0→after=1；紧接着 multi 跑同一条，
    before=1 而 after 仍=1 → 幂等生效、没有新增行。

但 `_r10dup.py` 在同一个库里量到 after=2/3。差别是：
  `_r10dup.py` 一口气跑 16 条，`_r10root2.py` 只跑 1 条。
  → 怀疑 **`run_case` 内部先 reset 再跑**，而 reset 用的
    `apply_snapshot` 依赖 `repo.list_after_sales_of_order(order.id)`
    按**订单**删；若两条工单指向**同一个订单**，
    后一条 reset 会把前一条刚写的行一起删掉/重建，
    最终某条工单名下出现两行同键记录。

本脚本把"同一订单被几张工单共用"这件事直接量出来。

用法：
    python _r10root3.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10root3_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"] == "R10:compensate"]
print(f"R10:compensate 共 {len(r10)} 条")

print("\n== 每张工单对应的 order_id，以及该 order 被几张工单共用 ==")
order_to_tickets: dict[int, list[int]] = {}
for c in r10:
    with session_scope() as s:
        row = s.execute(
            _sqltext("SELECT order_id FROM tickets WHERE id=:k"), {"k": c["ticket_id"]}
        ).scalar()
    order_to_tickets.setdefault(row or -1, []).append(c["ticket_id"])
shared = {o: ts for o, ts in order_to_tickets.items() if len(ts) > 1}
print(f"   共用同一订单的组：{len(shared)} / {len(order_to_tickets)}")
for o, ts in list(shared.items())[:10]:
    print(f"      order={o} → tickets={ts}")

print("\n== 按原顺序一口气跑 16 条，数每轮的 before/after ==")
for i, c in enumerate(r10):
    tid, tk = c["tenant_id"], c["ticket_id"]
    with session_scope() as s:
        before = export_snapshot(s, tid, tk).get("after_sales") or []
        head = s.execute(_sqltext("SELECT COALESCE(MAX(id), 0) FROM audit_log")).scalar()
    res = run_case(c, arm="single", kind="scripted", k_index=0)
    with session_scope() as s:
        after = export_snapshot(s, tid, tk).get("after_sales") or []
        rows = s.execute(
            _sqltext(
                "SELECT id, order_id, ticket_id, idempotency_key FROM after_sales_actions "
                "WHERE ticket_id=:k ORDER BY id"
            ),
            {"k": tk},
        ).mappings().all()
    flag = ""
    if len(after) > 1:
        flag = "  ★ 重复"
    print(f"   {i+1:2d} {c['case_id']:14s} before={len(before)} after={len(after)} "
          f"db_rows={len(rows)} passed={res.passed}{flag}")
    if len(rows) > 1:
        for r in rows:
            print(f"        id={r['id']} order={r['order_id']} key={r['idempotency_key']}")

print(f"\n库：{DB_URL}")
