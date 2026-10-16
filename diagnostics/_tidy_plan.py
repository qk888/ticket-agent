r"""整理根目录的 `_` 前缀脚本：**只移动，不删除**。

背景：GitHub 仓库首页有 175 个文件，其中 158 个是 `_` 前缀的排查/补丁脚本，
把 README、Dockerfile、LICENSE 全埋了。整理目标：根目录只留被引用的文件。

## 做法与判据

分三类，判据全部可复现（不是靠感觉挑）：

  A. **保留在根目录** —— 被 README / 其他脚本 / 测试**精确引用**（完整文件名 +
     前后边界），或属于可持续复跑的体检链。这些一动，README 里的命令就失效。
  B. **移入 `diagnostics/`** —— 一次性排查。结论已经写进 README §4.2 的缺陷取证链，
     脚本本身留着当证据，但不该占首页。
  C. **不删** —— 第一版分类想删 28 个「补丁脚本」，最后决定**一个都不删**。
     理由见下方「为什么一个都不删」。

## 为什么一个都不删

这个项目**不是 git 仓库时**吃过一次大亏：README 被一条 PowerShell 命令压成一行，
没有备份，最后是从 DSH 会话记录里把 write/edit 调用逐条 replay 才恢复的
（见 README §4.2 的「README 事故与恢复」）。

现在是 git 仓库了，删除理论上可恢复。但「git 里有」和「别人 clone 下来能看见」
是两件事 —— 这些脚本是 35 个缺陷的**取证过程**，删掉之后 README 里写的
「实测依据 `_r10diag.py`」就变成了指向不存在文件的引用。
**引用失效比首页乱更糟。**

所以：全部保留，只是分目录。

## 引用检查的坑（本项目第 N 次踩到）

第一版用子串匹配（`if stem in content`），结果 `_loc.py` 被报成
「被 README/config.py/service.py 同时引用」—— 因为 `is_local`、`self._lock`
里都含 `_loc`。**宽松匹配制造幽灵证据。**

收紧为完整文件名 + 前后边界（`(?<![A-Za-z0-9_.])_loc\.py(?![A-Za-z0-9_])`）后
只剩 README.md 一处真实引用。这与缺陷 ⑧「能力只存在于声明里」、
缺陷 ㉟「判据的依据不可靠」是同一个形状：**证据本身要先被验证。**

## 另一次判错（已更正）

src/ 里有 7 个文件「引用」了 `_cpdiag.py` / `_gapdiag.py` 等，
看起来是硬依赖。逐行读出来全是**注释里的取证说明**：

    harness.py:569   #   实测（见 graph/checkpoint.py 与 _cpdiag.py）：消息 11→22 累积、
    corpus.py:472    #   而规则 R11 写得好好的、`_gapdiag.py` 手工构造也能判对。

即源码可运行性不依赖它们，但**可理解性依赖**。这类仍然归 B（移入 diagnostics/），
因为它们与 README 的引用同在一条叙事链上。
"""

from __future__ import annotations

import io
import re
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
DEST = ROOT / "diagnostics"

SEARCH_DIRS = ["README.md", "交付总览.md", "手动启动.md", "接哪个模型跑.md",
               "Docker部署.md", "src", "tests", "tools", "prompt"]

#: 可持续复跑的体检链（README 点名、可重复跑）
ALWAYS_KEEP = {
    "_readmecheck.py", "_endpointcheck.py", "_smoke.py", "_apicheck.py",
    "_reqkindcheck.py", "_p2e2e.py", "_p2check2.py", "_teach_api.py",
    "_resume.py", "_prun.py", "_report_only.py", "_progresscheck.py",
    "_frontendcheck.py", "_hitlcheck.py", "_crosscheck.py",
    "_sensitivity.py", "_faultcheck.py", "_conditions.py",
    "_corpuscheck.py", "_leakprobe.py", "_p19after.py", "_reprocheck.py",
    "_tidy_plan.py", "_rewrite_head.py",
}

