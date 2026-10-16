"""定性 24 条 verifier 放行零动作：把「case 层」和「run 层」分开。

`_vwhy3.py` 第一版失败的教训：那 24 条里有 10 条 case_id **不在这批 case
里**（换了 seed 就换了一批 case），我却在同一张表里打印 —— 于是 4 条真命中
被 10 行「不在这批 case 里」盖住，判读标准（pending_question 全 True？）永远
够不着。这不是测试脆弱，是**我的诊断脚本量错了对象**：`_vnone.py` 报的是
(case_id, k, arm) 三元组，我必须用**同一个 case 集合**重建，而不是换 seed。

本脚本两段，互不依赖：

  第一段（case 层，稳定）：对这 4 条确实命中的 case，直接读它们**造数时**
  写进 `build_eval_cases` 的字段 —— `expected_state` / `condition` /
  `turn2_state` / `customer_reply`。这里没有任何 agent 参与，全是确定性事实。

  第二段（run 层，不重建）：不动数据库，只从进度 jsonl 里读这 24 条的
  diffs。diffs 是评测器生成的机器判据，与重建哪批数据无关。

两条合起来才能回答：**golden 到底要求 open 还是要求写？**
  · golden 要求 open → verifier 放行正确，问题在 diffs 生成（口径不一致）
  · golden 要求写   → verifier 放行错，`pending_question` 是被审对象自己写的

用法：python _vwhy4.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent

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


def load_progress(path: Path) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("case_id"):
            out[(rec["case_id"], int(rec.get("k_index", 0)))] = rec
    return out


def main() -> int:
    print("=== 第一段：进度 jsonl 里这 24 条 run 的 diffs（run 层） ===")
    allhits: list[tuple[str, int, str, dict]] = []
    for name in ("_progress-merged-single.jsonl", "_progress-merged-multi.clean.jsonl"):
        recs = load_progress(ROOT / "eval_out" / name)
        for case_id, k, arm in TARGETS:
            r = recs.get((case_id, k))
            if r is None:
                continue
            if r.get("verifier_passed") is True and (r.get("observed_action") or "none") == "none":
                allhits.append((case_id, k, arm, r))
    for case_id, k, arm, r in allhits:
        print(
            f"{case_id:14} k={k} {arm:6} {str(r.get('condition')):24} "
            f"tools={r.get('tool_calls')} model={r.get('model_calls')} "
            f"write={r.get('write_calls')} turns={r.get('turns')} "
            f"expected_action={r.get('expected_action')!r}"
        )
        print(f"    diffs: {r.get('diffs')}")
    print(f"\n合计 {len(allhits)} 条 run 层命中\n")

    print("=== 第二段：这批 case 的 golden 终态（case 层，确定性） ===")
    print("（不改数据库，直接从造数结果读；下面每行是 golden 明确要求的字段）\n")
    import os

    os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/vwhy4.db")
    from ticket_agent.db import build_eval_cases, init_db, seed_all

    init_db(drop=True)
    seed_all(n_customers=1000, seed=11)
    cases = build_eval_cases(size=200, seed=7)
    by_id = {c["case_id"]: c for c in cases}
    print(f"重建 {len(cases)} 条 case（customers=1000 seed=11 size=200 seed=7）")
    found = 0
    for case_id, k, arm in TARGETS:
        c = by_id.get(case_id)
        if c is None:
            continue
        found += 1
        want = (c.get("expected_state") or {})
        print(
            f"{case_id:14} {str(c.get('condition')):24} level={c.get('level')} "
            f"style={c.get('style')} turns_want={c.get('turns')}"
        )
        print(f"    expected_state: {want}")
        print(f"    turn2_state:   {c.get('turn2_state')}")
        print(f"    customer_reply: {(c.get('customer_reply') or '')[:60]!r}")
    print(f"\n命中 {found} 条 case 层")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
