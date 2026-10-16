"""契约一致性测试 —— 本项目最重要的一组测试。

## 为什么这组测试值一整个文件

在写评测 harness 之前，项目里有三个**静默缺陷**，它们都不会让任何代码报错，
只会让最终报告上的数字变得毫无意义：

1. **规则两处实现且顺序不一致**
   `check_refund_eligibility` 判 R6→R4→R5→R2→R3→R1，
   而 golden 生成 `_decide_golden` 判 R6→R4→R3→R2→R1→R5。
   凡同时命中 R5 与 R2/R3/R1 的订单：golden 要 reject，工具说 escalate。
   → agent 听了工具的话，反被判错。分数整体崩，报告只说"准确率低"。

2. **golden 与写工具副作用不对齐**
   golden 的 R4 分支额外造一条 `requested` 退款；但 agent 侧正确动作
   `escalate_to_human()` 只改工单状态、不写退款表。→ 所有 R4 case 必然失败。

3. **判定字段没有任何工具能写**
   `TICKET_FIELDS` 里含 `intent`，但全项目无一处写它（seed 时是空串）。
   → 每条 case 都注定在 intent 上失败，准确率里混进一个恒假项。

三个缺陷的共同特征：**没有任何异常，只是数字错**。
所以它们必须在测试里被钉死——这些断言不是在测"代码能不能跑"，
而是在测"评测结论能不能信"。
"""

from __future__ import annotations

import datetime as dt
from itertools import product
from types import SimpleNamespace

import pytest

from ticket_agent import rules
from ticket_agent.db.models import (
    NON_REFUNDABLE_CATEGORIES,
    NON_RETURNABLE_CATEGORIES,
)
from ticket_agent.db.snapshot import (
    TICKET_FIELDS,
    TICKET_OBSERVED_FIELDS,
)
from ticket_agent.tools.registry import ROLE_TOOL_MATRIX, WRITE_TOOLS, registry


# --------------------------------------------------------------------------
# 缺陷 3：判定字段必须"可写"——没有工具能写的字段不该上判定台
# --------------------------------------------------------------------------

def test_no_unwritable_field_is_judged() -> None:
    """判定字段必须能被某个工具写到，否则每 case 必然失败。

    `intent` 就是这么个字段：曾经参与判定，却无工具可写。
    现在它被移到 `TICKET_OBSERVED_FIELDS`，只用于诊断。
    """
    all_tools = set()
    for names in ROLE_TOOL_MATRIX.values():
        all_tools |= set(names)

    # 改造后的现状：判定字段只剩 status，它是可写的（close_ticket / escalate_to_human）
    assert "intent" not in TICKET_FIELDS, "intent 不可写，不得参与终态判定"
    assert "intent" in TICKET_OBSERVED_FIELDS, "intent 应保留为观测字段以便诊断"

    # 断言这些写工具确实存在（TICKET_FIELDS 的可写性依赖它们）
    assert {"close_ticket", "escalate_to_human"} <= all_tools


def test_judged_ticket_fields_are_all_settable_by_write_tools() -> None:
    """更本质的不变式：判定字段 ⊆ 写工具能改动的字段。

    这里用维护性白名单表述：判定字段每增加一个，都必须在此登记
    "由哪个写工具负责"，否则测试失败，强迫作者想清楚
    "这个字段到底能不能被 agent 改变"。
    """
    WRITABLE_BY: dict[str, str] = {
        "status": "close_ticket / escalate_to_human",
    }
    for f in TICKET_FIELDS:
        assert f in WRITABLE_BY, (
            f"判定字段 {f!r} 未登记写入方：没有工具能改它的话，"
            "每条 case 都会在它上面失败，做出一个恒假项。"
        )
        assert f in TICKET_OBSERVED_FIELDS or f == "status"


# --------------------------------------------------------------------------
# 缺陷 1：规则判定顺序 —— 唯一事实来源
# --------------------------------------------------------------------------

