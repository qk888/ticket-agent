"""pytest 全局配置 —— ★ 存在的首要理由是**隔离测试库**。

## 为什么必须有这个文件

`test_service_api.py` 会调 `init_db(drop=True)` 造一批 40 客户的小数据集。
而在加这个文件之前，那次 `drop=True` 打的是**开发库**
（`settings.database_url` → `eval_out/ticket_agent.db`）。

后果不是报错，是**静默的数据消失**：跑一次 `pytest`，千级评测数据
（1000 客户 / 2365 订单 / 606 工单）就变成了 40 客户的小数据集；
此后 `build_eval_cases()` 只会返回几十条 case，而报表照样能生成、
照样显示 100% —— 看起来一切正常。

这与本项目反复栽的其它缺陷是**同一个模式**：失效不留痕迹。
（缺陷 ⑨ 基准漂移：时钟变了但指标没标注；缺陷 ⑫ 护栏守住了钱没守住谎：
越权写被拒却在过程指标里与成功无法区分。都是"错得很安静"。）

## 机制：环境变量必须在**导入 ticket_agent 之前**设好

`config.py` 末尾是模块级的 `settings = get_settings()`，而 `session.py` 的
`get_engine()` 又只在**第一次调用时**读 `settings.database_url` 并缓存引擎
（模块级 `_engine`）。所以时点是硬约束：

    conftest.py 顶层（早于任何测试模块 import ticket_agent）
        → 设 DATABASE_URL
        → 之后所有 import 与建库都走测试库

放到 fixture 里就晚了 —— 那时 `settings` 早已构造完毕。
"""

from __future__ import annotations

import os
from pathlib import Path

# ticket-agent/（conftest.py 的上一级）—— 不用 `ticket_agent.config.PROJECT_ROOT`，
# 那个包此刻还不能导入（导入它就会把 settings 定死）。
_ROOT = Path(__file__).resolve().parents[1]

#: 测试专用库。**刻意与开发库同目录但不同名** —— 放在 eval_out/ 下是为了
#: 它同样被 .gitignore 忽略、同样可随手删掉；不同名才是隔离本身。
TEST_DB_PATH = _ROOT / "eval_out" / "pytest_ticket_agent.db"

#: ★ 这是整个文件的关键一行。`setdefault` 而非直接赋值：允许外部
#: （CI 或临时排查）用环境变量指定别的库，但默认永远不是开发库。
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DB_PATH}")


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG001
    """测试结束后删掉测试库（含 WAL/SHM 边车文件）。

    不删的话它会一直躺在 eval_out/ 里，而某个只看文件名的人（可能就是
    几周后的我）会以为那是可用的评测数据 —— 又一个"看着像真的"的陷阱。

    ★ 必须先 `dispose()` 再删：Windows 上 SQLite 引擎会一直持有文件句柄，
    直接 `unlink` 会 PermissionError —— 而第一版正是把那个异常 `except OSError`
    吞掉的，于是"清理"这一步**静默地什么都没做**，文件照样留着。
    （又一个"看起来做了其实没做"，和这个项目其它缺陷同源。）
    """
    try:
        from ticket_agent.db.session import get_engine

        get_engine().dispose()
    except Exception:  # noqa: BLE001 —— 清理失败不该让测试会话变红
        pass

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(TEST_DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
