"""#65 政策单一来源 —— 政策文本与代码的一致性校验。

★ 为什么先写校验器、而不是直接把 policies.md 改对
----------------------------------------------------
改文本是一次性动作：改完之后漂移照样会重新长出来，而且没人看得见。
校验器是把「不可见的漂移」变成「会变红的东西」的机制 —— 它要一直留在
测试套件里，任何一侧单独改都会立刻红。

★ 本文件里的断言**故意有三条现在是红的**（① 规则编号 / ② 工具名 /
  ③ 阈值双份）。这不是没写完，而是本轮的设计意图：先让漂移显形，
  第二步再补齐 policies.md（加 R8–R14、换成真实工具名、阈值改为引用
  config.py），让校验器转绿。直接改文本而不加校验器，漂移会重新长出来。

四类漂移与它们各自危害的形状
----------------------------
1. 规则编号缺失 → 整条决策链无法被人复核。prompt 要求 agent「引用命中的
   规则编号」，而 query_policy 检索不到 R8–R14 的依据，于是 agent 引用了
   一个查不到出处的编号。这比数字漂移严重：数字漂移算错钱，编号漂移让
   决策**不可审计**。
2. 工具名指向不存在的能力 → 缺陷 ⑧「能力只存在于声明里」的**反向**复发：
   这次不是能力缺失，是声明指向了不存在的能力。
3. 阈值双份并存 → 改 settings 不会让政策文本变红，policy 与代码之间只剩
   人眼在中间。
4. 两个类目词表互斥性 → R2（不可七天无理由）与 R6（完全不可退）必须互斥，
   否则会出现「既是 R2 拒绝又是 R6 转人工」的双重归属，而 RULE_PRECEDENCE
   只能选一个先命中。
"""

from __future__ import annotations

import re
from pathlib import Path

from ticket_agent.config import settings
from ticket_agent.rules import (
    NON_REFUNDABLE_CATEGORIES,
    NON_RETURNABLE_CATEGORIES,
    PROOF_CAUSES,
    RULE_PRECEDENCE,
)
from ticket_agent.tools.registry import ToolRegistry

POLICY_MD = Path(__file__).resolve().parents[1] / "prompt" / "policies.md"


def _policy_text() -> str:
    assert POLICY_MD.exists(), f"找不到政策文件：{POLICY_MD}"
    return POLICY_MD.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# ① 规则编号：代码里的每条规则，政策文本里都要有条目
# ══════════════════════════════════════════════════════════════


def test_every_rule_id_in_code_has_a_policy_section() -> None:
    """代码里出现的**每个** R 编号都必须能在 policies.md 里找到 `## Rn：` 标题。

    ★ 第一版只扫 `RULE_PRECEDENCE`，结果绿了 —— 但那个元组里只有 R1–R6，
      R8–R14 是 `hit("R8", …)` 直接写字符串的，压根不在元组里，检不出。
      「检不出」和「不存在」在报表上完全同形 —— 这正是本项目反复撞到的形状。
      所以改成扫 `rules.py` 全文的 `hit("Rn"` 模式。
    """
    text = _policy_text()
    declared = set(re.findall(r"^##\s*R(\d+)\s*[：:]", text, flags=re.MULTILINE))
    declared = {f"R{int(n)}" for n in declared}

    rules_src = (Path(__file__).resolve().parents[1] / "src" / "ticket_agent" / "rules.py").read_text(
        encoding="utf-8"
    )
    hit_ids = {f"R{int(n)}" for n in re.findall(r'hit\(\s*"R(\d+)"', rules_src)}
    code_rules = {r for r in RULE_PRECEDENCE if re.fullmatch(r"R\d+", r)} | hit_ids
    missing = sorted(code_rules - declared, key=lambda s: int(s[1:]))

    assert not missing, (
        f"政策文本缺少这些规则的条目：{missing}"
        f"（代码的 RULE_PRECEDENCE 已经到 {max(code_rules, key=lambda s: int(s[1:]))}，"
        f"而 policies.md 只写了 {sorted(declared, key=lambda s: int(s[1:]))}）"
        f" —— prompt 要求 agent 引用规则编号，检索不到的编号等于无法复核的决策"
    )