def test_rule_precedence_order_is_the_documented_one() -> None:
    """顺序本身是契约，钉死它。改动必须是有意识的。"""
    assert rules.RULE_PRECEDENCE == (
        "R6",
        "R4",
        "R5",
        "R2",
        "R3",
        "CANCELLED",
        "UNPAID",
        "R1",
    )


def test_precedence_is_respected_when_multiple_rules_hit() -> None:
    """同时命中多条规则时，必须按 RULE_PRECEDENCE 的顺序取结论。

    这条专门覆盖缺陷 1：R5（已退过）与 R2（内衣）同时命中时，
    两个消费者必须给出一致的动作。
    """
    # 内衣 + 已退过一次 → R5 在 R2 之前，应判 escalate 而不是 reject
    d = rules.decide(
        order_status="signed",
        amount=199.0,
        categories={"内衣"},
        signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2),
        refund_count=1,
    )
    assert d.action == "escalate", "R5 排在 R2 之前，已退过应优先转人工"
    assert "R5" in d.rule_hits


def test_fresh_and_returnable_gets_refund() -> None:
    """基线：生鲜之外、期限内、未退过 → refund。"""
    d = rules.decide(
        order_status="signed",
        amount=88.0,
        categories={"服饰"},
        signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        refund_count=0,
    )
    assert d.action == "refund"
    assert d.eligible is True


def test_r6_beats_r4() -> None:
    """R6（生鲜完全不退）优先级高于 R4（转人工）—— 政策里写明了。"""
    d = rules.decide(
        order_status="signed",
        amount=9999.0,  # 会命中 R4 大额
        categories={"生鲜"},
        signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        is_blacklisted=True,  # 也命中 R4
        refund_count=0,
    )
    assert "R6" in d.rule_hits
    assert d.action == "escalate"


# --------------------------------------------------------------------------
# 缺陷 2：golden 与写工具的副作用必须对齐
# --------------------------------------------------------------------------

def test_escalate_does_not_write_refund() -> None:
    """转人工只改工单状态，不写退款表。

    这条覆盖缺陷 2：golden 曾以为 escalate 会造一条 requested 退款，
    而 `escalate_to_human()` 实际不写 → 所有 R4 case 必然失败。
    """
    # 大额 → escalate
    d = rules.decide(
        order_status="signed",
        amount=1000.0,
        categories={"服饰"},
        signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        refund_count=0,
    )
    assert d.action == "escalate"


def test_write_tools_are_declared() -> None:
    """写工具集合是权限裁剪与副作用对齐的共同依赖。

    ★ 本条原先只钉四个工具，加 `submit_after_sales_action` 时它按设计报警了。
    但**报警方式值得改进**：原来是硬编码一个集合，意味着每加一个写工具都要
    手工把期望值改宽一次 —— 而"手工改宽"正是这类哨兵失效的标准路径
    （改着改着就顺手把漏掉的那个也加进去了）。

    所以现在改成**双向检查**：
      1. 白名单必须是显式枚举（防止有人改成从注册表自动推导，那它就不再是哨兵）；
      2. 每个写工具都必须在 `ROLE_TOOL_MATRIX` 里至少被一个角色拿到 ——
         这一条才是真正会抓 bug 的，它抓的正是缺陷 ㉒ 的形状
         （"工具写好了，但四个角色谁都拿不到"）。
    """
    assert WRITE_TOOLS == frozenset(
        {
            "create_refund",
            "block_order",
            "close_ticket",
            "escalate_to_human",
            "submit_after_sales_action",
            "mark_evidence_received",
            # #2 多轮澄清闭环：把"在等客户回答什么"落库的写入口。
            # 它必须进 WRITE_TOOLS —— 否则它会进只读去重缓存，
            # 第二次提问会"回放"第一次的结果，而库里那列从未被真写过。
            "ask_customer",
        }
    )

    granted = set()
    for tools in ROLE_TOOL_MATRIX.values():
        granted |= set(tools)

    orphans = set(WRITE_TOOLS) - granted
    assert not orphans, (
        f"这些写工具没有被任何角色拿到，等于不存在：{sorted(orphans)}"
        "（缺陷 ㉒ 的形状：能力只存在于声明里）"
    )


