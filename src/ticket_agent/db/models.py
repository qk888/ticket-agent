"""数据模型。

设计要点（面试可讲）：
1. **所有业务表都带 tenant_id** —— 多租户隔离不是靠 where 子句自觉，而是靠
   `TenantScopedRepository` 统一注入过滤条件。
2. **audit_log 记录每一次状态变更** —— 评测时用于归因（谁改的、改了什么、什么时候），
   也是"禁止动作"检测的数据源。
3. **写操作的幂等性靠唯一约束兜底**（refunds.idempotency_key），
   不是靠 agent 自觉 —— agent 重试时数据库会拦住它。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .. import clock


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    """当前时间。★ 走 `clock` 锚点（缺陷 ⑨ 的修复）。

    这里必须读锚点而不是墙上时钟：`created_at` 虽然不参与业务判定，
    但它被 `TenantRepo.list_orders_of_customer` / `list_refunds_of_order` /
    `list_tickets_of_customer` 当作 **排序键**，而排序结果会决定
    `ambiguous` 类 case 里 agent 先看到哪一笔订单 —— 也就是说它**间接
    参与判定**。用墙上时钟时，同一客户的几笔订单在同一毫秒内插入，
    两次造数得到的并列结构不同，SQLite 对并列行的 ORDER BY 顺序不作
    保证，于是评测集在 case 内容完全一致的情况下仍会给出不同结果
    （实测：`guesser` 的 forbidden 在 0/1/2 之间抖）。
    """
    return clock.now()


# ── 状态机常量（用字符串而不是 Enum，sqlite 友好，也便于人工看库）──────
ORDER_STATUSES = ("pending_paid", "shipped", "signed", "cancelled")
TICKET_STATUSES = ("open", "pending_human", "resolved", "rejected")
REFUND_STATUSES = ("requested", "approved", "rejected", "blocked")
# 非退款类售后动作的种类。★ 这份清单必须与 `rules.ACTIONS` 里的非退款动作、
# 以及 `rules.REQUEST_KINDS` 一一对应 —— 三处漂移的话，agent 会判定出一个
# 数据库写不进去的动作。`tests/test_rules_consistency.py` 里有哨兵在卡这件事。
AFTER_SALES_ACTION_KINDS = (
    "exchange",  # 换货
    "reship",  # 补发
    "compensate",  # 运费 / 损失补偿（★ 会出钱）
    "price_protect",  # 价保退差（★ 会出钱）
    "invoice",  # 开票
    "address_change",  # 修改收货地址
    "urge",  # 催单
)
# 会实际出钱的动作种类 —— 账本不变量（"累计付出不得超过订单金额"）必须把它们
# 和退款**放在同一个池子里**算。漏掉任何一个，就会重新造出缺陷 ⑥ 的形状：
# 护栏只守住了它认识的那一种钱。
MONEY_ACTION_KINDS = ("compensate", "price_protect")
AFTER_SALES_ACTION_STATUSES = ("requested", "approved", "rejected")
# 类目政策常量（R2/R6）的真实定义在 `rules.py`（政策叶子模块），这里 re-export
# 只为兼容既有 `from ..db.models import NON_RETURNABLE_CATEGORIES` 的引用。
# 不要在本地再定义一份 —— 两份定义会漂移，而政策必须只有一个事实来源。
from ..rules import (  # noqa: E402  （放在此处以贴近被 re-export 的常量）
    NON_REFUNDABLE_CATEGORIES,
    NON_RETURNABLE_CATEGORIES,
)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(32), default="")
    # 风控分 0~1，>0.7 触发人工（R4）
    risk_score: Mapped[float] = mapped_column(Float, default=0.1)
    # 历史投诉次数，用于潜变量模拟真实分布（对齐 CRMArena 的设计）
    complaint_count: Mapped[int] = mapped_column(Integer, default=0)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    orders: Mapped[list[Order]] = relationship(back_populates="customer")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_no", name="uq_order_no_per_tenant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_no: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="服饰")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending_paid")
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    shipped_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    signed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # R5：同一订单退款次数上限
    refund_count: Mapped[int] = mapped_column(Integer, default=0)
    # R8：同一订单换货次数上限（与 refund_count 对称 —— 规则要读它，
    # 就必须有个地方能写它，否则规则只能看一个永远为 0 的字段）
    exchange_count: Mapped[int] = mapped_column(Integer, default=0)
    # R9：同一订单补发次数上限
    reship_count: Mapped[int] = mapped_column(Integer, default=0)
    # R12：是否已开票（开票不是"动作"而是一个事实状态，所以留在订单上）
    invoice_issued: Mapped[bool] = mapped_column(Boolean, default=False)
    # R14：上一次催单时间，用于 24 小时冷却（催单也不产生"动作记录"，
    # 它是"客户催了几次"的计数事实）
    last_urge_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # ★ R11 价保退差要"该商品**现在**卖多少钱" —— 这不是订单属性，
    #   而是商品当前市价。放在这里只是因为本项目没有独立的商品表，
    #   而价保的动作对象是订单。
    #
    #   为什么必须有这一列（缺陷 ㉕ 的教训）：
    #   R11 的守卫是 `if current_price is None: 返回 need_clarification`。
    #   全项目**没有任何地方传过它** —— 于是 R11 恒判"未提供当前售价"，
    #   `price_protect` 动作永远产生不出来，`R11:price_protect` 这条评测
    #   条件永远是 0 条 case。
    #   而"规则写了但参数永远传不进"和"规则没写"在报表上完全同形：
    #   都是 0 条 case。这就是本项目缺陷 ⑧ 的形状 —— **能力只存在于声明里**。
    #
    #   语义：`None` = 该订单没有做过价格快照（老数据），此时 R11 正确地
    #   要求人工核实，而不是猜一个价。造数时按概率为部分订单写入。
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order")
    refunds: Mapped[list[Refund]] = relationship(back_populates="order")
    after_sales: Mapped[list[AfterSalesAction]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="服饰")
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)

    order: Mapped[Order] = relationship(back_populates="items")


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="requested")
    # ★ 幂等兜底：agent 重试时数据库直接拒绝，不依赖 agent 自觉
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="refunds")


class AfterSalesAction(Base):
    """非退款类售后动作（换货/补发/赔付/价保/开票/改址/催单）。

    为什么是**一张表**而不是七张：

    ① 这七个动作共享同一套生命周期（申请 → 受理 → 完成）、同一套幂等键机制、
       同一套审计留痕。拆成七张表意味着快照层、评测层、审计层各要写七份分支
       —— 而这个项目里所有的真实缺陷，形状都是"同一件事写在两个地方，改了一处"。
    ② 终态判定天然是同构的：评测只需要问"这笔动作存在吗、状态是什么、
       金额多少"，不需要知道换货和补发的业务差异（那个差异在 `rules.py` 里）。
       一张表让 `diff_snapshots` 能用一个多重集比较覆盖全部七种。
    ③ 需要区分时，`kind` 字段就够了；确实只有换货/补发才需要的字段
       （比如原商品与替换商品）留在 `detail` JSON 里，而不是为少数行加列。

    `kind` 与 `action` 的区别是本表最容易搞混的地方：
    - `kind` 是**客户要什么**（诉求类型，9 种之一，与 `rules.REQUEST_KINDS` 对应）
    - `action` 是**我们做了什么**（处置动作，12 种之一，与 `rules.ACTIONS` 对应）
    两者在多处并不相同（客户要"退货退款"→我们做 `refund`；客户要"价保"→
    我们做 `price_protect`）。把它们压成一个字段，正是这次重构要修的那个错误。
    """

    __tablename__ = "after_sales_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    # kind 取值见 AFTER_SALES_ACTION_KINDS
    kind: Mapped[str] = mapped_column(String(24), default="exchange", index=True)
    # ★ 会出钱的动作（compensate / price_protect）把金额写这里；
    #   不出钱的动作保持 0.0 —— 这样"累计付出"只需要把两个表加起来
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="requested")
    # ★ 幂等兜底：与 refunds 同一套机制，不依赖 agent 自觉
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    # 只有部分动作才需要的字段（替换商品名、新地址、发票抬头、赔付凭证等）
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="after_sales")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # 允许为空：L3 任务里客户没说清是哪个订单
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    intent: Mapped[str] = mapped_column(String(32), default="")
    # ★ 客户**要什么**（取值见 `rules.REQUEST_KINDS`）。
    #
    # 它和 `intent` 是两件事，不能合并：
    #   - `request_kind` = 客户提出的诉求，是**任务输入**；
    #   - `intent`       = 我们归档时打的分类，是**我们的判断**。
    #
    # ★★ 在它出现之前，`decide()` 只能假设"每一张售后工单都是退款申请"。
    #   于是真实语料里占 39.3% 的物流类工单丢进来，系统也只会去退钱；
    #   只有 21.4% 的真实诉求（退款）能被正确接住。加上这一列之后，
    #   "客户要补发 → 我们补发"才第一次可以被**表达**，
    #   也才第一次可以被**评测**（条件标签从 `R1:refund` 变成 `R8:reship` 之类）。
    #
    # ★ 为什么让它对 agent 可见（`get_ticket` 会返回它）：
    #   golden 是按它算出来的。如果 agent 读不到，就等于"标准答案用了一个
    #   agent 拿不到的信息"—— 这类不对称在本项目里已经出现过三次
    #   （缺陷 ② golden 凭空造退款 / ③ 快照比较不可写字段 / ⑫ 探针读错 dict），
    #   每次的表现都是同一个：**这条 case 永远不可能赢**。
    #   而且识别诉求本来也不是本项目的考点（政策判定与副作用控制才是）。
    #   把识别结果直接给出，考点才干净；真要考识别，那是另一个评测集的事。
    request_kind: Mapped[str] = mapped_column(String(24), default="refund", index=True)
    # 诉求原因（取值见 `rules.REQUEST_CAUSES`）。少数规则要区分"为什么要换/补"：
    # 同是换货，`missing`（少发漏发）与 `size`（尺码不符）的处置不同。
    request_cause: Mapped[str] = mapped_column(String(24), default="")
    # ★ 客户**自己报的金额**（赔付/价保要用），例如"赔我 20 元"里的 20。
    #
    #   为什么它必须是一列、且对 agent 可见（与 `request_kind` 同一条理由）：
    #   golden 与工具层都按这个数算钱。如果 golden 从正文解析、而 agent 自己
    #   读客户原话，两边可能读出不同的数 —— 那时这条 case 是**不可赢**的，
    #   而报表上只会显示"agent 又算错了金额"，没人看得出前提就不一致。
    #   本项目缺陷 ②③⑫ 全是这个形状（标准答案用了 agent 拿不到的信息）。
    #
    #   `0.0` = 客户没提金额。此时 R10 正确地要求先澄清，而不是替客户猜一个数。
    request_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # ★ 客户**是否已提供凭证**（照片 / 截图 / 检测报告）。
    #
    # 为什么需要它：政策文本（`prompt/policies.md` 的 R2 / R9 / R10）早就写了
    # "质量问题需提供凭证""运输破损需提供照片"，但**代码里 0 处能表达它** ——
    # 于是政策承诺了一件事，而系统从来没有检查过它。这是缺陷 ⑧
    # 「能力只存在于声明里」的形状，只不过这次发生在政策文本而不是代码里。
    #
    # 与 `request_kind` / `request_amount` 同一条理由：它是**任务输入**，
    # 不是答案。答案是我们做什么（受理 / 驳回 / 要求补凭证）。
    # golden 按它算，agent 也必须读得到，否则这条 case 不可赢。
    #
    # 默认 False = 客户还没给。此时需要凭证的规则正确地返回
    # `need_clarification`（要客户先给凭证），而不是替客户假设他已经给了。
    evidence_provided: Mapped[bool] = mapped_column(Boolean, default=False)
    # ★ 我们**正在等客户回答什么**（`ask_customer` 写入），`""` = 没有待答问题。
    #
    # 为什么需要它：政策里有六处会返回 `need_clarification`（R8/R9/R10/R11
    # 缺凭证、缺原因、缺金额、缺现价），而系统此前**没有任何字段能表达
    # "我已经问了客户，正在等他"**。于是这些 case 的终态只能是 `open`，
    # 而 verifier 只认 `basis == "ambiguous"` 一种澄清依据
    # （`graph/nodes.py` 的机器断言）—— 订单已关联的 need_clarification
    # 一律被判成"工单仍为 open，但没有任何需澄清的依据，可能存在漏处置"。
    # 这是 #2 要补的缺口：**不是 agent 做错了，是系统说不出它在等什么。**
    #
    # 为什么不新增一个 `awaiting_customer` 状态值：`status` 是唯一参与终态
    # 判定的字段（`snapshot.TICKET_FIELDS`），加一个值要同时改 golden、
    # diff、verifier、`_finalize` 四处，而"问了、客户还没答"时工单**确实
    # 就是 open**。所以状态留在 open，把"在等什么"记在这里。
    pending_question: Mapped[str] = mapped_column(Text, default="")
    # 客户**答复了什么**（`submit_customer_reply` 写入，第二轮的事实来源）。
    #
    # 与 `pending_question` 配对：一个记"我们问了什么"，一个记"客户答了
    # 什么"。它本身不参与判定（进 `TICKET_OBSERVED_FIELDS`），存在的意义
    # 是让第二轮有据可查 —— 审计与复盘时能看出"结论是从哪句话推出来的"。
    customer_reply: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="tickets")


class AuditLog(Base):
    """每一次状态变更都写这里。

    两个用途：
    1. 评测归因 —— 失败时能看出是哪一步走错了
    2. 禁止动作检测 —— 越权退款/非法类目退款都在这里留痕
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # actor: 角色名（triage/resolver/verifier）或 human / system
    actor: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class PolicyArticle(Base):
    """售后规则文档的切块结果，作为 RAG 检索源。"""

    __tablename__ = "policy_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc: Mapped[str] = mapped_column(String(128), default="policies")
    section: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # 规则编号（R1..R6），方便评测时定位命中哪条规则
    rule_id: Mapped[str] = mapped_column(String(16), default="")


class DatasetMeta(Base):
    """数据集元信息（键值表）。目前只存一个键：`anchor_at`。

    ★ 为什么这张表存在（缺陷 ⑨）：

    合成数据的时间戳是**相对造数那一刻**生成的，而 R1 判定"签收几天"
    用的是**读数据那一刻**的墙上时钟。两个"现在"不是同一个基准，
    于是数据每多放一天，窗口边界上的订单就翻一次 —— 同一个 seed
    在两天里跑出两份不同的评测集。1 条 case 换桶还会被分层抽样放大成
    "换掉一批 case"（实测 +12 小时就换掉 30 条里的 8 条）。

    修法是把"造数那一刻"冻进这张表，决策侧统一读它（见 `clock.py`）。
    真实业务里这张表可以是空的 —— 没有锚点时 `clock.now()` 回落到
    墙上时钟，那正是生产语义。
    """

    __tablename__ = "dataset_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
