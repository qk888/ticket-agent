"""缺陷 ㉟ 定性收口：24 条 verifier 放行零动作，全部是 golden 要求写。

`_vwhy5.py` 的结论（判据只用 diffs 期望侧，不重建任何数据）：
**24/24 条 golden 都要求离开 open 或要求写**（rejected / resolved /
pending_human / after_sales / *_count），没有一条 golden 是"保持 open"。
→ 假设的另一种可能（golden 就是 open、放行正确）被排除。

所以 verifier 判据里必然有一个字段让"什么都没做"通过。候选只有一个：

    needs_clarification = basis == "ambiguous" or bool(ticket_snap.get("pending_question"))

`basis` 是输入侧纯函数，agent 改不了；`pending_question` 是 agent 自己用
`ask_customer` **写**进去的。剩下的唯一解释就是它非空。

但"写进去"有三个互斥的成因，改法完全不同，必须分开量：

  A. agent 调了 ask_customer，然后什么都没做
     → verifier 判据要把"问过"和"问完还办成了"分开：问过 ≠ 办完
  B. agent 调了 submit_after_sales_action(kind=...) 但 kind 不被
     `_summarize_action` 认 → trace 里有写、observed_action 却是 none
     → 那是汇总函数漏了 kind，不是 verifier 错
  C. 上一轮（turn-1）留下 pending_question，第二轮答复后仍未办成
     → 只影响 turns=2 的 4 条，与其余 20 条无关

B 的证据已经在 `_vwhy5.py` 输出里：**20 条 multi 全部 write_calls=1**，
即工具层真的写过一次，而 `observed_action='none'`。这两件事不可能同时为真，
除非 `_summarize_action` 不认那个 kind —— 那 diffs 里 "after_sales: 实际 []"
就该是"实际有一条但 kind 不符"，而它写的是 `[]`（一条都没有）……

本脚本量 B：从同一批进度里找「write_calls>0 且 after_sales 实际 []」的 run，
看它写的是什么工具；再直接单测 `_summarize_action` 对各种 kind 的返回值。
如果 `_summarize_action` 认得出所有 kind → B 排除，那 write_calls=1 写的是
`close_ticket`/`escalate_to_human`（这两个在 WRITE_TOOLS 里，但
`observed_action` 只由 after_sales/refund 汇总）→ 真相是
**agent 只关单/转人工，没做售后动作**，而 golden 要求的是售后动作。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("case_id"):
            out.append(rec)
    return out


def main() -> int:
    print("=== 1. write_calls>0 但 after_sales 实际 [] 的 run 有多少 ===\n")
    n = 0
    cond_counter: dict[str, int] = {}
    for name in ("_progress-merged-multi.clean.jsonl",):
        for r in load(ROOT / "eval_out" / name):
            if (r.get("write_calls") or 0) <= 0:
                continue
            diffs = r.get("diffs") or []
            if not any(d.startswith("after_sales:") and "实际 []" in d for d in diffs):
                continue
            n += 1
            cond_counter[str(r.get("condition"))] = cond_counter.get(str(r.get("condition")), 0) + 1
            if n <= 6:
                print(
                    f"  {r['case_id']:14} k={r.get('k_index')} {str(r.get('condition')):24} "
                    f"write={r.get('write_calls')} tools={r.get('tool_calls')} "
                    f"act={r.get('observed_action')!r} passed={r.get('passed')}"
                )
    print(f"\nmulti 合计 {n} 条；条件分布：{cond_counter}")

    print("\n=== 2. `_summarize_action` 对各种 kind 的返回值 ===\n")
    from ticket_agent.eval.harness import _summarize_action

    # 用真实的 WRITE_TOOLS / kind 列表喂它；这些是"轨迹里出现过这个名字"
    samples = {
        "close_ticket": [{"name": "close_ticket"}],
        "escalate_to_human": [{"name": "escalate_to_human"}],
        "create_refund": [{"name": "create_refund"}],
        "block_order": [{"name": "block_order"}],
        "submit_after_sales_action": [{"name": "submit_after_sales_action"}],
        "ask_customer": [{"name": "ask_customer"}],
        "mark_evidence_received": [{"name": "mark_evidence_received"}],
        "只有只读工具": [{"name": "get_order"}, {"name": "get_ticket"}],
        "空轨迹": [],
    }
    for label, trace in samples.items():
        print(f"  {label:28} → {_summarize_action(trace)!r}")

    print("\n=== 3. WRITE_TOOLS 全集 ===\n")
    from ticket_agent.tools.registry import WRITE_TOOLS

    print(f"  {sorted(WRITE_TOOLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