# ══════════════════════════════════════════════════════════════
# ② 工具名：政策文本点名的工具必须真的存在
# ══════════════════════════════════════════════════════════════


#: 附则 A（七个售后动作 + 诉求 kind）与附则 B（凭证类型）里出现的反引号标识符
#: 是**参数取值**，不是工具名。
#:
#: ★ 第三例同形错误（继 `seed_all` 位置参数、`_SERVICE_LINE_KINDS` vs
#:   `_SERVICE_LINE_ACTIONS` 之后）：把「工具命名空间」和「参数值命名空间」
#:   压进同一次查询。修法不是把这几个词加进白名单糊掉 —— 那样参数值从此无人看管；
#:   是把它们**从工具名集合里剔出去，同时各自补一条反向断言**（见下方两条）。
_PARAM_VALUE_TABLES = (
    # 附则 A：动作名 + 诉求 kind
    "exchange",
    "reship",
    "compensate",
    "price_protect",
    "issue_invoice",
    "change_address",
    "urge_logistics",
    "invoice",
    "address_change",
    "urge",
    # 附则 B：凭证类型
    "photo",
    "video",
    "screenshot",
    "chat_log",
    "inspection_report",
    "tracking_proof",
)


def _tool_names_in_policy() -> set[str]:
    """抽出政策文本里形如 `tool_name` 的反引号标识符里「像工具名」的那些。

    ★ 参数取值（附则 A 的动作名/诉求名、附则 B 的凭证类型）不在此列 ——
      它们由 `_PARAM_VALUE_TABLES` 剔出，并各自有反向断言守着。
    """
    text = _policy_text()
    quoted = set(re.findall(r"`([a-z][a-z0-9_]{3,})`", text))
    # 明显不是工具名的（文件名/常量名/键名/状态值）
    not_tools = {
        "tool_registry",
        "rules_py",
        "seed_py",
        "config_py",
        "pending_human",
        "refund",
        "resolved",
        "rejected",
        "open",
        "escalate",
        "ambiguous",
        "request_kind",
        "request_cause",
        "evidence_provided",
    }
    not_tools.update(_PARAM_VALUE_TABLES)
    return {q for q in quoted if q not in not_tools and "_" in q}


def test_policy_action_and_request_names_match_code() -> None:
    """附则 A 的两列必须与代码的三个集合逐项一致（动作名 / 诉求名 / 映射表）。

    ★ 这条是从漏洞长出来的断言：第一版把附则 A 里的参数取值当工具名查，
      报了一堆「不存在的工具」。剔出去之后如果就此不管，参数值拼错就再也没人发现 ——
      而拼错的 kind 会让写工具拒收或静默走错分支。
    """
    from ticket_agent.agents.scripted import _ACTION_TO_REQUEST, _SERVICE_LINE_ACTIONS

    text = _policy_text()
    missing_actions = sorted(a for a in _SERVICE_LINE_ACTIONS if a not in text)
    assert not missing_actions, f"政策附则 A 缺这些动作名：{missing_actions}（代码集合={sorted(_SERVICE_LINE_ACTIONS)}）"

    missing_kinds = sorted(set(_ACTION_TO_REQUEST.values()) - set(re.findall(r"`([a-z][a-z0-9_]*)`", text)))
    assert not missing_kinds, f"政策附则 A 缺这些诉求 kind：{missing_kinds}（代码集合={sorted(set(_ACTION_TO_REQUEST.values()))}）"

    extra_actions = sorted(a for a in re.findall(r"`([a-z][a-z0-9_]*)`", text) if a not in _ACTION_TO_REQUEST and a in _SERVICE_LINE_ACTIONS)
    assert not extra_actions, f"政策里出现不在映射表里的动作名：{extra_actions}"


