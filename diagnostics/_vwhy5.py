"""把「同一批 case」坐实：进度文件里的 run 与 golden 到底是不是同一批。

`_vwhy4.py` 第二段的失败：我用了 `seed_all(1000, 11)` + `build_eval_cases(200, 7)`，
命中 4 条；而 `_vwhy3.py` 用了同样的参数，命中的是另外 4 条（globex-t368 / t359 /
t569 / t363 vs globex-t359/t569/t363/t368 —— 交集只有 3 条，且 t368 的条件在
两次之间从 `R8:reject` 变成 `R1:reject`）。

结论：**参数写一样 ≠ 重建的是同一批 case**。`seed_all` 的第三位参数、
`build_eval_cases` 的 seed/size 之外还有别的输入（时间锚点、`clock.now()`），
任何一项变了 case 集就变。这就是缺陷 ⑨「基准漂移」的又一个入口。

所以本脚本不重建、不猜，改用**评测自己的产物**：进度 jsonl 的每一行 run
都带 `case_id` + `k_index`，而 `diffs` 是评测器对 golden 比较的结果。
我要的「golden 要求什么」就在 diffs 的期望侧里，逐字可读 ——
不需要另造一份 golden，也就不可能再造出一批不同的 case。

判读标准（只看 diffs 期望侧）：
  · 期望侧要求离开 open（rejected / resolved / pending_human）或要求写
    （after_sales / refund_ledger / order.*_count）→ golden 要求写
  · 期望侧是 open → golden 就是保持 open

用法：python _vwhy5.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent

#: 期望侧里出现这些字样 = golden 要求"发生点什么"
NEEDS_WRITE = (
    "期望 'rejected'",
    "期望 'resolved'",
    "期望 'pending_human'",
    "after_sales: 期望",
    "refund_ledger: 期望",
    "order.exchange_count: 期望 1",
    "order.reship_count: 期望 1",
    "order.refund_count: 期望 1",
    "last_urge_at",
)


def load(path: Path) -> dict[tuple[str, int], dict]:
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
    print("=== 24 条 run：期望侧到底要求什么（全部来自 diffs 原文，无重建） ===\n")
    want_write = 0
    want_open = 0
    rows = []
    for name in ("_progress-merged-single.jsonl", "_progress-merged-multi.clean.jsonl"):
        recs = load(ROOT / "eval_out" / name)
        for (case_id, k), r in recs.items():
            if r.get("verifier_passed") is not True:
                continue
            if (r.get("observed_action") or "none") != "none":
                continue
            diffs = r.get("diffs") or []
            # 只看与"该不该离开 open / 该不该写"有关的 diff
            core = [d for d in diffs if any(s in d for s in NEEDS_WRITE)]
            if not core:
                continue
            needs = any("实际 'open'" in d or "实际 []" in d or "实际 0" in d for d in core)
            rows.append((case_id, k, r.get("arm"), r.get("condition"), needs, core, r))
    for case_id, k, arm, cond, needs, core, r in rows:
        tag = "golden 要求写/离开 open" if needs else "golden 就是 open"
        print(
            f"{case_id:14} k={k} {str(arm):6} {str(cond):24} {tag}\n"
            f"    tools={r.get('tool_calls')} write={r.get('write_calls')} "
            f"turns={r.get('turns')} expected_action={r.get('expected_action')!r}\n"
            f"    {core}"
        )
        want_write += 1 if needs else 0
        want_open += 0 if needs else 1
    print(f"\n合计 {len(rows)} 条：golden 要求写 {want_write} 条 / golden 就是 open {want_open} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
