"""把 24 条的定性收口：拿**同一批** case 重跑，只看 verifier 读到的字段。

已确认的事实链：
  1. 重建是确定性的 —— `_vwhy10.py` 连跑两次 `seed_all(n_customers=1000, seed=11)`
     + `build_eval_cases(size=200, seed=7)`，两批 case_id 列表**完全相同**。
     → 所谓"三次不同"是我自己的参数写错了（`_vwhy3/4` 用了别的组合），
       不是系统不确定。这条必须写在结论里，否则会误判成缺陷 ⑨ 复发。
  2. `_vwhy5.py`：24/24 条的 golden 都要求离开 open 或要求写。
     → verifier 放行"什么都没做"没有 golden 层面的正当理由。
  3. `_vwhy6.py`：`_summarize_action` 只认 4 个工具名，
     `submit_after_sales_action` 不在列 → 判 'none'。
     multi 有 11 条 `write_calls=1` 而 `after_sales: 实际 []`。
  4. `_vwhy8.py`：scripted 基线下 `submit_after_sales_action.urge` 成功写库、
     审计里有记录、`observed='resolve'`（来自 close_ticket）→
     **`_summarize_action` 漏 kind 是真的**，与 agent 无关。

所以 24 条要分成两类，改法完全不同：

  A 类（`_summarize_action` 漏 kind）：agent 真写了售后动作，标签却报 'none'。
     症状 = write_calls>0 + after_sales 实际 [] + observed 'none'。
     → 报表谎报"什么都没做"，而 verifier_passed 与它无关（这是**汇总**缺陷）。
  B 类（verifier 真放行）：write_calls 写的是 close_ticket/escalate_to_human
     （golden 要的是售后动作），或 turns=2 的第二轮。
     → 才是 verifier 判据要改的。

本脚本用同一批 case（参数已坐实）重跑全部 24 条，按 write_calls 与
diffs 形状分 A/B，并打印 verifier 实际读到的 status/basis/pending_question。
用法：python _vwhy11.py
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy11.db")

from ticket_agent.db import build_eval_cases, init_db, seed_all  # noqa: E402
from ticket_agent.db.session import session_scope  # noqa: E402
from ticket_agent.db.snapshot import export_snapshot  # noqa: E402
from ticket_agent.eval.harness import run_case  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402

#: (case_id, k, arm) —— 24 条命中（来自 _vnone.py）
TARGETS = [
    ("acme-t15", 2, "single"),
    ("globex-t543", 0, "single"),
    ("globex-t543", 1, "single"),
    ("globex-t543", 2, "single"),
    ("globex-t368", 2, "multi"),
    ("globex-t525", 0, "multi"),
    ("globex-t525", 1, "multi"),
    ("globex-t359", 2, "multi"),
    ("globex-t569", 1, "multi"),
    ("acme-t272", 0, "multi"),
    ("acme-t272", 1, "multi"),
    ("acme-t272", 2, "multi"),
    ("globex-t363", 0, "multi"),
    ("acme-t303", 0, "multi"),
    ("acme-t249", 1, "multi"),
    ("acme-t59", 0, "multi"),
    ("acme-t86", 0, "multi"),
    ("acme-t86", 1, "multi"),
    ("globex-t329", 1, "multi"),
    ("acme-t10", 0, "multi"),
    ("acme-t10", 2, "multi"),
    ("acme-t311", 0, "multi"),
    ("acme-t304", 1, "multi"),
]


def main() -> int:
    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    cases = build_eval_cases(size=200, seed=7)
    by_id = {c["case_id"]: c for c in cases}
    print(f"重建 {len(cases)} 条 case（参数已由 _vwhy10.py 坐实为确定性）\n")

    a_kind: list[str] = []
    b_ver: list[str] = []
    missing: list[str] = []

    for case_id, k, arm in TARGETS:
        c = by_id.get(case_id)
        if c is None:
            missing.append(f"{case_id}/k{k}")
            continue
        set_model_kind("scripted")
        res = run_case(c, arm=arm, kind="scripted", k_index=k)
        set_model_kind("")
        with session_scope() as s:
            snap = export_snapshot(s, c["tenant_id"], c["ticket_id"])
        t = snap.get("ticket") or {}
        pq = bool(t.get("pending_question"))
        needs_after = any(
            d.startswith("after_sales:") and "实际 []" in d for d in res.diffs
        )
        is_a = res.write_calls > 0 and needs_after and res.observed_action == "none"
        tag = "A(汇总漏 kind)" if is_a else "B(verifier 放行)"
        line = (
            f"{tag:18} {case_id:12} k={k} {arm:6} {res.condition:22} "
            f"write={res.write_calls} tools={res.tool_calls} turns={res.turns} "
            f"act={res.observed_action!r} pq={pq} basis={snap.get('order_basis')} "
            f"status={t.get('status')} ver={res.verifier_passed}"
        )
        print(line)
        if is_a:
            a_kind.append(case_id)
        else:
            b_ver.append(case_id)

    print(f"\n不在这批 case 里：{len(missing)} 条 {missing}")
    print(f"A 类（`_summarize_action` 漏 kind，agent 其实写了）：{len(a_kind)} 条 {a_kind}")
    print(f"B 类（verifier 判据放行）：{len(b_ver)} 条 {b_ver}")
    print(
        "\n结论口径：\n"
        "  · A 类是**报表汇总**缺陷：observed_action 报 'none' 而库里真有记录。\n"
        "    修 `_summarize_action` 认 submit_after_sales_action 的 kind。\n"
        "  · B 类里若 pq=True → 缺陷 ㉟ 成立：verifier 放行依据 `pending_question`\n"
        "    由被审对象自己书写（agent 问一句就免检）。\n"
        "  · B 类里若 pq=False 而 status=open → verifier 漏判，要查别的字段。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
