"""_cleanmerge.py — 把 merged-multi 里 sidecar 挂掉期间产生的空跑记录剔掉。

为什么需要它
-----------
`eval_out/_progress-merged-multi.jsonl` 原本是我用 `_mkmerged.py` 从 4 个分片
干净合并出来的 600 行。但随后一次 `_resume.py` 误跑（当时没注意到 sidecar 已挂）
往里面追加了 **198 行空跑记录**（798 - 600），特征是：

    turns=1  tools=0  action=none  cost=0.000000  input_tokens=0  output_tokens=0
    elapsed≈22s  →  模型根本没被调用，是连不上 sidecar 后的快速失败

这些记录**不是读数**，是故障痕迹。留在进度文件里的后果：
`_resume.py` 认为它们「已跑完」，于是永远不会重跑这 198 条，
而报表会把它们算进分母 —— 一张安静地偏低、且没有模型参与的报表。

判据（收紧到「没有任何模型参与痕迹」，三条同时满足）：
1. `tool_calls == 0` 且 `model_calls == 0`
2. `input_tokens == 0` 且 `output_tokens == 0`
3. `elapsed_ms < 30000`（真实 Hy3 run 约 3s/turn，至少 20s+；空跑是连接失败，约 22s）

★ 为什么不能只凭 `passed == False` 删：真实的失败 case 也是 passed=False，
   但它们有工具调用、有 token。**删错的代价是不可逆的**（进度文件是 append-only，
   删了就得重跑），所以判据必须收紧到「没有任何模型参与痕迹」。

★★ 第一次写这个判据时踩的坑（判据本身写错了）：
   原以为「零 token」就是污染。实测**不是** —— 原始 4 个分片里
   320/600 条本来就是零 token（`model_calls=3` 但 token=0，elapsed≈7s）。
   那是 Hy3 的 reasoning-only 行为：思考 token 不走 usage 字段。
   **拿一个正常现象当污染，会把 320 条真读数删掉。**
   真正区分污染与正常的办法是**键集合对比**：拿原始 4 个分片的
   `(case_id, arm, k_index)` 集合做基准，merged 里多出来的键才是污染
   （实测多 190 个键，全部是 `turns=1 tools=0 act=none el≈22s` 的形态）。
   本脚本因此提供 `--against` 参数：与基准文件做集合差，而不是猜字段。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_DIR = ROOT / "eval_out"


def is_dry(rec: dict) -> bool:
    return (
        rec.get("tool_calls", -1) == 0
        and rec.get("model_calls", -1) == 0
        and rec.get("input_tokens", -1) == 0
        and rec.get("output_tokens", -1) == 0
        and (rec.get("elapsed_ms") or 0) < 30000
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="剔除 merged 进度文件里误跑进去的记录")
    ap.add_argument("--file", default=str(EVAL_DIR / "_progress-merged-multi.jsonl"))
    ap.add_argument(
        "--against",
        default="",
        help="基准：可以是单个分片文件、也可以是 glob 模式（如 "
             "'eval_out/_progress-multi-shard*of4.jsonl'）。"
             "给它就按**键集合差**判定污染，而不是猜字段形态 —— 猜错会把真读数删掉",
    )
    ap.add_argument("--keep", type=int, default=600, help="基准确认过的干净唯一键数")
    ap.add_argument("--apply", action="store_true", help="真的写回；不加则只预演")
    args = ap.parse_args()

    path = Path(args.file)
    recs = []
    for l in path.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if not l or "__sig__" in l:
            continue
        try:
            recs.append(json.loads(l))
        except json.JSONDecodeError:
            pass

    def key(r: dict) -> tuple:
        return (r["case_id"], r["arm"], int(r["k_index"]))

    if args.against:
        base_keys: set[tuple] = set()
        base_files: list[Path] = []
        # 支持 glob：基准往往是「那 4 个分片」，不是一个文件
        raw = args.against
        if any(ch in raw for ch in "*?["):
            hits = sorted(EVAL_DIR.glob(Path(raw).name))
            base_files = hits
        else:
            p = Path(raw)
            base_files = [p if p.is_absolute() else ROOT / p]
        for base in base_files:
            if not base.exists():
                print(f"✗ 基准文件不存在：{base}")
                return 1
            for l in base.read_text(encoding="utf-8").splitlines():
                l = l.strip()
                if not l or "__sig__" in l:
                    continue
                try:
                    r = json.loads(l)
                except json.JSONDecodeError:
                    continue
                base_keys.add(key(r))
        if len(base_keys) != args.keep:
            print(f"✗ 基准（{len(base_files)} 个文件）有 {len(base_keys)} 个唯一键，与 --keep {args.keep} 不符")
            for b in base_files:
                print(f"    {b.name}")
            return 1
        polluted = [r for r in recs if key(r) not in base_keys]
        real = [r for r in recs if key(r) in base_keys]
        print(f"基准（{len(base_files)} 个文件）: {len(base_keys)} 键")
    else:
        polluted = [r for r in recs if is_dry(r)]
        real = [r for r in recs if not is_dry(r)]
        print("（未给 --against，按字段形态判定 —— 不可靠，仅供预演）")

    print(f"总记录 {len(recs)}：保留 {len(real)} / 剔除 {len(polluted)}")
    if polluted:
        r = polluted[0]
        print(
            f"  剔除样例：{r.get('case_id')} {r.get('condition')} "
            f"tools={r.get('tool_calls')} model_calls={r.get('model_calls')} "
            f"tok={r.get('input_tokens')}/{r.get('output_tokens')} "
            f"elapsed={r.get('elapsed_ms')}ms turns={r.get('turns')}"
        )
        from collections import Counter

        print("  剔除记录的 turns 分布:", Counter(r.get("turns") for r in polluted))

    if len(real) != args.keep:
        print(f"  ✗ 保留 {len(real)} 条，与预期 {args.keep} 不符 —— 停下人工核对，不要盲删")
        return 1

    keys = {key(r) for r in real}
    if len(keys) != len(real):
        print(f"  ✗ 保留记录有重复键（{len(real)} 条只有 {len(keys)} 个唯一键）")
        return 1
    print(f"  ✓ 保留 {len(keys)} 个唯一键，无重复")

    if not args.apply:
        print("\n（预演）加 --apply 才写回")
        return 0

    out = path.with_suffix(".clean.jsonl")
    with out.open("w", encoding="utf-8") as fh:
        for r in real:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"\n→ 已写 {out.name}（{len(real)} 行）")
    print(f"  ★ 原文件 {path.name} 未动；确认无误后手工替换，或改 --file 指向它")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
