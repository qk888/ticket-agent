"""验证新测试**真的抓得住**缺陷 ⑧（而不是恰好通过）。

方法：把缺陷逐个**重新注入**，确认对应测试变红，然后还原。
一个从没红过的测试，和一个从没失败过的 agent 一样可疑。

注入的五种：
  A. 往 pyproject 塞回 sse-starlette（幽灵依赖）
  B. 往 config.py 塞回 log_level（死配置）
  C. 把 .env.example 的 DATABASE_URL 改回 sqlite+aiosqlite（照抄就崩）
  D. 从 harness.py 拿掉 checkpointer=False（评测续跑陷阱）
  E. 把 checkpointer 的未知模式从抛异常改成静默降级（无声走错分支）

★ 覆盖面要说清楚：这五条打的全是**缺陷 ⑧ 那一族的声明测试**
（"声明与实装不一致"）。它不是"十三个缺陷都验一遍" ——
其余十二个缺陷的回归护栏在各自的测试文件里，本脚本不重复它们。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYPROJECT = ROOT / "pyproject.toml"
CONFIG = ROOT / "src" / "ticket_agent" / "config.py"
ENVEX = ROOT / ".env.example"
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_tests(*tests: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--no-header", "-x"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def restore(path: Path, backup: Path) -> None:
    shutil.copy2(backup, path)


results: list[tuple[str, bool, str]] = []


def injection(label: str, path: Path, mutate, test: str) -> None:
    """注入一个缺陷，看对应测试是否变红。

    ★ 必须校验"注入真的改变了文件"：第一版里 A 的锚点写成了
    `"rich>=13.7"` 而实际是 `"rich>=13.9"`，`str.replace` 静默什么也没做，
    于是报告成"测试漏掉了幽灵依赖" —— 把**脚本的错**算在了**测试的账**上。
    任何"注入失败"都必须与"测试没抓住"区分开，否则这个脚本本身
    就是又一个不可信的评测器。
    """
    backup = path.with_suffix(path.suffix + ".injurebak")
    shutil.copy2(path, backup)
    try:
        before = path.read_text(encoding="utf-8")
        mutate()
        after = path.read_text(encoding="utf-8")
        if before == after:
            results.append((label, False, "注入未生效"))
            print(f"  ✗ 注入未生效  {label}  ← 锚点没匹配上，这是脚本的问题")
            return
        code, out = run_tests(test)
        caught = code != 0
        reason = ""
        if caught:
            for line in out.splitlines():
                if "assert" in line.lower() or "Error" in line:
                    reason = line.strip()[:100]
                    break
        results.append((label, caught, reason))
        print(f"  {'✓ 被抓出' if caught else '✗ 漏掉了'}  {label}")
        if reason:
            print(f"       {reason}")
    finally:
        restore(path, backup)
        backup.unlink()


print("=" * 72)
print("注入缺陷，验证测试是否变红")
print("=" * 72)

# A. 幽灵依赖
def inject_ghost_dep() -> None:
    t = PYPROJECT.read_text(encoding="utf-8")
    anchor = '    "rich>=13.9",'
    assert anchor in t, f"锚点没找到，pyproject 变了：{anchor!r}"
    t = t.replace(anchor, anchor + '\n    "sse-starlette>=2.1",', 1)
    PYPROJECT.write_text(t, encoding="utf-8")


injection(
    "A. 塞回幽灵依赖 sse-starlette",
    PYPROJECT, inject_ghost_dep,
    "tests/test_declaration_integrity.py::test_no_declared_dependency_is_never_imported",
)


# B. 死配置
def inject_dead_config() -> None:
    t = CONFIG.read_text(encoding="utf-8")
    marker = "    # ★ 这里曾经有一个 `log_level"
    t = t.replace(marker, '    log_level: str = "INFO"\n' + marker, 1)
    CONFIG.write_text(t, encoding="utf-8")


injection(
    "B. 塞回死配置 log_level",
    CONFIG, inject_dead_config,
    "tests/test_declaration_integrity.py::test_dead_config_fields_are_documented_or_removed",
)


# C. 照抄就崩的 URL
def inject_bad_url() -> None:
    t = ENVEX.read_text(encoding="utf-8")
    t = t.replace(
        "# DATABASE_URL=sqlite:///./eval_out/ticket_agent.db",
        "DATABASE_URL=sqlite+aiosqlite:///./eval_out/ticket_agent.db",
        1,
    )
    ENVEX.write_text(t, encoding="utf-8")


injection(
    "C. .env.example 改回 sqlite+aiosqlite（照抄即崩）",
    ENVEX, inject_bad_url,
    "tests/test_declaration_integrity.py::test_env_example_database_url_is_synchronous",
)


# D. 评测续跑陷阱
def inject_no_optout() -> None:
    t = HARNESS.read_text(encoding="utf-8")
    i = t.index("            checkpointer=False,\n")
    # 连同上面几行注释一起拿掉，模拟"当初根本没写这段"
    j = t.rindex("# ★ 显式 `False`", 0, i)
    t = t[:j] + t[i + len("            checkpointer=False,\n"):]
    HARNESS.write_text(t, encoding="utf-8")


injection(
    "D. harness.py 拿掉 checkpointer=False",
    HARNESS, inject_no_optout,
    "tests/test_declaration_integrity.py::test_eval_harness_opts_out_of_persistence",
)


# E. 未知模式静默降级
def inject_silent_fallback() -> None:
    t = (ROOT / "src" / "ticket_agent" / "graph" / "checkpoint.py").read_text(encoding="utf-8")
    t = t.replace(
        '    raise ValueError(f"未知的 checkpointer 模式：{mode!r}（可选 memory | sqlite | postgres）")',
        "    return None",
        1,
    )
    (ROOT / "src" / "ticket_agent" / "graph" / "checkpoint.py").write_text(t, encoding="utf-8")


injection(
    "E. checkpointer 未知模式改为静默降级",
    ROOT / "src" / "ticket_agent" / "graph" / "checkpoint.py", inject_silent_fallback,
    "tests/test_declaration_integrity.py::test_checkpointer_factory_rejects_unknown_mode",
)

print()
print("=" * 72)
missed = [r for r in results if not r[1]]
if missed:
    print(f"✗ {len(missed)} 个注入没被抓住 —— 这些测试是装饰品：")
    for label, _, why in missed:
        print(f"   - {label}" + (f"（{why}）" if why else ""))
    sys.exit(1)
print(f"✓✓ 全部 {len(results)} 个注入都被抓住：这些测试有牙齿")
print()
print("（源码已全部还原；用 git status 或 pytest 全量再确认一次）")
