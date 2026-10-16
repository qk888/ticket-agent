"""定位 `after_sales` 重复行的根因。

`_r10dup2.py` 已把范围缩到：**`apply_snapshot` 之前库里就有残留行**
（`before` 非空），跑完再多一行。也就是说重复不是 agent 造成的，
是**重置没把 after_sales 清干净**。

本脚本验证这个假设，并找出漏掉的那一条是什么形状：
  1. 对同一条 case 连续跑 3 次，每次打印 before/after；
  2. 若 before 每轮都 +1 → apply_snapshot 漏删某类行；
  3. 直接查 after_sales 表，看那些行的 idempotency_key / order_id / ticket_id
     与快照里重建出来的有什么不同。

用法：
    python _r10root.py
"""
from __future__ import annotations

import os

DB_URL = "sqlite:///./eval_out/r10root_reqkind.db"
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy import text as _sqltext  # noqa: E402

from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402

seed_all(1000, 90, 7)
cases = build_eval_cases(size=605, seed=7)
r10 = [c for c in cases if c["condition"] == "R10:compensate"]
# ★ 不硬编码 case_id：不同 seed 下同名 case 的条件会变（本项目踩过：
#   诊断脚本跑出了结论，但结论指的是另一份数据上的同名 case）。
#   取第一条即可 —— 要证的是"重复行从哪来"这个机制，不是某一个案。
target = r10[0]
cid = target["case_id"]
tid, tk = target["tenant_id"], target["ticket_id"]
print(f"目标 {cid}  ticket_id={tk}")

print("\n== 连续跑 3 次，看 before 是否每轮 +1 ==")
for i in range(3):
    with session_scope() as s:
        before = export_snapshot(s, tid, tk).get("after_sales") or []
    res = run_case(target, arm="single", kind="scripted", k_index=0)
    with session_scope() as s:
        after = export_snapshot(s, tid, tk).get("after_sales") or []
    print(f"   第 {i+1} 轮：before={len(before)}  after={len(after)}  passed={res.passed}")

print("\n== 直接查 after_sales 表（不经过 snapshot 口径）==")
with session_scope() as s:
    rows = s.execute(
        _sqltext(
            "SELECT id, order_id, ticket_id, kind, amount, status, idempotency_key "
            "FROM after_sales_actions WHERE ticket_id=:k ORDER BY id"
        ),
        {"k": tk},
    ).mappings().all()
    print(f"   共 {len(rows)} 行")
    for r in rows:
        print(f"      id={r['id']} order={r['order_id']} kind={r['kind']} "
              f"amt={r['amount']} key={r['idempotency_key']}")

print("\n== 该 case 的 initial_state.after_sales ==")
print("   ", (target.get("initial_state") or {}).get("after_sales"))
print("== 该 case 的 expected_state.after_sales ==")
print("   ", (target.get("expected_state") or {}).get("after_sales"))
print(f"\n库：{DB_URL}")
