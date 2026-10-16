"""量各模块精确行数（按 splitlines 计，含空行与注释）。

★ 为什么要有这个脚本：PowerShell 的 `Get-Content | Measure-Object -Line`
   **跳过空行**，量出来的数比真实行数少一到两成。用它填文档里的代码规模表，
   等于给出一组偏小的数字 —— 而这种错误不会有人发现，因为没有任何东西会变红。
   文档里要写的数字，必须用与"读文件"同一套语义量出来。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def count(pattern: str) -> tuple[int, int]:
    files = sorted(ROOT.glob(pattern))
    total = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
    return len(files), total


def main() -> None:
    print(f"{'模块':<30}{'行数':>6}")
    print("-" * 36)
    total = 0
    for f in sorted((ROOT / "src" / "ticket_agent").rglob("*.py")):
        n = len(f.read_text(encoding="utf-8").splitlines())
        total += n
        rel = str(f.relative_to(ROOT / "src" / "ticket_agent")).replace("\\", "/")
        print(f"{rel:<30}{n:>6}")
    print("-" * 36)
    print(f"{'src/ticket_agent 合计':<30}{total:>6}")

    print()
    for label, pat in (
        ("src/ticket_agent/**/*.py", "src/ticket_agent/**/*.py"),
        ("tests/**/*.py", "tests/**/*.py"),
        ("tools/**/*.py", "tools/**/*.py"),
        ("顶层 _*.py（探针脚本）", "_*.py"),
    ):
        n, t = count(pat)
        print(f"{label:<30} 文件 {n:>3}   行数 {t:>6}")


if __name__ == "__main__":
    main()
