"""决策时钟：把「现在」从墙上时钟里解耦出来。

## 为什么需要这个模块（缺陷 ⑨）

合成数据是**一次性造出来、然后放着不动**的：`seed_all` 把订单时间戳写成
「造数那一刻往前 N 天」。而规则 R1 判定「签收几天」用的是**读数据那一刻**的
墙上时钟。于是数据每多放一天，所有订单在规则眼里就"老"了一天 ——
七天无理由窗口边界上的订单会从 `R1:refund` 翻成 `R1:reject`。

实测（`_driftcheck.py`，seed=7 / 60 客户）：数据放置 12 小时后，
全量 40 条 case 里 1 条条件翻转；**放置 30 天后 7 条翻转，`R1:refund` 归零**。
更糟的是 `_stratified_sample` 按 condition 分桶，1 条 case 换桶就会带动整批
抽样结果改变 —— 同一个 seed，两次运行给出**两份不同的评测集**。

这不是"数据老化"，是**基准漂移**：造数和判定用了两个不同的"现在"。

## 修法

造数时把「现在」写进库里（`dataset_meta` 表的 `anchor_at`），
之后凡是需要"现在"的地方都读它：

- 有锚点（合成数据）→ 用锚点。数据放着不动，判定基准也不动。
- 无锚点（真实业务）→ 用墙上时钟。真实数据一直在被写入，
  "造数时刻"这个概念本身不存在，当下就是当下。

## 为什么不用模块级 `_ANCHOR` 就完事

因为要跨进程。`ticket-agent run` 是另一个进程，它没有造数时的内存状态，
只能去库里读。所以锚点**存库**，内存缓存只是加速。

## 同一个缺陷的第二个来源：`created_at` 也在读墙上时钟

修完上面这处之后，`_faultcheck.py` 连跑三次仍然抖（`guesser` 的
forbidden = 2 / 1 / 2），说明还有第二处。

根因在 `models.utcnow()`：它当时写的是 `dt.datetime.now(utc)`，而
`Order.created_at` / `Ticket.created_at` / `Refund.created_at` 都拿它当默认值。
我一开始认为这没影响 —— 毕竟 `created_at` 不参与任何业务判定。

**它间接参与**：`TenantRepo.list_orders_of_customer` 用
`order_by(Order.created_at.desc())` 排客户名下的订单。同一客户的几笔订单在
`seed_all` 里是连续插入的，间隔不到 1 毫秒，于是时间戳大量并列；而 SQLite 对
**并列行的 ORDER BY 顺序不作任何保证**。两次造数的并列结构不同，返回顺序就
不同 —— `ambiguous` 类 case（客户名下多笔订单、工单没指明是哪一笔）里，
agent 先看到哪一笔订单直接决定它怎么处置。

实测（`_ordercheck.py`）：两次造数之间有 7 个客户的订单顺序不同，
全库重复时间戳 11~13 个；`guesser` 的违规命中在 `acme-t5` / `acme-t8`
两条 ambiguous case 上时有时无。**case 内容逐字段比对是 0 处不同** ——
变的只是读取顺序。

修法两处一起做，缺一不可：

1. `utcnow()` 改读 `clock.now()` —— 同一批数据内时间戳恒定；
2. 三处 `order_by(...created_at.desc())` 统统补 `id` 兜底 —— 锚点固定后
   并列反而更多，"顺序"不能交给数据库任意决定。

教训：**排序键也是参与判定的**。一个字段"不参与业务规则"不等于它
"不影响结果" —— 只要它决定了 agent 先看到什么，它就进了决策链路。


## 循环导入

本模块只 import `datetime`，是叶子模块。
读库的 `_load_from_db()` 里**延迟 import** `db.session` ——
否则 `clock → db.session → db.__init__ → db.seed → rules → clock` 成环
（与 `rules.py` 必须是叶子模块是同一个教训，见 README §5）。
"""

from __future__ import annotations

import datetime as dt

ANCHOR_KEY = "anchor_at"

_ANCHOR: dt.datetime | None = None
_LOADED = False  # 是否已经尝试从库里读过锚点（无论有没有读到）


def set_anchor(value: dt.datetime | None) -> None:
    """设置内存锚点。`None` 表示回到墙上时钟。"""
    global _ANCHOR, _LOADED
    _ANCHOR = _as_aware(value)
    _LOADED = True


def get_anchor() -> dt.datetime | None:
    """读锚点（必要时先从库里加载一次）。"""
    if not _LOADED:
        _load_from_db()
    return _ANCHOR


def reset() -> None:
    """清掉缓存。`init_db(drop=True)` 重建库时必须调 —— 否则库里锚点已换，
    内存里还留着上一批数据的锚点，评测集又会飘。"""
    global _ANCHOR, _LOADED
    _ANCHOR = None
    _LOADED = False


def now() -> dt.datetime:
    """当前决策时刻：有锚点用锚点，否则用墙上时钟。"""
    anchor = get_anchor()
    if anchor is not None:
        return anchor
    return dt.datetime.now(dt.timezone.utc)


def _as_aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _load_from_db() -> None:
    """从 `dataset_meta` 读锚点。读不到就保持 `None`（= 用墙上时钟）。

    刻意**不抛异常**：没有锚点是合法状态（真实业务），
    库还没建/表还不存在也是合法状态（还没 seed 就要能跑）。
    这与缺陷 ⑧ 里"缺依赖必须抛 RuntimeError"不同 ——
    那里的静默降级会让用户以为状态已落库，这里的降级就是正确行为本身。
    """
    global _ANCHOR, _LOADED
    _LOADED = True
    try:
        from .db.meta import read_anchor  # 延迟 import：避免成环

        _ANCHOR = _as_aware(read_anchor())
    except Exception:  # noqa: BLE001 —— 库不可用/无锚点都不是错误
        _ANCHOR = None
