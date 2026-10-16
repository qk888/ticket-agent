"""数据集元信息读写（目前只有「时间锚点」一个键）。

`clock.py` 通过 `read_anchor()` 拿锚点，`seed_all()` 通过 `write_anchor()` 落锚点。
两张表之间的分工：`dataset_meta` 存的是**关于这批数据的元信息**，
不属于任何业务实体，所以既不进 `models.py` 的业务表族、也不带 `tenant_id`。

★ 依赖方向：本模块 import `db.session`，`clock.py` **延迟** import 本模块。
反向（session 顶层 import clock）会成环：`session → clock → db.meta → session`。
见 README §5「为什么 `rules.py` 必须是叶子模块」是同一类教训。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from .models import DatasetMeta
from .session import session_scope


def read_anchor() -> dt.datetime | None:
    """读时间锚点。库还没建、表还不存在、没写过 —— 都返回 None（不是错误）。

    返回的是 naive datetime（SQLite 不存时区），调用方负责补 tzinfo。
    """
    try:
        with session_scope() as s:
            row = s.scalar(select(DatasetMeta).where(DatasetMeta.key == "anchor_at"))
            if row is None or not row.value:
                return None
            return dt.datetime.fromisoformat(row.value)
    except Exception:  # noqa: BLE001 —— 表不存在/库不可用都属于"没有锚点"
        return None


def write_anchor(value: dt.datetime) -> None:
    """写入（或覆盖）时间锚点。`seed_all` 造完数据后调一次。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    with session_scope() as s:
        row = s.scalar(select(DatasetMeta).where(DatasetMeta.key == "anchor_at"))
        if row is None:
            s.add(DatasetMeta(key="anchor_at", value=value.isoformat()))
        else:
            row.value = value.isoformat()


def clear_anchor() -> None:
    """删除锚点（回到墙上时钟语义）。测试与 `init_db(drop=True)` 后使用。"""
    with session_scope() as s:
        row = s.scalar(select(DatasetMeta).where(DatasetMeta.key == "anchor_at"))
        if row is not None:
            s.delete(row)