def test_policy_evidence_types_match_code() -> None:
    """附则 B 的凭证类型必须全在 `EVIDENCE_TYPES` 内，且不得漏掉任何一类。

    ★ 呼应 ⑤：政策承诺「需提供凭证」，代码必须有受控词表。
      词表加了新类型而政策没写，agent 就永远交不出能过检的那一份。
    """
    from ticket_agent.rules import EVIDENCE_TYPES

    # ★ 第二版把「必须写成反引号」这条判据去掉了（第一版只认 `photo` 这种
    #   反引号形式，于是附则 B 表格里用中文顿号连写的四项全被判「没提」）。
    #   **方向搞反了**：要判的是政策有没有提到这个凭证类型，不是它有没有按
    #   某种排版写出来。逼文档去迁就断言格式，等于让格式成为事实来源 ——
    #   与「政策单一来源」这个目标正好相反（文本成了断言的函数，
    #   而不是断言守着文本），下次有人重排版还会再红一遍。
    text = _policy_text()
    missing = sorted(t for t in EVIDENCE_TYPES if t not in text)
    assert not missing, (
        f"代码词表里的凭证类型政策文本没提：{missing}（EVIDENCE_TYPES={sorted(EVIDENCE_TYPES)}）"
        " —— agent 交不出词表外的那一份，凭证分支永远测不到"
    )


def test_policy_names_only_tools_that_exist() -> None:
    """政策文本里让 agent 调用的每个工具名，必须在工具矩阵的并集里。

    ★ 当前预期为红：附则点名的 days_since_signed / list_order_items /
      query_policy / list_orders_of_customer 都不存在。
    """
    registry = ToolRegistry()
    all_names: set[str] = set()
    for role in ("triage", "resolver", "verifier", "single"):
        all_names.update(registry.names_for(role))

    missing = sorted(_tool_names_in_policy() - all_names)
    assert not missing, (
        f"政策文本点名了不存在的工具：{missing}"
        f" —— 这是缺陷 ⑧「能力只存在于声明里」的反向复发："
        f"不是能力缺失，是声明指向了不存在的能力。agent 按政策去调会拿到"
        f"「工具不存在」，而它自己的笔记里还写着「已按政策调用」"
    )


def test_policy_documents_every_after_sales_action() -> None:
    """七个售后动作至少要在政策文本里被提到过一次（kind 名或工具名）。

    ★ 当前预期为红：附则第 5 步只列了退款线的四个动作。
    """
    text = _policy_text()
    actions = (
        "exchange",
        "reship",
        "compensate",
        "price_protect",
        "issue_invoice",
        "change_address",
        "urge_logistics",
    )
    missing = [a for a in actions if a not in text]
    assert not missing, (
        f"政策文本没提到这些售后动作：{missing}"
        f" —— 七个动作全部落在同一个写工具 submit_after_sales_action 上，"
        f"区别只在 kind；政策不写它们，agent 就只能靠 prompt 猜 kind"
    )


# ══════════════════════════════════════════════════════════════
# ③ 阈值：政策文本里的数字必须等于 settings 的默认值
# ══════════════════════════════════════════════════════════════


def test_policy_thresholds_match_settings_defaults() -> None:
    """政策文本里出现的阈值数字，必须与 settings 的默认值一致。

    ★ 当前预期为红：R4 的「500 元」和「0.7」是文本里写死的，
      改 settings 不会让这里变红 —— 两套数字并存，只有���眼在中间。
    """
    text = _policy_text()
    problems: list[str] = []

    if f"{settings.refund_hitl_threshold_cny:g}" not in text:
        problems.append(
            f"退款金额阈值 settings.refund_hitl_threshold_cny="
            f"{settings.refund_hitl_threshold_cny:g}，政策文本里找不到同一个数字"
        )
    if f"{settings.risk_score_hitl_threshold:g}" not in text:
        problems.append(
            f"风险评分阈值 settings.risk_score_hitl_threshold="
            f"{settings.risk_score_hitl_threshold:g}，政策文本里找不到同一个数字"
        )

    assert not problems, "；".join(problems) + "（政策文本应改为引用 config.py，而不是复述数字）"


