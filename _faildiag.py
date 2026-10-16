"""三条真实失败的定向取证：R14:urge_logistics / R12:issue_invoice / R10:compensate。

为什么要这个脚本
--------------
两臂新口径报表（eval_out/resume-scripted-{single,multi}-s7.md）出来以后，
违规动作已从 530 归零（那批是探针假阳性，根因是探针只认
check_refund_eligibility 而售后线的合法前置是 check_after_sales_eligibility），
写操作 1.02 → 1.35（口径修复被数据证实）。

但**真实失败还剩三条**（终态 diff 判的，与探针无关）：

    R14:urge_logistics   72 条  33.3%
    R12:issue_invoice    66 条  33.3%
    R10:compensate       45 条  80.0%

失败归因是「other+ticket_status = 92 条；other = 9 条」——
而这三个条件合计 72+66+45 = 183 条（k=3，所以失败的 run 是
183 - (24+22+36) = 101 条 ≈ 92+9）。数字量级对得上，
所以归因里的 other+ticket_status 主要就是这三条线。

★ 本脚本的做法：只读 `eval_out/_progress.jsonl`（多臂刚跑完的 1815 条），
把这三个条件的**失败 run** 的 diffs / observed_action / expected_action /
error / tool_calls 逐条打出来。**不重跑任何 case** —— 读数已经在文件里，
重跑一遍既慢又可能因为时间依赖（R14 有 24h 冷却、R11 有 15 天价保期）
得到不同的结果，那是自找的不可比。

用法：
    python _faildiag.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROGRESS = ROOT / "eval_out" / "_progress.jsonl"

TARGETS = ("R14:urge_logistics", "R12:issue_invoice", "R10:compensate")


def main() -> int:
    if not PROGRESS.exists():
        print(f"找不到进度文件：{PROGRESS}")
        print("（它由 _resume.py 写；上一轮 multi 臂跑完应当在）")
        return 1

    rows: list[dict] = []
    broken = 0
    with PROGRESS.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                broken += 1
    if broken:
        print(f"⚠ 进度文件有 {broken} 行残缺，已跳过")

    print(f"进度文件共 {len(rows)} 条 run；arm={rows[0].get('arm') if rows else '?'}")

    # ── 按条件汇总（先看形状，再逐条）───────────────────────────────
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cond[str(r.get("condition"))].append(r)

    print("\n== 三个目标条件的汇总 ==")
    for cond in TARGETS:
        rs = by_cond.get(cond, [])
        if not rs:
            print(f"{cond}: 这个条件在进度文件里没有 case —— 名字对不上，先核对")
            continue
        n = len(rs)
        npass = sum(1 for r in rs if r.get("passed"))
        print(
            f"{cond:<22} run={n}  passed={npass}  acc={npass / n:.1%}  "
            f"case={len({r.get('case_id') for r in rs})}"
        )

    # ── 逐条失败明细 ────────────────────────────────────────────────
    for cond in TARGETS:
        rs = [r for r in by_cond.get(cond, []) if not r.get("passed")]
        if not rs:
            continue
        print(f"\n== {cond}：失败 {len(rs)} 条 ==")

        diff_counter: Counter[str] = Counter()
        action_counter: Counter[str] = Counter()
        for r in rs:
            for d in r.get("diffs") or []:
                # 归一化：去掉具体金额/数字，看字段级形状
                key = str(d).split("：")[0].split(":")[0]
                diff_counter[key] += 1
            action_counter[f"{r.get('expected_action')}→{r.get('observed_action')}"] += 1

        print("  diff 字段分布：")
        for k, v in diff_counter.most_common():
            print(f"    {k:<28} {v}")
        print("  动作对（期望→实际）：")
        for k, v in action_counter.most_common():
            print(f"    {k:<40} {v}")

        # 打前若干条的完整 diff 原文
        show = 4
        print(f"  前 {min(show, len(rs))} 条完整明细：")
        for r in rs[:show]:
            print(f"    {r.get('case_id')} k={r.get('k_index')}")
            print(f"      expected_action={r.get('expected_action')!r}")
            print(f"      observed_action={r.get('observed_action')!r}")
            print(f"      tool_calls={r.get('tool_calls')} write={r.get('write_calls')} "
                  f"turns={r.get('turns')} elapsed={r.get('elapsed_ms')}")
            print(f"      forbidden={r.get('forbidden_hits')}")
            print(f"      verifier_passed={r.get('verifier_passed')}")
            if r.get("error"):
                print(f"      error={r.get('error')!r}")
            for d in r.get("diffs") or []:
                print(f"      diff: {d}")
            # 期望终态 vs 实际终态的关键量
            print(f"      expected(after_sales)={r.get('expected_after_sales')}")
            print(f"      expected(ticket)={r.get('expected_ticket')}")
            print(f"      expected(order)={r.get('expected_order')}")

    # ── 附带：这三条的 expected/observed 字段名是否真的存在 ─────────
    # （CaseResult 的字段名历史改过，凭记忆打 key 会全打 None 而不报错）
    sample = next((r for c in TARGETS for r in by_cond.get(c, []) if not r.get("passed")), None)
    if sample is not None:
        print("\n== 进度记录的全部键（用于核对上面打的 key 是否存在）==")
        print("   ", sorted(sample.keys()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
