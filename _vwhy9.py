"""为什么换了 seed 就换了一批 case（缺陷 ⑨ 的又一个入口）。

`_vwhy3/4/8` 三次重建，`seed_all(n_customers=1000, seed=11)` +
`build_eval_cases(size=200, seed=7)` 参数**一字不差**，但命中的 case 三次都不同
（_vwhy4 命中 4 条：t368/t359/t569/t363；_vwhy8 只命中 t359/t249）。

假设：`seed.py` 里还有第二个随机源没按 seed 重置。已知的辅助随机流
`_KIND_RNG` / `_PRICE_RNG` 已在缺陷 ㉛ 里修过（`_reseed_aux_rngs(seed)`），
所以要么有第三个流，要么某个流用了**非 seed 输入**（时间、os.urandom、
id()、dict 序、set 序）。

本脚本不改任何代码，只读代码找「所有模块级 Random / random. 调用点」，
再逐个数它们的 seed 来源。纯静态检查，无副作用。

用法：python _vwhy9.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src" / "ticket_agent"


def main() -> int:
    print("=== 1. 所有 `random.Random(` / `Random(` 构造点 ===\n")
    pat_new = re.compile(r"\b(?:random\.)?Random\s*\(")
    pat_seed = re.compile(r"\.seed\s*\(")
    pat_rand = re.compile(r"\b(?:random|rng|_RNG|_rng)\.(random|randint|choice|choices|uniform|shuffle|sample|random\(\)|randrange|gauss)\s*\(")

    files = sorted(SRC.rglob("*.py"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if pat_new.search(line) or pat_seed.search(line):
                print(f"  {rel}:{i}: {line.strip()[:120]}")

    print("\n=== 2. 模块级随机实例的赋值（找有没有漏 seed 的） ===\n")
    pat_assign = re.compile(r"^(_?[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(?:random\.)?Random\s*\(")
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if pat_assign.match(line):
                print(f"  {rel}:{i}: {line.strip()[:120]}")

    print("\n=== 3. 裸 `random.` 调用（不经过 seeded 实例） ===\n")
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"(?<![A-Za-z_])random\.(random|randint|choice|choices|uniform|shuffle|sample|randrange|gauss)\s*\(", line):
                print(f"  {rel}:{i}: {line.strip()[:120]}")

    print("\n=== 4. `seed_all` / `build_eval_cases` 的签名与重置调用 ===\n")
    for name in ("seed.py", "harness.py"):
        f = SRC / "db" / name if name == "seed.py" else SRC / "eval" / name
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"^def (seed_all|_reseed_aux_rngs|build_eval_cases)", line) or "_reseed_aux_rngs(" in line:
                print(f"  {rel}:{i}: {line.strip()[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
