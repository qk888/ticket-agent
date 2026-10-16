"""_mkmerged.py — 用**显式文件清单**合并进度，合并前做四道校验。

为什么不再用 glob
---------------
`_prun.py` 用 `glob("_progress-{arm}-shard*of*.jsonl")` 抓文件，而
`eval_out/` 里躺着**上一代 shard2of2 的残留**（各 4 条，kind=scripted
size=8 customers=40）。glob 把它们一起吞了，于是上一张报表的运行数是
948 而不是 600，还混进 273 条错误 —— 分母安静地错，报表照常出。

这与「只看 mtime 就说文件被覆盖」「只看文件名就说报表是最新的」同形：
**合并前的输入集必须自己证明自己是干净的**，否则分母安静地错。

本脚本的做法：
1. **显式列出 N 个文件名**（`--arms single,multi --shards 4`），一个不多一个不少；
2. 逐文件校验：指纹与本次参数逐项相等（`arm` 单独判，不混进参数 dict）；
3. 合并后校验：`(case_id, arm, k_index)` 无重复、铺满 case×k、各片 run 数一致；
4. 全部通过才写 `_progress-merged-<arm>.jsonl`；任一不过 → 拒绝写并打印 diff。

★ 本文件自己的坑（记下来免得再踩）：`want_sig` 里曾误放 `"arm": None`，
   导致 dict 推导把**每个**文件都判成「指纹不符 arm: 文件 'single' 本次 None」。
   一屏假阳性会把真正该看的 diff 淹没 —— **判据写错比没有判据更糟**。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_DIR = ROOT / "eval_out"


def main() -> int:
    ap = argparse.ArgumentParser(description="显式清单合并进度文件（带四道校验）")
    ap.add_argument("--arms", default="single,multi")
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--kind", default="llm")
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--customers", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--split", default="all")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--out", default="", help="输出路径模板，{arm} 会被替换；留空用 _progress-merged-{arm}.jsonl")
    ap.add_argument("--force", action="store_true", help="校验不过也写（只在确认要做实验时用）")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    # ★ 这里**不能**放 "arm"：它按臂分别判。放进来会让每个文件都报
    #   「指纹不符 arm」，那是校验器自己的 bug，不是数据的问题。
    want_sig = {
        "version": 2,
        "kind": args.kind,
        "size": args.size,
        "k": args.k,
        "customers": args.customers,
        "seed": args.seed,
        "split": args.split,
    }

    failures: list[str] = []
    for arm in arms:
        print(f"\n===== {arm} =====")
        files = [EVAL_DIR / f"_progress-{arm}-shard{i}of{args.shards}.jsonl" for i in range(args.shards)]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            msg = f"{arm}: 缺文件 {missing}"
            print(f"✗ {msg}")
            failures.append(msg)
            continue

        # ① 逐文件：指纹逐项相等
        per_file: list[tuple[Path, list[dict]]] = []
        for f in files:
            recs: list[dict] = []
            sig = None
            broken = 0
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    broken += 1
                    continue
                if "__sig__" in rec:
                    sig = rec["__sig__"]
                    continue
                recs.append(rec)
            if broken:
                msg = f"{arm}/{f.name}: {broken} 行残缺（断电瞬间写的半截）"
                print(f"  ✗ {msg}")
                failures.append(msg)
            if sig is None:
                msg = f"{arm}/{f.name}: 没有 __sig__ 行，无法证明属于本批"
                print(f"  ✗ {msg}")
                failures.append(msg)
                continue
            # ★ arm 单独判，不进 want_sig：`set(sig) | set(want_sig)` 会把
            #   文件里多出来的 "arm" 也算进 diff（值为 None），于是每个文件
            #   都被判「指纹不符 arm」。**多出来的键不是不匹配** ——
            #   只有两边都有、且值不同的键才算。这个判据写错一次，输出就是一屏假阳性。
            diff = {k: (sig.get(k), want_sig.get(k)) for k in set(sig) & set(want_sig) if sig.get(k) != want_sig.get(k)}
            if sig.get("arm") != arm:
                diff["arm"] = (sig.get("arm"), arm)
            if diff:
                msg = (
                    f"{arm}/{f.name}: 指纹不符 "
                    + ", ".join(f"{k}: 文件 {a!r} 本次 {b!r}" for k, (a, b) in sorted(diff.items()))
                )
                print(f"  ✗ {msg}")
                failures.append(msg)
                continue
            print(f"  ✓ {f.name}: {len(recs)} 条，指纹全项相符")
            per_file.append((f, recs))

        if len(per_file) != args.shards:
            failures.append(f"{arm}: 只有 {len(per_file)}/{args.shards} 个文件通过校验")
            continue

        # ② 各片 run 数一致（分片按 case 下标取模，每片只差 1 条 case）
        counts = {len(recs) for _, recs in per_file}
        if len(counts) != 1:
            msg = f"{arm}: 各片 run 数不一致 {sorted(counts)}"
            print(f"  ✗ {msg}")
            failures.append(msg)
        else:
            print(f"  ✓ 各片均为 {counts.pop()} runs")

        all_recs = [r for _, recs in per_file for r in recs]
        keys = [(r["case_id"], r["arm"], int(r["k_index"])) for r in all_recs]
        dup = [k for k, c in Counter(keys).items() if c > 1]

        # ③ 无重复键
        if dup:
            msg = f"{arm}: {len(dup)} 个重复键，例 {dup[:3]}"
            print(f"  ✗ {msg}")
            failures.append(msg)
        else:
            print(f"  ✓ 无重复键")

        # ④ case × k 铺满
        cases = sorted({k[0] for k in keys})
        ks = sorted({k[2] for k in keys})
        if len(set(keys)) != len(cases) * len(ks) or ks != list(range(args.k)):
            msg = f"{arm}: 覆盖不全 —— {len(cases)} case × k={ks} 应 {len(cases)*args.k}，实得 {len(set(keys))}"
            print(f"  ✗ {msg}")
            failures.append(msg)
        else:
            print(f"  ✓ 覆盖铺满：{len(cases)} case × k={len(ks)} = {len(set(keys))} runs")

        out = Path(args.out.replace("{arm}", arm)) if args.out else EVAL_DIR / f"_progress-merged-{arm}.jsonl"
        if failures and not args.force:
            print(f"  ⊘ 校验未全过，拒绝写 {out.name}")
            continue
        with out.open("w", encoding="utf-8") as fh:
            for _, recs in per_file:
                for rec in recs:
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"  → 已写 {out}（{len(all_recs)} 行）")

    if failures:
        print(f"\n✗ {len(failures)} 项校验未过：")
        for m in failures:
            print(f"    - {m}")
        return 1
    print("\n✓ 全部校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
