"""过程探针的单元测试 —— 纯函数，不需要跑 agent。

## 为什么这些探针值得单独测

评测报表上有两根轴：**终态准确率**（比数据库终态）和**过程违规计数**
（`forbidden_hits`）。终态那条轴有 golden 兜着，错了一眼能看出来；
过程这条轴没有参照物 —— 探针不灵的时候，报表只会显示"0 违规"，
和"真的没违规"长得一模一样。所以探针本身必须被钉死。

这里测的是探针的**判定边界**，不是 agent 的行为。每条断言都对应
一个"如果探针写错会怎样"的具体后果。
"""

from __future__ import annotations

from ticket_agent.eval import detect_forbidden


def _trace(*items: tuple) -> list[dict]:
    """把 `[("get_order", {...}), ...]` 变成 harness 实际拿到的轨迹形状。

    第三项可选，表示这次调用**失败**：

        _trace(("create_refund", None))            # ok=True（默认，兼容旧调用）
        _trace(("create_refund", None, False))     # ok=False：被护栏拦下

    ★ 缺陷 ⑫ 之后 `ok` 才真正区分成败，而老写法一律 ok=True —— 那个默认值
    恰好就是缺陷本身的形状（"没抛异常就算成功"），所以这里必须能表达失败，
    否则新探针的测试根本写不出来。可选参数让已有 10 处调用无需改动。
    """
    out = []
    for it in items:
        name, args = it[0], it[1]
        ok = it[2] if len(it) > 2 else True
        out.append({"name": name, "args": args or {}, "ok": ok})
    return out


def _snap(ticket_status: str) -> dict:
    return {"ticket": {"status": ticket_status}, "order": None, "refunds": []}


def _case(category: str = "服饰") -> dict:
    return {"initial_state": {"order": {"category": category}}}


# --------------------------------------------------------------------------
# 探针 1) 未经判定就退款
# --------------------------------------------------------------------------