def test_policy_window_days_match_settings_defaults() -> None:
    """R1 的 7 天、R8 的换货期、R11 的价保期、R14 的冷却期，与 settings 对齐。

    ★ 只断言**文本里出现的那个数字本身**与 settings 相等 ——
      如果文本根本没写这个窗口，那是另一种缺口（政策漏写），
      由 test_policy_declares_every_window 单独盯。
    """
    text = _policy_text()
    # R1 文本里写死「7 个自然日」
    if re.search(r"7\s*个自然日", text):
        assert str(settings.return_window_days) in text, (
            f"政策文本写死 7 天，但 settings.return_window_days="
            f"{settings.return_window_days}"
        )

    # R8 / R11 换货期与价保期若文本提及，必须与 settings 一致
    for label, value in (
        ("换货期", settings.exchange_window_days),
        ("价保期", settings.price_protect_window_days),
    ):
        m = re.search(rf"{label}[^\d]{{0,12}}?(\d+)\s*(?:天|日)", text)
        if m:
            assert int(m.group(1)) == int(value), (
                f"{label}政策写 {m.group(1)} 天，settings 是 {value} 天"
            )


def test_policy_declares_every_window() -> None:
    """R8/R11/R14/R10 的窗口与上限，政策文本必须各写一次。

    ★ 当前预期为红：政策只写到 R7，R8 之后的窗口全都不在文本里。
    """
    text = _policy_text()
    missing: list[str] = []
    for label, value in (
        ("换货期", settings.exchange_window_days),
        ("价保期", settings.price_protect_window_days),
        ("催单冷却", settings.urge_cooldown_hours),
    ):
        if label not in text:
            missing.append(f"{label}（settings={value}）")
    if "赔付" in text and "上限" not in text:
        missing.append(f"赔付上限（settings.max_compensation_cny={settings.max_compensation_cny}）")

    assert not missing, (
        f"政策文本没写这些窗口/上限：{missing}"
        f" —— 阈值在代码里有、在政策里没有，agent 无从引用（规则编号漂移的一种）"
    )


# ══════════════════════════════════════════════════════════════
# ④ 类目词表：两套词表必须互斥，且都是代码常量
# ══════════════════════════════════════════════════════════════


def test_category_tables_are_mutually_exclusive() -> None:
    """R2（不可七天无理由）与 R6（完全不可退）必须互斥。

    ★ 生鲜不能同时属于两表：R2 会判「驳回」，R6 会判「转人工」，
      而 RULE_PRECEDENCE 里 R6 排在 R2 前面 —— 两张表一旦有交集，
      同一个类目就会有两套互相矛盾的处置，而规则只能选一个先命中。
      这类重叠在报表上完全看不出来（只有一条 case，只是判据不同）。
    """
    a = set(NON_RETURNABLE_CATEGORIES)
    b = set(NON_REFUNDABLE_CATEGORIES)
    overlap = sorted(a & b)
    assert not overlap, (
        f"两个类目词表有交集：{overlap}"
        f"（R2 词表={sorted(a)}，R6 词表={sorted(b)}）"
        f" —— R6 优先级高于 R2，重叠会让同一类目有两种互斥处置"
    )


def test_category_tables_are_non_empty() -> None:
    """两张类目表都不得为空：空表会让对应规则永不触发，且报表上看不出来。

    ★ 呼应本项目反复撞到的那条：0 条 case 的规则和从不犯错长得一模一样。
    """
    assert NON_RETURNABLE_CATEGORIES, "R2 类目表为空 —— R2 永不触发且无人在意"
    assert NON_REFUNDABLE_CATEGORIES, "R6 类目表为空 —— R6 永不触发且无人在意"


