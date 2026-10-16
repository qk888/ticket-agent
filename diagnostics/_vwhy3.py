"""验证缺陷 ㉟ 的假设：那 24 条的 `pending_question` 是不是非空。

假设：verifier 放行条件里的 `pending_question` 由 agent 自己用 `ask_customer`
写进去 —— 它和 `basis`（输入侧纯函数）不同级。agent 问了一个 golden 并
**不需要**的问题，然后什么都不做，verifier 看到"问了客户"就放行。

做法：用同一批 case 参数重建数据，对这 24 条 (case_id, k) 重跑 **scripted**
基线，打印 verifier 判据实际读到的四个输入：status / basis / pending_question /
customer_reply。scripted 是确定性的，所以这些值是稳定可复现的。

如果这 24 条的 golden 都不要求"先澄清"，而 verifier 又确实放行了 →
缺陷 ㉟ 成立：**放行依据里混进了一个由被审对象书写的字段**。

用法：python _vwhy3.py
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy3.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot, resolve_ticket_order  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

# (case_id, k_index, arm, 备注) —— 来自 _vnone.py 在 600 run 里的命中
TARGETS = [
    ("acme-t15", 2, "single"),
    ("globex-t543", 0, "single"),
    ("globex-t543", 1, "single"),
    ("globex-t543", 2, "single"),
    ("globex-t368", 2, "multi"),
    ("globex-t525", 0, "multi"),
    ("globex-t525", 1, "multi"),
    ("globex-t359", 0, "multi"),
    ("globex-t569", 0, "multi"),
    ("acme-t272", 0, "multi"),
    ("acme-t272", 1, "multi"),
    ("acme-t272", 2, "multi"),
    ("globex-t363", 0, "multi"),
    ("acme-t303", 0, "multi"),
]


def main() -> int:
    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    cases = build_eval_cases(size=200, seed=7)
    by_id = {c["case_id"]: c for c in cases}
    print(f"重建 {len(cases)} 条 case（customers=1000 seed=11 size=200 seed=7）\n")

    rows = []
    for case_id, k, arm in TARGETS:
        c = by_id.get(case_id)
        if c is None:
            print(f"  {case_id}：不在这批 case 里（换了参数），跳过")
            continue
        set_model_kind("scripted")
        res = run_case(c, arm=arm, kind="scripted", k_index=k)
        set_model_kind("")
        with session_scope() as s:
            snap = export_snapshot(s, c["tenant_id"], c["ticket_id"])
        t = snap.get("ticket") or {}
        rows.append(
            {
                "case_id": case_id,
                "k": k,
                "arm": arm,
                "cond": res.condition,
                "status": t.get("status"),
                "basis": snap.get("order_basis"),
                "pending_question": bool(t.get("pending_question")),
                "customer_reply": bool(t.get("customer_reply")),
                "act": res.observed_action,
                "verifier": res.verifier_passed,
                "passed": res.passed,
            }
        )

    print(f"{'case_id':14} {'k':>1} {'arm':6} {'cond':24} {'status':14} {'basis':12} {'pq':5} {'reply':5} {'act':6} ver")
    for r in rows:
        print(
            f"{r['case_id']:14} {r['k']:>1} {r['arm']:6} {str(r['cond']):24} "
            f"{str(r['status']):14} {str(r['basis']):12} {str(r['pending_question']):5} "
            f"{str(r['customer_reply']):5} {str(r['act']):6} {r['verifier']}"
        )

    bad = [r for r in rows if r["verifier"] and r["act"] == "none" and r["status"] == "open"]
    print(f"\nscripted 基线下复现出 {len(bad)} 条（verifier 放行 + 零动作 + 仍 open）")
    for r in bad:
        print(
            f"  {r['case_id']} k={r['k']} {r['arm']} {r['cond']} "
            f"basis={r['basis']} pending_question={r['pending_question']}"
        )
    print(
        "\n判读：\n"
        "  · 若 bad 的 pending_question 全为 True → 缺陷 ㉟ 成立：\n"
        "    放行依据是一个 agent 自己写的字段。\n"
        "  · 若 bad 的 basis 全为 ambiguous → 放行正确（golden 就是保持 open），\n"
        "    那 24 条是「diffs 要求写」与「golden 要求 open」两份口径不一致，\n"
        "    要改的是 diff 生成而不是 verifier。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
