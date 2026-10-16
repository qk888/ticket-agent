"""为什么 17 条 case 不在这批里（最后一环）。

`_vwhy10.py` 证明 `seed_all(n_customers=1000, seed=11)` +
`build_eval_cases(size=200, seed=7)` **确定性可复现**（连跑两次逐位相同）。
但 `_vwhy11.py` 用同样的参数只命中 6/23 条。

假设只剩一个：**跑批时用的不是这两个参数**。
进度 jsonl 的 `__sig__` 行记着当时真正的参数 —— 它是 `_resume.py` 写的
指纹，本来就是为「拒绝参数不一致的续跑」而存在的。读它，一次定论。

用法：python _vwhy12.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
EVAL = ROOT / "eval_out"


def main() -> int:
    for f in sorted(EVAL.glob("_progress*.jsonl")):
        sigs = 0
        sig = None
        runs = 0
        first_case = None
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "__sig__" in rec:
                sigs += 1
                sig = rec
            elif rec.get("case_id"):
                runs += 1
                if first_case is None:
                    first_case = rec["case_id"]
        if sigs == 0 and runs == 0:
            continue
        print(f"{f.name}")
        print(f"    runs={runs}  __sig__ 行数={sigs}")
        if sig:
            print(f"    指纹: {json.dumps(sig, ensure_ascii=False, sort_keys=True)}")
        if first_case:
            print(f"    第一条 case_id: {first_case}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
