"""确认 11 条 write_calls=1 的那一次写到底是什么工具（第二版）。

第一版崩在 `res.tool_calls` 是 **int**（CaseResult 的过程轴字段叫这个名字，
值是计数，不是轨迹列表）。`run_case` 里的轨迹变量叫 `trace`。

本版把 trace 从 `run_case` 内部拿出来不现实（局部变量），改用**审计日志**：
`_audit()` 记下每一次工具调用（actor / action / ok / args），而 `apply_snapshot`
按 case 重置，所以跑完一条 case 后 audit_log 里正好是这一条的轨迹。
这比重建 trace 更可靠 —— 它就是系统自己的账。

用法：python _vwhy8.py
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy8.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.models import AuditLog  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

TARGETS = [
    ("globex-t359", 2),  # 之前是 R12:issue_invoice；本次重建后变成 R14:urge_logistics
    ("acme-t272", 0),
    ("acme-t272", 2),
    ("acme-t249", 1),
    ("acme-t10", 0),
    ("acme-t311", 0),
]


def main() -> int:
    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    cases = build_eval_cases(size=200, seed=7)
    by_id = {c["case_id"]: c for c in cases}
    print(f"重建 {len(cases)} 条 case（customers=1000 seed=11 size=200 seed=7）\n")
    print("★ 注意：这批 case 与跑批时的那批可能不同（见脚本 docstring 的原因），")
    print("  所以这里只回答『write_calls=1 的那一次写是什么工具』这个**与批次无关**的问题。\n")

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
            f"tool_calls={res.tool_calls} write_calls={res.write_calls} "
            f"passed={res.passed} verifier={res.verifier_passed}"
        )
        for d in res.diffs:
            print(f"    diff: {d}")
        with session_scope() as s:
            logs = (
                s.query(AuditLog)
                .filter(AuditLog.ticket_id == c["ticket_id"])
                .order_by(AuditLog.id.asc())
                .all()
            )
            print(f"    audit {len(logs)} 条（ticket_id={c['ticket_id']}）:")
            for a in logs:
                payload = a.payload if isinstance(a.payload, dict) else {}
                args = {kk: vv for kk, vv in payload.items() if kk not in ("result",)}
                print(
                    f"      · {a.actor:9} {a.action:28} ok={payload.get('ok', True)} args={args}"
                )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
