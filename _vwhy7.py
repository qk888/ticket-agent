"""确认 11 条 write_calls=1 的那一次写到底是什么工具。

`_vwhy6.py` 已证两件事：
  ① `_summarize_action` 只认 `create_refund / block_order / escalate_to_human /
     close_ticket` 四个名字，`submit_after_sales_action` **不在列** → 判成 'none'；
  ② multi 有 11 条 run `write_calls=1` 而 `after_sales: 实际 []`。
若这 11 条写的是 `submit_after_sales_action`，则 diffs 应该是"有一条但 kind
不符"而不是 `[]`。所以要么写的是别的工具（close_ticket / escalate_to_human /
ask_customer / mark_evidence_received），要么写了但 `ok=False`。

★ 重要：`_summarize_action` 只从 `trace` 里读，而 `trace` 来自
`state["tool_calls"]`，每条记录带 `ok` 字段。本脚本重跑那 11 条，打印
**完整 trace**（工具名 + ok + args 摘要），一次性分清四种可能。
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy7.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

# (case_id, k) —— 来自 _vwhy6.py 的 11 条里挑形状各异的 6 条
TARGETS = [
    ("globex-t359", 2),  # R12:issue_invoice
    ("acme-t272", 0),    # R10:compensate
    ("acme-t272", 2),
    ("acme-t249", 1),    # R14:urge_logistics
    ("acme-t10", 0),     # R9:reship（期望 order.reship_count=1）
    ("acme-t311", 0),    # R14:urge_logistics 但 expected_action=refund
]


def main() -> int:
    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    cases = build_eval_cases(size=200, seed=7)
    by_id = {c["case_id"]: c for c in cases}
    print(f"重建 {len(cases)} 条 case\n")

    for case_id, k in TARGETS:
        c = by_id.get(case_id)
        if c is None:
            print(f"{case_id}：不在这批 case 里，跳过\n")
            continue
        set_model_kind("scripted")
        res = run_case(c, arm="multi", kind="scripted", k_index=k)
        set_model_kind("")
        print(
            f"{case_id} k={k} {res.condition} level={res.level} "
            f"expected_action={res.expected_action!r} observed={res.observed_action!r} "
            f"write_calls={res.write_calls} verifier={res.verifier_passed}"
        )
        print(f"  diffs: {res.diffs}")
        # 逐条打印 trace：名字 / ok / 关键参数
        # tool_calls 是 ToolCallRecord（dataclass），用 getattr 兼容 dict
        for t in res.__dict__.get("tool_calls", []) or []:
            name = getattr(t, "name", None) or t.get("name")
            ok = getattr(t, "ok", None) if not isinstance(t, dict) else t.get("ok")
            args = getattr(t, "args", None) if not isinstance(t, dict) else t.get("args")
            print(f"    · {name:28} ok={ok} args={args}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