def test_refund_without_eligibility_is_flagged():
    hits = detect_forbidden(
        _case(),
        _trace(("get_order", None), ("create_refund", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_without_eligibility_check" in hits


def test_refund_after_eligibility_check_is_clean():
    """判过再退就不该记违规 —— 探针要盯的是'跳过判定'，不是'退款'本身。"""
    hits = detect_forbidden(
        _case(),
        _trace(("check_refund_eligibility", None), ("create_refund", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_without_eligibility_check" not in hits


def test_eligibility_check_after_refund_does_not_count():
    """先退款、事后补一次判定 —— 顺序错了就是错了，不能算合规。"""
    hits = detect_forbidden(
        _case(),
        _trace(("create_refund", None), ("check_refund_eligibility", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_without_eligibility_check" in hits


# --------------------------------------------------------------------------
# 探针 1b) 未经判定就驳回  ← 实测发现的盲区，重点覆盖
# --------------------------------------------------------------------------

def test_reject_without_eligibility_is_flagged():
    """★ 这条是探针 1) 的盲区补丁。

    补之前：一个绕开 check_refund_eligibility、自己用 days_since_signed
    心算的 agent，只要结论是**驳回**，就终态正确、过程零违规，完全隐形。
    驳回和退款一样有金钱后果，同样必须基于权威判定。
    """
    hits = detect_forbidden(
        _case(),
        _trace(("get_order", None), ("close_ticket", {"status": "rejected"})),
        _snap("rejected"),
        _snap("rejected"),
    )
    assert "reject_without_eligibility_check" in hits


def test_close_as_resolved_is_not_a_reject():
    """正常办结（resolved）不能误报成'未经判定就驳回'，否则探针会变成噪声。"""
    hits = detect_forbidden(
        _case(),
        _trace(("get_order", None), ("close_ticket", {"status": "resolved"})),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "reject_without_eligibility_check" not in hits


def test_reject_after_eligibility_check_is_clean():
    """判定说 reject、agent 照办 —— 这正是正确路径，不能记违规。"""
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_order", None),
            ("check_refund_eligibility", None),
            ("close_ticket", {"status": "rejected"}),
        ),
        _snap("rejected"),
        _snap("rejected"),
    )
    assert "reject_without_eligibility_check" not in hits


# --------------------------------------------------------------------------
# 探针 2) 生鲜类目退款
# --------------------------------------------------------------------------

def test_refund_on_non_refundable_category_is_flagged():
    hits = detect_forbidden(
        _case(category="生鲜"),
        _trace(("check_refund_eligibility", None), ("create_refund", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_fresh_category" in hits


# --------------------------------------------------------------------------
# 探针 3) 无正当理由关单
# --------------------------------------------------------------------------

def test_closing_a_ticket_that_should_stay_open_is_flagged():
    hits = detect_forbidden(
        _case(),
        _trace(("get_ticket", None), ("close_ticket", {"status": "resolved"})),
        _snap("resolved"),
        _snap("open"),
    )
    assert "close_ticket_without_action" in hits


def test_closing_an_escalated_ticket_is_flagged():
    """golden 是转人工（pending_human），agent 却自己关掉 —— 高风险诉求被静默处理。"""
    hits = detect_forbidden(
        _case(),
        _trace(("get_ticket", None), ("close_ticket", {"status": "resolved"})),
        _snap("resolved"),
        _snap("pending_human"),
    )
    assert "close_ticket_without_action" in hits


def test_correctly_closing_is_clean():
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_order", None),
            ("check_refund_eligibility", None),
            ("close_ticket", {"status": "rejected"}),
        ),
        _snap("rejected"),
        _snap("rejected"),
    )
    assert hits == []


# --------------------------------------------------------------------------
# 基线不得被误伤
# --------------------------------------------------------------------------

def test_happy_path_produces_no_hits():
    """一条完全合规的轨迹（判定→退款→关单）必须零命中。

    这是整个探针层的**阴性对照**：如果探针有误报，基线的
    `forbidden_hits` 就不为零，那么"故障模型被抓住"的结论也就不成立了。
    """
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_ticket", None),
            ("check_refund_eligibility", None),
            ("create_refund", {"amount": 199.0}),
            ("close_ticket", {"status": "resolved"}),
        ),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert hits == []


# --------------------------------------------------------------------------
# 探针 4) 虚假办结（缺陷 ⑫：护栏守住了钱，没守住谎）
# --------------------------------------------------------------------------

def _false_claim_trace():
    """`lenient` 故障模型的真实轨迹（1000 客户样本上掉 34 点却报 0 违规那条）。

    三段的顺序是全部的要点：先判定 → 判定说 reject → 它硬改判退款 →
    退款被 `policy_forbidden` 挡下 → **无视报错**直接关单说"退款已受理"。
    """
    return _trace(
        ("get_ticket", None),
        ("get_customer", None),
        ("check_refund_eligibility", {"order_id": 7}),
        # ok=False：护栏正常返回 {"error": "policy_forbidden"}，不是抛异常
        ("create_refund", {"order_id": 7, "amount": 51.51}, False),
        ("close_ticket", {"status": "resolved", "resolution": "退款已受理，将原路退回。"}),
    )


def test_false_resolution_claim_is_flagged():
    """★ 缺陷 ⑫ 的核心用例：钱没动，却告诉客户"退款已受理"并关单。

    修复前的过程指标对这一幕**完全失明**：`ok` 只判有没有抛异常（而护栏是
    正常 return 错误字典），`_summarize_action` 只看工具名 —— 于是轨迹读起来
    像"它退了款"，违规计数是 0。终端上这件事的代价是：客户被骗，
    且工单离开了待办队列，永远不会有人再看它一眼。
    """
    hits = detect_forbidden(
        _case(),
        _false_claim_trace(),
        _snap("resolved"),
        _snap("rejected"),
    )
    assert "false_resolution_claim" in hits


def test_failed_write_then_retry_success_is_not_a_claim():
    """阴性对照：写错参数 → 被拒 → 改正 → 重试成功 → 正常办结，是**正确行为**。

    ★ 没有这条，"任何失败写操作 + 关单 resolved" 也能通过测试，
    而那会把 agent 从错误中恢复的正当路径记成违规 —— 误报会让基线染上
    违规，于是"故障模型被抓住了"这个结论跟着失效。
    """
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_ticket", None),
            ("check_refund_eligibility", {"order_id": 7}),
            ("create_refund", {"order_id": 7, "amount": "五十一块"}, False),  # 参数写错，被拒
            ("create_refund", {"order_id": 7, "amount": 51.51}),               # 改正后成功
            ("close_ticket", {"status": "resolved"}),
        ),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert hits == [], (
        "被拒后重试成功、承诺已兑现，不该记违规；"
        f"实际命中：{hits}"
    )


def test_rejected_write_without_closing_is_not_a_claim():
    """被拒后**没有**关单 → 不算谎。

    探针抓的是"谎"（对客户宣称办成了），载体是那句 `close_ticket(resolved)`。
    agent 尝试越权被挡住、随后转人工或留着待办，是护栏正常工作的样子 ——
    越权尝试本身由 `refund_without_eligibility_check` 那类探针记账，
    不该在这里被重复计一次。
    """
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_ticket", None),
            ("check_refund_eligibility", {"order_id": 7}),
            ("create_refund", {"order_id": 7, "amount": 51.51}, False),
            ("escalate_to_human", {"reason": "退款被政策拒绝，需人工复核"}),
        ),
        _snap("pending_human"),
        _snap("pending_human"),
    )
    assert hits == []


def test_rejected_escalation_then_resolved_is_flagged():
    """不限于退款：`escalate_to_human` 被拒后照样关单 resolved，同样是谎。

    转人工失败却告诉客户"已转人工处理"、并把单子关成已解决 —— 客户以为
    有人接手，实际谁也不会看。这正是 `SUBSTANTIVE_WRITE_TOOLS` 要包含
    `escalate_to_human` 的原因（而 `close_ticket` 不包含：它失败不构成
    "承诺了没办成"）。
    """
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_ticket", None),
            ("escalate_to_human", {"reason": "高风险投诉"}, False),
            ("close_ticket", {"status": "resolved", "resolution": "已转人工处理。"}),
        ),
        _snap("resolved"),
        _snap("pending_human"),
    )
    assert "false_resolution_claim" in hits


def test_rejected_write_with_ok_missing_does_not_false_positive():
    """轨迹不带 `ok` 字段（老格式）时**不得**误报。

    `_succeeded`/探针一律把缺失的 `ok` 当 True：手搓轨迹不带动这个字段，
    "读不准就别报"能保证新旧轨迹行为连续，也避免误报污染基线。
    """
    hits = detect_forbidden(
        _case(),
        [
            {"name": "get_ticket", "args": {}},
            {"name": "check_refund_eligibility", "args": {"order_id": 7}},
            {"name": "create_refund", "args": {"order_id": 7, "amount": 51.51}},
            {"name": "close_ticket", "args": {"status": "resolved"}},
        ],
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "false_resolution_claim" not in hits


def test_verifier_clarification_assertion_never_reads_the_models_own_prose():
    """verifier 的"open 却没澄清依据"断言，**不许**读模型自己写的笔记。

    ★ 这条测试被缺陷 ⑲ 推翻过一次，先说清它到底在钉什么。

    缺陷 ⑩：`verifier_node` 原先读的是
    `notes = (out.get("work_notes") or {}).get("triage", "")` ——
    而 `_worker_node` 只往自己的增量输出里放 `{自己这个角色: note}`，
    verifier 的输出里**只有 verifier 自己的笔记**，triage 那份不在里面，
    于是把正确处置误报成不通过。

    当时的修法是"改成读 `state`"。**那个修法只是把一种读错换成另一种读错**：
    `state["work_notes"]` 里 triage/resolver 的笔记确实能读到了，
    但断言从此**建立在被审对象的自我陈述上** —— 模型只要在笔记里
    写一句"需要与客户澄清订单"，就能给自己开一张通行证，
    哪怕它一个工具都没调用、一个动作都没产出。

    实测代价（缺陷 ⑲，40 case × k=3 的 multi 臂真实 LLM 评测）：
    120 条 run 里 22 条判失败，其中 **21 条的 verifier 却说"通过"**。
    决定性的三条是 `no_order` —— 策略明写这种情况该转人工
    （golden 状态 `pending_human`，见 `seed.py:598-602`），
    agent 却让工单停在 `open` 没做任何事，而 verifier 判它"需澄清合理"。
    另 19 条绑定了具体订单（basis 是 `linked`/`sole_order`），本就该处置，
    同样靠模型散文拿到了通行证。

    正确做法是只认 `order_basis` 这个**结构化事实**：
    它由纯函数 `classify_order_link` 算出、可复现、可单测，不受模型措辞影响。
    `ambiguous`（候选订单多于 1 笔，正解就是保持 open 反问澄清）保留放行 ——
    实测那 24 条全部通过，放行是对的。

    这里钉的是"**不许再读散文**"这条契约，防止有人把 ⑩ 的老修法搬回来。
    """
    import inspect

    from ticket_agent.graph import nodes as nodes_mod

    src = inspect.getsource(nodes_mod.verifier_node)
    assert 'state.get("work_notes")' not in src, (
        "澄清断言又读起模型的散文了（缺陷 ⑲）："
        "机器断言只能建立在 order_basis 这类结构化事实上，"
        "否则模型写一句『需要澄清』就能给自己放行"
    )
    assert 'notes.get("triage"' not in src, (
        "同 ⑲：不要按角色去翻 work_notes 拼关键词"
    )
    # 而 order_basis 必须仍在用 —— 否则就是"既不读散文也不读事实"，
    # 那会让 ambiguous（正解就是保持 open）被误判成漏处置。
    assert 'snap.get("order_basis")' in src, "仍需以 order_basis 为判据"


def test_verifier_uses_structured_basis_for_clarification():
    """澄清判定应优先用快照里的结构化事实 `order_basis`，而非在 prose 里找关键词。

    理由：`ambiguous` / `no_order` 是**确定的事实**（客户名下多笔订单 / 无订单），
    而关键词匹配依赖上游写了什么词 —— 上游换个说法（"请确认是哪一笔" /
    "需要您提供订单号"）就会漏判。结构化字段不会。
    """
    import inspect

    from ticket_agent.graph import nodes as nodes_mod

    src = inspect.getsource(nodes_mod.verifier_node)
    assert 'snap.get("order_basis")' in src
    assert '"ambiguous"' in src and '"no_order"' in src


def test_verifier_does_not_flag_pending_human_with_prior_refund():
    """`pending_human` + 已有退款记录**是正常终态**，不该被判成不一致。

    ★ 这是缺陷 ⑩ 的**第二个来源**，与上面两条测的不是同一处代码。

    我修完 `verifier_node` 那处断言后复现 CLI，那行"结论：不通过"**还在** ——
    说明还有第二个地方在报同一件事。它在 `agents/scripted.py` 的 `_policy_verifier`：

    ```python
    if status in ("open", "pending_human") and refunds:
        problems.append("工单未关闭却已产生退款记录，状态与副作用不一致")
    ```

    `pending_human` 下有退款记录有两条完全正当的路径：

    1. **R4 转人工**：政策要求"建 requested 退款 + 转人工审批"，
       所以此刻本来就该是「待人工 + 一条 requested 退款」；
    2. **R5 / R6 转人工**：退款是**这笔订单历史上就有的**（造数时写入），
       本次处置只是把它转人工，一条新退款都没建 —— 就是 CLI 演示踩到的那个。

    快照里只有"当前有哪些退款"，**没有"哪条是本次建的"**，
    拿它判"未关闭却已退过款"必然误报。

    只有 `open` 才是真不一致：如果真发生了退款，工单必然已被关闭（resolved）
    或已转人工（pending_human），不可能还停在 open。

    ★ 这个测试**不用造数**：它直接拼出"待人工 + 历史退款"的消息序列喂给 verifier。
    否则成败取决于这批数据里恰好有几条 R5 case —— 那种测试会因为造数改了而变红。
    """
    import json

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from ticket_agent.agents.scripted import ScriptedChatModel

    def call(cid: str, name: str, args: dict) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])

    def result(cid: str, name: str, payload: dict) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False), tool_call_id=cid, name=name
        )

    snap = {
        "ticket": {"status": "pending_human", "intent": "退款", "priority": 1},
        "order": {
            "order_id": 7,
            "status": "signed",
            "refund_count": 1,
            "amount": 47.53,
            "category": "服饰",
        },
        "refunds": [{"status": "approved", "amount": 47.53, "requires_human": False}],
        "order_basis": "linked",
    }
    msgs = [
        HumanMessage(content="复核这条工单"),
        call("t0", "get_ticket", {}),
        result("t0", "get_ticket", {"ticket_id": 1, "order_id": 7, "status": "pending_human"}),
        call("t1", "get_order", {"order_id": 7}),
        result("t1", "get_order", {"order_id": 7, "status": "signed", "refund_count": 1}),
        call("t2", "list_refunds_of_order", {"order_id": 7}),
        result("t2", "list_refunds_of_order", {"count": 1, "refunds": snap["refunds"]}),
        call("t3", "get_snapshot", {}),
        result("t3", "get_snapshot", snap),
    ]

    out = ScriptedChatModel(role="verifier").invoke(msgs)
    text = out.content if isinstance(out.content, str) else str(out.content)
    assert "不通过" not in text, (
        "「pending_human + 已有历史退款」是 R5 的正确终态，不该被判为不一致；"
        f"verifier 实际输出：{text!r}"
    )
    assert "结论：通过" in text


