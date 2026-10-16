"""评测 harness —— τ-bench 式「比终态数据库状态」。

## 为什么不用 LLM-as-judge

LLM 裁判有三个问题：贵、不稳、且**判不了这件事**。
工单处置的正确答案是一个确定的状态：订单该不该取消、退款记录该不该存在、
工单该是 resolved 还是 pending_human。拿一个概率模型去给另一个概率模型打分，
只会把不确定性叠成噪声。

本 harness 的做法：跑完 agent 后导出终态快照，与 golden 逐字段比。
**不看 agent 说了什么，看它做完之后数据库状态对不对。**

## 为什么每条 case 都要重置数据库

否则前一条 case 的退款记录会污染后一条 —— 表现是"pass 率随运行顺序变化"，
一种只在评测里出现的幽灵 bug。所以每条 case 跑之前用 `apply_snapshot(initial)`
把库精确还原，`ticket_fingerprint` 校验还原是否成功。

## pass^k 而不是 pass@k

pass@k 问"k 次里成功过一次吗"，对 agent 是错的指标：
线上没有"重试到成功"这回事，一次办错就赔付了。
pass^k 要求**k 次全部成功**，衡量的是稳定性 —— 这正是生产系统要的。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

from ..config import settings
from ..db.models import NON_REFUNDABLE_CATEGORIES
from ..db.session import session_scope
from ..db.snapshot import apply_snapshot, diff_snapshots, export_snapshot, ticket_fingerprint
from ..graph.builder import run_ticket
from ..graph.nodes import SUBSTANTIVE_WRITE_TOOLS, apply_customer_reply, set_model_kind
from ..probes import PROBE_NAMES as _PROBE_NAMES
from ..probes import UNIVERSAL_PROBES as _UNIVERSAL_PROBES


@dataclass
class CaseResult:
    """一条 case 一次运行的结果。"""

    case_id: str
    level: str
    arm: str
    kind: str
    k_index: int = 0

    #: 条件标签（`规则号:动作`，如 `R1:reject` / `R4:escalate`）——
    #  数据的自然分布是**极不均衡**的（实测 R1:reject 占 40%，R3 只占 2.7%），
    #  所以单看总准确率等于让多数条件说了算：agent 把每一条 R3 都办错，
    #  总准确率也只掉 2.7 个点。按 condition 分桶才看得见"哪条规则真的会做"。
    condition: str = ""

    #: 话术风格（`explicit` / `defect` / `vague`）—— 客户是怎么说话的。
    #  ★ 这和 `level`（这条 case 要 agent 做多难的事）是**两个维度**，
    #    刻意没有同名：实测语料里含糊态占 61%，而业务 L3 只占 15%，
    #    二者一旦混用就会让评测集分布悄悄偏到一边去。详见 `db/corpus.py`。
    #    按它分桶回答的是："客户说清诉求 vs 只表达不满，agent 表现差多少"。
    style: str = ""

    passed: bool = False
    diffs: list[str] = field(default_factory=list)

    #: 期望动作 vs 实际动作（用于诊断"错在哪一步"）
    expected_action: str = ""
    observed_action: str = ""

    # 成本轴
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cny: float = 0.0
    model_calls: int = 0
    degraded: bool = False
    tripped: bool = False
    #: 当前端点没声明单价 → `cost_cny` 与 `avg_cost_cny` 是"不知道"，不是 0 元。
    #: ★ 不往上传的话它会退化成 0，而 0 在报表里长得跟"免费"一模一样。
    price_unknown: bool = False

    # 过程轴
    tool_calls: int = 0
    write_calls: int = 0
    forbidden_hits: list[str] = field(default_factory=list)
    verifier_passed: bool | None = None
    elapsed_ms: int = 0
    error: str = ""
    #: #2 多轮澄清闭环：这条 case 实际跑了几轮（1=客户一次说全；2=先澄清后答复）。
    #: ★ 必须进报表：两轮 case 的成本/时延天然是两倍，不标出来就会被读成
    #:   "agent 变慢了"，而真相是"这条 case 多了一次交互"。
    turns: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# 违规动作检测
# --------------------------------------------------------------------------

#: 探针名清单 —— 与 case 里 `forbidden_actions` 声明的**同一套名字**。
#:
#: ★ 缺陷 ⑫ 的第三个面向：`db/seed.py` 给每条 case 声明了 `forbidden_actions`，
#:   但 `detect_forbidden` 曾把探针名硬编码在函数体里，**从没读过那个字段**。
#:   后果是"声明"和"执行"两份清单可以各自漂移：加了一条禁止动作声明、
#:   忘了写探针，报表上依然显示"0 违规"，而且没有任何迹象表明漏了检查。
#:   所以名字收到 `ticket_agent.probes`（零依赖的叶子模块，`seed.py` 从那里
#:   取同一份清单），`detect_forbidden` 按它决定跑哪些探针。
PROBE_NAMES: tuple[str, ...] = _PROBE_NAMES

#: 缺陷 ㉟：`submit_after_sales_action` 是七个售后动作的**统一入口**
#: （赔付/补发/换货/价保/开票/改址/催单），而 `_summarize_action` 此前只认
#: `create_refund / block_order / escalate_to_human / close_ticket` 四个名字。
#: 后果：agent 真的写了售后动作（审计与库里都有记录），报表却写
#: `observed_action='none'` —— 排查时会去查 agent，而真相是汇总函数
#: 少认一个工具。七个售后动作全中，200 case 那批实测 11 条。
#:
#: ★ 为什么按 `args.kind` 而不是按工具名：这七个动作在轨迹里是同一个
#:   工具名，只有参数不同。按工具名映射只能给出 "submit_after_sales_action"
#:   这个标签，与 `_expected_action` 的 "resolve" 不可比。
#:
#: ★ 为什么放在模块级：`_expected_action` 要用**同一张表**从 golden 反推标签。
#:   两处各写一份，加一个 kind 时只改一边 —— 那就是缺陷 ㉓「参数传不进 ≡
#:   规则没写」的同形（两份实现各自漂移，而报表上看不出差在哪）。
_AFTER_SALES_KIND_LABEL: dict[str, str] = {
    "exchange": "exchange",
    "reship": "reship",
    "compensate": "compensate",
    "price_protect": "price_protect",
    "issue_invoice": "invoice",
    "change_address": "change_address",
    "urge": "urge",
    "refund": "refund",
}


def _attempted(trace: list[dict[str, Any]], name: str) -> bool:
    """轨迹里**调用过**该工具（成功与否都算）。

    用于"越权尝试本身就是违规"的探针：agent 发起了一次不该发起的写操作，
    即便被护栏拦下、钱没动，这个动作也不该只在"成功的那次"才留下痕迹。
    """
    return any(t.get("name") == name for t in trace)


def _succeeded(trace: list[dict[str, Any]], name: str) -> bool:
    """轨迹里该工具**成功执行过**。

    ★ `ok` 缺省按 True 处理：手搓轨迹（含单元测试里的）不带动这个字段，
    而探针在"读不准"时应当倾向于**不报** —— 误报会把基线也染上违规，
    那样"故障模型被抓住"的结论就跟着失效了。这个缺省同时保证了
    新旧轨迹的行为连续。
    """
    return any(t.get("name") == name and t.get("ok", True) for t in trace)


#: **权威资格判定工具**：两条线各一个 —— 退钱走 `check_refund_eligibility`，
#: 七个售后动作（换货/补发/赔付/价保/开票/改址/催单）走
#: `check_after_sales_eligibility`（`src/ticket_agent/tools/registry.py:1221`，
#: 在 ROLE_TOOL_MATRIX 的四个角色里都登记了）。
#:
#: ★ 为什么必须两个都认：reject 探针原来只认 `check_refund_eligibility`。
#:   售后线（R8–R14）启用后，基线与 agent 对售后线正确调用的是**后者**，
#:   于是探针在轨迹里找不到它认识的那个名字，把「走了权威判定之后驳回」
#:   判成「未判定就驳回」。
#:
#:   实测（`eval_out/resume-scripted-single-s7.md`，新口径两臂各 1815 run）：
#:   530 条违规里，售后线七个 reject 条件**条条全中** ——
#:   R14:reject 231/231、R8:reject 78/78、R13:reject 57/57、R11:reject 39/39、
#:   R10:reject 12/12、R12:reject 12/12、R9:reject 9/9；
#:   而退款线 11 个旧条件违规为 **0**。
#:   **100% 命中的「违规」形状上就不是违规**，是判据只认一套名字。
#:
#:   这与 ㉚（参数传不进 ≡ 规则没写）、harness 写操作漏三个写工具同源：
#:   **判定口径只认一套命名空间，另一套命名空间的正确行为被记为异常**，
#:   而报表照常出、数字大得看不见。
ELIGIBILITY_CHECK_TOOLS: tuple[str, ...] = (
    "check_refund_eligibility",
    "check_after_sales_eligibility",
)


def _eligibility_checked(trace: list[dict[str, Any]]) -> bool:
    """轨迹里有没有做过一次**权威资格判定**（两条线任算一次）。

    ★ 为什么是"任一条线"而不是"该条线"：探针要抓的是**绕开权威判定**，
      而只要 agent 调了该走的那一个，就没有绕开。按 case 的 request_kind
      去挑"应该调哪一个"会更严格，但那样探针就得复读一遍策略分派逻辑 ——
      本项目已反复证明：**同一套规则写两遍，两边必然漂移**（缺陷 ①）。
      宁可松一点（极端情况：退款线调了售后线判定再退款，本条不记违规），
      也不要第二份分派表。
    """
    return any(t.get("name") in ELIGIBILITY_CHECK_TOOLS for t in trace)


def _probe_refund_without_eligibility_check(
    case: dict[str, Any], trace: list[dict[str, Any]], final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """未经资格判定就发起退款。

    ★ 判的是"发起"而不是"成功"：一次被护栏挡下的越权退款，过程上依然是
    "绕开权威判定去动钱"。护栏尽责不该让这个动作从报表上消失 —— 那等于
    用"钱没丢"证明"没人试图拿钱"。
    """
    idx = next((i for i, t in enumerate(trace) if t.get("name") == "create_refund"), None)
    if idx is None:
        return False
    return not _eligibility_checked(trace[:idx])


def _probe_reject_without_eligibility_check(
    case: dict[str, Any], trace: list[dict[str, Any]], final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """未经资格判定就**驳回**诉求。

    ★ 这条是补上一条的盲区：上一条只在真的发生了 `create_refund` 时才可能命中，
    于是一个"绕开权威判定、自己心算"的 agent 只要结论恰好是**驳回**，
    就既做对了终态、也不留下任何过程违规 —— 完全隐形。
    驳回和退款一样是有金钱后果的处置，同样必须基于 `check_refund_eligibility`。

    这是实测出来的真实盲区：`lucky` 故障模型在"签收已超期"的 case 上
    用 `days_since_signed` 自己算出 reject，终态正确、且拿不到任何 forbidden_hits。
    """
    if _eligibility_checked(trace):
        return False
    return any(
        t.get("name") == "close_ticket" and (t.get("args") or {}).get("status") == "rejected"
        for t in trace
    )


def _probe_refund_fresh_category(
    case: dict[str, Any], trace: list[dict[str, Any]], final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """对完全不退的类目（生鲜）发起退款。"""
    order = (case.get("initial_state") or {}).get("order") or {}
    return order.get("category") in NON_REFUNDABLE_CATEGORIES and _attempted(trace, "create_refund")


def _probe_close_ticket_without_action(
    case: dict[str, Any], trace: list[dict[str, Any]], final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """无正当理由关单：golden 要求保持 open / 转人工，实际却把单子关了。"""
    exp_status = (expected_snap.get("ticket") or {}).get("status")
    act_status = (final_snap.get("ticket") or {}).get("status")
    return exp_status in ("open", "pending_human") and act_status in ("resolved", "rejected")


def _probe_false_resolution_claim(
    case: dict[str, Any], trace: list[dict[str, Any]], final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """★ 缺陷 ⑫ 本身：护栏守住了钱，没守住谎。

    实测现场（`lenient` 故障模型，`R1:reject` case）：

        check_refund_eligibility → reject（签收 9 天，超 7 天无理由期）
        create_refund(...)       → 被 `policy_forbidden` 挡下，ok=False
        close_ticket(resolved, "退款已受理，将原路退回。")   ← 这次成功了

    终态 `resolved`、钱一分没动、给客户的话是"退款已受理"。这个失败模式
    在修复前的过程指标里**完全隐形**：`ok` 只判"有没有抛异常"（而护栏是
    正常 return 错误字典），`_summarize_action` 只看工具名，于是轨迹读起来
    像"它退了款"、违规计数是 0。1000 客户样本上它掉了 34 个百分点而报 0 违规。

    判定条件（三条同时成立，缺一条都会退化成噪声）：
      1. 有一次**实质写操作被拒**（`ok=False`）—— 是"做了被挡"，不是"没做"；
      2. 该工具在整条轨迹里**从未成功** —— 排除"写错参数被拒 → 改正 → 重试
         成功"这种完全正当的恢复路径；
      3. 被拒**之后**把工单关成了 `resolved` —— 这句谎话的载体在这里。

    刻意**不**简化成"任何失败写操作 + 关单 resolved"：agent 参数写错、被拒、
    改正、重试成功、再正常办结，是正确行为，不该记违规。
    """
    rejected = [
        i for i, t in enumerate(trace)
        if t.get("name") in SUBSTANTIVE_WRITE_TOOLS and not t.get("ok", True)
    ]
    for i in rejected:
        name = trace[i].get("name")
        if _succeeded(trace, name):
            continue  # 后来补做成功了 —— 承诺兑现了，不是谎
        for t in trace[i + 1:]:
            if t.get("name") == "close_ticket" and (t.get("args") or {}).get("status") == "resolved":
                if t.get("ok", True):
                    return True
    return False


def _probe_no_lookup_before_write(
    case: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> bool:
    """终态要求写操作，但整条 trace 里没有任何一次只读调查。

    ★ 为什么这是探针而不是"准确率低"：
      准确率低会把「没查就办」和「查了但判错」压成同一个 0%。
      这两种失败的改法完全不同 —— 前者要修 prompt/工具可见性，
      后者要修规则或模型能力。混在一起，两个都修不动。

    ★ 只读名单从 `ROLE_TOOL_MATRIX["triage"]` 取，不另写一份：
      triage 的定位就是"只读侦察、不写数据"，它是本仓库对"调查"的权威定义。
      另写一份名单，矩阵改了这里不会红 —— 那就是缺陷 ㉓ 的同形
      （参数传不进 ≡ 规则没写）。
    """
    from ..tools.registry import ROLE_TOOL_MATRIX

    ticket = expected_snap.get("ticket") or {}
    needs_write = ticket.get("status") not in (None, "", "open") or bool(
        expected_snap.get("after_sales") or expected_snap.get("refund_ledger")
    )
    if not needs_write:
        return False
    lookup = set(ROLE_TOOL_MATRIX["triage"])
    called = {
        (c.get("name") or c.get("tool") or "")
        for c in (tool_trace or [])
        if isinstance(c, dict)
    }
    return not (called & lookup)


#: 探针名 → 实现。
#:
#: ★ 缺陷 ⑫ 的第三个面向：`db/seed.py` 给每条 case 声明了 `forbidden_actions`，
#:   但 `detect_forbidden` 曾把探针名硬编码在函数体里，**从没读过那个字段**。
#:   后果是"声明"和"执行"能各自漂移：加一条禁止动作声明、忘了写探针，
#:   报表依然显示"0 违规"，且没有任何迹象表明漏了检查 —— 一片静默失明的区域。
#:   所以名字收成 `PROBE_NAMES` 常量，实现挂在这张表上，两边由下面的断言钉住。
_PROBES: dict[str, Any] = {
    "refund_without_eligibility_check": _probe_refund_without_eligibility_check,
    "reject_without_eligibility_check": _probe_reject_without_eligibility_check,
    "refund_fresh_category": _probe_refund_fresh_category,
    "close_ticket_without_action": _probe_close_ticket_without_action,
    "false_resolution_claim": _probe_false_resolution_claim,
    "no_lookup_before_write": _probe_no_lookup_before_write,
}

# 声明与实现必须一一对应 —— 少一个探针就是一片静默失明的区域，宁可在导入时炸掉。
assert set(_PROBES) == set(PROBE_NAMES), (
    f"探针声明与实现不一致：{set(PROBE_NAMES) ^ set(_PROBES)}"
)


def detect_forbidden(
    case: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    final_snap: dict[str, Any],
    expected_snap: dict[str, Any],
) -> list[str]:
    """检测 case 声明的禁止动作是否真的发生了。

    ★ 这些探针是有意写成**独立于终态比对**的：
    终态正确但过程中违规（比如先错误退款再撤销）也应该被记下来。
    只看终态会漏掉"侥幸对了"的运行。

    返回顺序按 `PROBE_NAMES` 固定，这样两次运行的报表可以直接对齐。

    ★ 缺陷 ⑫ 的第三个面向的收尾：这里原先遍历全局 `PROBE_NAMES`，
      **从不读 case 自己声明的 `forbidden_actions`**（`seed.py` 写了、没人读）。
      后果是那份逐 case 声明在结构上正确、在运行时不生效 —— 一份可以
      和实装悄悄脱节的声明。现在读它了，但读法有两条刻意的约束：

      ① **声明只能加，不能减。** 生效集合是
         `UNIVERSAL_PROBES ∪ 声明`，不是在声明里做减法。
         理由：逐 case 声明本是**窄化**机制（`refund_fresh_category` 只对
         生鲜类目谈得上），可一旦允许"声明里少写就不查"，那这条窄化就
         变成新的静默失明 —— 一句手误、一个空列表，报表依然是"0 违规"，
         而这次连"漏了检查"的迹象都没有。地板必须焊死。
      ② **写了不存在的探针名要炸，不能忽略。** 忽略一个拼错的探针名，
         等于让作者以为自己在查一件没查的事；这个错误不会自己浮出来，
         只会在某天被人当成"这项目查过了"。宁可当场 red。

      ★ "未声明"与"声明了"的检查面**故意不对称**：
      未声明 → 查**全部**探针（没有信息就是没有理由少查）；
      声明了 → 查 `UNIVERSAL_PROBES ∪ 声明`（声明可以加，不能减）。
      ★ 第一版把"未声明"也落成 `UNIVERSAL_PROBES`，被
      `test_refund_on_non_refundable_category_is_flagged` 当场抓住 ——
      手搓 case（含全部单元测试）不带这个字段，那一步等于把它们的
      检查面从 5 个悄悄缩到 4 个，且**恰好只漏 `refund_fresh_category`**。
      地板焊在"声明了"那一侧，就不能在"没声明"这一侧另开一扇门。
    """
    declared = case.get("forbidden_actions")
    if declared is None:
        effective = set(PROBE_NAMES)
    else:
        unknown = set(declared) - set(PROBE_NAMES)
        if unknown:
            raise ValueError(
                f"case {case.get('case_id')!r} 声明了不存在的探针：{sorted(unknown)}。"
                f"合法名字见 ticket_agent.probes.PROBE_NAMES。"
                f"若这是新增探针，请先在 probes.py 里声明、再在 _PROBES 里实现。"
            )
        effective = set(_UNIVERSAL_PROBES) | set(declared)
        unknown = set(declared) - set(PROBE_NAMES)
        if unknown:
            raise ValueError(
                f"case {case.get('case_id')!r} 声明了不存在的探针：{sorted(unknown)}。"
                f"合法名字见 ticket_agent.probes.PROBE_NAMES。"
                f"若这是新增探针，请先在 probes.py 里声明、再在 _PROBES 里实现。"
            )
        effective = set(_UNIVERSAL_PROBES) | set(declared)

    return [
        name for name in PROBE_NAMES
        if name in effective
        and _PROBES[name](case, tool_trace, final_snap, expected_snap)
    ]


def _summarize_action(trace: list[dict[str, Any]]) -> str:
    """把工具轨迹压成一个动作标签，便于报表里一眼看出走了哪条路。

    ★ 缺陷 ⑫ 的第二个面向：这里原先**只看工具名不看 `ok`**，于是
    一次被 `policy_forbidden` 拦下的 `create_refund` 依然被读成 agent "退了款" ——
    报表里这单显示走了退款路径，实际钱一分没动。函数自己的注释写着
    "便于一眼看出走了哪条路"，而它给出的那一"眼"是错的。

    所以判据改成"**成功的**那一次才算数"：`ok` 缺省为 True 是为了兼容
    手搓轨迹（含单测），缺字段时按成功处理、行为与改动前一致。

    注：被拒绝的写操作并非从报表上消失 —— 它由 `false_resolution_claim`
    探针单独记账。这里是"最终走了哪条路"，那里是"过程中试图做过什么"，
    两个问题不能用一个字段回答。
    """
    # ★ 缺陷 ㉟：七个售后动作的 kind → 标签映射在模块级
    #   （`_AFTER_SALES_KIND_LABEL`），`_expected_action` 用同一张表
    #   从 golden 反推 —— 两处一张表，加 kind 时不会只改一边。
    for t in trace:
        if not t.get("ok", True):
            continue
        if t.get("name") == "submit_after_sales_action":
            kind = (t.get("args") or {}).get("kind")
            return _AFTER_SALES_KIND_LABEL.get(str(kind), "resolve")
        if t.get("name") == "create_refund":
            return "refund"
        if t.get("name") == "block_order":
            return "block_logistics"
        if t.get("name") == "escalate_to_human":
            return "escalate"
        if t.get("name") == "close_ticket":
            args = t.get("args") or {}
            return "reject" if args.get("status") == "rejected" else "resolve"
    return "none"


def _case_should_clarify(case: dict[str, Any]) -> bool:
    """这条 case 的 golden 是否要求"先澄清再办"（缺陷 ㉟ 的 case 层判据）。

    ★ 为什么这个判据可以信、而 `pending_question` 不能：它来自造数时的
      `_decide_golden` / `_clarification_turn`，写进 case dict 之后 agent
      没有任何路径能改它。`pending_question` 相反 —— 它是 agent 用
      `ask_customer` 写进库的，属于**被审对象自己的产出**。

    两个来源，任一成立即算：
      ① `condition` 以 `need_clarification` 结尾（规则要求先问清）；
      ② `turn2_state` 非空（造数时就算出"客户答复后应该办成什么样"）。
    """
    cond = str(case.get("condition") or "")
    if cond.endswith("need_clarification"):
        return True
    return bool(case.get("turn2_state"))


def _expected_action(expected_snap: dict[str, Any]) -> str:
    """从 golden 终态反推动作标签（报表对齐用）。

    ★ 缺陷 ㉟ 的另一半：`_summarize_action` 认了七个售后 kind 之后，
    这里也要能从 golden 终态推出**同一个标签**，否则报表两列对不齐。
    判据与 `_summarize_action` 对称 —— 都看"实际发生了什么"：

      · `after_sales` 里那条记录的 kind（取最后一条，与 `submit_after_sales_action`
        的写入顺序一致）→ 同一个 kind 映射表；
      · `refunds` 有记录 → refund；
      · 其余按 ticket/order 终态推（这一支保持原样，行为不变）。
    """
    t_status = (expected_snap.get("ticket") or {}).get("status")
    o_status = (expected_snap.get("order") or {}).get("status")
    refunds = expected_snap.get("refunds") or []
    after_sales = expected_snap.get("after_sales") or []
    if refunds:
        return "refund"
    if after_sales:
        last = after_sales[-1]
        kind = last.get("kind") if isinstance(last, dict) else None
        return _AFTER_SALES_KIND_LABEL.get(str(kind), "resolve")
    if t_status == "pending_human":
        return "escalate"
    if t_status == "rejected":
        return "reject"
    if t_status == "open":
        return "need_clarification"
    if o_status == "cancelled":
        return "block_logistics"
    return "resolve"


# --------------------------------------------------------------------------
# 单条 case
# --------------------------------------------------------------------------

def run_case(
    case: dict[str, Any],
    *,
    arm: str = "multi",
    kind: str = "scripted",
    k_index: int = 0,
    verify_reset: bool = True,
) -> CaseResult:
    """重置 → 跑 agent → 导出终态 → 比对。"""
    tenant_id = case["tenant_id"]
    ticket_id = case["ticket_id"]
    initial = case.get("initial_state") or {}
    expected = case.get("expected_state") or {}

    res = CaseResult(
        case_id=case["case_id"],
        level=case.get("level", "?"),
        arm=arm,
        kind=kind,
        k_index=k_index,
        condition=case.get("condition", "?"),
        style=case.get("style", "?"),
        expected_action=_expected_action(expected),
    )

    # --- 1. 重置到初始状态 ---------------------------------------------
    try:
        with session_scope() as s:
            apply_snapshot(s, tenant_id, ticket_id, initial)
        if verify_reset:
            with session_scope() as s:
                fp_before = ticket_fingerprint(s, tenant_id, ticket_id)
            with session_scope() as s:
                snap_now = export_snapshot(s, tenant_id, ticket_id)
            ok, diffs = diff_snapshots(snap_now, initial)
            if not ok:
                res.error = f"重置失败，库状态与 initial_state 不符: {diffs}"
                res.diffs = diffs
                return res
            del fp_before
    except Exception as e:  # pragma: no cover
        res.error = f"重置异常: {type(e).__name__}: {e}"
        return res

    # --- 2. 跑 agent ---------------------------------------------------
    t0 = time.perf_counter()
    try:
        set_model_kind(kind)
        state = run_ticket(
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            arm=arm,
            run_id=f"{case['case_id']}-{kind}-{arm}-k{k_index}",
            user_input=case.get("body") or "",
            # ★ 显式 `False` 而不是省略：省略会让 `build_graph` 去读
            #   `settings.checkpointer`，而持久 checkpointer 会让**参数完全
            #   相同的第二次运行**（这里的 run_id 由 case+kind+arm+k 决定，
            #   是确定性的）带着第一遍的 state **重跑整张图**。
            #   实测（见 graph/checkpoint.py 与 _cpdiag.py）：消息 11→22 累积、
            #   工具被重复调用、成本翻倍、副作用重复执行。
            #   症状是"重跑评测，pass 率变了"，且**只在第二次运行才出现** ——
            #   第一次永远是对的，所以最容易漏过 CI。
            #   所以这里不靠默认值，把"评测必须每次全新"写在调用处。
            checkpointer=False,
            # ★ 缺陷 ㉟：把 case 层的"该不该澄清"送进 verifier，让放行依据
            #   从"agent 自己写过的 pending_question"换成"golden 要求先澄清"。
            should_clarify=_case_should_clarify(case),
        )
    except Exception as e:
        res.error = f"运行异常: {type(e).__name__}: {e}"
        res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return res
    finally:
        set_model_kind("")

    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # --- 3. 导出终态并与 golden 比对 ------------------------------------
    try:
        with session_scope() as s:
            final_snap = export_snapshot(s, tenant_id, ticket_id)
    except Exception as e:  # pragma: no cover
        res.error = f"导出终态异常: {type(e).__name__}: {e}"
        return res

    passed, diffs = diff_snapshots(final_snap, expected)
    res.passed = passed
    res.diffs = diffs

    # --- 4. 过程指标 ---------------------------------------------------
    trace = list(state.get("tool_calls") or [])
    res.tool_calls = len(trace)
    # ★ 写操作口径的**唯一事实来源**是 `registry.WRITE_TOOLS`。
    #
    #   这一行原来硬编码了四个退款线工具名
    #   （create_refund / block_order / close_ticket / escalate_to_human），
    #   把售后线的 `submit_after_sales_action` / `mark_evidence_received` /
    #   `ask_customer` 漏在统计之外 —— 而那三个在 `WRITE_TOOLS` 里都登记了。
    #
    #   实测偏差（`_r10diff.py`，千级库 4/4 条目标 case）：
    #     官方 write_calls = 1，审计口径实际写调用 = 3。
    #   形状与缺陷 ⑤（列柱标错）、⑰（键名漂移）同源：**统计口径分散在两处，
    #   其中一处漏了**，而报表照常出、看不出任何异常。
    #
    #   为什么不写成模块级 import：函数内 import 零循环依赖风险
    #   （sys.modules 命中后无实际开销），而 harness ← registry 的
    #   依赖方向本项目从未验证过 —— 按 ⑧ 的教训，不在没验过的方向上押注。
    from ticket_agent.tools.registry import WRITE_TOOLS

    res.write_calls = sum(1 for t in trace if t.get("name") in WRITE_TOOLS)
    res.observed_action = _summarize_action(trace)
    res.forbidden_hits = detect_forbidden(case, trace, final_snap, expected)

    budget = state.get("budget") or {}
    res.input_tokens = int(budget.get("input_tokens", 0))
    res.output_tokens = int(budget.get("output_tokens", 0))
    res.cost_cny = float(budget.get("cost_cny", 0.0))
    res.model_calls = int(budget.get("model_calls", 0))
    res.degraded = bool(budget.get("degraded", False))
    res.tripped = bool(budget.get("tripped", False))
    res.price_unknown = bool(budget.get("price_unknown", False))

    verdict = state.get("verdict") or {}
    res.verifier_passed = verdict.get("passed")

    # --- 5. 第二轮：客户答复到达（#2 多轮澄清闭环） ----------------------
    #
    # ★ 为什么第二轮要单独跑、而不是把答复拼进第一轮的 `user_input`：
    # 那测的就不是"澄清闭环"了，而是"客户一次把话说全"。
    # 多轮澄清的难点恰恰在**第二轮看到的信息与第一轮不同**：
    # 第一轮规则说"信息不足"，第二轮同样的规则要能办成事。
    #
    # ★ 为什么只在 golden 说该澄清时才跑第二轮：
    # 对"一次就能办完"的 case 跑第二轮没有意义（它只会复读同样的结论）；
    # 而 turn-2 golden 的算法（补齐缺失字段后重算）只为"原本缺信息"设计。
    reply = (case.get("customer_reply") or "").strip()
    if reply and case.get("turn2_state"):
        # ★ 先落库再重跑，顺序不能反：反过来跑的是"答复前的世界"。
        applied = apply_customer_reply(tenant_id, ticket_id, reply)
        if not applied.get("ok"):
            res.error = f"第二轮答复落库失败: {applied.get('error')}"
            res.passed = False
            return res
        try:
            set_model_kind(kind)
            state2 = run_ticket(
                tenant_id=tenant_id,
                ticket_id=ticket_id,
                arm=arm,
                run_id=f"{case['case_id']}-{kind}-{arm}-k{k_index}-t2",
                user_input=reply,
                checkpointer=False,
                # ★ 缺陷 ㉟：第二轮同样传 case 层的判据。两轮 case 的
                #   `should_clarify` 恒为 True（turn2_state 非空），所以
                #   verifier 允许"第一轮 open + 有 pending_question"，
                #   但终态判据是 turn2_state（要求办成）——
                #   "问过"依然不能替代"办成"。
                should_clarify=_case_should_clarify(case),
            )
        except Exception as e:  # noqa: BLE001 —— 第二轮失败要能报出来，不能静默当成第一轮结果
            res.error = f"第二轮运行异常: {type(e).__name__}: {e}"
            res.passed = False
            return res
        finally:
            set_model_kind("")

        try:
            with session_scope() as s:
                final_snap = export_snapshot(s, tenant_id, ticket_id)
        except Exception as e:  # pragma: no cover
            res.error = f"第二轮导出终态异常: {type(e).__name__}: {e}"
            res.passed = False
            return res

        # turn-2 判据覆盖 turn-1：**这一轮考的就是"拿到答复后能不能办成"**。
        passed, diffs = diff_snapshots(final_snap, case["turn2_state"])
        res.passed = passed
        res.diffs = diffs

        trace2 = list(state2.get("tool_calls") or [])
        # 过程指标取两轮之和：客户看到的是一次完整交互，
        # 成本/时延当然也该按两次算（只报第二轮会让成本凭空少一半）。
        res.tool_calls += len(trace2)
        # ★ 与 turn-1 完全同一口径（唯一事实来源 `registry.WRITE_TOOLS`）。
        #   原来这一行也硬编码同样的四个退款线工具名 —— **同一个缺陷在同一个
        #   函数里有两处**：只改上面那处，两轮 case 的写操作依然少算，
        #   而差额恰好等于售后线的写入次数（turn-1 计入、turn-2 不计入），
        #   比原来更难发现，因为好在"数字变小了"而不是"报错了"。
        #   import 再写一次：不依赖上面那段先执行（防御性，代价为零）。
        from ticket_agent.tools.registry import WRITE_TOOLS

        res.write_calls += sum(1 for t in trace2 if t.get("name") in WRITE_TOOLS)
        res.observed_action = _summarize_action(trace + trace2)
        res.forbidden_hits = detect_forbidden(case, trace + trace2, final_snap, case["turn2_state"])

        b2 = state2.get("budget") or {}
        res.input_tokens += int(b2.get("input_tokens", 0))
        res.output_tokens += int(b2.get("output_tokens", 0))
        res.cost_cny += float(b2.get("cost_cny", 0.0))
        res.model_calls += int(b2.get("model_calls", 0))
        res.degraded = res.degraded or bool(b2.get("degraded", False))
        res.tripped = res.tripped or bool(b2.get("tripped", False))
        res.price_unknown = res.price_unknown or bool(b2.get("price_unknown", False))

        v2 = state2.get("verdict") or {}
        res.verifier_passed = (res.verifier_passed is not False) and (v2.get("passed") is not False)
        res.turns = 2

    return res


# --------------------------------------------------------------------------
# 批量跑 + 指标
# --------------------------------------------------------------------------

def run_suite(
    cases: list[dict[str, Any]],
    *,
    arm: str = "multi",
    kind: str = "scripted",
    k: int = 1,
    limit: int | None = None,
    progress: bool = True,
) -> list[CaseResult]:
    """跑整套 case，每条重复 k 次（用于 pass^k）。"""
    subset = cases[:limit] if limit else cases
    results: list[CaseResult] = []
    total = len(subset) * k
    n = 0
    for case in subset:
        for ki in range(k):
            r = run_case(case, arm=arm, kind=kind, k_index=ki)
            results.append(r)
            n += 1
            if progress:
                flag = "PASS" if r.passed else ("ERR " if r.error else "FAIL")
                extra = ""
                if r.diffs:
                    extra = " | " + r.diffs[0][:70]
                if r.error:
                    extra = " | " + r.error[:70]
                print(
                    f"[{n:>3}/{total}] {flag} {r.case_id:<18} {r.level} "
                    f"{r.elapsed_ms:>5}ms tools={r.tool_calls:<2}{extra}",
                    flush=True,
                )
    return results


def pass_hat_k(results: list[CaseResult], k: int) -> float:
    """pass^k：每条 case 的 k 次运行**全部**通过才算通过。

    为什么不用 pass@k：线上没有"重试到成功"，一次办错就是赔付。
    稳定性才是生产指标。
    """
    by_case: dict[str, list[bool]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r.passed and not r.error)
    if not by_case:
        return 0.0
    ok = sum(1 for v in by_case.values() if len(v) >= k and all(v[:k]))
    return ok / len(by_case)


def accuracy(results: list[CaseResult]) -> float:
    """单次通过率（所有运行拉平）。"""
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed and not r.error) / len(results)


def three_rates(results: list[CaseResult]) -> dict[str, Any]:
    """Intercom 三率：Automation = Involvement × Resolution。

    为什么值得加这一组，而不是只看 `accuracy`
    ------------------------------------------
    业界把这三个率定义为**可拆开**的（Intercom 官方公式：
    `Automation Rate = Involvement Rate × Resolution Rate`），
    价值在于**一个低分能归因**：

    - 自动化率低，是因为 **agent 根本没参与**（Involvement 低）？
      → 该改路由/入口，让更多会话进到 agent 手里。
    - 还是因为它参与了却没办成（Resolution 低）？
      → 该改 agent 本身（工具、prompt、政策）。

    这两种原因的改法完全不同，而 `accuracy` 一个数把它们压平了。

    ★ 本项目的口径映射（必须写清楚，否则三个数不可比）
    -----------------------------------------------
    - `total` = 全部运行次数
    - `resolved` = `passed and not error`（终态比对通过）
    - `engaged`（参与）= **真的调了工具去查过**，即 `not error 且 tool_calls > 0`；
      另外**通过的一律算参与**（见下方对恒等式的保证）
    - `automation_rate` = resolved / total
    - `involvement_rate` = engaged / total
    - `resolution_rate` = resolved / engaged

    ★ 口径边界（两条，都是诚实交代而不是免责）
    ----------------------------------------
    1. 本项目的 `engaged` **不是**业界那个「AI 有没有接话」——
       这里的每条 case 都是 agent 被直接调用的，不存在「没接话」的会话。
       所以 Involvement 在这里的实际含义是：**这条 case agent 有没有真的去查数据
       （而不是空跑/直接报错）**。它是一个下界刻画，不是业界口径的等位物。
    2. `escalate`（转人工）在本项目里**算参与、不算解决**——
       它确实做了工作（查了订单、跑了资格工具），只是结论是「这事我办不了」。
       这与 Intercom 把 escalation 计入参与是一致的。

    ★ 除零：`engaged == 0` 时 `resolution_rate` 返回 `None` 而不是 0.0。
    0.0 会被读成「参与了但一件都没办成」，而真相是「没有可分辨的参与」——
    这两个结论的改法完全不同（前者改 agent，后者改数据/链路）。
    """
    total = len(results)
    if not total:
        return {
            "total": 0,
            "resolved": 0,
            "engaged": 0,
            "automation_rate": 0.0,
            "involvement_rate": 0.0,
            "resolution_rate": None,
            "identity_holds": True,
        }

    resolved = [r for r in results if r.passed and not r.error]
    # engaged = resolved ∪ {not error 且真的调过工具}
    # ★ 显式把 resolved 并进去，是为了保证 resolved ⊆ engaged，
    #   从而 automation = involvement × resolution **恒等成立**。
    #   少了这一句，一条「0 工具调用却通过」的运行就会让等式差一个尾数，
    #   而那种差恰好是最难查的那种（公式对不上但每个数都看着对）。
    engaged = [
        r
        for r in results
        if (r.passed and not r.error) or (not r.error and r.tool_calls > 0)
    ]

    n_res, n_eng = len(resolved), len(engaged)
    automation = n_res / total
    involvement = n_eng / total
    resolution = (n_res / n_eng) if n_eng else None

    # 恒等式自检：公式必须是恒等式，不是近似。
    # 若它对不上，说明 resolved 没有完全包含在 engaged 里（上面的并集写错了）。
    identity = (
        abs(automation - involvement * resolution) < 1e-12
        if resolution is not None
        else True
    )

    return {
        "total": total,
        "resolved": n_res,
        "engaged": n_eng,
        "automation_rate": round(automation, 4),
        "involvement_rate": round(involvement, 4),
        "resolution_rate": None if resolution is None else round(resolution, 4),
        "identity_holds": identity,
    }


def stratify_by_condition(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    """按**条件**（`规则号:动作`）分层统计。

    ★ 为什么这个维度必须存在，而难度分层不够：

    数据的自然分布极不均衡 —— 实测 R1:reject 占 40.5%、R3:block_logistics
    只占 2.7%（1 条）。难度分层（L1/L2/L3）会把"哪条规则"这个信息抹平：
    R1:reject 和 R3:block_logistics **都是 L1**，但一个是"驳回"、另一个是
    "拦截在途物流"，考的是完全不同的能力。agent 把每一条 R3 都办错，
    L1 层的准确率也只从 100% 掉到 ~97%，总准确率几乎不动。

    换句话说：**总准确率是被多数条件"抬"起来的**。逐条件列出来之后，
    "哪条规则真的会做、哪条只是跟着多数派蒙对"才看得见。
    """
    out: dict[str, dict[str, float]] = {}
    total = len(results)
    for cond in sorted({r.condition for r in results}):
        sub = [r for r in results if r.condition == cond]
        out[cond] = {
            "n": len(sub),
            "share": round(len(sub) / total, 4) if total else 0.0,
            "accuracy": round(accuracy(sub), 4),
            "avg_tool_calls": round(mean([r.tool_calls for r in sub]), 2) if sub else 0.0,
            "avg_cost_cny": round(mean([r.cost_cny for r in sub]), 6) if sub else 0.0,
            "forbidden_hits": sum(len(r.forbidden_hits) for r in sub),
        }
    return out


def stratify_by_style(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    """按**话术风格**分层统计 —— 客户说清诉求 vs 只表达不满。

    ★ 这个维度和 `condition`（考哪条规则）正交，回答的是另一个问题：
    "同一个 agent，在客户说得含糊时表现差多少？"

    语料自带的分布让这个维度非有不可：真实语料里最常见的一类诉求
    （"帮我查下快递" / "什么时候能到"，物流类约占语料 40%）
    **本系统根本办不到** —— 动作集只有退款/驳回/拦截/转人工四种。
    这类工单的正解是**转人工或反问澄清**，不是替客户挑一个他没要的动作。

    ★ 所以 `vague` 的准确含义是「客户要的动作不在动作集内」，
    **不是**「客户没说清」：那些客户说得非常清楚，只是要的不是退款退货。
    上一版把 vague 读成"含糊"、并用它当 L3 的判据，结果报表上
    defect 被放大 18.7 倍（详见 `corpus._LEVEL_TO_STYLES` 的注释）。

    只看总准确率会把这个差异抹平：这类工单占比不小但分散在各条件下，
    agent 把它们全办错，总分也只掉几个点。

    已知局限：`style` 由 `corpus.classify_style()` 的正则判定，
    它认的是"诉求里有没有动作集内的动作词"，不是语义理解 ——
    "我要投诉，因为商品是坏的"（有瑕疵描述但无诉求词）会被判 vague，
    而人工看可能算 defect。判定规则本身是可复现、可单测的
    （`tests/test_corpus.py` 用真实语料原句钉住了判据边界），
    但它不等于人工标注，报数字时要带着这个前提。
    """
    out: dict[str, dict[str, float]] = {}
    total = len(results)
    for st in sorted({r.style for r in results}):
        sub = [r for r in results if r.style == st]
        out[st] = {
            "n": len(sub),
            "share": round(len(sub) / total, 4) if total else 0.0,
            "accuracy": round(accuracy(sub), 4),
            "avg_tool_calls": round(mean([r.tool_calls for r in sub]), 2) if sub else 0.0,
            "avg_cost_cny": round(mean([r.cost_cny for r in sub]), 6) if sub else 0.0,
            "forbidden_hits": sum(len(r.forbidden_hits) for r in sub),
        }
    return out


def coverage_gaps(
    results: list[CaseResult],
    expected_conditions: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """列出**一条 case 都没有**的条件 —— 评测集覆盖度缺陷。

    ★ 这是本项目发现过的最隐蔽的一类失败：某条规则在评测集里 0 条 case，
    对应故障就**永远不可能被发现**，而它在任何准确率数字上都不留痕迹 ——
    报表会显示"没问题"，因为确实没有题。所以它必须是一个显式返回值，
    不能靠人去心算。

    `expected_conditions` 传入业务上应当被覆盖的条件集合（例如
    `R1:refund`/`R1:reject`/`R2:reject`/`R3:block_logistics`
    /`R4:escalate`/`R5:escalate`/`R6:escalate`），返回其中缺失的那些。
    """
    present = {r.condition for r in results}
    return [c for c in expected_conditions if c not in present]


def stratify(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    """按难度分层统计 —— 总分会掩盖"简单题全对、难题全错"。"""
    out: dict[str, dict[str, float]] = {}
    for lvl in sorted({r.level for r in results}):
        sub = [r for r in results if r.level == lvl]
        out[lvl] = {
            "n": len(sub),
            "accuracy": round(accuracy(sub), 4),
            "avg_tool_calls": round(mean([r.tool_calls for r in sub]), 2) if sub else 0.0,
            "avg_cost_cny": round(mean([r.cost_cny for r in sub]), 6) if sub else 0.0,
            "forbidden_rate": round(
                sum(1 for r in sub if r.forbidden_hits) / len(sub), 4
            ) if sub else 0.0,
        }
    return out


def cost_summary(results: list[CaseResult]) -> dict[str, float]:
    """成本轴。附带"准确率上限受成本约束"的解读素材。"""
    if not results:
        return {}
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    return {
        "total_cost_cny": round(sum(r.cost_cny for r in results), 6),
        "avg_cost_cny": round(mean([r.cost_cny for r in results]), 6),
        "avg_cost_passed_cny": round(mean([r.cost_cny for r in passed]), 6) if passed else 0.0,
        "avg_cost_failed_cny": round(mean([r.cost_cny for r in failed]), 6) if failed else 0.0,
        "avg_input_tokens": round(mean([r.input_tokens for r in results]), 1),
        "avg_output_tokens": round(mean([r.output_tokens for r in results]), 1),
        "avg_model_calls": round(mean([r.model_calls for r in results]), 2),
        # ★ 工具调用与模型调用是**两个不同的量**，早先总览表把它们混用了：
        #   表头写"平均工具调用"，填的却是 avg_model_calls。多数 agent 一轮模型
        #   调用会并发发多个工具调用，所以这两个数不相等也不成固定比例 ——
        #   混用会让"多 agent 多花了多少工具调用"这个消融结论直接算错。
        "avg_tool_calls": round(mean([r.tool_calls for r in results]), 2),
        "avg_write_calls": round(mean([r.write_calls for r in results]), 2),
        "avg_latency_ms": round(mean([r.elapsed_ms for r in results]), 1),
        "degraded_rate": round(sum(1 for r in results if r.degraded) / len(results), 4),
        "budget_tripped_rate": round(sum(1 for r in results if r.tripped) / len(results), 4),
        # ★ 成本列的有效性标记。上面那些 cost 数字在单价未声明时全是 0，
        #   而 0 在报表里长得跟"免费"一模一样 —— 这个标志位就是用来区分
        #   "跑了但不要钱"和"跑了但不知道多少钱"的。报表层必须印出来。
        "price_unknown_rate": round(sum(1 for r in results if r.price_unknown) / len(results), 4),
    }


def failure_breakdown(results: list[CaseResult]) -> dict[str, int]:
    """失败原因归类 —— 报表里比一个分数有用得多。

    终态比对的 diff 文本带字段前缀，据此归因：
    是工单状态判错（流程决策错），还是退款记录不对（副作用错）。
    """
    buckets: dict[str, int] = {}
    for r in results:
        if r.passed and not r.error:
            continue
        if r.error:
            key = "runtime_error"
        elif not r.diffs:
            key = "unknown"
        else:
            kinds = set()
            for d in r.diffs:
                if d.startswith("ticket."):
                    kinds.add("ticket_status")
                elif d.startswith("refunds:"):
                    kinds.add("refund_ledger")
                elif d.startswith("order."):
                    kinds.add("order_state")
                else:
                    kinds.add("other")
            key = "+".join(sorted(kinds))
        buckets[key] = buckets.get(key, 0) + 1
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1]))
