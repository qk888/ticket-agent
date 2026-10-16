"""只读核对：两个 clean 合并文件是否完整（turn2、turns 分布、passed）。"""
import json
import pathlib
from collections import Counter

for name in ("single", "multi"):
    # single 没有被空跑污染过（污染只发生在 multi 侧），直接用干净合并产物
    path = f"eval_out/_progress-merged-{name}.clean.jsonl"
    if not pathlib.Path(path).exists():
        path = f"eval_out/_progress-merged-{name}.jsonl"
    p = pathlib.Path(path)
    recs = [
        json.loads(l)
        for l in p.read_text(encoding="utf-8").splitlines()
        if l.strip() and "__sig__" not in l
    ]
    recs = [r for r in recs if r.get("arm") == name]
    keys = set((r["case_id"], r["k_index"]) for r in recs)
    print(f"{name}: {len(recs)} runs, unique keys={len(keys)}")
    print("  turns:", dict(Counter(r.get("turns") for r in recs)))
    print("  passed:", sum(1 for r in recs if r.get("passed")))
    print("  style:", dict(Counter(r.get("style") for r in recs)))
    print("  k_index:", dict(Counter(r.get("k_index") for r in recs)))
    print("  conditions:", len(set(r.get("condition") for r in recs)))