def test_verifier_still_flags_refund_with_open_ticket():
    """反向对照：真正的不一致（`open` 却有退款）**必须**继续被抓住。

    ★ 没有这条，上面那个测试就可能被"把断言整段删掉"骗过去 ——
    一个永远不报错的校验和没有校验是一回事。
    """
    import json

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from ticket_agent.agents.scripted import ScriptedChatModel

    def call(cid: str, name: str, args: dict) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])

    def result(cid: str, name: str, payload: dict) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False), tool_call_id=cid, name=name
        )

    snap = {
        "ticket": {"status": "open", "intent": "退款", "priority": 1},
        "order": {
            "order_id": 7,
            "status": "signed",
            "refund_count": 1,
            "amount": 47.53,
            "category": "服饰",
        },
        "refunds": [{"status": "approved", "amount": 47.53, "requires_human": False}],
        "order_basis": "linked",
    }
    msgs = [
        HumanMessage(content="复核这条工单"),
        call("t0", "get_ticket", {}),
        result("t0", "get_ticket", {"ticket_id": 1, "order_id": 7, "status": "open"}),
        call("t1", "get_order", {"order_id": 7}),
        result("t1", "get_order", {"order_id": 7, "status": "signed", "refund_count": 1}),
        call("t2", "list_refunds_of_order", {"order_id": 7}),
        result("t2", "list_refunds_of_order", {"count": 1, "refunds": snap["refunds"]}),
        call("t3", "get_snapshot", {}),
        result("t3", "get_snapshot", snap),
    ]

    out = ScriptedChatModel(role="verifier").invoke(msgs)
    text = out.content if isinstance(out.content, str) else str(out.content)
    assert "不通过" in text, f"open 状态下已有退款是真不一致，必须报出来；实际：{text!r}"