# --------------------------------------------------------------------------
# 工具层与规则层的一致性：工具结论必须由 rules.decide 推导
# --------------------------------------------------------------------------

def test_eligibility_tool_delegates_to_rules_module() -> None:
    """工具层不得重新实现规则 —— 只能委托。

    用源码级检查：`check_refund_eligibility` 的函数体里必须出现
    `decide_for_order`/`decide`，且不得再出现被淘汰的规则常量判据，
    防止有人"顺手"在工具里又写一遍规则。
    """
    import inspect

    # 注意：不能写 `from ticket_agent.tools import registry`——
    # 那是包级 re-export 的 **ToolRegistry 实例**，会遮住同名子模块。
    # 直接从子模块取函数。
    from ticket_agent.tools.registry import check_refund_eligibility

    src = inspect.getsource(check_refund_eligibility)
    assert "decide_for_order" in src, "工具必须委托 rules.decide_for_order"
    # 不得再自己拼 cats & NON_RETURNABLE_CATEGORIES 这类判断
    assert "NON_RETURNABLE_CATEGORIES" not in src
    assert "NON_REFUNDABLE_CATEGORIES" not in src


def test_seed_golden_delegates_to_rules_module() -> None:
    """golden 生成同样只能委托 rules.decide。"""
    import inspect

    from ticket_agent.db import seed as seed_mod

    src = inspect.getsource(seed_mod._decide_golden)
    assert "decide(" in src, "golden 必须委托 rules.decide"
    assert "NON_RETURNABLE_CATEGORIES" not in src
    assert "NON_REFUNDABLE_CATEGORIES" not in src


# --------------------------------------------------------------------------
# 全量枚举：规则两种状态组合下，工具与 golden 必须逐条一致
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,category,days,amount,blacklisted,refund_count",
    [
        # 每一条都是"曾经会不一致"或"边界"的组合
        ("signed", "服饰", 1, 100.0, False, 0),      # 正常可退
        ("signed", "内衣", 1, 100.0, False, 0),      # R2
        ("signed", "内衣", 1, 100.0, False, 1),      # R5 vs R2 ← 缺陷 1
        ("signed", "定制", 9, 100.0, False, 1),      # R5 vs R1
        ("signed", "生鲜", 1, 100.0, False, 0),      # R6
        ("signed", "生鲜", 1, 9999.0, True, 0),      # R6 vs R4
        ("signed", "服饰", 1, 9999.0, False, 0),     # R4 大额 ← 缺陷 2
        ("signed", "服饰", 1, 100.0, True, 0),       # R4 黑名单
        ("signed", "服饰", 9, 100.0, False, 0),      # R1 超期
        ("shipped", "服饰", None, 100.0, False, 0),  # R3 拦截物流
        ("cancelled", "服饰", None, 100.0, False, 0),  # 已取消
        ("pending_paid", "服饰", None, 100.0, False, 0),  # 未支付
    ],
)
def test_decide_is_deterministic_across_consumers(
    status: str, category: str, days: int | None, amount: float,
    blacklisted: bool, refund_count: int,
) -> None:
    """同一个输入，两次调用必须完全一致（纯函数不变式）。

    这是"工具与 golden 用同一个函数"的可验证形式：
    只要 `decide` 是纯函数，两个消费者就不可能给出不同结论。
    """
    signed = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        if days is not None
        else None
    )
    kwargs = dict(
        order_status=status,
        amount=amount,
        categories={category},
        signed_at=signed,
        is_blacklisted=blacklisted,
        risk_score=0.9 if blacklisted else 0.1,
        refund_count=refund_count,
    )
    a = rules.decide(**kwargs)
    b = rules.decide(**kwargs)
    assert a.action == b.action
    assert a.reasons == b.reasons
    assert a.rule_hits == b.rule_hits


