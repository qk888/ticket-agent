"""_mergecheck.py — 只读体检：把 eval_out 下所有进度文件摊开，判定哪些属于本批。

为什么需要它
-----------
上一张 `eval-single-llm-7c.md` 是**坏的**：运行数 948（应为 600）、
运行错误 273（真实跑批零错误）。根因是 `_prun.py` 的合并段用
`glob("_progress-{arm}-shard*of*.jsonl")` 抓文件，**没有校验抓到的文件
是否都属于同一次跑批** —— 上一代 shard2of2 时代的残留被一起吞了。

这与「只看 mtime 就说文件被覆盖」「只看文件名就说报表是最新的」同形：
**合并前的输入集必须自己证明自己是干净的**，否则分母安静地错。

本脚本只读不写，输出每个文件的指纹与记录数，并给出「哪些该进报表」的判定。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVAL_DIR = ROOT / "eval_out"

#: 本批（2026-10-15 那次 1200/1200）的期望参数。与 _prun.py 命令行一致。
BATCH = {
    "version": 2,
    "arm": None,  # 按臂分别判
    "kind": "llm",
    "size": 200,
    "k": 3,
    "customers": 1000,
    "seed": 7,
    "split": "all",
}
SHARDS = 4


def scan(path: Path) -> dict:
    sig = None
    keys: list[tuple] = []
    broken = 0
    for line in path.read_text(encoding="utf-8").splitlines():
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
        keys.append((rec.get("case_id", ""), rec.get("arm", ""), int(rec.get("k_index", -1))))
    return {"path": path, "sig": sig, "keys": keys, "broken": broken}


def main() -> int:
    files = sorted(EVAL_DIR.glob("_progress*.jsonl"))
    if not files:
        print("✗ eval_out 下没有任何进度文件")
        return 1

    print(f"共 {len(files)} 个进度文件\n")
    scanned = []
    for f in files:
        s = scan(f)
        scanned.append(s)
        sig = s["sig"]
        if sig is None:
            sig_txt = "（无 __sig__ 行 —— 旧格式或非 _resume.py 产物）"
            match = "?"
        else:
            diff = {
                k: (sig.get(k), BATCH.get(k))
                for k in set(sig) | set(BATCH)
                if k != "arm" and sig.get(k) != BATCH.get(k)
            }
            match = "✓同批" if not diff else "✗异批 " + ",".join(f"{k}:{a}→{b}" for k, (a, b) in sorted(diff.items()))
            sig_txt = f"arm={sig.get('arm')} kind={sig.get('kind')} size={sig.get('size')} k={sig.get('k')} seed={sig.get('seed')} customers={sig.get('customers')}"
        dup = len(s["keys"]) - len(set(s["keys"]))
        print(
            f"{f.name:<40} 记录 {len(s['keys']):>4}  重复 {dup}  残缺 {s['broken']}  {match}\n"
            f"    {sig_txt}"
        )

    print("\n" + "=" * 78)
    # 判定：文件名形如 _progress-<arm>-shard<i>of4.jsonl 且指纹同批
    keep: dict[str, list[dict]] = {}
    for s in scanned:
        name = s["path"].name
        arm = None
        for a in ("single", "multi"):
            if name.startswith(f"_progress-{a}-shard"):
                arm = a
                break
        if arm is None:
            continue
        if s["sig"] is None:
            continue
        if any(s["sig"].get(k) != BATCH.get(k) for k in ("kind", "size", "k", "customers", "seed", "split", "version")):
            continue
        if s["sig"].get("arm") != arm:
            continue
        keep.setdefault(arm, []).append(s)

    for arm in ("single", "multi"):
        got = keep.get(arm, [])
        print(f"\n【{arm}】入选 {len(got)} 个文件（期望 {SHARDS} 个）")
        if len(got) != SHARDS:
            print(f"  ✗ 文件数不对 —— 拒绝出报表。期望恰好 {SHARDS} 个，"
                  f"多出来的多半是上一代 shard 残留，少一个就是有片没跑完。")
            for s in got:
                print(f"    入选：{s['path'].name}")
            continue
        allkeys: list[tuple] = []
        for s in got:
            allkeys.extend(s["keys"])
            if len(s["keys"]) != 150:
                print(f"  ✗ {s['path'].name} 只有 {len(s['keys'])} 条（期望 150）")
        dup = [k for k, c in Counter(allkeys).items() if c > 1]
        print(f"  合计 {len(allkeys)} 条 / 去重 {len(set(allkeys))} 条 / 重复 {len(dup)}")
        # 覆盖度：case × k 铺满
        cases = sorted({k[0] for k in allkeys})
        ks = sorted({k[2] for k in allkeys})
        print(f"  case 数 {len(cases)}，k_index 取值 {ks}")
        expect_pairs = len(cases) * len(ks)
        print(f"  期望 case×k = {expect_pairs}，实际 {len(set(allkeys))}"
              + ("  ✓" if expect_pairs == len(set(allkeys)) else "  ✗ 有缺号"))
        if dup:
            print(f"  ✗ 重复键 {dup[:5]}{' …' if len(dup) > 5 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