ONEOFF_HINTS = ("probe", "diag", "why", "diff", "dup", "root")
PATCH_HINTS = ("fix", "adddefect", "add782", "addprobe", "dedup", "cleanmerge",
               "fixtests", "mkmerged", "doc_sync", "split", "sync")


def _referenced(name: str) -> list[str]:
    """精确匹配完整文件名，前后都不能是标识符字符。"""
    pat = re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_])")
    hits: list[str] = []
    for target in SEARCH_DIRS:
        p = ROOT / target
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file()
                     and f.suffix in (".py", ".md", ".mjs", ".txt")]
        else:
            continue
        for f in files:
            try:
                if pat.search(io.open(f, encoding="utf-8", errors="ignore").read()):
                    hits.append(str(f.relative_to(ROOT)))
            except Exception:
                pass
    return hits


def _depends_on_cwd(path: Path) -> bool:
    """脚本是否靠 cwd（而非 `Path(__file__)`）定位项目 —— 移动目录会打断它。

    判据：出现 `eval_out` / `data/` / `src/ticket_agent` 这类**相对路径字面量**，
    且没有 `Path(__file__).parent` 兜底。
    """
    try:
        t = io.open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return False
    has_relative = any(s in t for s in ("eval_out", "data/raw", "src/ticket_agent", "prompt/"))
    has_anchor = "__file__" in t and "parent" in t
    return has_relative and not has_anchor


def classify() -> tuple[list[str], list[str], dict[str, str]]:
    keep: list[str] = []
    move: list[str] = []
    notes: dict[str, str] = {}

    for p in sorted(ROOT.glob("_*")):
        if not p.is_file():
            continue
        name = p.name
        if name in ALWAYS_KEEP:
            keep.append(name)
            continue
        refs = _referenced(name)
        if refs:
            keep.append(name)
            notes[name] = f"被引用：{', '.join(refs[:3])}"
            continue
        stem = name[:-3] if name.endswith(".py") else name
        if any(h in stem for h in (ONEOFF_HINTS + PATCH_HINTS)):
            move.append(name)
        else:
            move.append(name)

    return keep, move, notes


def main() -> int:
    apply = "--apply" in sys.argv
    keep, move, notes = classify()

    print(f"[分类] 根目录 `_` 脚本：保留 {len(keep)} / 移入 diagnostics/ {len(move)}")

    # ── 预检：会因移动而失效的脚本 ──────────────────────────────
    risky = [n for n in move if _depends_on_cwd(ROOT / n)]
    if risky:
        print(f"\n[预检] {len(risky)} 个脚本靠 cwd 定位项目，移动后要从 diagnostics/ 里跑：")
        for n in risky:
            print(f"    {n}")

    # ── 预检：ALWAYS_KEEP 里实际不存在的 ────────────────────────
    missing = [n for n in ALWAYS_KEEP if not (ROOT / n).exists()]
    if missing:
        print(f"\n[预检] ALWAYS_KEEP 声明了但不存在的文件：{missing}")

    if not apply:
        print("\n[试运行] 未传 --apply，不改动任何文件。预览前 20 个将移动的：")
        for n in move[:20]:
            print(f"    {n:<32} {notes.get(n, '一次性排查 / 补丁脚本')}")
        print(f"\n确认后执行：python _tidy_plan.py --apply")
        return 0

    DEST.mkdir(exist_ok=True)
    moved = 0
    for n in move:
        src = ROOT / n
        dst = DEST / n
        if dst.exists():
            print(f"  [跳过] {n} 目标已存在")
            continue
        shutil.move(str(src), str(dst))
        moved += 1

    print(f"\n[完成] 移动 {moved} 个文件到 diagnostics/")
    print(f"[完成] 根目录剩余 `_` 脚本 {len(keep)} 个")

    # ── 移动后立刻复跑体检，确认没打断任何东西 ──────────────────
    print("\n[验证] 重跑文档体检与测试收集……")
    import subprocess
    for cmd in (["python", "-X", "utf8", "_readmecheck.py"],
                ["python", "-X", "utf8", "-m", "pytest", "-q", "--collect-only"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        tail = (r.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
        print(f"  {' '.join(cmd[:4])} → exit {r.returncode} | {tail[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
