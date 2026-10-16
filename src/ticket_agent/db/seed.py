"""合成业务数据 + 从历史工单构造评测集。

为什么要有这个模块：
1. **没有真实工单系统也能开工** —— 200 个客户 / 90 天数据，几秒生成
2. **golden 可控** —— 每条评测 case 的目标状态是我们自己"人工处置"出来的，
   这样才能保证 ground truth 一定正确
3. **分布可控** —— 风控分、投诉次数、类目比例都能调，方便做分层评测

真实场景替换点：把 `seed_all()` 换成从你的工单系统导出 + `apply_snapshot()` 导入即可，
评测层完全不用改。
"""

from __future__ import annotations

import datetime as dt
import math
import random
import re
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..probes import PROBE_NAMES, UNIVERSAL_PROBES
from ..rules import PROOF_CAUSES, decide, decide_for_order, expected_action_for
from . import corpus
from .models import (
    NON_REFUNDABLE_CATEGORIES,
    NON_RETURNABLE_CATEGORIES,
    Customer,
    Order,
    OrderItem,
    PolicyArticle,
    Refund,
    Ticket,
)
from .meta import write_anchor
from .session import TenantRepo, init_db, session_scope
from .snapshot import (
    classify_level,
    classify_order_link,
    export_snapshot,
    resolve_ticket_order,
)

CATEGORIES = ["服饰", "鞋靴", "数码", "家居", "美妆", "内衣", "定制", "数字商品", "生鲜", "图书"]
SKU_NAMES = {
    "服饰": ["纯棉圆领T恤", "休闲直筒长裤", "羊毛混纺外套", "针织开衫"],
    "鞋靴": ["轻量跑步鞋", "真皮休闲皮鞋", "帆布低帮鞋"],
    "数码": ["蓝牙耳机", "65W 氮化镓充电器", "机械键盘", "便携显示器"],
    "家居": ["乳胶枕", "四件套床品", "收纳箱三件套"],
    "美妆": ["氨基酸洁面", "保湿精华液", "防晒喷雾"],
    "内衣": ["无痕内衣套装", "莫代尔家居服"],
    "定制": ["刻字皮带（定制）", "定制尺寸衬衫"],
    "数字商品": ["云存储年卡", "在线课程兑换码"],
    "生鲜": ["智利车厘子 2kg", "冰鲜三文鱼 300g"],
    "图书": ["《分布式系统设计》", "《数据密集型应用系统设计》"],
}
TENANTS = ["acme", "globex"]

# ★ 工单正文的来源：真实语料，不是这 9 条模板。
#
# 下面这份 `TICKET_TEMPLATES` 保留为**兜底**，只在 `data/raw/` 下的语料
# 缺失时使用（`corpus.pick()` 返回 None 的那条路径）。它已经不是主路径。
#
# 为什么必须换掉它：9 句模板配上"造 2000 客户"意味着同一句话在评测集里
# 出现上百次，那"准确率"衡量的就越来越像"agent 有没有记住这 9 句"，
# 而不是"它能不能读懂客户在说什么"。这是本项目所有指标里最容易被质疑
# 的一点，也是唯一一个改代码解决不了的问题 —— 多样性只能来自真实语料。
#
# 语料提供**语言**，不提供**正确答案**：正误仍由 `rules.decide()` 推导。
# 详见 `corpus.py` 的模块文档。
TICKET_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "L1": [
        ("退款进度咨询", "我申请的退款什么时候能到账？"),
        ("物流查询", "我的包裹现在到哪里了？"),
        ("发票问题", "我需要这笔订单的发票，怎么开？"),
    ],
    "L2": [
        ("申请退货", "我买的衣服不合身，想退货。"),
        ("商品质量问题", "收到的鞋子有开胶，我要退款。"),
        ("未收到货", "显示已发货但我一直没收到，怎么处理？"),
    ],
    "L3": [
        ("我要退款", "我要退款。"),
        ("有问题", "这个东西有问题，你们看着办。"),
        ("申请售后", "麻烦帮我处理一下售后。"),
    ],
}