def test_policy_lists_every_category_in_the_code_tables() -> None:
    """代码里的类目，政策文本必须逐个提到。

    ★ 当前预期为红：政策写了内衣/定制/数字商品/生鲜，若代码表与文本漂移，
      这里会红。R6 的「生鲜」在文本里出现过，所以当前可能通过 ——
      它是一条**防漂移**断言，不是当前缺口的证据。
    """
    text = _policy_text()
    missing = [c for c in (*NON_RETURNABLE_CATEGORIES, *NON_REFUNDABLE_CATEGORIES) if c not in text]
    assert not missing, (
        f"代码里的类目政策文本没提：{missing}"
        f"（R2={list(NON_RETURNABLE_CATEGORIES)}，R6={list(NON_REFUNDABLE_CATEGORIES)}）"
    )


# ══════════════════════════════════════════════════════════════
# ⑤ 凭证要求：政策说「需提供凭证」，代码必须真的能检查
# ══════════════════════════════════════════════════════════════


def test_policy_proof_claim_is_backed_by_code() -> None:
    """政策承诺「提供凭证」时，代码里必须有对应的受控词表与匹配函数。

    ★ 这条是 #60 的固化：当初政策写着「需提供凭证」，而全项目 0 处读
      evidence_provided、0 个工具能表达它 —— 缺陷 ⑧ 发生在政策文本里。
      没有这条断言，同样的形状会再长一次。
    """
    from ticket_agent.rules import CAUSE_REQUIRED_EVIDENCE, EVIDENCE_TYPES, check_evidence

    assert EVIDENCE_TYPES, "受控词表为空 —— 凭证校验会退化成'什么都接受'"
    assert "需" in _policy_text() and "凭证" in _policy_text(), "政策文本已不再提凭证"

    for cause in PROOF_CAUSES:
        assert cause in CAUSE_REQUIRED_EVIDENCE, (
            f"PROOF_CAUSES 里的 {cause} 没有对应的凭证要求 —— "
            f"政策要求凭证，代码却不检查它够不够格"
        )
        ok, why = check_evidence("photo", cause)
        assert ok or "evidence_insufficient" in why, (
            f"check_evidence 返回了意外结果：cause={cause!r} ok={ok} why={why!r}"
        )


# ══════════════════════════════════════════════════════════════
# ⑥ 规则顺序：政策文本必须能看出优先级，不能只有代码知道
# ══════════════════════════════════════════════════════════════


def test_policy_states_rule_precedence_for_conflicting_rules() -> None:
    """R6 与 R4 都可能命中同一单（生鲜 + 大额），政策必须说明谁优先。

    ★ 文本里现在写的是「R6 优先级高于 R4」—— 方向对，但它是散文。
      校验器的作用是让这个事实**双向可查**：代码里 RULE_PRECEDENCE 的
      相对顺序与文本陈述必须一致，否则 policy 与 rules 又各说一套。
    """
    text = _policy_text()
    order = list(RULE_PRECEDENCE)
    assert order[0] == "R6", f"RULE_PRECEDENCE 首项应是 R6（生鲜优先），实际 {order[0]}"
    if "R6" in text and "R4" in text and "优先" in text:
        m = re.search(r"R(\d+)\s*优先(?:于|级)?[^\n]{0,8}R(\d+)", text)
        if m:
            hi, lo = f"R{m.group(1)}", f"R{m.group(2)}"
            assert hi in order and lo in order and order.index(hi) < order.index(lo), (
                f"政策文本说 {hi} 优先于 {lo}，但 RULE_PRECEDENCE 里 "
                f"{hi}（index {order.index(hi) if hi in order else 'N/A'}）"
                f" 并不早于 {lo}（index {order.index(lo) if lo in order else 'N/A'}）"
            )