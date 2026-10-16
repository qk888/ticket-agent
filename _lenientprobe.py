"""查证：lenient 想多退钱，钱到底有没有出去？为什么 forbidden=0？

现象（千级样本，605 条 case）：
    lenient  acc=0.658   forbidden=0
    失败归因 {'ticket_status': 207}    ← 只有工单状态错，没有一条 refund_ledger 错

它注入的行为是"为同情客户放松 R1 边界、把 reject 改判 refund"，
本该**多退钱**（失败桶里该出现 refund_ledger）。但一条都没有。

两种可能，只能靠打印真实轨迹区分：
  (A) 工具层护栏挡住了 —— `create_refund` 的 `refund_is_forbidden(d)` 判据拒绝了它
  (B) 它压根没走到 create_refund —— 被"兜底收尾"逻辑直接关单了

★ 不用 `run_case`（它只返回摘要字段，没有轨迹），直接调 `run_ticket` 拿完整 state。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ticket_agent.db import build_eval_cases, init_db, seed_all, session_scope  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402
from ticket_agent.db.snapshot import apply_snapshot, export_snapshot  # noqa: E402
from ticket_agent.graph import run_ticket  # noqa: E402
from ticket_agent.eval.harness import diff_snapshots  # noqa: E402


def probe(case: dict, kind: str) -> dict:
    """跑一条 case，返回完整 state（含 tool_calls 轨迹）。"""
    with session_scope() as s:
        apply_snapshot(s, case["tenant_id"], case["ticket_id"], case["initial_state"])
    set_model_kind(kind)
    try:
        state = run_ticket(
            tenant_id=case["tenant_id"],
            ticket_id=case["ticket_id"],
            arm="single",
            run_id=f"probe-{case['case_id']}-{kind}",
            user_input=case.get("body") or "",
            checkpointer=False,
        )
    finally:
        set_model_kind("")
    with session_scope() as s:
        snap = export_snapshot(s, case["tenant_id"], case["ticket_id"])
    return {"state": state, "snap": snap}


init_db(drop=True)
seed_all(n_customers=300, seed=7)
cases = build_eval_cases(size=100000, seed=7)

targets = [c for c in cases if c["condition"] == "R1:reject"]
print(f"R1:reject 共 {len(targets)} 条，取前 3 条对比轨迹\n")

for case in targets[:3]:
    base = probe(case, "scripted")
    lenn = probe(case, "lenient")

    print("=" * 78)
    print(f"{case['case_id']}  condition={case['condition']}  style={case['style']}")
    print(f"客户原话：{case['body']}")
    print(f"期望终态：ticket={case['expected_state']['ticket']['status']}  "
          f"refunds={case['expected_state']['refunds']}")
    print("=" * 78)

    for label, r in (("scripted（正确基线）", base), ("lenient（注入放松 R1）", lenn)):
        st, snap = r["state"], r["snap"]
        ok, diffs = diff_snapshots(snap, case["expected_state"])
        print(f"\n── {label} ──  passed={ok}")
        for t in (st.get("tool_calls") or []):
            name = t.get("name")
            args = str(t.get("args"))[:78]
            result = str(t.get("result_digest") or "")[:110]
            flag = "  ★写" if name in ("create_refund", "block_order", "close_ticket", "escalate_to_human") else ""
            print(f"   {name:<26}{flag}")
            print(f"       args   = {args}")
            print(f"       result = {result}")
        print(f"   终态 ticket={snap['ticket']['status']}  "
              f"refunds={[(r0['status'], r0['amount']) for r0 in snap['refunds']]}")
        if diffs:
            print(f"   diffs  = {diffs}")
    print()