def test_every_action_maps_to_a_legal_terminal_state() -> None:
    """每个动作都必须能被 golden 映射到合法终态。

    防止新增动作后 `_decide_golden` 漏掉分支（那里会 raise，这里提前发现）。

    ★ 这条原先硬编码五个动作，加七个售后动作时按设计报警了。
    但它**只覆盖退款线**（`rules.decide` 是退款线的入口），
    所以断言集合必须按线拆开写，否则它会让"服务线的动作能不能落成终态"
    这件事看起来已经被测过了 —— 那是比不测更糟的假安全感。
    """
    refund_line = {
        "refund",
        "reject",
        "block_logistics",
        "escalate",
        "need_clarification",
    }
    service_line = {
        "exchange",
        "reship",
        "compensate",
        "price_protect",
        "issue_invoice",
        "change_address",
        "urge_logistics",
    }
    assert set(rules.ACTIONS) == refund_line | service_line

    # ★ 服务线的动作必须在 `_AFTER_SALES_GOLDEN` 里有落点 —— 这是本条的真正价值：
    #   规则层新增了动作、而 golden 忘了映射时，`_decide_golden` 会在**跑到那条
    #   case 时**才 raise，评测跑到一半才炸；这里能在收集阶段就红。
    from ticket_agent.db.seed import _AFTER_SALES_GOLDEN

    assert set(_AFTER_SALES_GOLDEN) == service_line, (
        "服务线动作与 golden 映射表不一致："
        f"漏了 {sorted(service_line - set(_AFTER_SALES_GOLDEN))}，"
        f"多了 {sorted(set(_AFTER_SALES_GOLDEN) - service_line)}"
    )

    # 穷举退款线参数空间，确认任何输入都落在五种动作之内
    seen: set[str] = set()
    for status, cat, rc, black in product(
        ["signed", "shipped", "cancelled", "pending_paid"],
        ["服饰", "内衣", "生鲜", "定制"],
        [0, 1],
        [False, True],
    ):
        d = rules.decide(
            order_status=status,
            amount=600.0 if black else 50.0,
            categories={cat},
            signed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
            is_blacklisted=black,
            refund_count=rc,
        )
        seen.add(d.action)
    assert seen <= set(rules.ACTIONS), f"出现未登记动作: {seen - set(rules.ACTIONS)}"

    # ★ 服务线：每种诉求都必须**在合法前提下真的产出一个动作**。
    #   这一条抓的是另一种失效 —— 规则分支写了、但条件永远满足不了
    #   （例如 R11 价保要求 `current_price`，若调用方从不传，价保分支就是死代码，
    #   而"死代码"和"没有这条规则"在报表上长得一模一样：都是 0 条 case）。
    now = dt.datetime.now(dt.timezone.utc)
    order = SimpleNamespace(
        status="signed",
        amount=120.0,
        category="服饰",
        signed_at=now - dt.timedelta(days=2),
        paid_at=now - dt.timedelta(days=5),
        refund_count=0,
        exchange_count=0,
        reship_count=0,
        last_urge_at=None,
        invoice_issued=False,
    )
    produced: dict[str, set[str]] = {}
    for kind in sorted(rules.REQUEST_KINDS):
        acts: set[str] = set()
        for status in ["signed", "shipped", "paid", "cancelled", "pending_paid"]:
            order.status = status
            for cause in ["missing", "quality", ""]:
                try:
                    d = rules.decide_for_order(
                        order,
                        items=[],
                        customer=SimpleNamespace(is_blacklisted=False, risk_score=0.0),
                        request_kind=kind,
                        request_cause=cause,
                        request_amount=20.0,
                        current_price=100.0,
                    )
                except ValueError:
                    continue  # 某些诉求×状态组合本就无意义，规则抛错是设计的一部分
                acts.add(d.action)
        produced[kind] = acts

    for kind, acts in produced.items():
        want = rules.expected_action_for(kind)
        assert acts, f"诉求 {kind!r} 在任何合法前提下都产不出动作（规则分支不可达）"
        assert want in acts, (
            f"诉求 {kind!r} 的预期动作 {want!r} 从未被产出，实际只产出 {sorted(acts)} "
            "—— 要么规则写死了，要么前提条件永远凑不齐"
        )
