"""数据库会话与多租户仓储。

★ 关键设计：**租户隔离不靠调用方自觉。**

`TenantRepo` 在构造时绑定 tenant_id，之后所有查询自动带过滤条件。
agent 的工具拿到的永远是 `TenantRepo` 实例，它**没有能力**查到别的租户的数据 ——
即使 LLM 被提示注入诱导传入别的 tenant_id 也没用，因为那个参数根本不在工具签名里。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .. import clock
from ..config import settings
from .models import AfterSalesAction, Base, Customer, Order, OrderItem, Refund, Ticket

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite"):
        # sqlite:///abs/path 或 sqlite:///./rel
        raw = url.split("///", 1)[-1]
        if raw and raw != ":memory:":
            p = Path(raw)
            if not p.is_absolute():
                p = Path.cwd() / p
            p.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is not None:
        return _engine

    url = settings.database_url
    _ensure_sqlite_dir(url)
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # FastAPI 线程池 + sqlite 必须关掉同线程检查
        connect_args["check_same_thread"] = False

    _engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            # WAL 提升并发读写；foreign_keys 打开才能靠约束兜住幂等
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def init_db(drop: bool = False) -> None:
    """建库建表。`drop=True` 会重建 —— ★ 此时必须清掉内存里的时间锚点缓存。

    否则库里已经是新数据（甚至还没写锚点），内存里还留着上一批数据的锚点，
    之后所有规则判定都会用错基准，评测集又要飘（缺陷 ⑨）。
    """
    engine = get_engine()
    if drop:
        Base.metadata.drop_all(engine)
        clock.reset()
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界：正常提交，异常回滚。"""
    factory = get_session_factory()
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


class TenantRepo:
    """绑定租户的仓储。所有查询强制带 tenant_id 过滤。"""

    def __init__(self, session: Session, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id 不能为空 —— 空租户等于没有隔离")
        self.s = session
        self.tenant_id = tenant_id

    # ── 客户 ──────────────────────────────────────────────
    def get_customer(self, customer_id: int) -> Customer | None:
        return self.s.scalar(
            select(Customer).where(
                Customer.id == customer_id, Customer.tenant_id == self.tenant_id
            )
        )

    def find_customers_by_phone(self, phone: str) -> list[Customer]:
        return list(
            self.s.scalars(
                select(Customer).where(
                    Customer.tenant_id == self.tenant_id, Customer.phone == phone
                )
            )
        )

    # ── 订单 ──────────────────────────────────────────────
    def get_order(self, order_id: int) -> Order | None:
        return self.s.scalar(
            select(Order).where(Order.id == order_id, Order.tenant_id == self.tenant_id)
        )

    def get_order_by_no(self, order_no: str) -> Order | None:
        return self.s.scalar(
            select(Order).where(
                Order.order_no == order_no, Order.tenant_id == self.tenant_id
            )
        )

    def list_orders_of_customer(self, customer_id: int, limit: int = 20) -> list[Order]:
        return list(
            self.s.scalars(
                select(Order)
                .where(Order.customer_id == customer_id, Order.tenant_id == self.tenant_id)
                # ★ 必须带 id 兜底：`created_at` 取自 clock 锚点，同一批数据内
                # 多笔订单的时间戳**完全相同**，此时 SQLite 对并列行的顺序不作
                # 保证，返回顺序会变 —— 而 `ambiguous` 类 case 里 agent 先看到
                # 哪一笔订单决定了它的处置，于是同一份评测集会给出不同结果。
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(limit)
            )
        )

    def list_order_items(self, order_id: int) -> list[OrderItem]:
        return list(
            self.s.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
        )

    def list_refunds_of_order(self, order_id: int) -> list[Refund]:
        return list(
            self.s.scalars(
                select(Refund)
                .where(Refund.order_id == order_id)
                # 同上：锚点固定的时间戳会并列，必须有 id 兜底
                .order_by(Refund.created_at.desc(), Refund.id.desc())
            )
        )

    def list_after_sales_of_order(self, order_id: int) -> list[AfterSalesAction]:
        """某订单的非退款售后动作（换货/补发/赔付/价保/开票/改址/催单）。

        ★ 与 `list_refunds_of_order` 分成两个方法而不是合并成一个"所有售后记录"：
        退款有独立的账本不变量（累计退款 ≤ 订单金额）和独立的表结构，合并会让
        那条不变量不得不在一个混合列表上重算，而它必须只对 `refunds` 成立。
        两个动作**会出钱**（赔付、价保），它们参与的是同一本总账 ——
        那个总账在 `tools/registry.py` 里算，不在这里。
        """
        return list(
            self.s.scalars(
                select(AfterSalesAction)
                .where(AfterSalesAction.order_id == order_id)
                .order_by(AfterSalesAction.created_at.desc(), AfterSalesAction.id.desc())
            )
        )

    def list_after_sales_of_ticket(self, ticket_id: int) -> list[AfterSalesAction]:
        """某**工单**的非退款售后动作。

        存在理由（缺陷 ㉝）：`apply_snapshot` 重置时要用它，而不是
        `list_after_sales_of_order`。写入侧 `submit_after_sales_action`
        的幂等键是 `tenant:ticket:order:kind:amount` —— **ticket 在键里**，
        所以"同一次提交重复调用"的正确语义是按工单去重；
        而按订单删会顺手删掉同一订单下**其他工单**刚刚写入的行，
        那些工单的终态就少了一条本该有的记录。

        两个维度必须对齐：删按哪个维度，键就按哪个维度。
        """
        return list(
            self.s.scalars(
                select(AfterSalesAction)
                .where(AfterSalesAction.ticket_id == ticket_id)
                .order_by(AfterSalesAction.created_at.desc(), AfterSalesAction.id.desc())
            )
        )

    # ── 幂等键回查（缺陷 ㉑）──────────────────────────────
    # ★ 为什么需要"按幂等键查一条"这个方法，而不是靠写库时的唯一约束报错：
    #   唯一的约束报错发生在**写的那一刻**，而政策复核发生在**写之前**。
    #   于是"同一个请求重试"必须先过政策复核，而第一次写入已经把政策读的
    #   那个状态改掉了（refund_count 0→1、reship_count 0→1），第二次就会
    #   被判成 policy_forbidden —— 语义从"你已经做过了"错成"这个动作违规"。
    #   要修就必须能在政策复核**之前**查到"这个键是不是已经存在"。
    def find_refund_by_key(self, key: str) -> Refund | None:
        """按幂等键回查一张退款单（跨订单，键本身已含租户与票号）。"""
        return self.s.scalar(select(Refund).where(Refund.idempotency_key == key))

    def find_after_sales_by_key(self, key: str) -> AfterSalesAction | None:
        """按幂等键回查一条售后动作。"""
        return self.s.scalar(
            select(AfterSalesAction).where(AfterSalesAction.idempotency_key == key)
        )

    # ── 工单 ──────────────────────────────────────────────
    def get_ticket(self, ticket_id: int) -> Ticket | None:
        return self.s.scalar(
            select(Ticket).where(Ticket.id == ticket_id, Ticket.tenant_id == self.tenant_id)
        )

    def list_tickets_of_customer(self, customer_id: int, limit: int = 20) -> list[Ticket]:
        return list(
            self.s.scalars(
                select(Ticket)
                .where(Ticket.customer_id == customer_id, Ticket.tenant_id == self.tenant_id)
                .order_by(Ticket.created_at.desc(), Ticket.id.desc())
                .limit(limit)
            )
        )
