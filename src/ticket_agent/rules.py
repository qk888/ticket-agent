"""售后规则引擎 —— **规则的唯一事实来源**。

★ 为什么要有这个模块（这是一次真实缺陷修复的结果）：

最初 `check_refund_eligibility`（工具层）和 `_decide_golden`（评测集 golden 生成）
各自实现了一遍规则，两处**判定顺序不一致**：

    工具：R6 → R4 → R5 → R2 → R3 → R1
    golden：R6 → R4 → R3 → R2 → R1 → R5     ← R5 掉到了最后

后果不是"差一点"，而是**灾难性的**：凡同时命中 R5 与 R2/R3/R1 的订单，
golden 期望「reject」而工具告诉 agent 应该「escalate」。
agent 老老实实听了工具的话，却被判错。评测分数会因为一个内部不一致而整体崩掉，
而报告上只会显示"agent 准确率很低"——排错要花掉一整天。

同类问题还有一处：golden 在 R4 分支里额外造了一条 `requested` 退款，
但 agent 侧正确的动作 `escalate_to_human` 并不写退款表 → 所有 R4 case 必然失败。

教训：**同一个业务规则在多处实现，就一定会漂移。**
所以规则收敛到这里，工具和 golden 都调它；再加一个一致性测试
（`tests/test_rules_consistency.py`）把"两处必须一致"变成机器可校验的断言。

## 关于「golden 也调同一个函数」是否让评测失去意义

有一种批评：golden 和工具共用代码，那"agent 选对动作"不就成了自我验证？

不是。本评测真正要测的不是"你能不能重新实现一遍规则"——规则已经作为工具给出了，
任何 agent 都能调。要测的是**agent 拿到结论之后是否照做**：

- 客户情绪激烈、反复纠缠时，LLM 会不会绕过「生鲜不退」直接退款？
- 工具说 escalate，LLM 会不会自作主张 reject 掉、把客户打发走？
- 工具说 need_clarification，LLM 会不会为了"闭环"把单子关了？

这些才是线上事故的真实来源。**金标准是政策文档，不是 agent 的实现**；
agent 被考核的是在压力下遵守政策的能力 —— 与 τ-bench 的考核意图一致。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from . import clock
from .config import settings

#: ★ 类目政策常量定义在**本模块**而非 `db.models` —— 它们是政策（R2/R6 的适用范围），
#: 不是存储模式。放在这里让 `rules` 成为不依赖任何内层模块的叶子，
#: 否则 `rules → db.models → db.__init__ → db.seed → rules` 会成环导入。
#: `db.models` 仍 re-export 这两个名字，老代码 `from ..db.models import ...` 不受影响。
#: 不支持无理由退货的类目（R2）
NON_RETURNABLE_CATEGORIES = ("内衣", "定制", "数字商品")
#: 完全不支持退款的类目（R6）
NON_REFUNDABLE_CATEGORIES = ("生鲜",)

#: ★ 规则判定顺序 —— 唯一事实来源。改动这里会同时影响工具与 golden，
#: 一致性测试会在两边漂移时立刻报错。
RULE_PRECEDENCE: tuple[str, ...] = (
    "R6",
    "R4",
    "R5",
    "R2",
    "R3",
    "CANCELLED",
    "UNPAID",
    "R1",
)

#: 动作 → 说明。工具与提示词都用这套词汇，避免各写各的。
ACTIONS = {
    # ── 原有的退款线（v1 就有的五种结局）──────────────────────
    "refund": "受理无理由退货并创建退款单",
    "reject": "驳回诉求并关闭工单",
    "block_logistics": "发起物流拦截（不得直接退款）",
    "escalate": "转人工审批",
    "need_clarification": "需要向客户澄清后处理",
    # ── 售后非退款线（见 REQUEST_KINDS 的由来）────────────────
    "exchange": "受理换货，创建换货单（寄回旧品 + 寄出新品的流程）",
    "reship": "受理补发，创建补发单（少发/漏发/丢件/破损，不涉及退款）",
    "compensate": "发放运费或损失补偿（金额不退还商品款）",
    "price_protect": "价保退差：按下单后降价金额退差额",
    "issue_invoice": "为该订单开具发票",
    "change_address": "修改发货前订单的收货地址",
    "urge_logistics": "登记催单：催促仓库发货或催促承运商",
}


#: 客户**诉求类型** → 说明。
#:
#: ★★ 这是本项目最大的一次建模修正，值得讲清楚。
#:
#: 在引入它之前，`decide()` 隐含了一个假设：**每一张售后工单都是退款申请**。
#: 于是"客户要什么"和"我们决定做什么"被压缩成同一个字段（`Decision.action`），
#: 系统能表达的结局只有五种：退钱 / 驳回 / 拦物流 / 转人工 / 反问。
#:
#: 这个假设是错的，而且**可以被自己的语料证伪**。`data/raw/jddc_aftersale.jsonl`
#: 里 10,217 条真实客服会话各自带一个 `scene` 标签，分布是：
#:
#:     物流 39.3% · 退款 21.4% · 退货 16.6% · 质量 6.8% · 补发 5.6%
#:     赔付 3.3% · 投诉 2.9% · 发票 2.1% · 改址 2.0%
#:
#: 也就是说**只有 21.4% 的真实诉求落在"退款"这一类里**，而项目把它当成了全部。
#: 剩下 78.6% 的工单丢进来，系统只能给出"退款/驳回/转人工"——
#: 客户要补发，它去退钱；客户要改地址，它去退钱；客户只是催发货，它还是去退钱。
#:
#: 把两个维度拆开之后：
#:
#:     诉求类型（客户要什么）  ×  规则判定（我们能不能给）  →  动作（我们做什么）
#:
#: 才可能出现"客户要补发 → 我们补发"这种**非退款的正确结局**，
#: 也才可能测出"客户要补发 → agent 却去退款"这种**真实事故**。
#:
#: ★ 词表直接对齐语料的 scene 标签（外加"退货退款"这一细分），
#: 所以"客户在说什么"这件事**有真实来源**，不是我配的。
REQUEST_KINDS = {
    "refund": "退款（不涉及寄回商品）",
    "return_refund": "退货退款（需寄回商品后退款）",
    "exchange": "换货（寄回旧品，换同款或他款）",
    "reship": "补发（少发/漏发/丢件/运输破损）",
    "compensate": "赔付（运费补偿或损失补偿）",
    "price_protect": "价保（下单后降价，退差额）",
    "invoice": "开发票",
    "address_change": "修改收货地址",
    "urge": "催发货 / 催件（物流类，占真实诉求 39.3%）",
}

#: 涉及"钱出去"的诉求类型 —— R4 的大额门槛只对它们成立。
#: 补发/换货/改址/发票都不走账，拿 ¥500 去卡它们是把规则用错了地方。
MONEY_REQUEST_KINDS: frozenset[str] = frozenset(    {"refund", "return_refund", "compensate", "price_protect"}
)

#: 需要"寄回商品"的诉求 —— 决定了是否要生成退货物流单。
RETURN_REQUEST_KINDS: frozenset[str] = frozenset({"return_refund", "exchange"})

#: 诉求原因（`Ticket.request_cause`）词表。少数规则需要区分"为什么要换/补"。
REQUEST_CAUSES = {
    "": "未指明",
    "quality": "商品质量问题",
    "wrong_item": "发错货",
    "missing": "少发/漏发",
    "lost": "运输丢件",
    "damaged": "运输破损",
    "no_reason": "七天无理由",
    "size": "尺码不符",
    "late": "发货或配送超时",
    "price_drop": "下单后降价",
    "shipping_fee": "运费争议",
}

#: 政策文本（`prompt/policies.md` 的 R2 / R9 / R10）明确要求"提供凭证"的原因。
#:
#: ★ 为什么它必须是**代码里的常量**而不是一段注释：
#: 政策原文写着"质量问题需提供凭证"，而在 `Ticket.evidence_provided` 这一列
#: 出现之前，**全项目 0 处读它、0 个工具能表达它**。于是客户说"衣服破了我要换货"
#: 而照片还没给，系统照样判"受理换货"—— 政策承诺了一件事，系统从来没检查过。
#: 这正是缺陷 ⑧「能力只存在于声明里」，只不过这次发生在政策文本里。
#:
#: 放在 `rules.py`（而不是 `seed.py`）是因为它是**政策**的一部分：
#: 规则要用它判 need_clarification，造数要用它决定哪些工单带凭证，
#: 两处必须是同一份定义，否则又会漂移成缺陷 ① 的形状。
PROOF_CAUSES: frozenset[str] = frozenset(
    {
        "quality",  # R2：签收后质量问题需走质量投诉流程并提供凭证
        "damaged",  # R9：运输破损需提供破损照片确认
        "wrong_item",  # R9：发错货需提供收到的实物照片
        "lost",  # R9：丢件需提供快递轨迹或签收异常证明
    }
)

#: 受控词表：凭证只有这几种形态，其余一律拒收（#60）。
#:
#: ★ 为什么必须有词表：`mark_evidence_received(evidence_type, note)` 的
#:   `evidence_type` 原本是**任意自由字符串**，而它翻的却是一个只表达
#:   「有 / 没有」的布尔列。于是 agent 传 `"none"`、`"不知道"`、`"待补"`
#:   同样能把 `evidence_provided` 置成 True —— 系统对凭证的**存在性**
#:   有记录，对凭证的**内容**零检查。政策写「需提供凭证」，代码检查的
#:   却是「客户有没有说过凭证这两个字」。
EVIDENCE_TYPES: frozenset[str] = frozenset(
    {
        "photo",  # 实物照片
        "video",  # 开箱视频
        "screenshot",  # 页面 / 订单截图
        "chat_log",  # 与商家的聊天记录
        "inspection_report",  # 第三方检测报告
        "tracking_proof",  # 快递轨迹 / 签收异常证明
    }
)

#: 每种索赔原因，需要哪几类凭证才算数（政策里「凭证」一词在此被具体化）。
#:
#: ★ 判据一句话：凭证要能证明**问题本身**，不是证明「买过、聊过」。
#:   破损 / 错发 / 质量问题看的是东西本身 → 必须有实物影像或检测报告；
#:   丢件看的是物流链路 → 快递轨迹或签收异常证明也算；
#:   聊天记录与订单截图在任何一种原因下都只证明「有过这笔交易」。
#:
#:   放进代码而不是 prompt：它是政策，而本项目的铁律是「政策必须抵达
#:   某个可执行的检查点」，否则就是缺陷 ⑧ 在凭证这件事上的第二次复发。
CAUSE_REQUIRED_EVIDENCE: dict[str, frozenset[str]] = {
    "quality": frozenset({"photo", "video", "inspection_report"}),
    "damaged": frozenset({"photo", "video", "inspection_report"}),
    "wrong_item": frozenset({"photo", "video", "inspection_report"}),
    "lost": frozenset({"tracking_proof", "photo", "video"}),
}


def check_evidence(evidence_type: str, request_cause: str = "") -> tuple[bool, str]:
    """这份凭证能不能支撑这一次索赔。返回 (是否通过, 原因)。

    三种结果对应三种不同的失败，改法也完全不同：

    - 通过；
    - `evidence_type_unknown` —— 不在受控词表里。这不是「凭证不合格」，
      是「根本没说清交的是什么」，要客户重新发；
    - `evidence_insufficient` —— 类型合法但证明不了这次索赔的原因，
      要客户补对类型的那一份。

    ★ `request_cause` 为空时**一律放行**：那时规则正在返回
      `need_clarification`（先缺原因），凭证给没给不影响本步判定。
      在这里硬拦会造出一个新的不可赢 case —— 那正是 #2 刚清掉的那类坑：
      「case 之所以不可赢的原因，和被检查的原因不是同一个」。
    """
    t = (evidence_type or "").strip()
    if t not in EVIDENCE_TYPES:
        return (
            False,
            f"evidence_type_unknown: {t!r} 不在受控词表 {sorted(EVIDENCE_TYPES)} 内",
        )
    need = CAUSE_REQUIRED_EVIDENCE.get((request_cause or "").strip())
    if not need:
        return (True, "凭证类型合法，且本次索赔不要求特定凭证")
    if t in need:
        return (True, f"{t} 可以支撑 {request_cause}")
    return (
        False,
        f"evidence_insufficient: {request_cause} 需要 "
        f"{'/'.join(sorted(need))}，收到的是 {t}",
    )


class PolicyViolation(ValueError):
    """政策禁止该动作。

    是 `ValueError` 的子类，所以 HTTP 层既有的 `ValueError → 409` 映射
    会自动把它转成 409（与状态机冲突同类：请求本身合法，但与当前政策/状态冲突），
    同时调用方可以单独捕获它来做 override 记账。
    """


@dataclass
class Decision:
    """一次规则判定的结果。"""

    action: str
    reasons: list[str] = field(default_factory=list)
    eligible: bool = False
    amount: float = 0.0
    rule_hits: list[str] = field(default_factory=list)
    #: ★ 本判定是否允许人工直接批准退款。
    #:
    #: 这个字段存在的理由是一次真实缺陷（详见 README §4.2 缺陷 ⑥）：
    #: `escalate` 这一个动作名底下其实藏着**两种截然不同的语义**——
    #:
    #: - **推迟（deferral）**：政策允许退款，但决定权交给人工。R4（黑名单/
    #:   高风险/大额）就是这个意思："金额超过 500 元必须转人工审批" ——
    #:   人工审完当然可以批。此时 `human_override_allowed=True`。
    #: - **禁止（prohibition）**：政策根本不允许这笔退款，转人工只是让人去
    #:   处理客户情绪，不是让人去批准。R5（同一订单最多退一次）、R6（生鲜
    #:   完全不退，优先级最高）属于这一类。此时 `human_override_allowed=False`。
    #:
    #: 修缺陷之前，这两种 escalate 在代码里长得一模一样，于是人工审批接口
    #: 拿到了一个 R5 工单也照批不误 —— 实测把一单 47.53 元的图书退成了
    #: 95.06 元（refund_count 1 → 2）。**把"人能压过机器"错当成了"人能压过政策"。**
    #:
    #: 默认值取 `False` 是刻意的：涉及钱的地方，**默认拒绝**。
    #: 只有明确知道该规则属于「推迟」时才会被置为 True。
    human_override_allowed: bool = False
    #: 本次判定处理的**诉求类型**（见 `REQUEST_KINDS`）。
    #: 加这个字段是为了让"客户要什么"在结论里留下痕迹 —— 排查
    #: "客户明明要补发，为什么走了退款"时，第一眼要能分清
    #: 是**理解错了诉求**（kind 就错了）还是**理解对了但办了错事**（kind 对、action 错）。
    request_kind: str = "refund"

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "action": self.action,
            "reasons": self.reasons,
            "amount": round(self.amount, 2),
            "rule_hits": self.rule_hits,
            "human_override_allowed": self.human_override_allowed,
            "request_kind": self.request_kind,
        }


def refund_is_forbidden(d: Decision) -> bool:
    """政策是否**禁止**这笔退款（区别于「需人工审批」）。

    这是给写路径用的单一判据 —— `tools.registry.create_refund` 与
    `service.approve_run` 都调它，避免又出现"两处各判一次、然后漂移"。

    规则：权威判定说可以退（`action == "refund"`）就不算禁止；
    否则只有在明确属于「推迟」类型（`human_override_allowed`）时才放行。
    """
    return d.action != "refund" and not d.human_override_allowed


# ── 诉求类型 → 期望动作（一张表，三个调用方共用）──────────────────
#
# ★ 为什么要有这张表，而不是让每个调用方自己写 if/elif：
#
#   同一个"客户要补发 → 我们该做 reship"的对应关系，至少有三处需要知道：
#   ① `tools/registry.py` 的写工具护栏要复核"agent 声称的动作对不对"；
#   ② `db/seed.py` 的 golden 要把动作写成终态；
#   ③ `agents/scripted.py` 的规则天花板基线要按动作分派工具。
#
#   三处各写一遍 = 三处会漂移。这个项目里所有的真实缺陷形状都是这样来的：
#   "同一件事写在两个地方，改了一处"。所以这里定成唯一事实来源。
#
# ★ 注意 `refund` 与 `return_refund` 都映射到 `refund` 动作：
#   对系统而言"直接退款"和"退货后退款"在**钱**上是同一件事（都出一笔退款），
#   差别在要不要寄回商品 —— 那是逆向物流流程，本项目不实现（记录在已知不足）。
#   这是**有意识的简化**，不是遗漏：写成两个动作会让账本不变量、终态判定、
#   权限矩阵都要多一套分支，而它们的行为完全相同。
REQUEST_ACTION: dict[str, str] = {
    "refund": "refund",
    "return_refund": "refund",
    "exchange": "exchange",
    "reship": "reship",
    "compensate": "compensate",
    "price_protect": "price_protect",
    "invoice": "issue_invoice",
    "address_change": "change_address",
    "urge": "urge_logistics",
}


def expected_action_for(request_kind: str) -> str:
    """该诉求**在政策允许时**对应的动作。未知诉求直接抛错。

    ★ 与 `_decide_service` 末尾的哨兵同一个用意：宁可炸，也不要静默返回
      一个默认动作 —— 那会造出最难排查的一类事故（agent 办事了、办的不是那件事）。
    """
    if request_kind not in REQUEST_ACTION:
        raise ValueError(
            f"未知诉求类型 {request_kind!r}，请同步更新 rules.REQUEST_ACTION"
        )
    return REQUEST_ACTION[request_kind]


def is_money_action(action: str) -> bool:
    """该动作是否会实际出钱（决定要不要走账本不变量）。

    ★ 这份判据必须与 `db.models.MONEY_ACTION_KINDS` 一致，所以那里也
      定义了同名常量。两处的**用途不同**（rules 是叶子模块，不能 import db；
      db 是为了给 ORM 层一个查询用的常量），因此这里加一条一致性测试卡住它们
      —— 见 `tests/test_rules_consistency.py`。
    """
    return action in ("refund", "compensate", "price_protect")


def _days_since(signed_at: dt.datetime | None) -> int | None:
    """签收至今的天数。★ 用 `clock.now()` 而不是 `dt.datetime.now()`。

    这一行是缺陷 ⑨ 的现场：合成数据的时间戳相对造数那一刻生成，
    如果这里读墙上时钟，数据每放一天判定基准就跟着漂一天，
    七天窗口边界上的订单会在两次运行之间从 `R1:refund` 翻成 `R1:reject`。
    改读 `clock.now()` 后，造数与判定共用同一个"现在"。
    """
    if signed_at is None:
        return None
    signed = signed_at
    if signed.tzinfo is None:
        signed = signed.replace(tzinfo=dt.timezone.utc)
    return (clock.now() - signed).days


def decide(
    *,
    order_status: str,
    amount: float,
    categories: set[str],
    signed_at: dt.datetime | None,
    is_blacklisted: bool = False,
    risk_score: float = 0.0,
    refund_count: int = 0,
    # ── 非退款线才用得到的入参（默认值保证老调用方行为不变）──
    request_kind: str = "refund",
    request_cause: str = "",
    request_amount: float | None = None,
    paid_at: dt.datetime | None = None,
    current_price: float | None = None,
    exchange_count: int = 0,
    reship_count: int = 0,
    last_urge_at: dt.datetime | None = None,
    invoice_issued: bool = False,
    evidence_provided: bool = False,
) -> Decision:
    """判定一张售后工单该如何处置。

    这是一个**纯函数**：不碰数据库、不看时间以外的东西，
    所以可以拿任意构造的场景做单测，也可以在 golden 生成时复用。

    参数刻意都是标量/集合而非 ORM 对象 —— 这样评测层可以构造"假设状态"来推演，
    不必真的往库里写数据。

    ★ `request_kind` 是第二个维度（见 `REQUEST_KINDS` 的长注释）：
    它回答"**客户要什么**"，返回值里的 `action` 回答"**我们做什么**"。
    在它出现之前本方法隐含假设"每张工单都是退款申请"，于是占真实诉求 78.6% 的
    补发/换货/催单/价保/发票/改址/赔付只能被塞进"退款/驳回/转人工"三个桶里。

    ★★ **默认参数保证向后兼容**：`request_kind="refund"` 走的是与引入本参数之前
    逐字节相同的规则链（R6→R4→R5→R2→R3→CANCELLED→UNPAID→R1）。
    新能力是**纯增量**，不是替换 —— 这条约束是刻意守住的。
    """
    if request_kind not in REQUEST_KINDS:
        # ★ 不猜、不回落默认值。拼错的诉求类型若静默走退款线，
        #   就会造出"客户明明要补发、系统却退了钱"这类最难排查的事故。
        raise ValueError(f"未知诉求类型 {request_kind!r}，请同步更新 rules.REQUEST_KINDS")

    if request_kind not in ("refund", "return_refund"):
        return _decide_service(
            request_kind=request_kind,
            request_cause=request_cause,
            request_amount=request_amount,
            order_status=order_status,
            amount=amount,
            categories=categories,
            signed_at=signed_at,
            paid_at=paid_at,
            current_price=current_price,
            is_blacklisted=is_blacklisted,
            risk_score=risk_score,
            exchange_count=exchange_count,
            reship_count=reship_count,
            last_urge_at=last_urge_at,
            invoice_issued=invoice_issued,
            evidence_provided=evidence_provided,
        )

    d = _decide_refund_line(
        order_status=order_status,
        amount=amount,
        categories=categories,
        signed_at=signed_at,
        is_blacklisted=is_blacklisted,
        risk_score=risk_score,
        refund_count=refund_count,
    )
    # 退款线内部不区分 refund / return_refund（两者的钱规则完全相同，
    # 区别只在**要不要生成退货物流单**，那是履约层的事），
    # 但结论里要留下准确痕迹，否则排查时分不清客户是"只退钱"还是"退钱并寄回"。
    d.request_kind = request_kind
    return d


def _decide_refund_line(
    *,
    order_status: str,
    amount: float,
    categories: set[str],
    signed_at: dt.datetime | None,
    is_blacklisted: bool = False,
    risk_score: float = 0.0,
    refund_count: int = 0,
) -> Decision:
    """退款线：R6→R4→R5→R2→R3→CANCELLED→UNPAID→R1。

    ★ 这条链与引入 `request_kind` 之前**完全一致**，一个字都没改。
    之所以抽成独立函数而不是往 `decide` 里塞 if，是为了让"退款规则有没有被动过"
    这件事在 diff 里一眼可见 —— 144 个测试和已跑完的评测口径都压在这条链上。
    """
    reasons: list[str] = []
    hits: list[str] = []

    def hit(rule: str, text: str) -> None:
        hits.append(rule)
        reasons.append(f"{rule}：{text}")

    # ── R6：生鲜硬拒绝（优先级最高，且高于 R4）──────────────
    # ★ human_override_allowed 保持默认 False：R6 是**禁止**，不是推迟。
    # 人工的任务是安抚客户并说明政策，不是把这笔钱退出去。
    fresh = categories & set(NON_REFUNDABLE_CATEGORIES)
    if fresh:
        hit("R6", f"{'、'.join(sorted(fresh))} 属生鲜类目，不支持任何形式退款，应致歉并转人工")
        return Decision("escalate", reasons, False, amount, hits)

    # ── R4：黑名单 / 高风险 / 大额 → 必须人工 ────────────────
    # ★ 这三条是**推迟**（deferral）：政策允许退款，只是把决定权交给人工，
    # 所以 human_override_allowed=True —— 人工审批时可以直接批准。
    # 这是 R4 与 R5/R6 的唯一区别，也正是缺陷 ⑥ 的修复点。
    if is_blacklisted:
        hit("R4", "该客户在黑名单中，必须转人工审批")
        return Decision("escalate", reasons, False, amount, hits, human_override_allowed=True)
    if risk_score > settings.risk_score_hitl_threshold:
        hit("R4", f"客户风险评分 {risk_score} 超过 {settings.risk_score_hitl_threshold}，必须转人工审批")
        return Decision("escalate", reasons, False, amount, hits, human_override_allowed=True)
    if amount > settings.refund_hitl_threshold_cny:
        hit("R4", f"金额 {amount} 元超过 {settings.refund_hitl_threshold_cny} 元，必须转人工审批")
        return Decision("escalate", reasons, False, amount, hits, human_override_allowed=True)

    # ── R5：同一订单最多退一次 ──────────────────────────────
    # ★ 保持 False：R5 是**禁止**。转人工是让人去解释"不能退两次"，
    # 而不是让人有权限退第二次 —— 否则护栏等于不存在。
    if refund_count >= settings.max_refunds_per_order:
        hit("R5", f"该订单已有 {refund_count} 条退款记录，不得再次退款，需人工处理")
        return Decision("escalate", reasons, False, amount, hits)

    # ── R2：不支持无理由退货的类目 → 驳回 ───────────────────
    nonret = categories & set(NON_RETURNABLE_CATEGORIES)
    if nonret:
        hit("R2", f"{'、'.join(sorted(nonret))} 不支持七天无理由退货")
        return Decision("reject", reasons, False, amount, hits)

    # ── R3：已发货未签收 → 只能拦截物流 ─────────────────────
    if order_status == "shipped":
        hit("R3", "订单已发货未签收，不得直接退款，应发起物流拦截")
        return Decision("block_logistics", reasons, False, amount, hits)

    # ── CANCELLED：已取消订单 → 无货可退 ────────────────────
    if order_status == "cancelled":
        hit("CANCELLED", "订单已取消，没有可退的商品，应说明情况并驳回")
        return Decision("reject", reasons, False, amount, hits)

    # ── UNPAID：未支付 → 不存在退款 ─────────────────────────
    if order_status == "pending_paid":
        hit("UNPAID", "订单尚未支付，不存在退款")
        return Decision("reject", reasons, False, amount, hits)

    # ── R1：七天无理由期限 ──────────────────────────────────
    days = _days_since(signed_at)
    if days is not None and days > settings.return_window_days:
        hit("R1", f"签收已 {days} 天，超过 {settings.return_window_days} 天无理由期限")
        return Decision("reject", reasons, False, amount, hits)

    if days is None:
        # 已签收时间缺失但状态是 signed —— 数据不完整，交给人工而不是猜
        hit("R1", "订单缺少签收时间，无法确认是否在七天期限内")
        return Decision("escalate", reasons, False, amount, hits)

    reasons.append(f"R1：签收 {days} 天，在 {settings.return_window_days} 天期限内")
    return Decision("refund", reasons, True, amount, ["R1"])


def _decide_service(
    *,
    request_kind: str,
    request_cause: str = "",
    request_amount: float | None = None,
    order_status: str = "",
    amount: float = 0.0,
    categories: set[str] | None = None,
    signed_at: dt.datetime | None = None,
    paid_at: dt.datetime | None = None,
    current_price: float | None = None,
    is_blacklisted: bool = False,
    risk_score: float = 0.0,
    exchange_count: int = 0,
    reship_count: int = 0,
    last_urge_at: dt.datetime | None = None,
    invoice_issued: bool = False,
    evidence_provided: bool = False,
) -> Decision:
    """**非退款线**：换货 / 补发 / 赔付 / 价保 / 开票 / 改址 / 催单（R8–R14）。

    这条线存在的理由，见 `REQUEST_KINDS` 上方那段长注释：真实语料里
    78.6% 的售后诉求不是退款，而系统此前只有"退款/驳回/转人工"三个出口。

    ## 与退款线的三条结构性差异

    1. **不涉及"退钱"的动作不该有金额门槛。** 换货、补发、改址、开票都不走账，
       拿 R4 的 ¥500 去卡它们是规则用错了地方。所以金额检查只出现在
       真的会出钱的两种（赔付 R10、价保 R11）里，且用的是**本次出账金额**
       而不是订单总额 —— 一个 ¥800 的订单补发一件 ¥20 的配件，不构成大额风险。

    2. **生鲜的硬禁止（R6）只对"需要寄回商品"的诉求成立。** 生鲜不可二次销售，
       所以换货禁止；但**补发不需要寄回** —— 生鲜坏了补发一箱恰恰是正确处置。
       这是把"退款"和"补发"拆开之前**不可能表达**的结局。

    3. ★★ **本线所有 `escalate` 一律 `human_override_allowed=False`。**

       这个标志位目前全项目只有一个消费方：人工审批接口 `approve_run`——
       它被批准后会执行 `create_refund`（按订单金额退款）。于是对一个
       "补偿 30 元"或"在途换货"的工单，如果置 True，人工点一下"批准"
       就会把**整单金额**退出去。这正是缺陷 ⑥ 的形状：
       **标志位表达的是"人能压过政策"，一旦被错误的语义复用就会变成"人能压过钱"。**

       非退款线的诉求里，人工确实可能放行（比如破例），但那必须走**显式
       override 并指定金额**（`approve_run(..., override=True, amount=...)`），
       不能靠一个为退款线设计的布尔值顺手获得权限。
    """
    reasons: list[str] = []
    hits: list[str] = []
    cats = categories or set()

    def hit(rule: str, text: str) -> None:
        hits.append(rule)
        reasons.append(f"{rule}：{text}")

    def result(
        action: str,
        *,
        eligible: bool = False,
        amt: float = 0.0,
    ) -> Decision:
        return Decision(
            action,
            reasons,
            eligible,
            amt,
            hits,
            human_override_allowed=False,
            request_kind=request_kind,
        )

    def cause_text() -> str:
        return REQUEST_CAUSES.get(request_cause) or request_cause or "未指明"

    # ── R6：生鲜不支持**需要寄回商品**的售后 ────────────────
    # 与退款线同序：类目硬禁止排在客户级风控之前。
    fresh = cats & set(NON_REFUNDABLE_CATEGORIES)
    if fresh and request_kind in RETURN_REQUEST_KINDS:
        hit("R6", f"{'、'.join(sorted(fresh))} 属生鲜类目、不可二次销售，不支持需要寄回的售后，应致歉并转人工")
        return result("escalate")

    # ── R4（客户级风控）：与退款线共用同一套阈值 ──────────────
    # 黑名单与高风险是**客户属性**，与诉求类型无关，所以放在分派之前。
    if is_blacklisted:
        hit("R4", "该客户在黑名单中，必须转人工审批")
        return result("escalate")
    if risk_score > settings.risk_score_hitl_threshold:
        hit("R4", f"客户风险评分 {risk_score} 超过 {settings.risk_score_hitl_threshold}，必须转人工审批")
        return result("escalate")

    # ══════════ R9 补发（少发/漏发/发错/破损/丢件）══════════
    if request_kind == "reship":
        if order_status == "pending_paid":
            hit("R9", "订单尚未支付，不存在已发出的商品，无货可补")
            return result("reject")
        if order_status == "cancelled":
            hit("R9", "订单已取消，不存在漏发或破损，应说明情况并驳回")
            return result("reject")
        if reship_count >= settings.max_reships_per_order:
            hit("R9", f"该订单已有 {reship_count} 次补发记录，需人工核实是否重复索赔")
            return result("escalate")
        if not request_cause:
            # ★ 补发必须有理由 —— 缺理由就补发等于白送一件商品。
            hit("R9", "补发必须先确认少发了什么或哪里破损，需向客户澄清")
            return result("need_clarification")
        if request_cause not in ("missing", "wrong_item", "damaged", "lost", "quality"):
            hit("R9", f"补发只适用于少发/漏发/发错/破损/丢件，当前原因为「{cause_text()}」")
            return result("reject")
        if request_cause in PROOF_CAUSES and not evidence_provided:
            # ★ 与 R8/R10 同一条守卫：补发前先要凭证。
            #   "运输破损"要有照片、"发错货"要有收到的实物照片、"丢件"要有轨迹证明 ——
            #   没有这些，补发就是凭客户一句话白送一件商品。
            hit("R9", f"原因为「{cause_text()}」按政策需提供凭证，客户尚未提供，需先索取")
            return result("need_clarification")
        # ★ 丢件可以发生在**未签收**时，所以补发线允许 signed 之外的状态 ——
        #   这正是"物流 39.3% 是最大诉求类"落地的位置。
        hit("R9", f"原因为「{cause_text()}」，属平台责任，受理补发")
        return result("reship", eligible=True)

    # ══════════ R8 换货（需寄回旧品 + 寄出新品）══════════
    if request_kind == "exchange":
        if order_status == "pending_paid":
            hit("R8", "订单尚未支付，无货可换")
            return result("reject")
        if order_status == "cancelled":
            hit("R8", "订单已取消，无货可换，应说明情况并驳回")
            return result("reject")
        if order_status != "signed":
            # 在途货物：拦截、改派、还是等签收后拒收，取决于承运商实际状态，
            # 不是 agent 能独立决定的 → 转人工。
            hit("R8", "订单尚未签收，在途货物换货需人工判断是拦截还是等签收拒收")
            return result("escalate")
        ex_days = _days_since(signed_at)
        if ex_days is None:
            hit("R8", "订单缺少签收时间，无法确认是否在换货期限内")
            return result("escalate")
        if ex_days > settings.exchange_window_days:
            hit("R8", f"签收已 {ex_days} 天，超过 {settings.exchange_window_days} 天换货期限")
            return result("reject")
        if exchange_count >= settings.max_exchanges_per_order:
            hit("R8", f"该订单已有 {exchange_count} 次换货记录，再次换货需人工核实")
            return result("escalate")
        nonret = cats & set(NON_RETURNABLE_CATEGORIES)
        if nonret and request_cause not in ("quality", "wrong_item", "damaged"):
            # ★ R2 的语义是"不支持**无理由**"，不是"一概不能换" ——
            #   贴身衣物因卫生原因不能无理由换，但收到破损的当然能换。
            hit("R2", f"{'、'.join(sorted(nonret))} 不支持无理由换货（质量问题除外），当前原因为「{cause_text()}」")
            return result("reject")
        if request_cause in PROOF_CAUSES and not evidence_provided:
            # ★ R2/R9 的政策原文："签收后如有质量问题，需走质量投诉流程并提供凭证"。
            #
            #   在 `evidence_provided` 这一列出现之前，这段政策**只存在于文本里**：
            #   没有任何代码读它、没有任何工具能表达它。于是一个客户说
            #   "衣服破了我要换货"而照片还没给，系统照样判"受理换货"——
            #   政策承诺了一件事，系统从来没检查过它。这是缺陷 ⑧
            #   「能力只存在于声明里」的形状，只不过发生在政策文本而非代码里。
            #
            #   ★ 这里是 `need_clarification` 而不是 `reject`：缺凭证不是客户的错，
            #   是我们还没拿到判断所需的信息。替客户假设"他已经给过照片"
            #   （即默认 evidence_provided=True）会让这条分支永远测不到。
            hit("R2", f"原因为「{cause_text()}」按政策需提供凭证，客户尚未提供，需先索取")
            return result("need_clarification")
        hit("R8", f"签收 {ex_days} 天，在 {settings.exchange_window_days} 天换货期限内，受理换货")
        return result("exchange", eligible=True)

    # ══════════ R10 赔付（运费补偿 / 损失补偿）══════════
    if request_kind == "compensate":
        if order_status in ("pending_paid", "cancelled"):
            hit("R10", "订单未支付或已取消，不存在可赔付的损失")
            return result("reject")
        comp = float(request_amount or 0.0)
        if comp <= 0:
            hit("R10", "赔付金额未确定，需向客户确认金额或索取凭证")
            return result("need_clarification")
        if comp > settings.max_compensation_cny:
            # ★ 这是**权限边界**（agent 单笔能发多少），不是政策禁止 ——
            #   所以是转人工，而且不给"人工一键退整单"的口子（见函数头第 3 条）。
            hit("R10", f"赔付金额 {comp} 元超过 agent 权限上限 {settings.max_compensation_cny} 元，必须转人工审批")
            return result("escalate")
        if request_cause in PROOF_CAUSES and not evidence_provided:
            # 同 R8：赔钱之前先要凭证。金额是客户自己报的，没有凭证就没有依据。
            hit("R10", f"原因为「{cause_text()}」按政策需提供凭证，客户尚未提供，需先索取")
            return result("need_clarification")
        hit("R10", f"赔付 {comp} 元（{cause_text()}），在 agent 权限上限 {settings.max_compensation_cny} 元之内")
        return result("compensate", eligible=True, amt=comp)

    # ══════════ R11 价保退差（下单后降价）══════════
    if request_kind == "price_protect":
        if order_status in ("pending_paid", "cancelled"):
            hit("R11", "订单未支付或已取消，不存在价保退差")
            return result("reject")
        if current_price is None:
            # ★ 不给"猜一个当前售价"的余地：差额直接决定退多少钱。
            hit("R11", "未提供当前售价，无法计算降价差额，需先核实")
            return result("need_clarification")
        cur = float(current_price)
        if cur >= float(amount):
            hit("R11", f"当前售价 {cur} 元不低于下单金额 {amount} 元，不存在降价")
            return result("reject")
        pp_days = _days_since(paid_at)
        if pp_days is None:
            hit("R11", "订单缺少支付时间，无法确认是否在价保期限内")
            return result("escalate")
        if pp_days > settings.price_protect_window_days:
            hit("R11", f"下单已 {pp_days} 天，超过 {settings.price_protect_window_days} 天价保期限")
            return result("reject")
        diff = round(float(amount) - cur, 2)
        if diff > settings.refund_hitl_threshold_cny:
            hit("R4", f"价保差额 {diff} 元超过 {settings.refund_hitl_threshold_cny} 元，必须转人工审批")
            return result("escalate")
        hit("R11", f"下单 {pp_days} 天，售价由 {amount} 元降至 {cur} 元，退差额 {diff} 元")
        return result("price_protect", eligible=True, amt=diff)

    # ══════════ R12 开票 ══════════
    if request_kind == "invoice":
        if order_status == "pending_paid":
            hit("R12", "订单尚未支付，不能开具发票")
            return result("reject")
        if order_status == "cancelled":
            hit("R12", "订单已取消，不能开具发票")
            return result("reject")
        if invoice_issued:
            hit("R12", "该订单已开具发票，不得重复开票（如需重开应走红冲重开流程）")
            return result("reject")
        hit("R12", "订单已支付且未开票，受理开票")
        return result("issue_invoice", eligible=True)

    # ══════════ R13 修改收货地址 ══════════
    if request_kind == "address_change":
        if order_status == "signed":
            hit("R13", "订单已签收，收货地址已无意义，应说明情况并驳回")
            return result("reject")
        if order_status == "cancelled":
            hit("R13", "订单已取消，无需修改收货地址")
            return result("reject")
        if order_status == "shipped":
            # 地址已经交到承运商手里，系统改不动它 —— 只能人工联系承运商改派。
            hit("R13", "订单已发货，地址只能由承运商改派，需人工联系承运商")
            return result("escalate")
        if order_status in ("pending_paid", "paid"):
            # ★ 两种"未发货"都受理，但它们是不同的现场：
            #   `pending_paid` = 还没付钱（改了地址不影响任何已发生的事）
            #   `paid`         = 钱付了、货没发（**最该改地址的时刻**，改晚了就发错）
            #   缺陷 ㉖ 之前造数里根本没有 `paid`，所以 R13 的成功路径
            #   只能靠"未支付"的订单走到 —— 那是它最不该被验证的场景。
            hit("R13", "订单尚未发货，受理修改收货地址")
            return result("change_address", eligible=True)
        # ★ 刻意不用 `else` 兜底受理：那样将来新增任何订单状态，
        #   都会**静默地**变成"允许改地址"。默认动作要是安全的那个 ——
        #   不确定就转人工，而不是默认放行。
        hit("R13", f"订单状态 {order_status} 未知，无法判断能否改址，需人工确认")
        return result("escalate")

    # ══════════ R14 催单（催发货 / 催件）══════════
    if request_kind == "urge":
        if order_status == "signed":
            hit("R14", "订单已签收，物流已完结，无需催单")
            return result("reject")
        if order_status == "cancelled":
            hit("R14", "订单已取消，无需催单")
            return result("reject")
        if order_status == "pending_paid":
            hit("R14", "订单尚未支付，不存在待发货，应引导客户先完成支付")
            return result("reject")
        if last_urge_at is not None:
            lu = last_urge_at
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=dt.timezone.utc)
            gap_h = (clock.now() - lu).total_seconds() / 3600.0
            if gap_h < settings.urge_cooldown_hours:
                hit("R14", f"{gap_h:.1f} 小时前已催过单，{settings.urge_cooldown_hours} 小时冷却期内不得重复催单")
                return result("reject")
        hit("R14", "订单已支付且未签收，受理催单")
        return result("urge_logistics", eligible=True)

    # ★ 防漂移哨兵：新增了 REQUEST_KINDS 却没写规则时必须立刻炸，
    #   而不是静默返回一个默认动作（那会造出最难排查的一类事故）。
    raise ValueError(
        f"诉求类型 {request_kind!r} 已在 REQUEST_KINDS 中，但 _decide_service 里没有对应规则，"
        "请补齐规则分支（不要让它走默认放行）"
    )


def decide_for_order(
    order: Any,
    *,
    items: list[Any] | None = None,
    customer: Any = None,
    refund_count: int | None = None,
    request_kind: str = "refund",
    request_cause: str = "",
    request_amount: float | None = None,
    current_price: float | None = None,
    exchange_count: int | None = None,
    reship_count: int | None = None,
    last_urge_at: Any = None,
    invoice_issued: bool | None = None,
    evidence_provided: bool | None = None,
) -> Decision:
    """从 ORM 对象取字段后判定。工具层用这个。

    ★ `request_kind` 默认 `"refund"`：所有既有调用方（退款工具、golden、评测）
      不传它，行为与拆分诉求维度之前**完全一致**。新线必须显式传，
      这样"哪条线被走了"在调用点一眼可见，而不是靠 order 状态猜。
    """
    cats = {getattr(i, "category", None) for i in (items or [])}
    cats.discard(None)
    cats.add(getattr(order, "category", "") or "")
    cats.discard("")

    rc = refund_count
    if rc is None:
        rc = int(getattr(order, "refund_count", 0) or 0)

    ec = exchange_count
    if ec is None:
        ec = int(getattr(order, "exchange_count", 0) or 0)

    rsc = reship_count
    if rsc is None:
        rsc = int(getattr(order, "reship_count", 0) or 0)

    # ★ `current_price` 必须**和上面三个计数器一样**从 order 回落。
    #   它原先直接透传形参，而工具层（`registry.py` 的 `submit_after_sales_action`）
    #   并不传它 —— 于是 golden（显式传 order.current_price）判得出退差价，
    #   agent（不传 → None）只能判"未提供当前售价，需先核实"。
    #   症状是最难查的那种：报表上 agent 好像"算错了金额"，实际是**这张 case
    #   天生不可赢**——同一份输入，两处读法不一致。
    cp = current_price
    if cp is None:
        _cp_from_order = getattr(order, "current_price", None)
        cp = float(_cp_from_order) if _cp_from_order is not None else None

    # `last_urge_at` / `invoice_issued` 用哨兵值区分"没传"与"确实没有"：
    # 不传 → 从 ORM 对象读；显式传 None/False → 表示"这一次没有这个前提"。
    lu = last_urge_at if last_urge_at is not None else getattr(order, "last_urge_at", None)
    ii = (
        bool(invoice_issued)
        if invoice_issued is not None
        else bool(getattr(order, "invoice_issued", False))
    )
    # ★ `evidence_provided` 与上面两个同一个形状：工具层不传时从工单回落。
    #   它不在 order 上（凭证是**工单**的属性，不是订单的属性），所以回落源
    #   是调用方手里的 ticket —— 由 `registry.py` 显式传进来，见那里的注释。
    ep = bool(evidence_provided)

    return decide(
        order_status=str(getattr(order, "status", "") or ""),
        amount=float(getattr(order, "amount", 0.0) or 0.0),
        categories=cats,
        signed_at=getattr(order, "signed_at", None),
        is_blacklisted=bool(getattr(customer, "is_blacklisted", False)),
        risk_score=float(getattr(customer, "risk_score", 0.0) or 0.0),
        refund_count=rc,
        request_kind=request_kind,
        request_cause=request_cause,
        request_amount=request_amount,
        paid_at=getattr(order, "paid_at", None),
        current_price=cp,
        exchange_count=ec,
        reship_count=rsc,
        last_urge_at=lu,
        invoice_issued=ii,
        evidence_provided=ep,
    )
