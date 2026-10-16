"""把 `_tidy_plan.py` 的判据收紧：**零引用的脚本不该留在根目录**。

## 上一轮留下的不一致

`_tidy_plan.py` 分两类：被精确引用的留根目录，一次性排查移 diagnostics/。
但 ALWAYS_KEEP 白名单里有 8 个**零引用**的脚本也留在了根目录
（`_readmecheck.py` 等被引用，`_teach_api.py` / `_progresscheck.py` 等没有）。

结果：README §5 写「根目录只放**可持续复跑**的体检链（被 README / 测试**精确引用**的）」，
而实际有 6 个零引用的赖在那儿。**文档说的判据和执行的判据不是同一个。**

## 修法：给「留在根目录」一个可判定的定义

根目录 = 满足**至少一条**：

  1. 被 README / 测试 / 其他脚本**精确引用**（完整文件名 + 前后边界）
  2. 属于命名过的**正式 CLI 子命令**（`ticket-agent xxx` 的实现者）
  3. 在 README §5 目录树里**点名**（读者 clone 下来要找得到）

零引用且不在 §5 目录树里的 → 移 diagnostics/。

这不是「追求干净」，是**让文档的判据与执行的判据同一个**。
否则下一次有人照 §5 的规则判断该放哪，会得到不同答案 ——
这类「规则写在 A 处、执行在 B 处」的分裂，本项目已经撞到过 5 次（⑬⑭⑮⑯⑰）。

## 一个都不删，仍然成立

移走 ≠ 删掉。`diagnostics/README.md` 说明这些脚本是什么、怎么跑。
README 里没引用它们，所以移走**不会制造失效引用**。
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
DEST = ROOT / "diagnostics"

SEARCH_DIRS = ["README.md", "交付总览.md", "手动启动.md", "接哪个模型跑.md",
               "Docker部署.md", "数据集跑批结果汇总.md", "src", "tests", "tools", "prompt"]

#: 根目录脚本的「保留」判据 —— 与 README §5 写的那句必须一致
ALWAYS_KEEP = {
    # 正式 CLI 子命令 / 命名过的体检链
    "_readmecheck.py", "_endpointcheck.py", "_smoke.py", "_apicheck.py",
    "_reqkindcheck.py", "_resume.py", "_prun.py", "_progresscheck.py",
    "_report_only.py", "_sensitivity.py", "_faultcheck.py", "_conditions.py",
    "_p2e2e.py", "_p2check2.py", "_crosscheck.py", "_reprocheck.py",
    "_frontendcheck.py", "_hitlcheck.py", "_corpuscheck.py", "_leakprobe.py",
    "_p19after.py", "_proofcheck.py", "_gennorm.py", "_multipair.py",
    # 本节说明 + 上一次整理的执行器（写在这里是为了让规则可追溯）
    "_tidy_plan.py", "_rewrite_head.py", "_tighten_tidy.py",
}


def _referenced(name: str) -> list[str]:
    pat = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])")
    hits: list[str] = []
    for target in SEARCH_DIRS:
        p = ROOT / target
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file() and f.suffix in (".py", ".md", ".mjs")]
        else:
            continue
        for f in files:
            try:
                if pat.search(io.open(f, encoding="utf-8", errors="ignore").read()):
                    hits.append(str(f.relative_to(ROOT)))
            except Exception:
                pass
    return hits


def _in_readme_tree(name: str) -> bool:
    """README §5 目录树里是否点名了这个脚本。"""
    readme = io.open(ROOT / "README.md", encoding="utf-8").read()
    tree = readme.split("## 5. 目录结构", 1)[-1].split("## 6.", 1)[0]
    return name in tree


def main() -> int:
    apply = "--apply" in sys.argv
    move: list[tuple[str, str]] = []

    for p in sorted(ROOT.glob("_*")):
        if not p.is_file():
            continue
        name = p.name
        if name in ALWAYS_KEEP:
            continue
        refs = _referenced(name)
        if refs:
            continue
        if _in_readme_tree(name):
            # README 点名但没人引用 —— 这是文档与实现的漂移，不该靠移动掩盖
            print(f"[漂移] {name}：README §5 点名，但全仓零引用 —— 手动决定去留")
            continue
        move.append((name, "零引用且不在 README §5 目录树"))

    print(f"[分类] 将移入 diagnostics/：{len(move)} 个")
    for name, why in move:
        print(f"    {name:<34} {why}")

    if not apply:
        print("\n[试运行] 未传 --apply。确认后执行：python _tighten_tidy.py --apply")
        return 0

    moved = 0
    for name, _ in move:
        src, dst = ROOT / name, DEST / name
        if dst.exists():
            print(f"  [跳过] {name} 已存在")
            continue
        shutil.move(str(src), str(dst))
        moved += 1
    print(f"\n[完成] 移动 {moved} 个")

    print("\n[验证]")
    for cmd in (["python", "-X", "utf8", "_readmecheck.py"],
                ["python", "-X", "utf8", "-m", "pytest", "-q", "--collect-only"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        tail = (r.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
        print(f"  {' '.join(cmd[:4])} → exit {r.returncode} | {tail[0]}")

    # ── 收尾：根目录不能再有「零引用且未被 ALWAYS_KEEP 豁免」的残留 ──
    leftovers = []
    for p in sorted(ROOT.glob("_*")):
        if not p.is_file() or p.name in ALWAYS_KEEP:
            continue
        if not _referenced(p.name) and not _in_readme_tree(p.name):
            leftovers.append(p.name)
    if leftovers:
        print(f"\n★ 仍有 {len(leftovers)} 个不符合保留判据：{leftovers}")
        return 1
    n_root = len([p for p in ROOT.glob("_*") if p.is_file()])
    n_diag = len(list(DEST.glob("_*")))
    print(f"\n[收尾] 根目录 {n_root} 个 / diagnostics/ {n_diag} 个，全部符合判据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
