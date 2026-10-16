"""诊断：重新 seed 出来的数据，到底哪里不一样？

`_nondetcheck.py` 已把范围缩小到"**重新 seed** 这一步"：
  - 同一份 cases 连跑三次 → 0/0/0（agent 与评测层是稳的）
  - 每次重新 seed 再跑 → 2/2/1（数据在变）
  - 子进程不重新 seed → 每次 1（稳）

时钟锚点已修（`_driftcheck.py` 正向 0 差异），所以还有第二个非确定源。
这里逐个字段比对两次 seed 的结果，指出第一个不同的字段。
"""

import hashlib
import sys

sys.path.insert(0, "src")

from sqlalchemy import select  # noqa: E402

from ticket_agent import clock  # noqa: E402
from ticket_agent.db import (  # noqa: E402
    Order,
    Refund,
    Ticket,
    build_eval_cases,
    init_db,
    seed_all,
    session_scope,
)
from ticket_agent.db.snapshot import export_snapshot, resolve_ticket_order  # noqa: E402
from ticket_agent.db.session import TenantRepo  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def dump(session) -> dict[str, list]:
    """把关心的字段全导出来，按 id 排序。"""
    orders = list(session.scalars(select(Order).order_by(Order.id)))
    tickets = list(session.scalars(select(Ticket).order_by(Ticket.id)))
    refunds = list(session.scalars(select(Refund).order_by(Refund.id)))
    return {
        "order.created_at": [(o.id, str(o.created_at)) for o in orders],
        "order.paid_at": [(o.id, str(o.paid_at)) for o in orders],
        "order.shipped_at": [(o.id, str(o.shipped_at)) for o in orders],
        "order.signed_at": [(o.id, str(o.signed_at)) for o in orders],
        "order.core": [(o.id, o.order_no, str(o.amount), o.status, o.category,
                        o.refund_count) for o in orders],
        "ticket.created_at": [(t.id, str(t.created_at)) for t in tickets],
        "ticket.core": [(t.id, t.tenant_id, t.customer_id, t.order_id, t.subject)
                        for t in tickets],
        "refund.core": [(r.id, r.order_id, str(r.amount), r.status, r.idempotency_key)
                        for r in refunds],
    }


def build() -> tuple[dict, list[dict], str]:
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    with session_scope() as s:
        data = dump(s)
    cases = build_eval_cases(size=30, seed=7)
    fps = "".join(f"{c['case_id']}:{c.get('condition','?')};" for c in
                  sorted(cases, key=lambda c: c["case_id"]))
    return data, cases, hashlib.sha256(fps.encode()).hexdigest()[:12]


runs = []
for i in range(4):
    data, cases, fp = build()
    runs.append((data, cases, fp))
    print(f"第 {i+1} 次  case 指纹={fp}  锚点={clock.get_anchor()}")
    print(f"         levels={{'L1': {sum(1 for c in cases if c['level']=='L1')}, "
          f"'L2': {sum(1 for c in cases if c['level']=='L2')}, "
          f"'L3': {sum(1 for c in cases if c['level']=='L3')}}}")

print("=" * 84)
print("逐字段比对（以第 1 次为基准）：")
keys = list(runs[0][0].keys())
for idx in range(1, len(runs)):
    print(f"\n── 第 1 次 vs 第 {idx+1} 次 ──")
    data_a, data_b = runs[0][0], runs[idx][0]
    any_diff = False
    for k in keys:
        a, b = data_a[k], data_b[k]
        if a == b:
            continue
        any_diff = True
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        print(f"  ✗ {k}: {len(diffs)}/{len(a)} 项不同")
        for x, y in diffs[:3]:
            print(f"      {x}")
            print(f"   →  {y}")
    if not any_diff:
        print("  （全部字段相同）")

print("=" * 84)
print("case 层面比对（第 1 次 vs 其余）：")
for idx in range(1, len(runs)):
    a = {c["case_id"]: c.get("condition") for c in runs[0][1]}
    b = {c["case_id"]: c.get("condition") for c in runs[idx][1]}
    flips = [(k, a[k], b[k]) for k in sorted(set(a) & set(b)) if a[k] != b[k]]
    print(f"  第 1 vs {idx+1}: 同 {len(set(a)&set(b))}  翻转 {len(flips)}  "
          f"仅基准 {len(set(a)-set(b))}  仅此轮 {len(set(b)-set(a))}")
    for k, x, y in flips[:5]:
        print(f"      {k}: {x} → {y}")
print("=" * 84)
