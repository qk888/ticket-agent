"""缺陷 ⑧ 验收：checkpointer 是否真的接通（而不只是"多了个文件"）。

要证明的四件事：
  1. memory（默认）→ get_checkpointer() 返回 None，行为与修复前完全一致；
  2. sqlite → 真的返回 saver、真的建库建表、真的落 checkpoint；
  3. ★ "续跑旧状态"的陷阱是真的 —— 同一个 thread_id 跑第二遍会命中旧状态；
  4. 评测层传 checkpointer=False 确实规避了那个陷阱（即使配置成 sqlite）。

第 3 条尤其重要：如果那个坑不存在，我在 harness.py 里加的注释就是多余的；
如果存在而 harness 没规避，评测结果就是错的。两个方向都要证。
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, "src")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from ticket_agent.config import get_settings, settings  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.graph.builder import run_ticket  # noqa: E402
from ticket_agent.graph.checkpoint import _CACHE, _sqlite_path, get_checkpointer  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def _q(path: object, sql: str, params: tuple = ()) -> list:
    """查一下 checkpoint 库就关掉。

    ★ 必须显式 close：裸 `sqlite3.connect(...).execute(...)` 的连接要等 GC
    才释放，在 Windows 上会让收尾的 unlink 抛
    `PermissionError: [WinError 32] 另一个程序正在使用此文件`
    —— 第一版就是这么栽的，表现为"脚本全绿但目录里多了个删不掉的文件"。
    """
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


print("=" * 72)
print("1) memory（默认）：行为必须与修复前一致（返回 None，不持久化）")
print("=" * 72)
check("settings.checkpointer 默认是 memory", settings.checkpointer == "memory", repr(settings.checkpointer))
check("get_checkpointer() 返回 None", get_checkpointer() is None)
check("显式 'memory'/'none'/'off'/'' 都返回 None",
      all(get_checkpointer(m) is None for m in ("memory", "none", "off", "")))

print()
print("=" * 72)
print("2) sqlite：真的建库、建表、落 checkpoint")
print("=" * 72)
cp_path = _sqlite_path()
if cp_path.exists():
    cp_path.unlink()
    print(f"  （清掉旧 checkpoint：{cp_path}）")

os.environ["CHECKPOINTER"] = "sqlite"
get_settings.cache_clear()
import ticket_agent.config as _cfg  # noqa: E402
import ticket_agent.graph.checkpoint as _ckpt  # noqa: E402

_cfg.settings = get_settings()
_ckpt.settings = _cfg.settings
_CACHE.clear()

saver = get_checkpointer()
check("CHECKPOINTER=sqlite 时返回了 saver", saver is not None, type(saver).__name__)
check("checkpoints.sqlite 文件已创建", cp_path.exists(), f"{cp_path.stat().st_size if cp_path.exists() else 0} B")

tables = [r[0] for r in _q(cp_path, "select name from sqlite_master where type='table'")]
check("setup() 建出了 checkpoint 表", "checkpoints" in tables, str(tables))

init_db(drop=True)
seed_all(n_customers=60, seed=7)
cases = build_eval_cases(size=6, seed=7)
c = cases[0]
print(f"  （用 {c['case_id']} / {c['level']} 试跑）")

state = run_ticket(c["tenant_id"], c["ticket_id"], arm="single",
                   run_id="cpprobe-fixed-thread", checkpointer=None)
rows = _q(cp_path, "select count(*) from checkpoints where thread_id='cpprobe-fixed-thread'")[0][0]
check("checkpoint 真的写进了库", rows > 0, f"{rows} 行")
check("state 正常返回", bool(state.get("final_answer") or state.get("finished")))

print()
print("=" * 72)
print("3) ★ 持久 checkpointer 的真实后果：第二遍带着旧 state 重跑整张图")
print("=" * 72)
# ★ 这一节原先断言"第二遍会跳过已做过的工具调用"—— 那是我**猜的**，
#   一跑就证伪了。真实行为是：第二遍把第一遍的 state 当输入**重跑整张图**，
#   消息累积、工具被重复调用。断言必须钉住实测行为，不能钉住想象。
state2 = run_ticket(c["tenant_id"], c["ticket_id"], arm="single",
                    run_id="cpprobe-fixed-thread", checkpointer=None)
n1 = len(state.get("messages") or [])
n2 = len(state2.get("messages") or [])
t1 = len(state.get("tool_calls") or [])
t2 = len(state2.get("tool_calls") or [])
check("消息历史累积（第二遍带着第一遍的全部历史）", n2 == 2 * n1, f"{n1} → {n2}")
check("工具被重复调用", t2 == 2 * t1, f"tool_calls {t1} → {t2}")
print("  ★ 这正是评测层必须传 checkpointer=False 的原因：")
print("     消息越跑越长、成本翻倍、副作用重复执行，")
print("     而**第一次运行永远是对的**，所以只在重跑时才暴露。")

print()
print("=" * 72)
print("4) 评测层规避：即使配置是 sqlite，checkpointer=False 也必须不落盘")
print("=" * 72)
_CACHE.clear()
before = _q(cp_path, "select count(*) from checkpoints where thread_id='cpprobe-fresh-thread'")[0][0]
run_ticket(c["tenant_id"], c["ticket_id"], arm="single",
           run_id="cpprobe-fresh-thread", checkpointer=False)
after = _q(cp_path, "select count(*) from checkpoints where thread_id='cpprobe-fresh-thread'")[0][0]
check("checkpointer=False 时不写 checkpoint", before == 0 and after == 0, f"{before} → {after}")
print("  （不落盘即无处可续，第二遍必然是全新的一次运行）")

print()
print("=" * 72)
print("5) 未知模式必须报错，不静默降级")
print("=" * 72)
try:
    get_checkpointer("mysql")
    check("未知模式抛 ValueError", False, "竟然没抛")
except ValueError as exc:
    check("未知模式抛 ValueError", True, str(exc)[:70])

try:
    get_checkpointer("postgres")
    check("postgres 缺依赖时抛 RuntimeError（非静默降级）", False, "竟然没抛")
except RuntimeError as exc:
    check("postgres 缺依赖时抛 RuntimeError（非静默降级）", True, str(exc).split("\n")[0][:70])

# 收尾：恢复默认配置，并把探针写的 checkpoint 清掉（别污染项目目录）
os.environ.pop("CHECKPOINTER", None)
get_settings.cache_clear()
# ★ 必须先关连接再删文件：SqliteSaver 持有打开的 sqlite 连接，
#   Windows 上文件被占用时 unlink 会抛
#     PermissionError: [WinError 32] 另一个程序正在使用此文件
#   （第一版就踩了这个 —— 清理失败会让"脚本跑完了但目录里多了个文件"。）
saver_conn = getattr(saver, "conn", None)
if saver_conn is not None:
    try:
        saver_conn.close()
        print("  （已关闭 sqlite 连接）")
    except Exception as exc:
        print(f"  （关闭连接失败，忽略：{exc}）")
_CACHE.clear()
if cp_path.exists():
    try:
        cp_path.unlink()
        print(f"（已清理探针 checkpoint：{cp_path.name}）")
    except PermissionError as exc:
        print(f"（清理失败，可手动删除 {cp_path}：{exc}）")

print()
print("=" * 72)
if FAILED:
    print(f"✗ 共 {len(FAILED)} 项失败：")
    for f in FAILED:
        print(f"   - {f}")
    sys.exit(1)
print("✓✓ 缺陷 ⑧ 验收通过：checkpointer 三模式真的接通，且评测层规避了续跑陷阱")