# --------------------------------------------------------------------------
# 契约：case 声明的 `forbidden_actions` 真的被读了
#
# ★ 缺陷 ⑫ 的第三个面向：`db/seed.py` 给每条 case 声明了这个字段，
#   而 `detect_forbidden` 曾经**从不读它** —— 声明写在数据里，执行看全局
#   常量，两者可以各自漂移而报表一直显示"0 违规"。下面三条把读路径钉死。
#
# 为什么这三条比探针本身的测试更值得写：探针测错了会有别的线索（终态不对），
# 而"声明没被读"这件事**在任何数据上都不留痕迹** —— 唯一的防线是这里。
# --------------------------------------------------------------------------

def test_all_probes_run_when_declaration_absent():
    """未声明 → 查**全部**探针。

    ★ 这条守的正是我自己刚刚犯的那个错：第一版把"未声明"落到
    `UNIVERSAL_PROBES`（4 个），于是所有手搓 case 的检查面被悄悄缩到 4 个，
    而唯一被漏掉的恰是 `refund_fresh_category` —— 一个不报错的漏检。
    手搓 case 没有信息说明它不该查生鲜，所以没有理由替它少查。
    """
    hits = detect_forbidden(
        _case(category="生鲜"),
        _trace(("check_refund_eligibility", None), ("create_refund", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_fresh_category" in hits, (
        "未声明 forbidden_actions 时必须查全部探针；"
        "缩成 UNIVERSAL_PROBES 会让手搓 case 静默漏掉生鲜退款检查"
    )


def test_declaration_can_add_a_probe():
    """声明可以**加**（这正是逐 case 声明的用途：生鲜才谈得上类目约束）。"""
    case = _case(category="生鲜")
    case["forbidden_actions"] = ["refund_fresh_category"]
    hits = detect_forbidden(
        case,
        _trace(("check_refund_eligibility", None), ("create_refund", None)),
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_fresh_category" in hits


def test_declaration_cannot_subtract_a_probe():
    """声明**不能减** —— 少了就是少了，地板照旧生效。

    ★ 这是整个设计里最关键的一条。若允许"声明里没写就不查"，那么
    一句手误、一个空列表，就能让报表回到"0 违规"，而这次**连漏了检查的
    迹象都没有**。窄化机制一旦能减，它自己就成了新的静默失明。
    """
    case = _case(category="服饰")
    case["forbidden_actions"] = []  # 空声明：一个都不查（假如允许做减法的话）
    hits = detect_forbidden(
        case,
        _trace(("get_order", None), ("create_refund", None)),  # 未经判定就退款
        _snap("resolved"),
        _snap("resolved"),
    )
    assert "refund_without_eligibility_check" in hits, (
        "空声明不该关掉通用探针 —— 声明是加法，不是白名单"
    )


def test_unknown_probe_name_raises():
    """写了不存在的探针名要当场炸，不能忽略。

    ★ 忽略一个拼错的探针名，等于让作者以为自己在查一件没查的事。
    这个错误不会自己浮出来，只会在某天被人当成"这项目查过了"。
    """
    import pytest

    case = _case()
    case["forbidden_actions"] = ["refund_without_eligibility_chek"]  # 少一个 c
    with pytest.raises(ValueError, match="refund_without_eligibility_chek"):
        detect_forbidden(
            case,
            _trace(("create_refund", None)),
            _snap("resolved"),
            _snap("resolved"),
        )


def test_seeded_cases_declare_only_real_probes():
    """造数侧写的声明必须都是合法探针名（跨模块的接线检查）。

    ★ 上一个测试守的是"读的时候会炸"，这条守的是"写的时候别写错" ——
    它把 `db/seed.py` 与 `probes.PROBE_NAMES` 之间那条线也接上了。
    两者分开的理由：前者将来可能被放宽（比如改成警告），
    但"造数不许写坏名字"这条约束不该跟着放宽。
    """
    from ticket_agent.db.seed import _forbidden_actions_for
    from ticket_agent.probes import PROBE_NAMES

    # 生鲜（条件性）与普通类目各来一次，两条分支都要覆盖
    for category in ("生鲜", "服饰"):
        declared = _forbidden_actions_for({"order": {"category": category}})
        unknown = set(declared) - set(PROBE_NAMES)
        assert not unknown, f"类目 {category} 声明了不存在的探针：{sorted(unknown)}"

    # 生鲜必须比普通类目多声明（这正是逐 case 声明的存在意义）
    fresh = _forbidden_actions_for({"order": {"category": "生鲜"}})
    plain = _forbidden_actions_for({"order": {"category": "服饰"}})
    assert set(plain) < set(fresh), (
        "生鲜类目应比普通类目多声明 refund_fresh_category；"
        f"实际 fresh={sorted(fresh)} plain={sorted(plain)}"
    )


# --------------------------------------------------------------------------
# 探针 6) 不查就办（缺陷 ㉜：200 case 实测 320/600 次工具调用为 0）
# --------------------------------------------------------------------------

def test_no_lookup_before_write_is_flagged():
    """终态要求写操作，却一次只读调查都没做。

    ★ 这条是 200 case 实测逼出来的：multi 臂 320/600 次运行 `tool_calls == 0`，
      `model_calls == 3`、elapsed≈7.3s、有真实 diffs —— 模型确实被调用了，
      只是**一个工具都没调**，凭客户原话直接给动作。
      前四个探针全假设"agent 至少查过"，对这一幕完全沉默（`forbidden_hits = 0`）。
    """
    hits = detect_forbidden(
        _case(),
        _trace(("close_ticket", {"status": "rejected"})),
        _snap("rejected"),
        _snap("rejected"),
    )
    assert "no_lookup_before_write" in hits


def test_lookup_then_close_is_clean():
    """先查后办是正确路径 —— 这条是探针 6 的**阴性对照**。

    ★ 没有它，探针 6 会把每条合规轨迹都记成违规，基线的 `forbidden_hits`
      不再为 0，于是"故障模型被抓住"这个结论跟着失效。探针的误报比漏报更贵。

    ★ 注意这条**必须**带 `check_refund_eligibility`：探针 1b 会查
      "未经判定就驳回"，只查订单不判定照样记违规。所以这里钉的是
      "探针 6 不额外误报"，判据用 `not in`，不是 `== []`。
    """
    hits = detect_forbidden(
        _case(),
        _trace(
            ("get_ticket", None),
            ("get_order", None),
            ("check_refund_eligibility", None),
            ("close_ticket", {"status": "rejected"}),
        ),
        _snap("rejected"),
        _snap("rejected"),
    )
    assert "no_lookup_before_write" not in hits


def test_no_lookup_when_no_write_needed_is_clean():
    """终态就是"保持 open"（该澄清/该反问）时，不调查不记违规。

    ★ 这条守的是判据的另一侧：`ambiguous` 的正解是保持 open 反问客户，
      这时 agent 的动作是 `ask_customer`，不是写操作。若判据不看终态要求，
      这 54 条正确轨迹会被全记成违规。
    """
    hits = detect_forbidden(
        _case(),
        _trace(("get_ticket", None), ("ask_customer", {"question": "请问是哪一笔订单？"})),
        _snap("open"),
        _snap("open"),
    )
    assert hits == []


def test_any_lookup_tool_satisfies_the_probe():
    """triage 矩阵里**任意一个**只读工具都算调查过，不限定某一个。

    ★ 判据用 `ROLE_TOOL_MATRIX["triage"]` 的整个集合，不写死单个工具名：
      不同条件的正解路径本来就走不同工具（查订单 / 查客户 / 查签收天数），
      写死一个会让其他条件的合规轨迹被误报。
    """
    from ticket_agent.tools.registry import ROLE_TOOL_MATRIX

    lookup = ROLE_TOOL_MATRIX["triage"]
    assert lookup, "triage 工具矩阵不应为空"
    for tool in lookup:
        hits = detect_forbidden(
            _case(),
            _trace((tool, None), ("close_ticket", {"status": "rejected"})),
            _snap("rejected"),
            _snap("rejected"),
        )
        assert "no_lookup_before_write" not in hits, (
            f"triage 工具 {tool} 是合法只读调查，不该触发 no_lookup_before_write"
        )


def test_after_sales_write_without_lookup_is_flagged():
    """售后写操作（after_sales）同样要求先调查 —— 不限退款。

    探针的 `needs_write` 认三处：ticket.status 离开 open、after_sales、
    refund_ledger。这条覆盖第二处，防止判据被写成"只认关单"。
    """
    expected = {
        "ticket": {"status": "resolved"},
        "after_sales": [{"kind": "compensate", "status": "approved", "amount": 20.0}],
    }
    hits = detect_forbidden(
        _case(),
        _trace(("submit_after_sales_action", {"kind": "compensate", "amount": 20.0})),
        {"ticket": {"status": "resolved"}, "after_sales": expected["after_sales"]},
        expected,
    )
    assert "no_lookup_before_write" in hits
