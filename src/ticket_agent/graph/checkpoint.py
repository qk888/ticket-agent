"""图状态持久化（checkpointer）工厂。

★ 为什么需要这个文件（缺陷 ⑧ 的修复）：

`config.py` 里一直有 `checkpointer: str = "memory"  # memory | sqlite | postgres`
和 `postgres_dsn`，但**没有任何代码读过它们** —— `build_graph()` 的
`checkpointer` 参数默认永远是 `None`，于是：

- 声明了三种持久化模式，实际只有"不持久化"一种；
- `builder.build_graph()` 的 docstring 写着"支持 thread_id 断点续跑、
  也支持 interrupt() 实现 HITL"，但在 HTTP 服务里**一次都没生效过**；
- README §8 已知不足第 5 条说"生产应换成 postgres checkpointer"——
  那句话当时说的是"以后要做"，而不是"配置项已经接通、只差装包"。

这是一个比崩溃更安静的缺陷：**配置项看起来像能力**。
`ticket-agent info` 会打印配置，读的人会以为改一行 `.env` 就能落库。

本模块把它接通：`get_checkpointer()` 按 `settings.checkpointer` 返回实例，
`build_graph()` 在不显式传参时用它。

三种模式的真实能力边界（不要含糊）：

| 模式 | 落盘 | 进程重启后可见 | 多副本共享 | 本项目实测 |
|---|---|---|---|---|
| `memory` | 否 | 否 | 否 | ✅ 默认，评测用 |
| `sqlite` | 是 | 是 | 同机多进程 | ✅ 已接通 |
| `postgres` | 是 | 是 | 是 | ⚠️ 需 `pip install -e ".[postgres]"` |

★ 即便开了 sqlite，`RunStore`（运行记录/摘要）**仍然在内存里** ——
两者是两件事：checkpointer 存的是**图状态**（节点级快照，可续跑），
RunStore 存的是**面向人的运行摘要**。缺陷 ⑦ 讲的是后者。
把 checkpointer 接上并不会修好 `GET /runs` 的多副本一致性。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings

# ★ 进程内缓存：sqlite saver 持有一个连接，每调一次就新建会在长跑的服务里
#   把连接耗光（而且每个都是独立锁，反而削弱了 SqliteSaver 自带的串行化）。
#   缓存键包含 mode，避免运行期改配置后拿到旧实例。
_CACHE: dict[str, Any] = {}


def _sqlite_path() -> Path:
    """checkpoint 数据库落在 eval_out 下，与业务库分开。

    分开的理由：业务库会被评测反复 drop/create，而 checkpoint 是调试线索，
    跟着一起被清掉就失去了"重启后还能捞回上次跑到哪"的意义。
    """
    settings.eval_dir.mkdir(parents=True, exist_ok=True)
    return settings.eval_dir / "checkpoints.sqlite"


def get_checkpointer(mode: str | None = None) -> Any | None:
    """按配置返回 checkpointer 实例；**默认 ``memory`` 返回 ``None``（即不持久化）**。

    ★ 为什么 `memory` 不是 `InMemorySaver()` 而是 `None`：

    `InMemorySaver` 的生命周期跟 `build_graph()` 返回的图对象绑在一起。
    本项目每次跑工单都新建一次图（`stream_run`/`run_case` 各建各的），
    所以那个内存 saver 里永远只有当前这一次运行 —— 它不提供任何
    `None` 给不了的能力，却会让 `configurable.thread_id` 被塞进 config，
    平白多一层"看起来在持久化"的错觉。

    ★★ 更要紧的是：**持久 checkpointer 与评测语义是冲突的**，
    而且实测下来的行为比"续跑"更微妙（这里原先写的是"会跳过已做过的
    工具调用"，那是猜的，`_cpdiag.py` 一测就证伪了）：

        第二遍会**带着第一遍的全部 state 重跑整张图** —— 不是跳过，
        也不是从头开始。实测（`R1:refund` 的 `acme-t6`，同一 thread_id）：
          · 消息数 11 → 22（第一遍的历史被当成输入累积进来）
          · 工具被**重复调用**：审计日志里 `get_ticket`/`get_customer`/
            `check_refund_eligibility`/`create_refund`/`close_ticket`
            各出现了第二遍，`tool_calls` 从 5 涨到 10
          · 第二笔退款被**缺陷 ⑥ 的政策护栏**挡下（审计记为
            `create_refund.blocked`），所以钱没退两遍 ——
            但那是别处修好的洞救了这里，不是 checkpointer 的性质；
            换成 `close_ticket` 这种没有护栏的动作，副作用就是真重复。

    对评测的后果：成本翻倍、消息越跑越长、副作用重复执行。
    症状是"重跑一遍评测，pass 率变了"，且**只在第二次运行才出现** ——
    第一次跑永远是对的，所以这类问题特别容易漏过 CI。

    所以 `eval/harness.py` 显式传 `checkpointer=False` —— 不要依赖默认值，
    因为这个坑值得在调用处写明白（`False` 是 `build_graph` 里"强制不持久化"
    的哨兵值，见其 docstring 的三态约定）。

    ★ 这里刻意不吞异常。如果 `.env` 写了 `CHECKPOINTER=postgres` 但没装
    `langgraph-checkpoint-postgres`，正确行为是启动就报错让人知道，
    而不是悄悄退回内存模式 —— 后者会让人以为状态已经落库了。
    """
    mode = (mode or settings.checkpointer or "memory").strip().lower()

    if mode in ("", "none", "off", "memory"):
        return None

    if mode == "sqlite":
        if "sqlite" in _CACHE:
            return _CACHE["sqlite"]
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "CHECKPOINTER=sqlite 需要 langgraph-checkpoint-sqlite：\n"
                "    python -m pip install langgraph-checkpoint-sqlite"
            ) from exc

        path = _sqlite_path()
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn)
        # 建表是幂等的；不调则首次运行会在 get_tuple 时才炸
        saver.setup()
        _CACHE["sqlite"] = saver
        return saver

    if mode == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "CHECKPOINTER=postgres 需要可选依赖组：\n"
                '    python -m pip install -e ".[postgres]"\n'
                "并设置 POSTGRES_DSN。"
            ) from exc

        if not settings.postgres_dsn:
            raise RuntimeError("CHECKPOINTER=postgres 但 POSTGRES_DSN 为空。")
        saver = PostgresSaver.from_conn_string(settings.postgres_dsn)
        saver.setup()
        return saver

    raise ValueError(f"未知的 checkpointer 模式：{mode!r}（可选 memory | sqlite | postgres）")


__all__ = ["get_checkpointer"]
