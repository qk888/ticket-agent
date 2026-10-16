"""定性：verifier 为什么放行"终态要求写、实际零动作"的 run（缺陷 ㉟）。

`_vnone.py` 量出形状：single 4 条 / multi 20 条 `verifier_passed=True` 而
`observed_action='none'` 且 diffs 要求写操作。

verifier（`nodes.py:610-712`）对 `status == "open"` 的放行条件是：

    needs_clarification = basis == "ambiguous" or bool(ticket_snap.get("pending_question"))

`basis` 是**输入侧**的结构化事实（`classify_order_link` 纯函数，agent 改不了）。
但 `pending_question` 是 agent 自己用 `ask_customer` **写**进去的 ——
它和 `basis` 不同级：一个是不可伪造的输入，一个是被审对象自己的产出。

假设（待数据验证）：那 24 条的 `pending_question` 非空，即 agent 问了一个
**golden 并不需要**的问题，然后什么都不做。verifier 的判据看到"问了客户"
就放行，而 golden 要求的是直接驳回/赔付。→ 缺陷 ⑲ 的同形第三例：
判据的依据从"模型散文"换成了"结构化字段"，但那个字段仍然由被审对象书写。

本脚本从进度 jsonl 里取出这 24 条的完整记录，打印判定所需字段。
（进度 jsonl 只有 CaseResult 的字段；快照字段需要另外取，见下面 print 的说明。）
"""
from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", required=True)
    args = ap.parse_args()
    recs = load(ROOT / args.progress)

    hits = [
        r
        for r in recs
        if r.get("verifier_passed") is True
        and (r.get("observed_action") or "none") == "none"
        and any(
            isinstance(d, str) and d.startswith(("ticket.status:", "after_sales:", "refund_ledger:"))
            for d in (r.get("diffs") or [])
        )
    ]
    print(f"{args.progress}：命中 {len(hits)} 条\n")

    print("字段清单（第一条的键，用于确认能判什么）:")
    if hits:
        print("  ", sorted(hits[0].keys()))
    print()

    for r in hits:
        print(
            f"{r['case_id']:14} {str(r.get('condition')):26} "
            f"k={r.get('k_index')} turns={r.get('turns')} tools={r.get('tool_calls')} "
            f"model={r.get('model_calls')} act={r.get('observed_action')!r} "
            f"passed={r.get('passed')} verifier={r.get('verifier_passed')}"
        )
        print(f"    diffs: {r.get('diffs')}")
        print(f"    error: {r.get('error')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
