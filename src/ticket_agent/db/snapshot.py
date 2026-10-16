"""状态快照与终态比对 —— 本项目评测的地基。

τ-bench 的核心思想：**不看 agent 说了什么，看它做完之后数据库状态对不对。**

这里的实现比 τ-bench 更细一层：
- 先导出「关键业务状态」的归一化字典（订单状态、退款列表、工单状态）
- 目标状态（golden）由人工处置过的历史工单导出
- 对比时只比**业务语义**，不比自增 id、不比时间戳精度、金额比到分
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from .models import AfterSalesAction, Order, Refund, Ticket
from .session import TenantRepo

# 快照里包含哪些字段
#
# ★ 分两组，这个区分很重要：
#   *_FIELDS          → **参与终态判定**，不一致就是 agent 做错了
#   *_OBSERVED_FIELDS → **只记录、不上判定台**，用于报告里做诊断分析
#
# 为什么要分开：`intent` 曾是判定字段，但全项目没有任何工具能写它
#（`registry.py` 只在 `get_ticket` 里读过它，grep 无赋值处），
# 而 seed 时它是空串 → 每一条 case 都注定在 intent 上失败，
# 报出来的"准确率"里混进了一个与被测能力无关的恒假项。
# 更本质的理由：intent 是 agent 自拟的自由文本标签，政策文档从未规定它的取值词表，
# 拿它跟一个种子值比大小是**没有契约的比对**。
# 终态要判的是业务后果 —— 订单状态、退款记录、工单是否关闭；
# intent 只说明"agent 认为这是什么问题"，留着看分布，但不该决定对错。
ORDER_FIELDS = ("status", "refund_count", "amount", "category")
ORDER_FLOAT_FIELDS = {"amount"}
REFUND_FIELDS = ("status", "amount", "requires_human")
REFUND_FLOAT_FIELDS = {"amount"}
TICKET_FIELDS = ("status",)
# ★ `pending_question` / `customer_reply` 是**只观测不判定**的（#2）。
#
# 为什么不进 TICKET_FIELDS：它们是"对话到了第几轮"的记录，不是业务后果。
# "问了客户什么问题"没有唯一正确答案 —— 同一件事可以问"请提供照片"也可以问
# "能拍一下破损处吗"，两种都对。把它送上判定台，就等于拿一句没有契约的
# 自由文本比大小（正是 `intent` 被移出判定字段的同一个理由）。
#
# 但 verifier 需要**结构化地知道**"这单是在等客户"：否则订单已关联的
# need_clarification case 会被机器断言记成"漏处置"（见 models.py 里
# `pending_question` 的注释）。判定的是"有没有在等"这个**布尔事实**，
# 不是"等的具体是哪句话"—— 所以字段进观测组，`nodes.py` 读它的非空性。
#
# ★ `request_kind` / `request_cause` / `request_amount` / `evidence_provided`
#   也必须进观测组，理由是**重置**而不是判定（#2 撞出来的真缺口）：
#   `apply_snapshot` 按快照的 ticket 键逐字段 setattr 还原世界，而快照的
#   ticket 键就是这两个元组的并集。这四个字段不进观测组 → 它们从来没有
#   被导出过 → 重置永远还原不了它们 → 上一轮跑过的 `apply_customer_reply`
#   把 `evidence_provided` 置成 True 之后，**下一条 case 的重置无法把它改回
#   False**，于是"凭证没给"的工单从第二轮起全都判成"凭证已给"。
#
#   这是缺陷 ③（比对一个从未被写入的字段）的**镜像同形**：
#   ③ 是"判定台读了快照里没有的字段"，这里是"重置还原不了快照里没有的字段"。
#   同一个病根：字段只在一边注册，另一边不知道它存在。
#
#   为什么它们依然**不进** TICKET_FIELDS：turn-2 golden 的 ticket 字典只有
#   status/intent（`_decide_golden` 的产物），若这四个进判定组，
#   每一条 case 都会报"期望 ''，实际 'quality'"这种与能力无关的恒假差异。
#   它们进观测组只为两件事：① 重置能还原；② 报表能诊断"这条 case 到底缺什么"。
TICKET_OBSERVED_FIELDS = (
    "intent",
    "priority",
    "pending_question",
    "customer_reply",
    "request_kind",
    "request_cause",
    "request_amount",
    "evidence_provided",
)

# ── 非退款售后动作（⑦ 换货 / 补发 / 赔付 / 价保 / 开票 / 改址 / 催单）──
#
# ★ 为什么它必须参与终态判定（而不是只观测）：
#   如果 agent 对"客户要补发"的工单什么都没做，只是把工单关了 —— 只要工单
#   终态恰好蒙对，旧判据就会判它通过。而"补发了一件商品"是一个**业务后果**，
#   与"退了一笔钱"同级，必须能被比对看见。
#
# ★ 为什么用 `(kind, status, amount)` 三元组而不是整行比较：
#   `reason` 是 agent 拟的自由文本（和 `ticket.intent` 同一性质，见文件头那段
#   讨论）—— 政策从未规定它的取值词表，拿它比大小是没有契约的比对。
#   `detail` 同理（新地址、发票抬头都是自由文本）。判定只认**有契约的三个量**：
#   做了什么（kind）、做成了没有（status）、付了多少钱（amount）。
AFTER_SALES_FIELDS = ("kind", "status", "amount")
AFTER_SALES_FLOAT_FIELDS = {"amount"}

# 订单上的两个"次数账本"（换货/补发），与 refund_count 同级参与判定。
#
# ★ 加进 ORDER_FIELDS 而不是另开一套判据：
#   `R8` / `R9` 的规则读 `exchange_count` / `reship_count`（"同一订单最多换一次"），
#   意味着它们是**业务状态**而不是日志。如果 golden 因规则判定写了它、而 agent
#   没写（或反之），终态就不一致，评测必须看得见。
#
# ★ 对老 case（纯退款线）没有影响：两条线都不碰这两个计数器，两边恒为 0、相等。
#   代价只有一个 —— `ticket_fingerprint` 会变（导出内容多了两个键），
#   于是旧库里存的指纹全部失配、评测集需要重建。这是**应该发生**的：
#   订单表加了列，数据模型变了，快照结构就该跟着变，而不是让指纹假装没变。
LEDGER_FIELDS = ("exchange_count", "reship_count")
ORDER_FIELDS_WITH_LEDGERS = tuple(ORDER_FIELDS) + tuple(LEDGER_FIELDS)

#: **只进快照、不进判定台**的订单字段。
#:
#: ★ 为什么需要这一组：`apply_snapshot` 按快照的 order 键逐字段 setattr 还原世界，
#:   所以一个字段要被重置还原，**必须先被导出**。而 `last_urge_at` /
#:   `invoice_issued` 恰恰不在 ORDER_FIELDS_WITH_LEDGERS 里 —— 它们一个是 R14
#:   （催单 24 小时冷却）读的状态，一个是 R12（不得重复开票）读的状态，
#:   工具会写它们，快照却从来没有导出过它们。
#:
#:   实测后果（`eval_out/resume-scripted-{single,multi}-s7.md`，k=3 两臂各 1815 run）：
#:     `R14:urge_logistics` 72 条 33.3%、`R12:issue_invoice` 66 条 33.3%
#:     —— 正好是 **1/3**：k=0 那次通过，k=1/k=2 失败。
#:     因为 k=0 跑完 `submit_after_sales_action` 把 `last_urge_at` 刷成当下、
#:     `invoice_issued` 置 True，而 k=1 的重置还原不了这两个字段
#:     → R14 撞上「24 小时冷却期内」、R12 撞上「已开具发票，不得重复开票」
#:     → 都判 reject，而 golden 期望 resolved。
#:
#: ★ 为什么它们仍然**不进** ORDER_FIELDS_WITH_LEDGERS（即不进判定台）：
#:   `last_urge_at` 是时间戳，拿它比大小会把时钟不确定性引进终态比对；
#:   `invoice_issued` 虽是可比的布尔量，但它的业务后果已经由 `after_sales`
#:   表表达（开票记录本身就被比对），再判一次是冗余。
#:   它们进快照只为**一件事：重置能还原**。
#:
#: ★ 形状与 snapshot.py:54-69 记的那次同源：`request_kind` 那四个字段当初
#:   也是「重置还原不了」而不是「判定不到」。字段只在一边注册，另一边不知道它存在。
ORDER_RESET_ONLY_FIELDS = ("last_urge_at", "invoice_issued")


def _observed_ticket_fields() -> tuple[str, ...]:
    """判定字段 + 观测字段，用于导出完整快照。"""
    return tuple(TICKET_FIELDS) + tuple(TICKET_OBSERVED_FIELDS)


def _round_float(v: Any, tolerance: float) -> Any:
    """按容差取整，避免 499.99999 vs 500.00 被判成不同。"""
    if isinstance(v, float):
        # 容差 0.01 元 → 保留 2 位
        digits = max(0, len(str(tolerance).split(".")[-1])) if tolerance < 1 else 0
        return round(v, digits)
    return v


def classify_order_link(
    linked_order: Order | None,
    orders: list[Order],
) -> tuple[Order | None, str]:
    """工单指向哪笔订单 —— `resolve_ticket_order` 的**纯函数内核**。

    ★ 为什么内核算成纯函数、而不是让第二个调用方自己再写一遍：

    造数侧（`seed.seed_all`）必须在工单**落库之前**就知道"这条工单将来会被
    解析成哪笔订单、因而落在哪一层难度" —— 它要用这个结论去挑话术。如果它
    自己再写一遍判断，就回到了下面那条老路上：同一件事实现三遍、结论不同。

    `linked_order` 是 `ticket.order_id` 指向的订单（没有则为 None），由调用方
    取好传入 —— 这样纯函数不依赖 repo，两个调用方共用同一套判据。
    """
    if linked_order is not None:
        return linked_order, "linked"
    if len(orders) == 1:
        return orders[0], "sole_order"
    if not orders:
        return None, "no_order"
    return None, "ambiguous"


def classify_level(order: Order | None, basis: str) -> str:
    """订单侧状态 → 难度层。返回 `"L1"` / `"L2"` / `"L3"`。

    ★ 这个函数存在的理由和 `classify_order_link` 是同一条：**让"这条 case 落在
    哪一层"只有一个来源**。

    原先 `level` 被决定了两次且结论常常不同 —— 造数侧 `seed.seed_all` 用
    `rng.choices(["L1","L2","L3"], weights=[40,45,15])` 随机抽一个，评测侧
    `seed.build_eval_cases` 又由订单状态重推一遍。同一个量、两处判据，于是
    "case 里记的难度层"与"正文实际所属的层"可以不一致：报表上 `defect` 话术
    占 43.0%，而语料里只有 2.6%（放大 18.7 倍）。

    判据本身只有三行，且**刻意保守**：

    - `no_order` / `ambiguous` → L3。这两种 basis 的正解不是"办某件事"而是
      "先问清楚"（`ambiguous` 保持 open 反问澄清、`no_order` 转人工），
      所以最考的是意图理解。
    - 订单在途或未签收 → L2。规则要算时效与物流状态（R3 拦截 / R1 超期）。
    - 其余 → L1。规则直接命中。

    它不看工单正文，也不看风格 —— `level` 问的是"要 agent 做多难的事"，
    那件事由订单数据决定；`style`（客户怎么讲话）是另一个维度。
    """
    if basis in ("no_order", "ambiguous"):
        return "L3"
    if order is not None and (order.status == "shipped" or order.signed_at is None):
        return "L2"
    return "L1"


def resolve_ticket_order(repo: TenantRepo, ticket: Ticket) -> tuple[Order | None, str]:
    """工单到底指向哪一笔订单。返回 `(order, basis)`。

    ★ 这个函数存在的唯一理由是**消除一处会让评测失真的重复判断**。

    早前"工单关联哪笔订单"这件事被实现了三遍，且结论不同：
      1. agent 侧（`scripted._policy`）：未关联时看候选，**恰好 1 笔就认定它**
      2. golden（`seed._decide_golden`）：未关联就一律"保持 open，等澄清"
      3. 快照（`export_snapshot`）：只看 `ticket.order_id`，未关联就导出 `order: None`

    三者不一致的后果是**评测不可赢**：客户名下只有一笔订单时根本不存在歧义，
    处置它才是正确行为，但 golden 判它错；更糟的是即使 agent 处置了，
    第 3 点也让终态比对看不见那笔订单，反而报"期望无订单关联，实际存在"。

    basis 的四种取值：
    - `linked`     — 工单显式关联了订单
    - `sole_order` — 未关联，但该客户名下**恰好一笔**，无歧义，可认定
    - `ambiguous`  — 未关联且候选 **>1**，**这才是真正需要反问澄清的情形**
    - `no_order`   — 该客户名下没有任何订单，无从处置，应转人工

    ★ 判据本身在 `classify_order_link` 里，这里只负责取数据。造数侧要调的是
    那个纯函数 —— 见它的 docstring。
    """
    linked = repo.get_order(ticket.order_id) if ticket.order_id else None
    return classify_order_link(linked, repo.list_orders_of_customer(ticket.customer_id))


def export_snapshot(session: Session, tenant_id: str, ticket_id: int) -> dict[str, Any]:
    """导出与工单相关的业务状态快照（终态比对的左侧）。

    ★ 观测哪笔订单必须用 `resolve_ticket_order`，与 golden 完全同一套判断 ——
    否则"未关联订单"的工单会出现 golden 按甲订单判、快照按乙订单（或 None）比，
    指标里混进纯粹由不一致产生的假失败。
    """
    repo = TenantRepo(session, tenant_id)
    tol = settings.eval_amount_tolerance_cny

    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        return {"error": "ticket_not_found", "ticket_id": ticket_id}

    snap: dict[str, Any] = {
        "ticket": {
            f: _round_float(getattr(ticket, f, None), tol) for f in _observed_ticket_fields()
        },
        "order": None,
        "refunds": [],
        "after_sales": [],
    }

    order, basis = resolve_ticket_order(repo, ticket)
    snap["order_basis"] = basis
    if order is not None:
        # ★ `ORDER_FIELDS_WITH_LEDGERS + ORDER_RESET_ONLY_FIELDS`：
        #   判定台只认前者（见 diff_snapshots），后者纯粹为了让
        #   `apply_snapshot` 能把 `last_urge_at` / `invoice_issued` 还原回去。
        #   **一个字段要被重置还原，必须先被导出** —— 这句是这一行的全部理由。
        snap["order"] = {
            f: _round_float(getattr(order, f, None), tol)
            for f in tuple(ORDER_FIELDS_WITH_LEDGERS) + tuple(ORDER_RESET_ONLY_FIELDS)
        }
        refunds = sorted(repo.list_refunds_of_order(order.id), key=lambda r: r.id)
        snap["refunds"] = [
            {f: _round_float(getattr(r, f, None), tol) for f in REFUND_FIELDS}
            for r in refunds
        ]
        actions = sorted(repo.list_after_sales_of_order(order.id), key=lambda a: a.id)
        snap["after_sales"] = [
            {f: _round_float(getattr(a, f, None), tol) for f in AFTER_SALES_FIELDS}
            for a in actions
        ]
    return snap


def diff_snapshots(
    actual: dict[str, Any], expected: dict[str, Any]
) -> tuple[bool, list[str]]:
    """比对终态。返回 (是否一致, 差异描述列表)。

    比对规则：
    - ticket.status 必须完全一致（intent 只观测、不判定，见文件头说明）
    - order.status 必须一致；refund_count / exchange_count / reship_count 必须一致
    - refunds 按 (status, amount) 排序后做多重集比较 —— 不关心创建顺序
    - after_sales 按 (kind, status, amount) 排序后同样做多重集比较
    """
    diffs: list[str] = []

    a_t, e_t = actual.get("ticket") or {}, expected.get("ticket") or {}
    for f in TICKET_FIELDS:
        if f in e_t and a_t.get(f) != e_t.get(f):
            diffs.append(f"ticket.{f}: 期望 {e_t.get(f)!r}，实际 {a_t.get(f)!r}")

    a_o, e_o = actual.get("order"), expected.get("order")
    if e_o is not None:
        if a_o is None:
            diffs.append("order: 期望存在订单快照，实际缺失")
        else:
            for f in ORDER_FIELDS_WITH_LEDGERS:
                if f in e_o and a_o.get(f) != e_o.get(f):
                    diffs.append(f"order.{f}: 期望 {e_o.get(f)!r}，实际 {a_o.get(f)!r}")
    elif a_o is not None:
        diffs.append("order: 期望无订单关联，实际存在")

    # 退款多重集比较：顺序无关，但数量与内容必须一致
    def _key(r: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(r.get(f) for f in REFUND_FIELDS)

    a_r = sorted([_key(r) for r in (actual.get("refunds") or [])])
    e_r = sorted([_key(r) for r in (expected.get("refunds") or [])])
    if a_r != e_r:
        diffs.append(f"refunds: 期望 {e_r}，实际 {a_r}")

    # 非退款售后动作的多重集比较：同一套语义（顺序无关、内容必须一致）
    def _a_key(a: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(a.get(f) for f in AFTER_SALES_FIELDS)

    a_s = sorted([_a_key(a) for a in (actual.get("after_sales") or [])])
    e_s = sorted([_a_key(a) for a in (expected.get("after_sales") or [])])
    if a_s != e_s:
        diffs.append(f"after_sales: 期望 {e_s}，实际 {a_s}")

    return (not diffs), diffs


def apply_snapshot(session: Session, tenant_id: str, ticket_id: int, snap: dict[str, Any]) -> None:
    """把快照写回数据库。

    两个用途：
    1. **评测前置**：每条 case 跑之前把库重置到「初始状态」，保证可复现
    2. **golden 造数**：用人工处置结果构造目标状态，再导出成 expected
    """
    repo = TenantRepo(session, tenant_id)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"ticket {ticket_id} 不存在（tenant={tenant_id}）")

    t_snap = snap.get("ticket") or {}
    for f, v in t_snap.items():
        if hasattr(ticket, f):
            setattr(ticket, f, v)

    o_snap = snap.get("order")
    order, _basis = resolve_ticket_order(repo, ticket)
    if o_snap is not None and order is not None:
        for f, v in o_snap.items():
            if hasattr(order, f):
                setattr(order, f, v)

        # 退款记录按快照重建：先删后建，保证幂等
        existing = repo.list_refunds_of_order(order.id)
        want = o_snap.get("refund_count")
        if want is not None or "refunds" in snap:
            for r in existing:
                session.delete(r)
            session.flush()
            for i, r_snap in enumerate(snap.get("refunds") or []):
                session.add(
                    Refund(
                        order_id=order.id,
                        ticket_id=ticket.id,
                        amount=float(r_snap.get("amount", 0.0)),
                        reason=str(r_snap.get("reason", "snapshot")),
                        status=str(r_snap.get("status", "requested")),
                        requires_human=bool(r_snap.get("requires_human", False)),
                        idempotency_key=f"snap-{ticket_id}-{i}-{hashlib.md5(json.dumps(r_snap, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]}",
                    )
                )

        # 非退款售后动作同样按快照重建（换货/补发/赔付/价保/开票/改址/催单）。
        # ★ 与退款分开的两个块而不是合并成一个循环：两张表的字段不同，
        #   合并需要到处 if kind == ... 分支，那正是本文件开头警告的形状。
        #
        # ★★ 缺陷 ㉝：这里原来按 **order** 删（`list_after_sales_of_order`），
        #   而写入侧 `submit_after_sales_action` 的幂等键只含
        #   `tenant:ticket:order:kind:amount` —— **不含 cause**。
        #   于是同一个工单、同一个订单、同一种 kind、同一个金额，
        #   但 cause 不同的两次提交会算出**同一个键**：
        #   第二次被幂等挡下返回 duplicate，而**第一次已经落库**。
        #   更糟的是按 order 删会把同一订单下**别的工单**刚写的行一起删掉，
        #   于是那条工单的终态少了它本该有的记录。
        #   两个维度（删按 order、键按 ticket+order）不一致，
        #   症状是 `after_sales: 期望 1 条，实际 2 条` 或反过来少一条。
        #   修法：删也按 ticket 限定，与写入侧的键同一维度。
        if "after_sales" in snap:
            for a in repo.list_after_sales_of_ticket(ticket.id):
                session.delete(a)
            session.flush()
            for i, a_snap in enumerate(snap.get("after_sales") or []):
                session.add(
                    AfterSalesAction(
                        order_id=order.id,
                        ticket_id=ticket.id,
                        kind=str(a_snap.get("kind", "exchange")),
                        amount=float(a_snap.get("amount", 0.0)),
                        reason=str(a_snap.get("reason", "snapshot")),
                        status=str(a_snap.get("status", "requested")),
                        requires_human=bool(a_snap.get("requires_human", False)),
                        detail=dict(a_snap.get("detail") or {}),
                        idempotency_key=f"snap-a-{ticket_id}-{i}-{hashlib.md5(json.dumps(a_snap, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]}",
                    )
                )
    session.flush()


def ticket_fingerprint(session: Session, tenant_id: str, ticket_id: int) -> str:
    """初始状态的指纹。

    用途：评测集版本校验 —— 如果数据库里的初始状态被改过，指纹就变了，
    harness 会拒绝跑这条 case，避免"评测集悄悄漂移"。
    """
    snap = export_snapshot(session, tenant_id, ticket_id)
    # ★ `default=str`：`ORDER_RESET_ONLY_FIELDS` 把 `last_urge_at`（datetime）
    #   引进了快照，而裸 `json.dumps` 撞上 datetime 会抛
    #   `TypeError: Object of type datetime is not JSON serializable`。
    #   它会被 `run_case` 的 `except Exception` 吞成「重置异常」——
    #   于是所有初始态带 `last_urge_at` 的 case 集体变红，
    #   而红的理由与代码无关（R14:reject 那 231 条的 seed 冷却态正是这种）。
    #   指纹只做版本校验，`str(datetime)` 对同一值确定，够用。
    blob = json.dumps(snap, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def order_state_fingerprint(order: Order, refunds: list[Refund], ticket: Ticket | None = None) -> str:
    """单个订单的状态指纹，用于快照 diff 与回归比对。"""
    payload = {
        "order": {f: getattr(order, f, None) for f in ORDER_FIELDS},
        "refunds": sorted(
            [{f: getattr(r, f, None) for f in REFUND_FIELDS} for r in refunds],
            key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
        ),
        "ticket": {f: getattr(ticket, f, None) for f in _observed_ticket_fields()}
        if ticket
        else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