def _pick_ticket_text(
    rng: random.Random,
    level: str,
    *,
    predicate: Callable[[str], bool] | None = None,
    kinds: frozenset[str] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """按业务层取一条工单正文：(subject, body, 语料元信息)。

    真实语料优先；语料缺失（`data/raw/` 没下载）时退回 9 条内置模板，
    此时元信息里的 `source` 是 `"builtin"` —— 这样**报表能看出这次跑的
    是不是真语料**，而不是靠人记得有没有下过文件。

    `predicate` 是对**正文**的额外约束（收一个 `str`，返回是否可用），
    用于让正文与订单相容，例如"这句话预设货已到手，而这个客户只有
    未付款的订单"。谓词只作用于语料；内置模板太短、无所谓前提，
    故不受它约束。抽不到合用的就返回原样，由调用方决定怎么办 ——
    静默换成不通顺的句子比留下一个矛盾样本更糟。

    `kinds` 是**要造哪一类诉求**（缺陷 ㉙）。传了它，抽到的话术必须
    真的属于这几类之一 —— 见 `corpus.pick` 的说明。它只作用于语料；
    内置模板那 9 句是兜底路径，不做筛选。
    """
    item = corpus.pick(rng, level, predicate=predicate, kinds=kinds)
    if item is not None:
        return item.subject, item.body, {
            "source": "corpus",
            "scene": item.scene,
            "tone": item.tone,
            "style": item.style,
        }
    subject, body = rng.choice(TICKET_TEMPLATES[level])
    return subject, body, {"source": "builtin", "scene": "", "tone": "", "style": ""}


#: 指定诉求的抽样比例与候选（缺陷 ㉙）。
#:
#: ★ 为什么需要它：改之前，工单诉求完全等于"随机抽到的那句话的诉求"，
#:   也就是**语料的自然分布**。实测 `compensate` 0.6%、`price_protect` 2.2%，
#:   而一条 40 case 的评测集里，0.6% 的诉求抽到的期望条数是 0.24 条 ——
#:   **绝大多数时候是 0 条，而 0 条 case 与"规则没写"在报表上完全同形。**
#:
#: ★ 只给一部分工单指定诉求，不是全部：
#:   真实流量本来就该是自然分布，全部指定会把评测集做成"每种诉求等量"，
#:   那是另一种失真。指定是为了**保证覆盖**，不是为了改分布。
#:
#: ★ 候选只列"自然分布里会消失"的那几类。`refund` / `urge` / `return_refund`
#:   本来就占大头（52.8% / 23.9% / 10.1%），不需要帮它们。
_KIND_FORCE_SHARE = 0.20
_KIND_FORCE_CANDIDATES: tuple[str, ...] = (
    "compensate",       # 0.6% —— 不帮就恒为 0 条 case
    "price_protect",    # 2.2%
    "exchange",         # 2.2%
    "address_change",   # 1.9%
    "invoice",          # 2.2%
    "reship",           # 4.1%
)

#: 决定"这条工单要不要指定诉求"的独立随机流。
#:
#: ★ 与 `_PRICE_RNG` 同一个理由：本函数是**后加**的。若它去消耗主 `rng`，
#:   整条流位移、同 seed 的数据集全变、历史报表不再可复现。
_KIND_RNG = random.Random(0x5EED_1CE5)


def _maybe_forced_kind(rng: random.Random) -> str | None:
    """要不要给这条工单**指定诉求**；返回 None 表示保持自然分布。

    用 `_KIND_RNG`（独立流）决定"指不指定"与"指定哪一类"，
    **但把决定权交给调用方传入的 `rng` 决定"抽中哪一句"** ——
    两件事分开，前者不该扰动主随机流。
    """
    if _KIND_RNG.random() >= _KIND_FORCE_SHARE:
        return None
    return _KIND_RNG.choice(_KIND_FORCE_CANDIDATES)


def _rand_amount(rng: random.Random) -> float:
    """订单金额 —— 对数正态，中位数约 90 元。

    ★ 这里原本是 `round(rng.uniform(29, 2500), 2)`，看起来无害，实际是
    **本项目最严重的评测缺陷的源头**：

    均匀分布下 P(金额 > 500) ≈ 75%，而 R4（大额必须转人工）在
    `RULE_PRECEDENCE` 里排在 R2/R3/R1 之前 → **3/4 的订单还没走到别的规则
    就被 R4 拦下**。实测结果：149 笔订单里 108 笔首条命中 R4，
    评测集的 30 条 case 只有 5 种条件，R5/CANCELLED 一条都没有。

    后果不是"覆盖度不够"这么轻：它意味着那份"100% 准确率"是在一个
    **主要只考了"会不会转人工"** 的测试集上拿到的。policy 里 7 条规则，
    5 条从未被任何 case 触及。

    改对数正态的理由是**真实性**，不是为了刷覆盖：日用品零售的订单金额
    本就长尾右偏，中位数在几十到一百元量级；一笔 500 元以上的单子应当是
    少数派，而不是默认情形。阈值规则只有落在长尾上才是"护栏" ——
    一条 75% 情况都命中的规则不是护栏，是主流程。
    """
    return round(min(math.exp(rng.gauss(4.5, 1.0)), 20000.0), 2)


def _as_aware(value: dt.datetime) -> dt.datetime:
    """把可能 naive 的时间戳补成 UTC aware。

    SQLite 不保存时区，取出来的 `signed_at` 是 naive 的，
    直接与 aware 的 `now()` 相减会抛 `TypeError`。
    工具层（`registry.days_since_signed`）已有同样的兜底，这里是第二处。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def _force_price_drop(order: Order) -> None:
    """把一笔订单就地改造成"可价保"的状态（缺陷 ㉚ 的修法配套）。

    ★ 为什么造数器要主动改造数据，而不是"再多造点让随机性撞上"：

      `R11:price_protect` 的成功路径要求**四个条件同时成立** ——
      有现价（29%）× 现价低于下单金额 × 有支付时间 × 在 15 天价保期内。
      实测 924 笔订单里 38 笔合格（4.1%），而每张工单只能关联
      **该客户自己**的订单，一个客户平均才 2.3 笔 —— 于是 16 条
      price_protect 工单里 0 条落得进去。

      关键认识：**造数器的职责是产出能考到每个分支的场景，不是复刻真实分布。**
      "真实分布"这件事由 `_KIND_FORCE_SHARE` 那一层管（只给 20% 的工单指定
      诉求，其余仍是自然分布）。如果造数器连一个可价保的世界都造不出来，
      那条规则就永远不会被验证 —— 而"0 条 case"和"规则写对了"在报表上
      完全同形，这正是本项目反复撞到的那个坑。

      改造只动合成数据，语义上也成立：价保工单**必然**对应
      "这位客户刚买了东西，而它现在降价了"这个处境。

    `paid_at` 回拨到 3 天前：既在 15 天窗口内，又让 R1 的七天退货窗口
    不误伤这条 case（两者用的是不同的时间字段，但都读 `paid_at`）。
    """
    if order.current_price is None:
        order.current_price = round(float(order.amount) * 0.85, 2)
    elif float(order.current_price) >= float(order.amount):
        order.current_price = round(float(order.amount) * 0.85, 2)
    if order.paid_at is None:
        order.paid_at = clock.now() - dt.timedelta(days=3)
    if order.status in ("pending_paid", "cancelled"):
        order.status = "paid"


#: 商品现价专用的独立随机流（缺陷 ㉕ 的修法配套）。
#:
#: ★ 为什么不复用主 `rng`：`current_price` 是**后加**的字段，如果它去消耗
#:   全局随机流，整条流会整体位移 —— 同一份 seed 造出来的订单/工单/
#:   条件分布全部改变，之前所有报表数字不再可复现（实测 `R5:escalate`
#:   这个条件直接从测试数据里消失，两条 HITL 测试随即变红）。
#:   造数是评测的**基准**：基准漂了，历史读数就失去意义。
#:   独立流让 price 只影响 price —— 加一个字段不该重排已有数据。
_PRICE_RNG = random.Random(0x5EED_9A17)


def _reseed_aux_rngs(seed: int) -> None:
    """把辅助随机流重置到本次造数的 seed 上（缺陷 ㉛）。

    ★ 为什么必须做：`_KIND_RNG` / `_PRICE_RNG` 是**模块级全局**，如果只
      构造一次就再也不重置，那么 `seed_all(seed=7)` 调用第二次时，
      它们已经停在上一次的位置 —— **同一个 seed 造出不同的数据**。

      `_PRICE_RNG` 的注释写得很清楚："造数是评测的基准，基准漂了，
      历史读数就失去意义"。而"每次从同一个位置开始"正是基准的一部分：
      只把它做成独立流、却不按 seed 重置，等于只做了一半。

      症状极其隐蔽：`test_service_api.py` 的 HITL 护栏测试依赖这份
      60 客户数据里**恰好**有一条 R6:escalate，而它前面的用例已经消耗过
      `_KIND_RNG` —— 轮到它时随机流漂了，R6 就没了。报错是
      "这次 seed 没造出 R6:escalate 的 case，测试失去意义"，
      看起来像测试太脆，实际是造数不可复现。

      两个流用**不同的派生种子**，避免它们同步同步地走成同一条轨道。
    """
    _KIND_RNG.seed((seed * 0x9E3779B1) ^ 0x5EED_1CE5)
    _PRICE_RNG.seed((seed * 0x85EBCA77) ^ 0x5EED_9A17)


def _rand_dt(rng: random.Random, days_ago_max: int, days_ago_min: int = 0) -> dt.datetime:
    """相对**锚点**往前随机一个时刻。

    ★ 必须读 `clock.now()` 而不是 `dt.datetime.now()`：造数与判定要用
    同一个"现在"，否则数据一放旧就漂（缺陷 ⑨）。
    `seed_all` 在造数前已把锚点设成造数那一刻，所以这里拿到的是固定值。
    """
    d = rng.randint(days_ago_min, days_ago_max)
    return clock.now() - dt.timedelta(
        days=d, hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
    )


def seed_all(
    n_customers: int = 200,
    days: int = 90,
    seed: int = 42,
    tenants: list[str] | None = None,
) -> dict[str, int]:
    """生成合成数据。返回各表行数统计。

    ★ 造数的同时**冻结时间锚点**（缺陷 ⑨）：数据的时间戳是相对"此刻"
    生成的，所以必须把"此刻"记下来，供之后每一次规则判定读取。
    否则数据放旧一天，判定基准就跟着漂一天，同一个 seed 会在两天里
    跑出两份不同的评测集。
    """
    rng = random.Random(seed)
    # ★ 辅助流也要回到本次 seed 的起点（缺陷 ㉛的修法）。
    #   否则连调两次 `seed_all(seed=7)` 会得到两份不同的数据 ——
    #   而"同一个 seed 给同一份数据"是评测的基准本身。
    _reseed_aux_rngs(seed)
    tenants = tenants or TENANTS
    init_db()

    # 锚点 = 造数这一刻。之后 `clock.now()`、`_rand_dt`、`fresh` 判定
    # 读的都是它 —— 造数与判定共用同一个"现在"。
    anchor = dt.datetime.now(dt.timezone.utc)
    clock.set_anchor(anchor)

    stats = {"customers": 0, "orders": 0, "items": 0, "tickets": 0, "policies": 0}

    # ★ 订单号流水计数器（缺陷 ⑪）。
    #
    #   原先订单号是 `f"{租户}{%y%m%d}{rng.randint(1000,9999)}"` ——
    #   「日期 + 4 位随机数」。在 60 客户的小规模下从没出过事，
    #   扩到 1000 客户时直接炸：
    #
    #       sqlite3.IntegrityError: UNIQUE constraint failed:
    #       orders.tenant_id, orders.order_no
    #
    #   不是运气差，是**数学上必然**：同一租户同一天只有 9000 个取值，
    #   而千级造数在 90 天里每天平均产出约 27 笔订单 × 2 个租户。
    #   生日悖论下，单日出碰撞的概率约 3.8%，180 个「租户×日」组合
    #   累积下来命中概率接近 100%。
    #
    #   根子不在随机数位数不够，而在**唯一性靠运气保证**：
    #   正确做法是让唯一性由构造保证 —— 每天一个单调递增的流水号，
    #   和真实订单系统的做法一致。这样无论造多少客户都不会撞。
    #
    #   key = (租户, 日期串)，value = 当天已发出的最大序号。
    #
    #   ★ 但这个计数器**必须从库里已有的单号续号**，不能从 0 开始。
    #     否则"再跑一次造数"（不 drop，比如想给已有库补客户）必然撞：
    #
    #         sqlite3.IntegrityError: UNIQUE constraint failed:
    #         orders.tenant_id, orders.order_no
    #
    #     —— 因为计数器已经从 0001 重新开始了，而库里 AC2608280001 还在。
    #     实测就是重造千级数据时撞上的。唯一性由构造保证，前提是构造
    #     知道**已经发过哪些号**；不读库就只是把撞车概率从"生日悖论"
    #     换成了"必然"。
    order_seq: dict[tuple[str, str], int] = {}

    with session_scope() as s:
        # 续号：扫一遍该租户已有的单号，取出每个「租户×日」的最大序号。
        # 单号形状 `AC2608280004`：前 2 位租户缩写 + 6 位日期 + 4 位序号。
        # 用正则而非切片，是为了**跳过不合形状的历史单号**（比如早先用
        # 随机数生成的那批），而不是把它们的尾部当序号读进来。
        for (existing_tenant, existing_no) in s.execute(
            select(Order.tenant_id, Order.order_no)
        ).all():
            m = re.match(r"^[A-Z]{2}(\d{6})(\d+)$", existing_no or "")
            if not m:
                continue
            key = (existing_tenant, m.group(1))
            seq = int(m.group(2))
            if seq > order_seq.get(key, 0):
                order_seq[key] = seq

    with session_scope() as s:
        for tenant in tenants:
            for i in range(n_customers // max(len(tenants), 1)):
                risk = round(rng.betavariate(2, 6), 3)  # 多数偏低，少数高风险
                cust = Customer(
                    tenant_id=tenant,
                    name=f"客户{tenant[:3]}{i:04d}",
                    phone=f"13{rng.randint(100000000, 999999999)}",
                    risk_score=risk,
                    complaint_count=rng.choices([0, 1, 2, 3, 5], weights=[60, 20, 10, 7, 3])[0],
                    is_blacklisted=rng.random() < 0.03,
                )
                s.add(cust)
                s.flush()
                stats["customers"] += 1

                # ★ 约 6% 的客户**一笔订单都没有** —— 这是 `no_order` 条件的唯一来源。
                #
                #   原先写的是 `range(rng.randint(1, 4))`，即人人至少一单，于是
                #   `resolve_ticket_order` 的 `no_order` 分支和 `_decide_golden` 里
                #   对应的"无从处置 → 转人工"路径**永远不会被执行**。
                #   这在评测报表上表现为"覆盖度缺陷：no_order 一条 case 都没有"
                #   （新增的 `by_condition` + `conditions_missing` 审计查出来的）。
                #
                #   而"注册了但没下过单的客户来提问"是完全真实的场景，正解是
                #   转人工核实而不是凭空处置 —— 一条从未被触发过的分支，
                #   和不存在的分支在评测上是同一回事。
                n_orders = 0 if rng.random() < 0.06 else rng.randint(1, 4)
                for _ in range(n_orders):
                    cat = rng.choices(
                        CATEGORIES,
                        weights=[22, 12, 15, 12, 10, 5, 4, 5, 5, 10],
                        k=1,
                    )[0]
                    amount = _rand_amount(rng)
                    paid_at = _rand_dt(rng, days)
                    # 状态分布：贴合真实（多数已签收）
                    #
                    # ★ `paid`（已支付、尚未发货）是缺陷 ㉖ 补上的：
                    #   原先只有 signed/shipped/pending_paid/cancelled 四种，
                    #   而"钱付了、货还没发"是**日常运营里最常见的一小段窗口**，
                    #   也是改地址（R13 成功路径）与赔付（R10 最常见的现场）
                    #   唯一能成立的状态。
                    #   缺了它，`R13:change_address` 这条条件永远是 0 条 case —
                    #   而 R13 的成功分支写得好好的，只是没有一刻世界长成那样。
                    status = rng.choices(
                        ["signed", "shipped", "paid", "pending_paid", "cancelled"],
                        weights=[62, 15, 10, 10, 3],
                    )[0]
                    # ★ 唯一性由构造保证，不靠随机数撞运气（缺陷 ⑪）。
                    #   同一租户同一天内单调递增，最长 5 位（99999 单/天够用）。
                    day_key = (tenant, f"{paid_at:%y%m%d}")
                    order_seq[day_key] = order_seq.get(day_key, 0) + 1
                    order_no = f"{tenant[:2].upper()}{day_key[1]}{order_seq[day_key]:04d}"
                    order = Order(
                        tenant_id=tenant,
                        customer_id=cust.id,
                        order_no=order_no,
                        category=cat,
                        amount=amount,
                        status=status,
                        paid_at=paid_at,
                        # ★ 只有真的发过货的状态才有发货时间。
                        #   `paid` = 已付未发货，给它写 shipped_at 会让
                        #   "未发货"的订单带一个发货时间戳 —— 而 R13 判
                        #   "能不能改地址"读的正是这个状态，一条自相矛盾的
                        #   记录足以让它给出既非对也非错的结论。
                        shipped_at=(
                            paid_at + dt.timedelta(days=1)
                            if status in ("shipped", "signed")
                            else None
                        ),
                        signed_at=(
                            paid_at + dt.timedelta(days=rng.randint(2, 5))
                            if status == "signed"
                            else None
                        ),
                        # ★ 商品现价：约 30% 的订单做一次价格快照，且**低于**下单金额
                        #   （降价才可能触发价保）。
                        #
                        #   为什么必须有它（缺陷 ㉕）：R11 的守卫是
                        #   `if current_price is None: 返回 need_clarification`，
                        #   而原先**没有任何地方写过这一列** —— 于是 R11 恒判
                        #   "未提供当前售价"，`price_protect` 永远造不出来。
                        #   在评测报表上，"参数永远传不进"和"规则没写"
                        #   完全同形：都是 0 条 case。
                        #
                        #   ★★ 这里刻意用**独立的随机流**，不动全局 `rng`。
                        #     第一次实现时我写成了 `rng.random()`，结果
                        #     **整条随机数流整体位移**：同一份 seed 造出来的
                        #     订单、工单、case 条件分布全变了，之前所有报表
                        #     数字不再可复现，`R5:escalate` 这个条件也从测试
                        #     数据里消失了。
                        #     一个"新加的字段"不该重排已有数据 —— 造数是
                        #     评测的**基准**，基准漂了，历史读数就没有意义。
                        #     独立流让 price 只影响 price，seed 的复现性
                        #     对已有字段依然成立。
                        current_price=(
                            round(amount * _PRICE_RNG.uniform(0.70, 0.95), 2)
                            if _PRICE_RNG.random() < 0.30
                            else None
                        ),
                    )
                    s.add(order)
                    s.flush()
                    stats["orders"] += 1

                    for _ in range(rng.randint(1, 3)):
                        nm = rng.choice(SKU_NAMES[cat])
                        s.add(
                            OrderItem(
                                order_id=order.id,
                                sku=f"{cat[:2]}-{rng.randint(1000, 9999)}",
                                name=nm,
                                category=cat,
                                qty=rng.randint(1, 2),
                                unit_price=round(amount / rng.randint(1, 3), 2),
                            )
                        )
                        stats["items"] += 1

                    # ★ 约 6% 的历史订单已经退过一次款 —— 这是 R5（同一订单最多退一次）
                    #   的触发条件。原先完全不造这类订单，导致 R5 **一条 case 都没有**：
                    #   `_whycoverage.py` 实测 `refund_count >= 1` 的订单为 0。
                    #   一条从未被触发的规则，在报表上和不存在的规则是同一回事。
                    #   退款记录与 refund_count 必须同时写，否则快照自身就不自洽。
                    if rng.random() < 0.06:
                        s.add(
                            Refund(
                                order_id=order.id,
                                ticket_id=None,
                                amount=amount,
                                reason="历史退款（合成数据）",
                                status="approved",
                                requires_human=False,
                                approved_by="seed",
                                idempotency_key=f"seed-{tenant}-{order.order_no}",
                            )
                        )
                        order.refund_count = 1
                        stats["refunds"] = stats.get("refunds", 0) + 1
                        s.flush()

                # 约 60% 客户有工单
                if rng.random() < 0.6:
                    orders = list(
                        s.scalars(select(Order).where(Order.customer_id == cust.id))
                    )

                    # ★ 顺序是这里的关键：**先定订单 → 由订单推难度层 → 再按层挑话术**。
                    #
                    #   上一版是反过来的：先随机抽一个 level，按 level 挑话术，
                    #   订单最后才定。那让 `level` 成了"随机的预言"，而评测侧
                    #   `build_eval_cases` 又由订单状态重推一遍 —— 同一个量被决定
                    #   两次、结论常常不同：case 里记的难度层与正文实际所属的层
                    #   对不上。实测后果是报表上的风格分布失真：`defect` 占
                    #   43.0% 而语料里只有 2.6%（放大 18.7 倍）—— 因为 L1+L2 占
                    #   样本 82%，而它们的首选话术池只有 172 条。
                    #
                    #   语义上也本该如此：`level` 问的是"这条 case 要 agent 做多难
                    #   的事"，那件事由**订单状态**决定；`style` 问的是"客户怎么
                    #   讲话"。让客户怎么讲话反过来决定他名下有哪些订单，是倒置。
                    #
                    #   现在两侧共用 `snapshot.classify_order_link` +
                    #   `snapshot.classify_level`，"这条 case 落在哪一层"只有一个答案。

                    # ★ 工单关联哪笔订单，决定了"这条 case 考哪条规则"，
                    #   所以这里不能均匀随机 —— 两种订单必须各有机会被选中：
                    #
                    #   (a) **已退过款**的订单 → `R5:escalate`（重复退款护栏）
                    #       退款订单只占约 6%，均匀抽样时"工单恰好关联到它"
                    #       几乎不发生：实测 155 笔订单 7 笔有退款、37 条关联工单
                    #       **0 条**落在这些订单上 → R5 恒为 0 条 case。
                    #
                    #   (b) **新近签收**的订单 → `R1:refund`（正常七天无理由退款）
                    #       订单时间跨度 90 天，而七天窗口只覆盖其中一小段，
                    #       均匀抽样时"抽到一笔刚签收的"同样是撞运气。
                    #       而客户来提退货，绝大多数就是刚收到货的时候 ——
                    #       均匀抽样在这里既不真实、又让主流程测不到。
                    #
                    #   (c) **刻意不关联** → `ambiguous` / `sole_order`
                    #       「客户名下有几笔订单、该处理哪一笔」这件事本身就要考。
                    #       上一版是靠"L3 有 70% 不关联"来制造它，而 L3 当时是
                    #       随机抽的；现在反过来 —— 不关联是一个**独立的造数决定**，
                    #       它造成的难度（要澄清）由订单侧推出来。
                    refunded = [o for o in orders if (o.refund_count or 0) >= 1]
                    fresh = [
                        o for o in orders
                        if o.signed_at is not None
                        and (clock.now() - _as_aware(o.signed_at)).days
                        <= settings.return_window_days
                    ]

                    # ★★ 缺陷 ㉙（下半）：**先定诉求，再挑订单**。
                    #
                    #   改之前是反的 —— 订单先定，句子后抽。于是"改收货地址"
                    #   这个诉求**永远**落到已发货订单上（实测 7 条 address_change
                    #   全部落在 shipped 4 / signed 3），而 R13 对已发货订单
                    #   只能 escalate / reject → `R13:change_address` 永远是 0 条 case。
                    #
                    #   更糟的是那 7 条 case **不可赢**：客户说"帮我改地址"，
                    #   而正确的答案是"改不了，已发货" —— 这不是 agent 的错，
                    #   是这个 case 被造坏了。评测集里出现不可赢的 case，
                    #   后果比缺一条 case 更严重：失败归因会被污染。
                    #
                    #   `_maybe_forced_kind` 用独立随机流，所以把它提到这里
                    #   不会扰动主 `rng` —— 但它引发的**分支变化**确实会
                    #   改变后续随机流（不同分支消耗不同数量的随机数）。
                    #   这是**有意的行为改变**，不是意外漂移：
                    #   旧数据集里那部分订单本身就不该配那个诉求。
                    forced_kind = _maybe_forced_kind(rng)

                    # 按诉求优先挑"能让这条 case 有解"的订单状态。
                    # `address_change` 要未发货（R13 的成功分支：paid/pending_paid）；
                    # `urge` 要已发货（催一件没发货的货没有意义）。
                    not_shipped = [
                        o for o in orders if o.status in ("paid", "pending_paid")
                    ]
                    in_transit = [o for o in orders if o.status == "shipped"]

                    # ★ `price_protect` 要一笔**真的能价保**的订单 —— 四个条件缺一不可：
                    #   有现价、现价确实低于下单金额、有支付时间、且在价保期限内。
                    #
                    #   实测（`_ppdiag2.py`，16 条 price_protect 工单）：
                    #     状态可用 11 / **有现价 3** / 确实降价 3 / 有支付时间 12
                    #   仅 29% 订单写了 `current_price`，而**那 3 条有现价的
                    #   全部因为"下单已 37/43 天，超过 15 天价保期限"被驳回**
                    #   → `R11:price_protect` 恒 0 条 case。
                    #
                    #   这和 `address_change` 是**同一个形状**：客户在为一个政策
                    #   明确禁止的诉求打电话，这几条 case 天生不可赢 ——
                    #   不是 agent 判错，是 case 被造坏了。
                    price_dropped = [
                        o for o in orders
                        if o.current_price is not None
                        and float(o.current_price) < float(o.amount)
                        and o.paid_at is not None
                        and (clock.now() - _as_aware(o.paid_at)).days
                        <= settings.price_protect_window_days
                        and o.status not in ("pending_paid", "cancelled")
                    ]

                    # R8 换货要**已签收且未超换货期限**（`settings.exchange_window_days`）。
                    exchangeable = [
                        o for o in orders
                        if o.status == "signed"
                        and o.signed_at is not None
                        and (clock.now() - _as_aware(o.signed_at)).days
                        <= settings.exchange_window_days
                    ]

                    preferred: list[Order] = []
                    if forced_kind == "address_change":
                        preferred = not_shipped
                    elif forced_kind == "urge":
                        preferred = in_transit
                    elif forced_kind == "price_protect":
                        preferred = price_dropped
                    elif forced_kind == "exchange":
                        # R8 换货的成功路径要求**已签收且未超换货期限**，
                        # 否则只能 escalate（在途）/ reject（超期）。
                        # 这是第三个同形状的缺口：`R8:exchange` 在 232 case
                        # 的样本里是 0 条 —— 而它和 address_change 一样，
                        # 不是规则没写，是工单挑错订单。
                        preferred = exchangeable

                    # ★★ `price_protect` 有第二层问题：合格订单**散在 400 个客户
                    #   头上**，而每张工单只能关联"该客户自己"的订单。
                    #   实测（`_ppdiag3.py`）：924 笔订单里 38 笔合格（4.1%），
                    #   但每个客户平均只有 2.3 笔订单 —— 于是 16 条 price_protect
                    #   工单里 **0 条**落进池子。
                    #
                    #   `address_change` 没这个问题，因为它只要一个**状态**
                    #   （paid/pending_paid 占 11%）；价保要四个条件叠加
                    #   （有现价 × 降价 × 有付款时间 × 15 天内），概率相乘就没了。
                    #
                    #   解法不是"再多造点数据"，而是**让造数器造出它要考的世界**：
                    #   造数器的职责是产出能考到每个分支的场景，不是复刻真实分布
                    #   —— 真实分布这件事由 `share` 那一层控制（只给 20% 的工单
                    #   指定诉求，其余仍是自然分布）。
                    #
                    #   所以：该客户名下没有合格订单时，就地**把一笔订单变成合格**。
                    #   这动的是合成数据，不是业务事实 —— 改的是"这位客户恰好
                    #   刚买了东西又在降价"，而这正是价保工单**必然**对应的处境。
                    if forced_kind == "price_protect" and not preferred and orders:
                        cand = next(
                            (o for o in orders if o.paid_at is not None), None
                        ) or orders[0]
                        _force_price_drop(cand)
                        preferred = [cand]

                    linked_order: Order | None
                    if preferred and rng.random() < 0.8:
                        # ★ 0.8 而非 1.0：留 20% 让这个诉求也能落在
                        #   "没有合适订单"的情形上 —— 那正是 R13 的 reject /
                        #   escalate 分支，也是必须被测到的另一半。
                        linked_order = rng.choice(preferred)
                    elif refunded and rng.random() < 0.35:
                        linked_order = rng.choice(refunded)      # (a) 重复退款场景
                    elif fresh and rng.random() < 0.6:
                        linked_order = rng.choice(fresh)         # (b) 新近签收，R1 主路径
                    elif orders and rng.random() < 0.25:
                        linked_order = None                      # (c) 需要澄清
                    else:
                        linked_order = rng.choice(orders) if orders else None

                    # 解析成哪笔订单、落在哪一层 —— 与评测侧同一套判据。
                    resolved, basis = classify_order_link(linked_order, orders)
                    level = classify_level(resolved, basis)

                    # ★ 正文的**事实前提**必须与订单相容。
                    #
                    #   实测（改之前）605 条 case 里 **84 条（13.9%）** 是自相矛盾的：
                    #   「收到的衣服有瑕疵，能帮我处理换货吗？」配一笔
                    #   `pending_paid`（未付款）订单 —— 没付款的订单不可能
                    #   已经收到货。读到这种样本的人只会问"这是造数造错了吧"，
                    #   而不会去看 agent 哪里错了 —— 评测集的自相矛盾会直接
                    #   污染失败归因。
                    #
                    #   现在把它做成**抽取时的约束**，而不是抽完再补救：订单已经
                    #   定了，能不能说"我收到了"就是已知的，直接据此筛话术。
                    #   `resolved is None`（ambiguous / no_order）时**不筛** ——
                    #   客户声称收到了货、库里却查不到已签收订单，这正是真实业务
                    #   里该先问清"是哪一笔"的情形，不是造数错误。
                    #
                    #   `meta["scene"]` / `meta["style"]` 只写进**统计和 case**，
                    #   **不写进 `Ticket.intent`** —— agent 能通过 `get_ticket`
                    #   读到 intent（registry.py:184），把语料 scene 写进去
                    #   就等于把答案标签递给 agent。
                    needs_receipt_ok = resolved is None or resolved.signed_at is not None

                    # ★★ 缺陷 ㉙：**按诉求抽样**，而不是让语料的自然分布决定一切。
                    #
                    #   改之前：`_pick_ticket_text` 只按业务层抽，抽到什么句子
                    #   就得到什么诉求。于是诉求分布 = 语料自然分布，而实测
                    #   `compensate` 只占 0.6%、`price_protect` 更少 ——
                    #   评测集里永远抽不到它们，`R10`/`R11` 恒为 0 条 case。
                    #
                    #   评测集和真实流量的要求是**相反**的：
                    #     · 真实流量要自然分布（那是业务事实）
                    #     · 评测集要按维度覆盖（否则没测到的分支和写对的分支
                    #       在报表上完全同形 —— "0 条 case"和"规则没写"一样绿）
                    #   这正是 `_stratified_sample` 已经在做的事，只是它当时
                    #   只覆盖"条件"这一个维度，诉求维度漏在外面。
                    #
                    #   `forced_kind` 已经在**挑订单之前**定下了（见上），
                    #   这里只是把它变成抽取约束。顺序不能反 —— 诉求决定
                    #   该挑哪笔订单，而不是反过来。
                    kinds = frozenset({forced_kind}) if forced_kind else None

                    # ★ `compensate` 额外要求话术里**带金额**（R10 的第一道守卫）。
                    #
                    #   语料里 `compensate` 只有 264 条，其中带"数字+元"的仅 25 条
                    #   （9%）。不筛的话，绝大多数 compensate 工单的
                    #   `request_amount` 是 0 → R10 判 `need_clarification` →
                    #   `R10:compensate` 这个条件仍然恒为 0 条 case，
                    #   等于我加了动作、加了规则、加了条件，报表上却看不见。
                    #
                    #   R10 的两条成功路径是 compensate（≤30 元）与 escalate（>30 元），
                    #   都要金额；没有金额的 compensate 只能测到 need_clarification
                    #   这一条分支 —— 那一条分支不值得占掉整个诉求的抽样额度。
                    #
                    # ★ 正文与订单的相容性约束：这句话若预设"货已到手"，
                    #   而客户名下只有未付款的订单，就不该用它 —— 那会造出
                    #   一个自相矛盾的样本，agent 无论怎么做都显得"不对"。
                    receipt_predicate = (
                        None if needs_receipt_ok
                        else (lambda text: not corpus.presupposes_receipt(text))
                    )

                    kind_predicate = receipt_predicate
                    if forced_kind == "compensate":
                        _inner = receipt_predicate

                        def _needs_amount(text: str, _inner=_inner) -> bool:
                            if _inner is not None and not _inner(text):
                                return False
                            return corpus.extract_amount(text) > 0

                        kind_predicate = _needs_amount

                    subject, body, text_meta = _pick_ticket_text(
                        rng,
                        level,
                        predicate=kind_predicate,
                        kinds=kinds,
                    )
                    stats["text_source_" + str(text_meta["source"])] = (
                        stats.get("text_source_" + str(text_meta["source"]), 0) + 1
                    )
                    # ★ 客户诉求（要什么）与原因 —— 决定这条工单走哪条业务线。
                    #
                    #   在它出现之前，`decide()` 只能假设"每张售后工单都是退款申请"，
                    #   于是真实语料里占 39.3% 的物流类、5.6% 的补发类丢进来，
                    #   系统也只会去退钱 —— 只有 21.4% 的真实诉求能被接住。
                    #
                    #   分类优先级：语料 scene（真实标注）→ 正文（纯函数、可复现）。
                    #   `classify_request` 判不出时返回 `refund`，
                    #   与改动前行为一致，保证旧 case 的 golden 不被悄悄改样。
                    req_kind = corpus.classify_request(body, str(text_meta.get("scene") or ""))
                    req_cause = corpus.classify_cause(body, req_kind)
                    # ★ 客户自己报的金额（赔付/价保要用）。
                    #   必须与 golden 读**同一个值** —— golden 从这里取，
                    #   agent 从 `get_ticket` 取同一个字段。如果各读各的
                    #   （一边解析正文、一边读字段），两边可能读出不同的数，
                    #   那条 case 就**不可赢**，而报表只会显示"agent 算错了金额"。
                    req_amount = corpus.extract_amount(body)
                    # ★ 客户是否给了凭证（政策 R2/R9/R10 的守卫）。
                    #
                    #   只对**政策需要凭证**的诉求置 True，其余一律 False ——
                    #   否则等于替所有工单假设"客户已经交过照片"，那会让
                    #   需要凭证的规则全部静默通过，永远测不到 need_clarification 分支。
                    #
                    #   阈值取 50%：低于它，需要凭证的条件几乎抽不到 case
                    #   （实测 109 条里客户原话自带凭证信号只有 5.5%）；
                    #   高于它，need_clarification 分支又会被稀释。50% 是让
                    #   两个分支都稳定出现的折中，且它只影响**新造的** case，
                    #   历史报表读的是另一批数据，不会被改。
                    needs_proof = req_cause in PROOF_CAUSES
                    evidence_provided = needs_proof and rng.random() < 0.5
                    stats["request_" + req_kind] = stats.get("request_" + req_kind, 0) + 1
                    if needs_proof:
                        stats["evidence_provided"] = stats.get("evidence_provided", 0) + (
                            1 if evidence_provided else 0
                        )
                        stats["evidence_needed"] = stats.get("evidence_needed", 0) + 1

                    t = Ticket(
                        tenant_id=tenant,
                        customer_id=cust.id,
                        order_id=linked_order.id if linked_order else None,
                        subject=subject,
                        body=body,
                        status="open",
                        intent="",
                        # ★ 刻意写进**独立字段**而不是 `intent`：
                        #   `intent` 是"我们的归档分类"，`request_kind` 是"客户要什么"。
                        #   两者混用会让"识别诉求"与"记录分类"再也分不开。
                        request_kind=req_kind,
                        request_cause=req_cause,
                        request_amount=req_amount,
                        evidence_provided=evidence_provided,
                        created_at=_rand_dt(rng, days, 1),
                    )
                    s.add(t)
                    stats["tickets"] += 1

        stats["policies"] = _seed_policies(s)

    # 锚点落库（跨进程可见：`ticket-agent run` 是另一个进程，
    # 它没有造数时的内存状态，只能去库里读）
    write_anchor(anchor)

    return stats


def _seed_policies(s: Session) -> int:
    """把 prompt/policies.md 切块入 PolicyArticle 表。

    切块策略：按 markdown 二级标题切，绝不跨节合并
    （对齐 AgentScope RAG 的 Parser/Chunker 设计原则）。
    兜底：文件缺失时用内置规则，保证离线可跑。
    """
    policy_file = settings.prompt_dir / "policies.md"
    raw = ""
    if policy_file.exists():
        raw = policy_file.read_text(encoding="utf-8")

    if not raw.strip():
        raw = _BUILTIN_POLICIES

    blocks: list[tuple[str, str, str]] = []  # (rule_id, section, content)
    cur_rule, cur_section, buf = "", "总则", []
    for line in raw.splitlines():
        if line.startswith("## "):
            if buf:
                blocks.append((cur_rule, cur_section, "\n".join(buf).strip()))
            cur_section = line[3:].strip()
            cur_rule = cur_section.split("：")[0].split(":")[0].strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        blocks.append((cur_rule, cur_section, "\n".join(buf).strip()))

    n = 0
    for rule_id, section, content in blocks:
        if not content:
            continue
        s.add(
            PolicyArticle(
                doc="policies", section=section, content=content, rule_id=rule_id or "总则"
            )
        )
        n += 1
    return n


_BUILTIN_POLICIES = """# 售后政策

## R1：七天无理由退货
客户签收商品之日起 7 个自然日内，商品完好的，可申请无理由退货。
超过 7 天的，不予无理由退货，但可协商其他方案。
订单状态为「已发货未签收」的，只能申请物流拦截，不能直接退款。
订单状态为「待付款」的，无退款概念。

## R2：不支持无理由退货的类目
内衣、定制类商品、数字商品（含云存储卡、课程兑换码等虚拟商品）不支持七天无理由退货。
上述类目在签收后如有质量问题，需走质量投诉流程并提供凭证。

## R3：已发货未签收的处理
订单已发货但客户未签收时，不得直接发起退款。
正确动作是发起物流拦截；拦截成功后订单状态变更为已取消，再按取消流程处理。

## R4：需要人工审批的情形
退款金额超过 500 元的，必须转人工审批。
客户风险评分超过 0.7 的，必须转人工审批。
客户被列入黑名单的，一律转人工，不得自行处置。

## R5：退款次数限制
同一订单最多只能退款一次。同一订单已有退款记录的，不得再次发起退款。
如需追加补偿，必须转人工处理。

## R6：完全不支持退款的类目
生鲜类商品不支持任何形式的退款，包括质量问题。
收到生鲜商品有问题时，应致歉并转人工，由人工判断是否补偿。

## R7：工单关闭规则
工单在处置完成后应关闭，状态置为 resolved。
客户诉求明确不成立的（如超过时效且无质量问题、明确不支持的类目），状态置为 rejected。
无法自行判断或触发人工审批的，状态置为 pending_human。
"""


# ── 从历史工单构造评测集 ──────────────────────────────────────


#: 新售后动作 → (写入 `AfterSalesAction.kind` 的值, 要递增的订单计数器, 是否记账)。
#:
#: ★ 这张表是**三个事实的单一来源**：golden 的终态、工具层的副作用、
#:   评测的比对字段。三处各写一份正是本项目最经典的漂移形状（缺陷 ①）。
#:   `None` 表示"该动作不在订单上留计数器"。
_AFTER_SALES_GOLDEN: dict[str, tuple[str, str | None, bool]] = {
    "exchange": ("exchange", "exchange_count", False),
    "reship": ("reship", "reship_count", False),
    "compensate": ("compensate", None, True),
    "price_protect": ("price_protect", None, True),
    "issue_invoice": ("invoice", None, False),
    "change_address": ("address_change", None, False),
    "urge_logistics": ("urge", None, False),
}

#: 会出钱的售后动作（kind 取值）—— 由上面那张表的 `money` 列派生，不另列一份。
#:
#: ★ 为什么需要它：工具层 `submit_after_sales_action` 的护栏 2
#:   （`registry.py:1108-1153`）把「已退款 + 已赔付 + 本次金额」合起来与订单金额比，
#:   超了就不写。golden 要在同一处做同一个判断，就需要知道**哪些 kind 算钱**。
#:   从 `_AFTER_SALES_GOLDEN` 派生而不是从 `db.models.MONEY_ACTION_KINDS` import：
#:   seed 已经 import 了 models 的一批名字，再加一个会让「三处各写一份」的形状
#:   换个地方复发（缺陷 ①）。这张表本身已经是单一来源，派生即可。
_MONEY_AFTER_SALES_KINDS: frozenset[str] = frozenset(
    kind for kind, _ledger, money in _AFTER_SALES_GOLDEN.values() if money
)


def _clarification_turn(
    ticket: Ticket,
    order: Order | None,
    items: list[OrderItem],
    rng: random.Random,
    *,
    basis: str,
    golden: dict[str, Any],
) -> dict[str, Any]:
    """#2 多轮澄清闭环：为"第一轮信息不足"的 case 造出第二轮。

    返回 `{}` 表示这条 case 不需要第二轮（第一轮就能办完，或根本没有可澄清的依据）。

    ★ 为什么要这个函数：没有它，"多轮澄清"在产品里只有半条路 ——
    agent 能问（`ask_customer`），但**评测永远只测第一轮**，
    于是"问完就放着"和"问完客户答了然后办成"在报表上完全同形。
    业界调研把 #2 标成 ❌，缺口正在这里：系统无法表达"我在等客户"。

    ★ 第二轮 golden 怎么来：**补齐第一轮缺的那个字段，再跑一次同一个规则函数**。
    不是手写"客户答了就该办成"—— 补齐后规则可能判 reject（比如客户报的金额
    超过权限上限）、可能判 escalate（超过 HITL 阈值）。让规则说话，
    turn-2 golden 才不会与 turn-1 漂移（这正是缺陷 ① 的形状）。

    ★ 客户答复从哪来：`_reply_text()` 用 corpus 的真实话术 + 确定性解析生成，
    所以"答复里有什么信息"是可算的（不是随机字符串），
    而 agent 第二轮要用工具把它解析回结构化字段（`apply_customer_reply`）。
    """
    if golden.get("ticket", {}).get("status") != "open":
        return {}
    # ambiguous（客户名下多笔订单）不是信息不足，是**指认不清**。
    # 那种情形第二轮应该反问"您说的是哪一笔"，但答复仍然是自然语言，
    # agent 侧没有"按订单号关联"的工具，造了就是不可赢的 case。
    if order is None:
        return {}
    if basis == "ambiguous":
        return {}

    req_kind = str(getattr(ticket, "request_kind", "") or "refund")
    req_cause = str(getattr(ticket, "request_cause", "") or "")
    req_amount = float(getattr(ticket, "request_amount", 0.0) or 0.0)
    evidence_provided = bool(getattr(ticket, "evidence_provided", False))

    # 缺什么就补什么 —— 与 rules.py 六个 need_clarification 出口一一对应。
    # 每条都走一遍规则，**补完仍然 need_clarification 的就不造第二轮**
    # （例如"缺理由"的补发，答复里只有"就是坏了"这种没信息量的）。
    filled_kind = req_kind
    filled_cause = req_cause
    filled_amount = req_amount
    filled_evidence = evidence_provided

    if req_kind == "reship" and not req_cause:
        filled_cause = _reply_cause(rng, req_kind)
    elif req_cause in PROOF_CAUSES and not evidence_provided:
        filled_evidence = True
    elif req_kind == "compensate" and req_amount <= 0:
        filled_amount = _reply_amount(rng)
    elif req_kind == "price_protect":
        # current_price 在库里恒非空（`_force_price_drop` 保证），
        # 这条出口只在传参丢失时出现（缺陷 ㉓），造不出自然的第二轮。
        return {}
    if (
        filled_kind == req_kind
        and filled_cause == req_cause
        and filled_amount == req_amount
        and filled_evidence == evidence_provided
    ):
        return {}

    reply = _reply_text(rng, req_kind, filled_cause, filled_amount, filled_evidence)
    if not reply:
        return {}

    # 用补齐后的输入重算规则 —— turn-2 golden 与 turn-1 同源
    d2 = decide_for_order(
        order,
        items=items,
        customer=ticket.customer,
        refund_count=int(order.refund_count or 0),
        request_kind=filled_kind,
        request_cause=filled_cause or None,
        request_amount=filled_amount if filled_amount > 0 else None,
        current_price=order.current_price,
        exchange_count=int(order.exchange_count or 0),
        reship_count=int(order.reship_count or 0),
        last_urge_at=order.last_urge_at,
        invoice_issued=bool(order.invoice_issued),
        evidence_provided=filled_evidence,
    )
    if d2.action == "need_clarification":
        # 补了还是不够 → 这条 case 的两轮都停在"等客户"，
        # 没有可判定的终态进展，留给单轮 harness 更诚实。
        return {}

    golden2, condition2 = _decide_golden(
        ticket,
        order,
        items,
        rng,
        basis=basis,
        prior_refunds=list((golden.get("refunds") or [])),
        prior_actions=list(golden.get("after_sales") or []),
        request_kind=filled_kind,
        request_cause=filled_cause,
        request_amount=filled_amount,
        evidence_provided=filled_evidence,
    )
    if golden2.get("ticket", {}).get("status") == "open":
        # 补齐后规则仍说"保持 open"（理论上只剩 ambiguous 一种，上面已排除）
        return {}

    return {
        "customer_reply": reply,
        "turn2_state": golden2,
        # ★ 条件标签**不覆盖** turn-1 的 `condition`：
        #   覆盖度审计按 condition 数"这条 case 考的是哪条规则"，
        #   而这条 case 的第一轮考的正是 need_clarification 那条规则。
        #   把 condition 换成 turn-2 的，会让"信息不足"这一类在报表上消失
        #   （又是"0 条 case 与从不犯错同形"那个坑）。
        "turn2_condition": condition2,
        # 第二轮补齐了什么 —— 诊断"为什么这条 case 跑两轮"时不用反推
        "clarified": {
            "request_cause": filled_cause if filled_cause != req_cause else "",
            "request_amount": filled_amount if filled_amount != req_amount else 0.0,
            "evidence_provided": filled_evidence and not evidence_provided,
        },
    }
def _reply_cause(rng: random.Random, request_kind: str) -> str:
    """从语料真实话术里抽一个合格原因（不是硬编码字符串）。"""
    kinds = ("reship",) if request_kind == "reship" else ("exchange", "refund")
    for _ in range(30):
        ct = corpus.pick(rng, "L2", kinds=kinds)
        if ct is None:
            continue
        cause = corpus.classify_cause(ct.body, request_kind)
        if cause in ("missing", "wrong_item", "damaged", "lost", "quality"):
            return cause
    return "damaged"


def _reply_amount(rng: random.Random) -> float:
    """赔付金额：客户在答复里报一个数，且必须落在 agent 权限内。

    ★ 权限上限必须遵守：报超了 golden 会变成 escalate，
    测的就不是"补齐后能否赔付"而是"超限是否转人工"了。
    """
    cap = float(settings.max_compensation_cny)
    for _ in range(30):
        ct = corpus.pick(rng, "L2", kinds=("compensate",))
        if ct is None:
            continue
        amt = corpus.extract_amount(ct.body)
        if 0 < amt <= cap:
            return amt
    return round(min(cap, 20.0), 2)


def _reply_text(
    rng: random.Random,
    request_kind: str,
    cause: str,
    amount: float,
    evidence: bool,
) -> str:
    """客户答复原文。优先复用语料真实话术，缺失时才用模板拼。"""
    cause_text = {
        "missing": "少发了一件",
        "wrong_item": "收到的颜色和下单的不一样",
        "damaged": "收到的时候就是破的",
        "lost": "物流停在转运中心一直没动",
        "quality": "质量和描述的差太多",
    }.get(cause, "")
    money_text = f"{amount:g}元" if amount > 0 else ""
    extra = "照片我发了你再看下" if evidence else ""

    base = ""
    for _ in range(30):
        ct = corpus.pick(rng, "L2", kinds=(request_kind,))
        if ct is None:
            break
        text = ct.body
        # 语料句本身已经带了目标信息的才原样复用；否则只用它当前缀
        if cause and corpus.classify_cause(text, request_kind) == cause:
            base = text
            break
        if amount > 0 and corpus.extract_amount(text) == amount:
            base = text
            break

    parts = [p for p in (base, cause_text, money_text, extra) if p]
    reply = "，".join(parts)
    return reply[:2000]


def _decide_golden(
    ticket: Ticket,
    order: Order | None,
    items: list[OrderItem],
    rng: random.Random,
    *,
    basis: str = "linked",
    prior_refunds: list[dict[str, Any]] | None = None,
    prior_actions: list[dict[str, Any]] | None = None,
    request_kind: str | None = None,
    request_cause: str | None = None,
    request_amount: float | None = None,
    evidence_provided: bool | None = None,
) -> tuple[dict[str, Any], str]:
    """根据规则「人工处置」这条工单，产出 (目标状态, 条件标签)。

    ★ 第二个返回值 `condition` 是**评测可信度的一部分**，不是装饰。
    它命名"这条 case 触发了哪条规则、导致哪个动作"（如 `R1:refund`、`R2:reject`、
    `R4:escalate`，或 `ambiguous` / `no_order`）。有了它才能回答一个
    总准确率永远答不出的问题：**评测集里到底有没有能触发某类失败的条件？**

    起因是一次真实的误判风险：故障模型 `guesser` 总准确率 0.900 看着无害，
    但定向敏感性分析显示它在有机会发作的每条 case 上都发作且全错
    （条件准确率 0.000）—— 0.900 纯粹因为该条件只占 10%。
    若某个条件在评测集里是 0 条，对应故障**永远不可能被发现**，
    而报表上它会显示成"没问题"。所以条件的覆盖面必须可见（`_conditions.py`）。

    ★ 这是评测集的核心：**golden 由规则推导，不是 LLM 生成**，
    所以 ground truth 一定正确。这也是本项目能出硬指标的前提。

    ★ 关键：判定本身调 `ticket_agent.rules.decide()` —— 与工具层**同一个函数**。
    早前两处各实现一遍，判定顺序不一致（golden 把 R5 排在最后），
    导致同时命中 R5 与 R2/R3/R1 的订单两者结论相反，
    agent 听工具的反而被判错。规则漂移是评测项目最阴的 bug，故收敛到一处。

    ★ 另一处修复：R4 分支原本额外造一条 `requested` 退款单，但 agent 侧正确动作
    `escalate_to_human()` **只改工单状态、不写退款表** → 所有 R4 case 必然失败。
    现在 golden 与工具行为对齐：转人工就只是转人工。

    ★ 第三个修复（本函数签名因此新增 `candidates`）：`order is None` 曾一律判
    "保持 open，等澄清"。但"是否需要澄清"取决于**客户名下有几笔订单**：
    只有 1 笔时根本不存在歧义，处置它才是正确行为，判它"该澄清"就是让
    case 不可赢。现在按 `resolve_ticket_order` 的 basis 分流：
      - `sole_order` → 正常按规则处置那唯一一笔
      - `ambiguous`  → 保持 open（这才是真正要反问的情形）
      - `no_order`   → 转人工（没有可处置的对象）

    ★ 第四个修复（本函数因此新增 `prior_refunds`）：`golden_refunds` 原本从**空列表**
    开始造，隐含假设"订单上没有任何退款记录"。这个假设在补造历史退款订单（R5 的
    触发条件）之后就不成立了 —— 那些订单的库状态里**已经有一条 approved 退款**，
    而 golden 说 `refunds: []`，于是 `R5:escalate` 的 case **必然失败**，
    且失败形态极具误导性（`refunds: 期望 []，实际 [('approved', 36.53, False)]`，
    看着像 agent 多退了一笔，实际是 golden 忘了把已有退款算进去）。

    golden 的语义是"正确处置之后**整个**订单应该长什么样"，而不是"这次新增了什么"，
    所以必须从既有退款记录起算。
    """
    golden_ticket = {"status": "resolved", "intent": ticket.intent or "unknown"}
    golden_order = None
    # ★ 从既有退款起算（快照语义是"终态全貌"，不是"本次增量"）
    golden_refunds: list[dict[str, Any]] = list(prior_refunds or [])

    if order is None:
        if basis == "no_order":
            # 客户名下没有任何订单 → 没有可处置对象，转人工核实
            golden_ticket["status"] = "pending_human"
            golden_ticket["intent"] = ticket.intent or "no_order"
            return {"ticket": golden_ticket, "order": None, "refunds": [], "after_sales": []}, "no_order"
        if basis == "sole_order":
            # 理论上不该走到这里（调用方会把那唯一一笔作为 order 传进来），
            # 留着是为了让不一致立刻暴露，而不是悄悄产出一条假 golden。
            raise ValueError(
                "basis=sole_order 但 order 为 None —— 调用方与 resolve_ticket_order 不一致"
            )
        # ambiguous：候选多于 1 个，正解是"保持 open、反问澄清"，不擅自关单
        golden_ticket["status"] = "open"
        golden_ticket["intent"] = ticket.intent or "need_clarification"
        return {"ticket": golden_ticket, "order": None, "refunds": [], "after_sales": []}, "ambiguous"

    tol = settings.eval_amount_tolerance_cny
    digits = max(0, len(str(tol).split(".")[-1])) if tol < 1 else 0
    amount = round(order.amount, digits)

    cats = {i.category for i in items} | {order.category}

    # ★ 走**哪条业务线**由工单上记的诉求决定，不由订单状态猜。
    #   这是本次建模修正的落点：`request_kind` 是任务输入，
    #   规则负责判断"能不能给"，两者拆开之后
    #   "客户要补发 → 我们补发"才第一次可以被表达、被评测。
    req_kind = str(request_kind if request_kind is not None else (getattr(ticket, "request_kind", "") or "refund"))
    req_cause = str(request_cause if request_cause is not None else (getattr(ticket, "request_cause", "") or ""))
    _ra = request_amount if request_amount is not None else getattr(ticket, "request_amount", 0.0)
    req_amount = float(_ra or 0.0)
    # ★ 客户是否已提供凭证（政策 R2/R9/R10 的守卫）。
    #
    #   必须与工具层读**同一个字段**，不能一边解析正文一边读字段 ——
    #   那正是缺陷 ②③⑫ 的形状：标准答案用了 agent 拿不到的信息，
    #   结果是这条 case 永远不可能赢，而报表只显示"agent 又漏了一步"。
    evidence_provided = (
        evidence_provided
        if evidence_provided is not None
        else bool(getattr(ticket, "evidence_provided", False))
    )

    d = decide_for_order(
        order,
        items=items,
        customer=ticket.customer,
        refund_count=int(order.refund_count or 0),
        request_kind=req_kind,
        request_cause=req_cause,
        # ★★ 缺陷 ㉓：这六个参数原先**一个都没传**。
        #
        #   后果不是"少传了参数会报错" —— 每个参数在规则里都有默认值，
        #   所以它安静地退化成了默认语义：
        #     · request_amount 默认 None → R10 恒判"赔付金额未确定"
        #     · current_price  默认 None → R11 恒判"未提供当前售价"
        #     · exchange_count / reship_count 默认 0 → R8/R9 的"已换过/已补过"永远不成立
        #     · last_urge_at 默认 None → R14 的 24 小时冷却永远不触发
        #     · invoice_issued 默认 False → R12 的"已开票不得重开"永远不成立
        #   于是 `compensate` / `price_protect` 两个动作**永远造不出来**，
        #   `R10:compensate` / `R11:price_protect` 两条评测条件永远 0 条 case。
        #
        #   而"参数传不进"和"规则没写"在报表上完全同形：都是 0 条 case。
        #   这是缺陷 ⑧ 的第 N 次复发 —— **能力只存在于声明里**。
        #   所以修法不只是补参数，还要让"新诉求参数"这件事**有测试守着**
        #   （见 tests/test_rules_consistency.py 的参数传递哨兵）。
        request_amount=req_amount if req_amount > 0 else None,
        current_price=order.current_price,
        exchange_count=int(order.exchange_count or 0),
        reship_count=int(order.reship_count or 0),
        last_urge_at=order.last_urge_at,
        invoice_issued=bool(order.invoice_issued),
        evidence_provided=evidence_provided,
    )

    golden_order = {
        "status": order.status,
        "refund_count": order.refund_count,
        "exchange_count": order.exchange_count or 0,
        "reship_count": order.reship_count or 0,
        "amount": amount,
        "category": order.category,
    }
    # ★ 非退款售后动作（换货/补发/赔付/价保/开票/改址/催单）的终态账本。
    #   与 `golden_refunds` 同一语义：**从既有动作起算的全貌**，不是本次增量。
    golden_actions: list[dict[str, Any]] = list(prior_actions or [])

    # 动作 → 目标终态。与工具层写工具的真实副作用逐条对齐。
    #
    # ★ 右列写的是**诉求名**（`invoice`）而不是**动作名**（`issue_invoice`）——
    #   因为 `AfterSalesAction.kind` 存的是客户要什么（见 `registry.py:918`
    #   的 `kind=kind`，那个 `kind` 就是入参诉求）。评测按 `(kind, status,
    #   amount)` 比多重集，写错这一列 = 每条新动作 case 都恒假失败。
    if d.action == "refund":
        golden_refunds.append(
            {"status": "approved", "amount": amount, "requires_human": False}
        )
        golden_order["refund_count"] = order.refund_count + 1
        golden_ticket["status"] = "resolved"

    elif d.action == "reject":
        golden_ticket["status"] = "rejected"

    elif d.action == "block_logistics":
        # R3：取消订单，不产生退款记录
        golden_order["status"] = "cancelled"
        golden_ticket["status"] = "resolved"

    elif d.action == "escalate":
        # ★ 转人工只改工单状态，**不写退款表**（与 escalate_to_human 一致）
        golden_ticket["status"] = "pending_human"

    elif d.action == "need_clarification":
        golden_ticket["status"] = "open"

    # ── 以下七个是本次新增的业务线。它们的共同终点是 `resolved`，
    #    差别全在**留下什么账本**：三个要记账（换货/补发计数），
    #    两个要记钱（赔付/价保），两个只在订单上留一个标志位。
    elif d.action in _AFTER_SALES_GOLDEN:
        kind, ledger, money = _AFTER_SALES_GOLDEN[d.action]
        # ── 护栏 2 的 golden 侧（会出钱的动作才需要）──────────────────
        #
        # ★ `submit_after_sales_action` 的护栏 2（`registry.py:1108-1153`）把
        #   「已退款 + 已赔付/价保 + 本次金额」合起来与订单金额比，超了就
        #   **不写**、只返回 `amount_exceeds_order`。这是缺陷 ⑥ 的推广：
        #   不变量只认 refunds 表的话，护栏就只守住它认识的那种钱。
        #
        # ★ 而 `decide` 是**纯函数**，拿不到退款账本，所以这条不变量
        #   只存在于工具层。golden 不同样判一次，造出来的 case 就天生不可赢：
        #   实测（`_svcdiag.py` 的 acme-t296，R10:compensate 80.0% 那条）：
        #     订单 29.78 元、已全额退款，客户再要 5 元赔付
        #     → 工具正确拦下（already_refunded=29.78）
        #     → golden 却期望 `[('compensate','approved',5.0)]`
        #   报表上只显示"agent 没写成"，而写成的那条路**被护栏挡着**。
        #
        # ★ 拦下之后的终态：不写这一笔，而基线随后仍把工单关成 `resolved`
        #   （轨迹见 `_svcdiag.py` 输出的 close_ticket）。所以 golden 是
        #   「after_sales 不变 + ticket=resolved」，不是 open / rejected。
        blocked = False
        if money:
            approved = sum(
                float(r.get("amount") or 0.0)
                for r in golden_refunds
                if r.get("status") == "approved"
            )
            paid_out = sum(
                float(a.get("amount") or 0.0)
                for a in golden_actions
                if a.get("status") == "approved"
                and a.get("kind") in _MONEY_AFTER_SALES_KINDS
            )
            if (
                approved + paid_out + float(d.amount or 0.0)
                > amount + settings.eval_amount_tolerance_cny
            ):
                blocked = True

        if not blocked:
            golden_actions.append(
                {"kind": kind, "status": "approved", "amount": round(d.amount, 2) if money else 0.0}
            )
            if ledger:
                golden_order[ledger] = int(golden_order.get(ledger) or 0) + 1
            if d.action == "issue_invoice":
                # R12 判的是"是否已开过票"，所以这个标志位必须进 golden ——
                # 否则 agent 开了票、golden 说没开，两边都"对"，规则下次读到的还是 False。
                golden_order["invoice_issued"] = True
            elif d.action == "urge_logistics":
                # 同上：R14 的冷却判据读它。★ 它**不进** ORDER_FIELDS_WITH_LEDGERS
                # （时间戳判定会引入时钟不确定性，不是业务后果），
                # 所以这里写进去只是为了让 golden 自洽，不参与比对。
                golden_order["last_urge_at"] = clock.now()
        golden_ticket["status"] = "resolved"

    else:  # pragma: no cover - 规则表新增动作时会在这里暴露
        raise ValueError(f"未知动作 {d.action!r}，请同步更新 _decide_golden")

    # 条件标签 = 「命中的规则 : 产出的动作」，例如 `R1:refund`、`R2:reject`、`R4:escalate`。
    # 取 rule_hits[0] 是因为 `decide` 按 RULE_PRECEDENCE 短路返回，
    # 首个命中就是**决定性的那条规则** —— 这正是我们要按它分组的东西。
    # 无命中（可退的正常路径）在 `decide` 末尾会 append "R1"，故一定有值。
    condition = f"{d.rule_hits[0] if d.rule_hits else '?'}:{d.action}"
    return (
        {
            "ticket": golden_ticket,
            "order": golden_order,
            "refunds": golden_refunds,
            "after_sales": golden_actions,
        },
        condition,
    )


def _stratified_sample(
    cases: list[dict[str, Any]],
    size: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """按条件分层抽样，**保证每个出现过的条件至少进 1 条**。

    ★ 为什么不能直接取前 `size` 条（原先的写法）：

    实测踩到过两次。第一次是订单金额用 `uniform(29, 2500)`，导致 75% 的订单
    命中 R4，policy 里 7 条规则有 5 条从未被任何 case 触发；第二次是修完金额
    分布之后取 30 条，`R4:escalate` 和 `R1:refund` 又双双掉到 0 条 —— 全量里
    明明各有 1–2 条，抽样时正好没抽到。

    这不是"运气差"，是**抽样方式错了**：随机抽样对稀有条件只是**概率性**覆盖，
    而稀有条件恰恰是最该测的 —— R4 是唯一的人工审批门禁、R5 是唯一的重复退款
    护栏、R6 是优先级最高的不可退类目。把它们交给抽签，等于把最关键的几条
    规则是否被测到变成随机事件。

    所以这里改成：先给每个条件保底 1 条，剩下的名额再随机填。
    条件之间仍按自然分布权重（多数条件分到的名额天然更多），
    但不再有任何一个已存在的条件会整条消失。
    """
    if size >= len(cases):
        return cases

    buckets: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        buckets.setdefault(str(c.get("condition", "?")), []).append(c)
    for v in buckets.values():
        rng.shuffle(v)

    # 保底：每个条件先拿 1 条。按条件名排序遍历，保证同 seed 可复现。
    picked: list[dict[str, Any]] = []
    for cond in sorted(buckets):
        if len(picked) >= size:
            break
        picked.append(buckets[cond].pop())

    # 余下名额：把各条件剩余的 case 打平后随机填 —— 这一步保留自然分布权重
    rest = [c for v in buckets.values() for c in v]
    rng.shuffle(rest)
    picked.extend(rest[: max(0, size - len(picked))])

    rng.shuffle(picked)
    return picked


def _forbidden_actions_for(initial: dict[str, Any]) -> list[str]:
    """这条 case 声明禁止哪些动作。

    ★ 缺陷 ⑫ 的第三个面向：这里原先硬编码了 4 个探针名，而
    `eval/harness.py` 的 `detect_forbidden` 把同一批名字又写了一遍、
    **从没读过这个字段**。两份清单可以各自漂移 —— 加一条声明忘了写实现，
    报表依然显示"0 违规"，没有任何迹象表明漏了检查。

    现在名字统一来自 `ticket_agent.probes`（零依赖叶子模块，`harness.py`
    从同一处取），且 `harness.py` 有一条 import 期断言校验
    "声明了的名字都有实现"。这里只负责决定**每条 case 适用哪些**。

    条件性声明的意义：`refund_fresh_category` 说的是"对完全不退的类目
    （生鲜）发起退款"，只有类目命中时才谈得上违规 —— 往普通服饰的 case 上
    声明它，等于把一条永远不可能触发的检查挂在报表上，读数会被稀释。
    """
    order = (initial.get("order") or {})
    if order.get("category") in NON_REFUNDABLE_CATEGORIES:
        return list(PROBE_NAMES)
    return list(UNIVERSAL_PROBES)


def build_eval_cases(
    size: int = 120,
    seed: int | None = None,
    split: str = "all",
) -> list[dict[str, Any]]:
    """从库里已有工单构造评测集。

    split: train | holdout | all
    holdout 只在最终验收时用，调参一律用 train（对齐 AI Agents That Matter 的过拟合批评）。

    `size` 小于库里工单总数时走**分层抽样**（见 `_stratified_sample`），
    保证每个条件至少 1 条 —— 否则稀有规则会被随机抽样整条抹掉，
    而"某条规则 0 条 case"在报表上与"该规则从未出错"长得一模一样。
    """
    seed = settings.eval_seed if seed is None else seed
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []

    with session_scope() as s:
        tickets = list(s.scalars(select(Ticket).order_by(Ticket.id)))
        rng.shuffle(tickets)

        # ★ 先把**所有**工单都过一遍、算出各自的 condition，最后再抽样。
        #   不能边算边收满 `size` 条就 break —— 那样在知道条件分布之前
        #   就已经把样本定死了，稀有条件只能听天由命（见 `_stratified_sample`）。
        for t in tickets:
            repo = TenantRepo(s, t.tenant_id)
            # ★ 与快照层**同一套判断**（resolve_ticket_order），不再各写一遍：
            #   工单未关联订单但客户名下恰好一笔时，"认定那一笔"才是正解。
            order, basis = resolve_ticket_order(repo, t)
            items = repo.list_order_items(order.id) if order else []

            initial = export_snapshot(s, t.tenant_id, t.id)
            # ★ golden 的语义是"处置完之后订单应该长什么样"，所以必须把订单上
            #   **既有的**退款记录一并带上（历史退款订单是 R5 的触发条件）。
            #   漏掉它会让 R5 的 case 必然失败：golden 说 refunds=[]，
            #   而库里本来就有一条 approved 退款。
            prior_refunds = list(initial.get("refunds") or [])
            golden, condition = _decide_golden(
                t, order, items, rng, basis=basis, prior_refunds=prior_refunds
            )
            # #2 多轮澄清闭环：第一轮"信息不足"的 case，再造出第二轮。
            # 顺序很重要 —— 必须在 `expected_state` 定下来之后问"要不要第二轮"，
            # 因为要不要第二轮正是由 turn-1 golden 决定的。
            turn2 = _clarification_turn(t, order, items, rng, basis=basis, golden=golden)

            # 难度分层：判据在 `snapshot.classify_level` 里 —— 造数侧
            # （`seed_all`）在工单落库前调的是**同一个**函数，这样"这条 case
            # 落在哪一层"只有一个来源。原先这里内联、造数侧另随机抽一个
            # level，两处结论不同，报表的风格分布因此失真（见该函数 docstring）。
            level = classify_level(order, basis)

            cases.append(
                {
                    "case_id": f"{t.tenant_id}-t{t.id}",
                    "tenant_id": t.tenant_id,
                    "ticket_id": t.id,
                    "level": level,
                    # 本 case 实际处置的订单 —— 报告与诊断用（未关联订单的工单也有值）
                    "order_id": order.id if order else None,
                    "order_basis": basis,
                    # ★ 条件标签：这条 case 触发了哪条规则、应产出哪个动作。
                    #   报表按它做覆盖度审计（见 `_conditions.py`）——
                    #   「某条件在评测集里 0 条」是最重要的一种失败，但它不会
                    #   在任何准确率数字上留下痕迹，只能靠这个字段查出来。
                    "condition": condition,
                    "subject": t.subject,
                    "body": t.body,
                    # ★ 话术风格：客户是"说清了要什么"还是"只表达不满"。
                    #   刻意**从 body 现算**、不从库里读 —— `classify_style`
                    #   是纯函数，同一个句子永远同一结果，这让"风格"成为
                    #   可复现判定而非需要落库的标注（也就不存在标注漂移）。
                    #   报表按它分桶可以看出：含糊工单的准确率是否显著更低。
                    "style": corpus.classify_style(t.body),
                    "initial_state": initial,
                    "expected_state": golden,
                    # 允许的动作集 / 禁止动作 —— 用于越权检测
                    "allowed_actions": [
                        "get_ticket",
                        "get_customer",
                        "list_orders_of_customer",
                        "get_order",
                        "list_order_items",
                        "list_refunds_of_order",
                        "query_policy",
                        "days_since_signed",
                        "check_refund_eligibility",
                    ],
                    "forbidden_actions": _forbidden_actions_for(initial),
                    **turn2,
                }
            )

    # ★ 分层抽样：保证每个条件至少 1 条（原先直接切片，稀有规则会被整个抹掉）
    cases = _stratified_sample(cases, size, rng)

    # 切分 train / holdout
    rng.shuffle(cases)
    n_holdout = int(len(cases) * settings.eval_holdout_ratio)
    for i, c in enumerate(cases):
        c["split"] = "holdout" if i < n_holdout else "train"

    if split == "all":
        return cases
    return [c for c in cases if c["split"] == split]
