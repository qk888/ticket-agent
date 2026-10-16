"""_report_only.py — 只从进度文件出报表，完全不碰 SQLite。

为什么需要它
-----------
`_resume.py --report` 这条路上，数据库有两个作用：
1. `build_eval_cases` 重建评测集清单（要 case 的 level/condition 等元数据）；
2. `--no-rebuild` 之后本该什么都不做。

但实测两次都卡死：`shard-multi-0.db` 被僵尸 python 占着锁（CPU 1 小时只涨 6.4s），
换 `shard-multi-1.db` 也卡（CPU 停在 5.2s）。墙钟等了 20+ 分钟，零进展。

而**报表根本不需要库**：`build_report(results, k=...)` 只吃 `CaseResult` 列表，
所有需要的字段（level / condition / style / diffs / passed …）都在进度文件里。
`_resume.py` 之所以去开库，是因为它把「重建评测集」和「出报表」写在同一条路上。

本脚本把这两件事拆开：只读 jsonl → `CaseResult` → `build_report` → markdown。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description="只从进度文件出报表（不连数据库）")
    ap.add_argument("--progress", required=True, help="进度 jsonl（merged 后的）")
    ap.add_argument("--arm", required=True, choices=("single", "multi"))
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--kind", default="llm")
    ap.add_argument("--out", required=True, help="输出 markdown 路径")
    ap.add_argument("--expect", type=int, default=0, help="期望 run 数；0 = 不校验")
    args = ap.parse_args()

    from ticket_agent.eval.harness import CaseResult
    from ticket_agent.eval.report import build_report, render_markdown

    recs = []
    for line in Path(args.progress).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "__sig__" in line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"⚠ 跳过一行残缺 JSON", file=sys.stderr)

    if args.expect and len(recs) != args.expect:
        print(f"✗ 进度文件有 {len(recs)} 条记录，与 --expect {args.expect} 不符 —— 拒绝出报表")
        return 1

    # 只保留本臂
    recs = [r for r in recs if r.get("arm") == args.arm]
    print(f"{args.arm}: {len(recs)} 条记录")

    results = []
    bad = 0
    for r in recs:
        try:
            results.append(CaseResult(**r))
        except TypeError as e:
            bad += 1
            if bad <= 3:
                print(f"⚠ CaseResult 构造失败（多余字段？）：{e}")
    if bad:
        print(f"⚠ 共 {bad} 条构造失败，已跳过")
    if not results:
        print("✗ 没有任何可用记录")
        return 1

    keys = {(r.case_id, r.arm, r.k_index) for r in results}
    print(f"  唯一键 {len(keys)} / 记录 {len(results)}"
          + ("  ✓" if len(keys) == len(results) else "  ✗ 有重复"))

    report = build_report(results, k=args.k, meta={"source": "_report_only.py"})
    Path(args.out).write_text(render_markdown(report), encoding="utf-8")
    print(f"报表：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
