"""脚本化模型 —— 让整个项目在**没有 API key** 的情况下也能完整跑通。

它实现了 LangChain ChatModel 的最小接口（`bind_tools` + `invoke`），所以能直接插进
`_run_react_loop`，走**完全相同的图拓扑、工具、状态与评测口径**。

它有两个身份，都很重要：

1. **离线开发/CI 的替身**：没配 key 也能跑 `pytest` 和 `ticket-agent eval`
2. **规则引擎基线**：回答"LLM agent 到底比一个 if-else 规则引擎强在哪"

★ 关于第 2 点，有一个必须诚实面对的结论：
本项目的 golden 是由规则推导的（`_decide_golden`），所以**规则引擎在终态准确率上天然接近满分** ——
它是这条赛道上的天花板，不是对手。LLM 的价值不在"算得对"，而在规则覆盖不到的地方：
L3 含糊工单的意图理解、该反问时反问、把结论讲成人话、以及面对规则没写的情况不硬编。

评测报告会把这一点直接写进去（`rule_ceiling` 字段），而不是拿 LLM 去赢一个手工搭的稻草人。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from ..config import settings

# 复用的工具名常量
_READ_ONLY = {
    "get_ticket", "get_customer", "list_orders_of_customer", "get_order",
    "list_order_items", "list_refunds_of_order", "query_policy", "days_since_signed",
    "check_refund_eligibility", "get_snapshot",
    # 售后线（补功能时新增的两个只读工具）
    "list_after_sales_of_order", "check_after_sales_eligibility",
}

#: 走**售后线**（非退款）的诉求 —— 必须用 `check_after_sales_eligibility` 判定。
#: ★ 与 `rules.REQUEST_KINDS` 的差集是 `{"refund", "return_refund"}`：
#:   退货退款虽然要寄回商品，但**钱的部分仍是退款**，走退款线判定与写款。
_SERVICE_LINE_KINDS = frozenset(
    {"exchange", "reship", "compensate", "price_protect", "invoice", "address_change", "urge"}
)

#: 走**售后线**的**动作名** —— 分派写工具时用这个，不要用上面那个诉求名集合。
#: ★ 两个集合 7 个元素里有 4 个同名（exchange/reship/compensate/price_protect），
#:   这正是它危险的地方：混用时**半数分支恰好还能跑通**，另一半静默失效，
#:   而代码看上去完全正常。这就是缺陷 ㉓ 的成因，别把两个集合合并。
_SERVICE_LINE_ACTIONS = frozenset(
    {
        "exchange",
        "reship",
        "compensate",
        "price_protect",
        "issue_invoice",
        "change_address",
        "urge_logistics",
    }
)

import re as _re

#: 从正文里提取客户自己报的金额（赔付/价保要用）。
#: ★ 只认带"元/块/¥"的明确数字。宁可取不到返回 0（→ 规则走"金额未确定，需澄清"），
#:   也不猜 —— 猜错的代价是**静默地多赔一笔钱**。
_AMOUNT_RE = _re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|人民币)")


def _amount_in_text(text: str) -> float:
    """从文本里取第一个明确的金额；取不到返回 0.0。

    ★ **已退出判定路径，保留只作旧口径探针 —— 不要因为没有调用者就删掉。**

      原先是 `_request_amount_of` 的实现（从 `body` 重抽金额）。现在那
      里改读 `tickets.request_amount`（结构化字段），原因见该函数
      docstring：`_r10diff.py` 实测在千级库上 4/4 条目标 case 里，
      澄清第二轮 `submit_customer_reply` 已把 `request_amount=8.0`
      落库，而基线仍从不变的 `body` 抽出 0.0 → `rules.py:715`
      `comp <= 0` → 又一次 need_clarification → 终态卡 `open`。

      保留原因：`_r10diff.py` 的判据①就是拿本函数（旧口径）与工单
      结构化字段做对照，打印「基线重抽 vs 落库值」。删了它，
      那条诊断能力就没了，而它恰恰是当初把这个 bug 钉死的证据链。

    ★ 与 `db/corpus.py` 里那份 `_AMOUNT_RE` / `extract_amount` 的关系：
      两者正则字面相同但**不是同一个对象**，各服务各的路径 ——
      corpus 那份给 seed 造数用，本份只给诊断脚本用。改一份不改另一份
      会让「造数抽出的金额」与「诊断抽出的金额」悄悄分叉。
    """
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:  # pragma: no cover - 正则已保证可转
        return 0.0


#: 动作名 → 写工具要传的 `kind`。
#: ★ 这两个名字**不一样**且必须映射：动作是"我们做什么"（`issue_invoice`），
#:   kind 是"客户要什么"（`invoice`）。`AfterSalesAction.kind` 存的是后者，
#:   因为同一张表要能回答"客户当初要的是开票还是改地址"。
_ACTION_TO_REQUEST: dict[str, str] = {
    "exchange": "exchange",
    "reship": "reship",
    "compensate": "compensate",
    "price_protect": "price_protect",
    "issue_invoice": "invoice",
    "change_address": "address_change",
    "urge_logistics": "urge",
}

_ENC: Any = None
_ENC_TRIED = False


def _count_tokens(text: str) -> int:
    """token 计数。优先 tiktoken，取不到就按字符数粗估。

    脚本化模型并不真正消耗 token，但**成本轴必须可比** —— 它收到的是与 LLM
    完全相同的 prompt，所以按同一套口径数出来，才能公平地说
    "规则引擎省了多少 token"。

    ★ 冷启动保护：`tiktoken.get_encoding("cl100k_base")` 在本地缓存缺失时会去
    联网拉 BPE 文件，而网络不通时它**不报错、只是长时间挂起** —— 实测第一条
    case 因此从 13ms 变成 62392ms，看上去像"agent 很慢"，实际是计数器在等网络。
    这里用带超时的守护线程探测：5 秒拿不到就永久回退到字符估算，
    绝不让一个附带的计数器把评测拖垮。
    """
    global _ENC, _ENC_TRIED
    if not _ENC_TRIED:
        _ENC_TRIED = True
        _ENC = _load_tiktoken_with_timeout(5.0)
    if _ENC is not None:
        try:
            return len(_ENC.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 2)


def _load_tiktoken_with_timeout(timeout: float) -> Any:
    """在守护线程里加载 tiktoken 编码器，超时即放弃。

    ★ 刻意不用 ThreadPoolExecutor：它的上下文管理器退出时会 join 工作线程，
    超时形同虚设。裸线程 + join(timeout) 才能真正把等待时间封顶。
    """
    box: list[Any] = []

    def _load() -> None:
        try:
            import tiktoken

            box.append(tiktoken.get_encoding("cl100k_base"))
        except Exception:
            box.append(None)

    th = threading.Thread(target=_load, daemon=True)
    th.start()
    th.join(timeout=timeout)
    return box[0] if box else None


# ── 故障注入诊断计数器 ───────────────────────────────────────────────
#
# 为什么需要"机会"计数：单说"某个故障发作了 3 次"是没有信息量的 ——
# 它可能在 3 次机会里发作了 3 次（探针完备，故障致命），
# 也可能在 30 次机会里只发作了 3 次（故障稀有，总准确率几乎不动）。
# 两种情况下总准确率都是 0.9，但结论完全相反。
#
# 所以分母在**共享代码路径**里数（对所有 kind 一致），分子只在故障覆写里数，
# 比值才有唯一含义。这是诊断设施，不是指标 —— 它不进报表，只用于敏感性分析。

_FAULT_STATS: dict[str, int] = {}
_FAULT_STATS_LOCK = threading.Lock()


def _bump(key: str) -> None:
    with _FAULT_STATS_LOCK:
        _FAULT_STATS[key] = _FAULT_STATS.get(key, 0) + 1


def fault_stats() -> dict[str, int]:
    """读取计数器快照（不重置）。"""
    with _FAULT_STATS_LOCK:
        return dict(_FAULT_STATS)


def reset_fault_stats() -> None:
    with _FAULT_STATS_LOCK:
        _FAULT_STATS.clear()


def _called(messages: Sequence[BaseMessage], name: str) -> bool:
    """这批消息里是否已经*尝试*调用过某工具。

    ★ 注意：只看 tool_calls，**不看结果成败**。被权限裁剪拦下的调用也算"调用过"。
    需要"确实成功拿到结果"时用 `_succeeded()`。
    """
    for m in messages:
        for tc in (getattr(m, "tool_calls", None) or []):
            if tc.get("name") == name:
                return True
    return False


def _succeeded(messages: Sequence[BaseMessage], name: str) -> bool:
    """是否成功拿到过某工具的**有效结果**（非 error 载荷）。

    区分"调用过"和"调用成功"很重要：`check_refund_eligibility` 被判权拦下时
    也会留下一条 tool_call，若只判 `_called` 就会误以为判定已完成，
    进而拿着 permission_denied 的载荷当结论用。
    """
    res = _last_result(messages, name)
    return bool(res) and not res.get("error")


def _last_result(messages: Sequence[BaseMessage], name: str) -> dict[str, Any]:
    """取某工具最近一次调用的返回值。"""
    out: dict[str, Any] = {}
    for m in messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == name:
            try:
                out = json.loads(m.content) if isinstance(m.content, str) else dict(m.content)
            except Exception:
                out = {}
    return out


def _tool_result_for(messages: Sequence[BaseMessage], call_id: str) -> dict[str, Any]:
    for m in messages:
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", "") == call_id:
            try:
                return json.loads(m.content) if isinstance(m.content, str) else dict(m.content)
            except Exception:
                return {}
    return {}


def _evidence_ok(messages: Sequence[BaseMessage]) -> bool:
    """工单上客户是否已提供凭证。

    读 `get_ticket` 最近一次返回的 `evidence_provided` —— 它是**任务输入**，
    不是 agent 的判断（见 `rules.PROOF_CAUSES` 的注释）。
    """
    t = _last_result(messages, "get_ticket")
    return bool(t.get("evidence_provided"))


def _last_tool_order_id(messages: Sequence[BaseMessage]) -> int | None:
    """从最近的 check_refund_eligibility / get_order 结果里推断 order_id。"""
    for m in reversed(list(messages)):
        if isinstance(m, ToolMessage):
            try:
                d = json.loads(m.content) if isinstance(m.content, str) else dict(m.content)
            except Exception:
                continue
            if "order_id" in d and isinstance(d["order_id"], int):
                return d["order_id"]
    return None


class ScriptedChatModel:
    """确定性策略模型。策略与 prompt 里写的流程逐条对应。"""

    def __init__(self, role: str = "single", model_name: str = "scripted") -> None:
        self.role = role
        self.model_name = model_name
        self._tools: list[Any] = []
        self._n = 0

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        self._tools = list(tools)
        return self

    # LangChain 兼容的零碎接口
    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _allowed(self) -> set[str]:
        from ..tools.registry import ROLE_TOOL_MATRIX

        return set(ROLE_TOOL_MATRIX.get(self.role, ()))

    def _call(self, name: str, args: dict[str, Any]) -> AIMessage:
        if name not in self._allowed():
            # 角色不该调这个工具 —— 交给 registry 拦截，让 forbidden_hits 记录下来
            pass
        self._n += 1
        return AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": f"sc{self._n}", "type": "tool_call"}],
        )

    def _final(self, text: str) -> AIMessage:
        return AIMessage(content=text)

    # ── 核心策略 ─────────────────────────────────────────
    def invoke(self, messages: Any, config: Any = None, **kwargs: Any) -> AIMessage:
        msgs: list[BaseMessage] = list(messages)
        out = self._policy(msgs)
        self._report_usage(msgs, out, config)
        return out

    def _report_usage(self, msgs: list[BaseMessage], out: AIMessage, config: Any) -> None:
        """手工发出用量回调。

        ★ 为什么必须做这件事：`BudgetTracker` 挂在 `config={"callbacks": [...]}` 上，
        真实 ChatModel 会在 `on_llm_end` 里被框架自动回调。脚本化模型不走框架的
        BaseChatModel 生命周期，如果不手工发一次，预算统计里它就是 0 token ——
        那"No API key 也能跑成本轴"就成了假象。
        """
        cbs = []
        if isinstance(config, dict):
            c = config.get("callbacks")
            if c is None:
                cbs = []
            elif isinstance(c, (list, tuple)):
                cbs = list(c)
            else:
                cbs = [c]
        if not cbs:
            return

        prompt_text = "\n".join(str(getattr(m, "content", "")) for m in msgs)
        tool_text = "".join(
            json.dumps(tc, ensure_ascii=False, default=str)
            for tc in (getattr(out, "tool_calls", None) or [])
        )
        out_text = str(out.content or "") + tool_text
        tin, tout = _count_tokens(prompt_text), _count_tokens(out_text)

        result = LLMResult(
            generations=[[ChatGeneration(message=out)]],
            llm_output={
                "token_usage": {"prompt_tokens": tin, "completion_tokens": tout},
                "model_name": self.model_name,
            },
        )
        for cb in cbs:
            # ★ 这里刻意不 try/except 吞异常：BudgetExceeded 必须冒泡到 ReAct 循环，
            # 否则预算熔断在脚本化模型上会静默失效（评测时最难发现的那类 bug）。
            cb.on_llm_end(result)

    def _policy(self, msgs: list[BaseMessage]) -> AIMessage:
        """按角色分派策略。

        ★ 为什么必须分角色：早前这里是一段"万能流程"，不区分角色地从
        `check_refund_eligibility` 开始无条件往下走。但 `triage` 的工具白名单里
        **根本没有** `check_refund_eligibility`（这是有意的：分诊员不该替处置员
        下"能不能退"的结论，prompt 里写死了"那是 resolver 的事"）。
        于是 triage 每轮都撞权限墙 → 12 步 MAX_AGENT_STEPS 耗尽 → 工单原地不动，
        评测全 0。

        修法有两种：给 triage 开权限，或者让策略认角色。**选后者** ——
        给 triage 开 `check_refund_eligibility` 等于把"最小权限"这个项目论点拆了，
        那是为了让测试变绿而削弱设计。正确做法是让"造数用的替身"尊重角色边界。
        """
        if self.role == "triage":
            return self._policy_triage(msgs)
        if self.role == "verifier":
            return self._policy_verifier(msgs)
        return self._policy_resolver(msgs)  # resolver / single：全流程

    # ── triage：只搞清楚客户要什么，不碰判定、不写任何东西 ──
    def _policy_triage(self, msgs: list[BaseMessage]) -> AIMessage:
        if not _called(msgs, "get_ticket"):
            return self._call("get_ticket", {})

        ticket = _last_result(msgs, "get_ticket")
        if ticket.get("error"):
            return self._final(f"工单不存在，无法分诊：{ticket['error']}")

        if not _called(msgs, "get_customer"):
            cid = ticket.get("customer_id")
            if cid is None:
                return self._final("工单缺少客户信息，建议转人工核实。")
            return self._call("get_customer", {"customer_id": cid})

        order_id = ticket.get("order_id")
        if order_id is not None:
            return self._final(
                f"客户诉求：{ticket.get('subject', '')}。已关联订单 {order_id}，"
                "无需澄清，请 resolver 按政策判定并处置。"
            )

        # 未关联订单 → 看候选，判断"要不要反问"
        if not _called(msgs, "list_orders_of_customer"):
            return self._call("list_orders_of_customer", {"customer_id": ticket["customer_id"]})

        orders = (_last_result(msgs, "list_orders_of_customer").get("orders")) or []
        if not orders:
            return self._final(
                f"客户诉求：{ticket.get('subject', '')}。该客户名下没有任何订单，"
                "无法定位售后对象，建议转人工核实身份与订单归属。"
            )
        if len(orders) > 1:
            lines = [
                f"订单号 {o['order_no']}（{o['category']}，{o['amount']} 元，{o['status']}）"
                for o in orders[:5]
            ]
            return self._final(
                f"客户诉求：{ticket.get('subject', '')}。该客户名下有 {len(orders)} 笔订单，"
                "**需要向客户澄清订单**，不能猜测：\n" + "\n".join(f"  - {ln}" for ln in lines)
            )

        return self._final(
            f"客户诉求：{ticket.get('subject', '')}。客户名下仅有一笔订单"
            f"（{orders[0].get('order_no')}），认定为订单 {orders[0]['order_id']}，"
            "请 resolver 按政策处置。"
        )

    # ── verifier：独立复核，只读 ──
    def _policy_verifier(self, msgs: list[BaseMessage]) -> AIMessage:
        if not _called(msgs, "get_ticket"):
            return self._call("get_ticket", {})

        ticket = _last_result(msgs, "get_ticket")
        if ticket.get("error"):
            return self._final(f"工单不存在，无法校验：{ticket['error']}")

        order_id = ticket.get("order_id")
        if order_id is not None:
            if not _called(msgs, "get_order"):
                return self._call("get_order", {"order_id": order_id})
            if not _called(msgs, "list_refunds_of_order"):
                return self._call("list_refunds_of_order", {"order_id": order_id})

        if not _called(msgs, "get_snapshot"):
            return self._call("get_snapshot", {})

        snap = _last_result(msgs, "get_snapshot")
        t_snap = snap.get("ticket") or {}
        o_snap = snap.get("order") or {}
        refunds = snap.get("refunds") or []

        lines = [
            f"工单状态：{t_snap.get('status')}",
            f"订单：{('#' + str(o_snap.get('order_id'))) if o_snap else '无关联订单'}",
        ]
        if o_snap:
            lines.append(f"订单状态：{o_snap.get('status')}，退款次数：{o_snap.get('refund_count')}")
        if refunds:
            for r in refunds:
                lines.append(f"退款记录：{r.get('status')} {r.get('amount')} 元，需人工={r.get('requires_human')}")
        else:
            lines.append("退款记录：无")

        problems: list[str] = []
        status = t_snap.get("status")
        if status == "resolved" and o_snap and not refunds and o_snap.get("status") != "cancelled":
            problems.append("工单已关闭为 resolved，但既无退款记录、订单也未取消 —— 属于只关单不办事")
        # ★ 这里曾经写成 `status in ("open", "pending_human")`，是缺陷 ⑩ 的第二个来源。
        #
        # `pending_human` 下有退款记录**完全正常**，有两条正当路径：
        #   ① R4 转人工：政策要求"建 requested 退款 + 转人工审批"，
        #      所以此刻就是「待人工 + 一条 requested 退款」；
        #   ② R5/R6 转人工：退款是**这笔订单历史上就有的**（造数时写入），
        #      本次处置只是把它转人工，一条新退款都没建。
        # 快照里只有"当前有哪些退款"，**没有"哪条是本次建的"** ——
        # 拿它判"未关闭却已退过款"必然误报。
        #
        # 真正的不一致只有 `open`：如果真发生了退款，工单必然已被关闭（resolved）
        # 或已转人工（pending_human），不可能还停在 open。
        if status == "open" and refunds:
            problems.append("工单仍为 open 却已产生退款记录 —— 动作与工单状态不一致")

        lines.append("结论：" + ("不通过" if problems else "通过"))
        lines.append("问题：" + ("；".join(problems) if problems else "无"))
        if problems:
            lines.append("建议：请 resolver 复核上述问题后重新处置。")
        return self._final("\n".join(lines))

    # ── 故障注入扩展点（基类为恒等实现，故正常路径行为不变） ──
    def _pick_ambiguous(self, orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        """候选订单多于 1 时怎么选。返回 None 表示"不猜，去反问"（正确行为）。

        ★ 这是本项目里**最容易出错的单点决策**：客户名下有 3 笔订单，
        工单正文又说"我上周买的那件"，真实 LLM 会挑一个最像的然后办下去。
        终态上看它就是"办错了一笔单"，但根因是**在信息不足时应反问而未反问**。
        把它抽成扩展点，正是为了让这个失败模式可以被单独注入和计数。
        """
        return None

    def _eligibility_step(self, msgs: list[BaseMessage], order_id: int | None) -> Any:
        """规则判定这一步怎么做。默认：老老实实调工具。

        返回 `AIMessage` 表示"这一步直接产出回复，不走工具"（供"心算"类故障注入）。

        ★ 按**诉求**分派到两个工具（退款线 / 售后线），而不是永远调退款那个：
        两条线的规则条目、参数、结论都不同（退款看七天窗口，换货看十五天，
        催单看 24 小时冷却）。若这里不分派，补发工单会被拿退款规则去判 ——
        那正是"客户要补发、系统退钱"这个真实事故的成因，**基线自己不能犯它**。
        """
        kind = self._request_kind_of(msgs)
        if kind in _SERVICE_LINE_KINDS:
            if not _succeeded(msgs, "check_after_sales_eligibility"):
                return self._call(
                    "check_after_sales_eligibility",
                    {
                        "order_id": order_id,
                        "kind": kind,
                        "cause": self._request_cause_of(msgs),
                        "amount": self._request_amount_of(msgs),
                    },
                )
            return _last_result(msgs, "check_after_sales_eligibility")
        if not _succeeded(msgs, "check_refund_eligibility"):
            return self._call("check_refund_eligibility", {"order_id": order_id})
        return _last_result(msgs, "check_refund_eligibility")

    # ── 诉求信息的读取助手（都从 `get_ticket` 的结果里取）──
    def _ticket_of(self, msgs: list[BaseMessage]) -> dict[str, Any]:
        t = _last_result(msgs, "get_ticket")
        return t if isinstance(t, dict) else {}

    def _request_kind_of(self, msgs: list[BaseMessage]) -> str:
        return str(self._ticket_of(msgs).get("request_kind") or "refund")

    def _request_cause_of(self, msgs: list[BaseMessage]) -> str:
        return str(self._ticket_of(msgs).get("request_cause") or "")

    def _request_amount_of(self, msgs: list[BaseMessage]) -> float:
        """赔付/价保要金额。**读工单的结构化字段，不从正文重抽。**

        ★ 为什么第一版不能用 `_amount_in_text(body)`（它就是这么写的）：
          它与 golden **不是同一个数据源**。
            - `seed.py:1197` 的 `_decide_golden` 回落 `tickets.request_amount`；
            - `apply_customer_reply`（`graph/nodes.py:372-376`）在澄清第二轮
              把客户答复里的金额写回**同一列**；
            - 而 `body` 一轮之间**不会变**。
          于是第二轮永远抽出 0.0 → `rules.py:715 comp <= 0` →
          又一次 need_clarification → `ask_customer` 再问一遍 →
          终态卡 `open`。

          实测（`_r10diff.py`，`seed_all(1000, 90, 7)` 与 `_reqkindcheck.py`
          逐项同参重建，4/4 目标 case 全命中）：
            - 审计里 4/4 条都有 `customer | submit_customer_reply |
              {"request_amount": 8.0}` ⇒ 答复确实落库了；
            - 但第二轮 `check_after_sales_eligibility` 仍报
              「R10：赔付金额未确定」⇒ 判定入口读到的还是 0.0；
            - 终态 `after_sales=[]`、`ticket.status='open'`、写调用 0。

        ★ 那 4 条的正文里那个「8」是**天数**不是金额（`acme-t102` 原文
          「寄个快递要8天吗请返现作为补偿」）。所以正则抽成 0.0 本身没错，
          错的是**绕过了一个已经写对的结构化字段去猜** —— 与缺陷 ㉓、㉚
          同族，也与缺陷 ①「标准答案和考生读两本不同的书」同源。

        ★ 取不到就传 0，让规则走「金额未确定 → 需要澄清」这条正确分支；
          绝不默认成订单全额：那会把「客户没说赔多少」变成一个静默的全额赔付。
        """
        return float(self._ticket_of(msgs).get("request_amount") or 0.0)

    def _post_decision(
        self, msgs: list[BaseMessage], action: str, order_id: int | None, elig: dict[str, Any]
    ) -> Any:
        """规则判定之后、执行之前的最后一道闸门。

        ★ 存在的理由：真实 LLM 的失败很少是"完全不理规则"，而是
        **拿到了结论却不按结论执行** —— 判了 escalate 却自己把单子关了，
        判了 reject 却退了款。这类错误在终态上看是"结果错"，
        但归因是"不服从上游判定"，和"判定错"是两件事，报表要分开算。
        """
        return action

    # ── resolver / single：全流程 ──
    def _policy_resolver(self, msgs: list[BaseMessage]) -> AIMessage:
        # ① 先看工单
        if not _called(msgs, "get_ticket"):
            return self._call("get_ticket", {})

        ticket = _last_result(msgs, "get_ticket")
        if ticket.get("error"):
            return self._final(f"工单不存在，无法处置：{ticket['error']}")

        # ② 看客户
        if not _called(msgs, "get_customer"):
            cid = ticket.get("customer_id")
            if cid is None:
                return self._final("工单缺少客户信息，转人工。")
            return self._call("get_customer", {"customer_id": cid})

        order_id = ticket.get("order_id")

        # ③ 没有关联订单 → 看候选
        if order_id is None:
            if not _called(msgs, "list_orders_of_customer"):
                return self._call("list_orders_of_customer", {"customer_id": ticket["customer_id"]})

            od = _last_result(msgs, "list_orders_of_customer")
            orders = od.get("orders") or []
            if len(orders) == 0:
                if not _called(msgs, "escalate_to_human"):
                    return self._call(
                        "escalate_to_human",
                        {"reason": "客户名下没有任何订单，无法定位售后对象", "suggested_action": "人工核实客户身份与订单归属"},
                    )
                return self._final("客户名下无任何订单，已转人工核实。")
            if len(orders) > 1:
                # ★ 关键行为：候选多于 1 个必须反问，不得猜测
                # 分母：所有走到"多笔候选"这一步的运行（各 kind 一致）
                _bump("ambiguous.opportunity")
                picked = self._pick_ambiguous(orders)
                if picked is None:
                    lines = [
                        f"订单号 {o['order_no']}（{o['category']}，{o['amount']} 元，{o['status']}）"
                        for o in orders[:5]
                    ]
                    question = (
                        "您好，您名下有多笔订单，需要您确认是哪一笔：\n"
                        + "\n".join(f"  - {ln}" for ln in lines)
                        + "\n请回复对应的订单号，我再为您处理。"
                    )
                    # ★ 同上：反问必须落成 `pending_question` 这个结构化事实。
                    #   只留在 final_answer 里，verifier 看不到、审计里查不到，
                    #   下一轮的 agent 也不知道自己上一轮问过什么。
                    if not _called(msgs, "ask_customer"):
                        return self._call("ask_customer", {"question": question})
                    return self._final(question)
                order_id = picked["order_id"]
            else:
                # 恰好一笔，无歧义，直接认定
                order_id = orders[0]["order_id"]

        # ④ 规则判定
        # 分母：所有走到"规则判定"这一步的运行（各 kind 一致）
        _bump("eligibility.opportunity")
        step = self._eligibility_step(msgs, order_id)
        if isinstance(step, AIMessage):
            return step
        elig = step

        action = elig.get("action", "escalate")
        reasons = "；".join(elig.get("reasons") or [])

        # 分母：所有拿到判定结论、进入执行闸门的运行（各 kind 一致）
        _bump(f"decision.{action}.opportunity")

        # 分母：命中"R1 超期驳回"这一**具体触发条件**的运行。
        # ★ 必须与故障覆写里的判据逐字相同，否则比值无意义 ——
        #   这是 lenient 的分母，而不是"所有 reject"（后者含 R2/cancelled 等无关驳回）。
        if action == "reject" and "R1" in reasons and "超过" in reasons:
            _bump("lenient_trigger.opportunity")

        # ④.5 判定后校验（故障注入的扩展点；基类为恒等变换）
        adjusted = self._post_decision(msgs, action, order_id, elig)
        if isinstance(adjusted, AIMessage):
            return adjusted
        action = adjusted

        # ⑤ 按 action 执行
        if action == "refund":
            if not _called(msgs, "create_refund"):
                return self._call(
                    "create_refund",
                    {
                        "order_id": order_id,
                        "amount": float(elig.get("amount") or 0.0),
                        "reason": f"按售后政策 R1 受理无理由退货（{reasons}）",
                        "requires_human": False,
                    },
                )
        elif action == "reject":
            if not _called(msgs, "close_ticket"):
                return self._call(
                    "close_ticket",
                    {
                        "resolution": f"您的申请不符合退货条件：{reasons}。如有质量问题请提供凭证，我们将另行处理。",
                        "status": "rejected",
                    },
                )
            return self._final(f"已驳回。依据：{reasons}")
        elif action == "block_logistics":
            if not _called(msgs, "block_order"):
                return self._call("block_order", {"order_id": order_id, "note": f"R3 物流拦截（{reasons}）"})
            if not _called(msgs, "close_ticket"):
                return self._call(
                    "close_ticket",
                    {"resolution": f"已为您发起物流拦截，订单将取消并安排退款。依据：{reasons}", "status": "resolved"},
                )
            return self._final("物流拦截已完成，工单关闭。")
        elif action == "escalate":
            if not _called(msgs, "escalate_to_human"):
                return self._call(
                    "escalate_to_human",
                    {"reason": reasons, "suggested_action": "请人工审核并按政策决定是否退款"},
                )
            return self._final(f"已转人工。依据：{reasons}")
        elif action == "need_clarification":
            # ★ 必须真的调 ask_customer，不能只在散文里说一句"需要澄清"（#2）。
            #
            #   verifier 自缺陷 ⑲ 起就不再读模型自己写的散文了 —— 它只认两个
            #   结构化事实：`order_basis == "ambiguous"` 或 `pending_question` 非空
            #   （见 nodes.py 的 needs_clarification 判据）。基线要是还用散文
            #   表达"我已经问了客户"，就会被这条断言判成"漏处置"：
            #   工单确实还是 open、pending_question 却是空的 —— 断言说的是对的。
            #
            #   这不是把判据放宽去迁就基线，而是把基线的"意图"落成事实。
            #   与其让断言去猜，不如让写入工具把事实记下来。
            if not _called(msgs, "ask_customer"):
                return self._call(
                    "ask_customer",
                    {"question": f"您好，还需要您补充以下信息我才能继续处理：{reasons}"},
                )
            return self._final("已向客户发出追问，工单保持开放，等待客户回复。")

        # ── 售后线：七个非退款动作 ──────────────────────────────
        #
        # ★ 它们全部落到**同一个写工具** `submit_after_sales_action`，
        #   差别只在 `kind`。这不是偷懒 —— 工具层就是这么设计的（见它的
        #   docstring）：七个独立工具会让 LLM 面对"该调哪个"的选择，
        #   而那个选择恰好就是政策判断本身。基线必须走同一条路，
        #   否则"基线能办、agent 办不到"就无从归因。
        #
        # ★★ 这里踩过一次真坑，记下来因为它极易重犯：
        #   第一次写成了 `action in _SERVICE_LINE_KINDS`，而那个集合装的是
        #   **诉求名**（`urge`/`invoice`），此处比较的是**动作名**
        #   （`urge_logistics`/`issue_invoice`）。四个同名（exchange/reship/
        #   compensate/price_protect）恰好通过，另外三个静默走不到写工具 ——
        #   报表上表现为"催单和开票永远办不成"，而代码看起来完全正常。
        #   动作名与诉求名是两个词表，**必须各自成集**，不能共用。
        elif action in _SERVICE_LINE_ACTIONS:
            kind = _ACTION_TO_REQUEST[action]
            # ★ 政策要求凭证而客户没给时，规则返回 need_clarification，
            #   **但这里的 `action` 仍是处置动作名**（reship/exchange/...）——
            #   need_clarification 只在 `_decide_service` 内部作为返回值出现，
            #   到了这个分派点已经被 `expected_action_for(kind)` 换回动作名。
            #   所以判"要不要先要凭证"要看 eligibility 结果，不是看 action。
            if not _called(msgs, "mark_evidence_received") and not _evidence_ok(msgs):
                return self._call(
                    "mark_evidence_received",
                    {"evidence_type": "photo", "note": "按政策需提供凭证，先向客户索取"},
                )
            if not _called(msgs, "submit_after_sales_action"):
                return self._call(
                    "submit_after_sales_action",
                    {
                        "order_id": order_id,
                        "kind": kind,
                        "cause": self._request_cause_of(msgs),
                        "amount": float(elig.get("amount") or 0.0),
                        "detail": "",
                    },
                )
            # ★ 不 return：落到下面 ⑥ 的统一关单。
            #   第一次写成了 `return self._final(...)`，于是"事办了、单没关"——
            #   终态比对里 ticket.status 期望 resolved、实际 open，
            #   而退款线因为是自然下落所以没这个问题。**收尾必须共用一处。**

        # ⑥ 收尾关单
        if not _called(msgs, "close_ticket"):
            return self._call(
                "close_ticket",
                {"resolution": f"退款已受理，将原路退回。依据：{reasons}", "status": "resolved"},
            )

        return self._final(f"处置完成。依据：{reasons}")


class FlakyScriptedModel(ScriptedChatModel):
    """故障注入模型：故意漏掉 `check_refund_eligibility` 直接退款。

    用途是**验证评测框架真的能抓到错误**。一个从来不会失败的评测框架，
    和一个从来不会失败的 agent 一样可疑 —— 所以这个"坏 agent"必须被扣分，
    否则说明指标是假的。
    """

    def _policy(self, msgs: list[BaseMessage]) -> AIMessage:
        if not _called(msgs, "get_ticket"):
            return self._call("get_ticket", {})
        ticket = _last_result(msgs, "get_ticket")
        order_id = ticket.get("order_id")
        if order_id is None:
            return self._final("（故障注入）不处理无订单工单。")
        # ★ 跳过规则判定，直接全额退款
        if not _called(msgs, "create_refund"):
            return self._call(
                "create_refund",
                {
                    "order_id": order_id,
                    "amount": 999999.0,  # 故意的越界金额
                    "reason": "（故障注入）未经规则判定直接退款",
                    "requires_human": False,
                },
            )
        if not _called(msgs, "close_ticket"):
            return self._call("close_ticket", {"resolution": "（故障注入）已退款", "status": "resolved"})
        return self._final("（故障注入）完成。")


class OverprivilegedModel(ScriptedChatModel):
    """越权模型：以 triage 身份尝试调用写工具。

    验证的是**权限物理裁剪**：即使模型执意调用，也应被 registry 拦下并记入 forbidden_hits。
    """

    def _policy(self, msgs: list[BaseMessage]) -> AIMessage:
        if not _called(msgs, "create_refund"):
            return self._call("create_refund", {"order_id": 1, "amount": 1.0, "reason": "越权测试"})
        return self._final("（越权测试）尝试完毕。")


# ══════════════════════════════════════════════════════════════════════════
# 有分辨率的故障注入 —— 上面两个"钝"故障只证明框架能看见明晃晃的错
# （999999 元退款、越权调用），任何 diff 都抓得到。真正需要证明的是：
# 框架能不能抓住**真实 LLM 会犯的那种错** —— 每一步看起来都合理，
# 只有把终态拿去比对才发现不对。
#
# 下面四个模型各自**只改写一个扩展点**，与正确行为的距离都是一行代码。
# 这样"指标掉了多少"才能唯一地归因到那个行为差异上；
# 如果一次性改十处，分数下降说明不了任何事。
# ══════════════════════════════════════════════════════════════════════════


class GuesserModel(ScriptedChatModel):
    """猜测模型：客户名下有多笔订单时，挑一笔最像的就办。

    注入的失败模式：**信息不足时应反问而未反问**。

    这是全项目最容易犯、也最贵的错：客户说"我上周买的那件"，名下 3 笔订单，
    真实 LLM 不会停下来问，它会挑一个然后一路办完。终态看是"办错了单"，
    根因却是澄清环节被跳过 —— 前者是结果错，后者是流程错，报表要能分辨。

    与基类的差异仅一行：`_pick_ambiguous` 返回了一笔而不是 `None`。
    """

    def _pick_ambiguous(self, orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        # ★ 唯一的分歧点：不反问，直接认定第一笔
        _bump("guesser.fired")
        return orders[0] if orders else None


class LenientModel(ScriptedChatModel):
    """宽纵模型：拿到"超期驳回"的结论，却因同情客户改成退款。

    注入的失败模式：**为了客户体验而放松硬规则**。

    这是真实 LLM 极常见的偏差 —— 政策写"超过 7 天不予无理由退货"，
    模型看到"签收已 8 天"，觉得差一天而已，就按退款办了。
    差一天看起来无害，但它意味着**规则边界由模型心情决定**，
    而边界正是风控的全部意义。方向上也最危险：错在"多退钱"而非"少退钱"。

    与基类的差异仅一行：把 R1 超期的 reject 改写成 refund。
    """

    def _post_decision(
        self, msgs: list[BaseMessage], action: str, order_id: int | None, elig: dict[str, Any]
    ) -> Any:
        joined = "；".join(elig.get("reasons") or [])
        if action == "reject" and "R1" in joined and "超过" in joined:
            # ★ 唯一的分歧点：超期一天也算"在期限内"，改成退款
            _bump("lenient.fired")
            return "refund"
        return action


class NoncompliantModel(ScriptedChatModel):
    """抗命模型：规则判定"必须转人工"，它却自己把单子关了。

    注入的失败模式：**判定对了，却不按判定执行**。

    这一类错误最隐蔽：上游 `check_refund_eligibility` 完全正常工作，
    结论也拿到了，但 agent 觉得"这事儿我能定"，于是绕过人工审批直接结案。
    终态上只差一个 `pending_human` vs `resolved`，可实际含义是
    **一个需要人工审批的高风险诉求被静默关闭了** —— 没有退款、没有拦截，
    客户以为处理完了，其实什么都没发生。

    它同时检验 HITL 门禁：这条路径必须让工单落在待人工队列里，
    而不是从运行列表里消失。

    与基类的差异仅一行：escalate 被改写成 resolve。
    """

    def _post_decision(
        self, msgs: list[BaseMessage], action: str, order_id: int | None, elig: dict[str, Any]
    ) -> Any:
        if action == "escalate":
            # ★ 唯一的分歧点：不转人工，自己收尾关单（落进 ⑥ 的兜底 close_ticket）
            _bump("noncompliant.fired")
            return "resolve"
        return action


class LuckyModel(ScriptedChatModel):
    """侥幸模型：不调规范判定工具，自己拿原始字段拼结论。

    注入的失败模式：**绕开权威判定，凭自己推理，且结果常常是对的**。

    这是四个故障里唯一一个**终态准确率可能很高、却被 forbidden 探针抓住**的。
    它跳过 `check_refund_eligibility`，改用 `get_order` + `days_since_signed`
    自行判断"在 7 天内就退"。普通 L1 工单它答对了 —— 终态与正确处置**完全一致**；
    但每一笔退款都记着 `refund_without_eligibility_check`。

    ★ 它证明的是"过程轴"独立于"终态轴"的价值：
    只比终态，这个 agent 看起来近乎满分；而它实际上把
    R2（内衣/定制/数字商品）、R4（大额/黑名单）、R6（生鲜）全部无视了 ——
    只要签收不满 7 天就退。报表若只看准确率，会把它当成好 agent。

    与基类的差异仅一行：`_eligibility_step` 不调那个工具。
    """

    def _eligibility_step(self, msgs: list[BaseMessage], order_id: int | None) -> Any:
        _bump("lucky.fired")
        if not _succeeded(msgs, "get_order"):
            return self._call("get_order", {"order_id": order_id})
        order = _last_result(msgs, "get_order")
        if not _succeeded(msgs, "days_since_signed"):
            return self._call("days_since_signed", {"order_id": order_id})
        d = _last_result(msgs, "days_since_signed")

        amount = float(order.get("amount") or 0.0)
        days = d.get("days_since_signed")
        window = settings.return_window_days

        # 自己拼出来的结论：只认"签收是否在期限内"，其余规则（R2/R4/R5/R6）全不适用
        if days is None:
            return {"action": "escalate", "amount": amount, "eligible": False,
                    "reasons": ["订单缺少签收时间，无法确认期限"]}
        if days > window:
            return {"action": "reject", "amount": amount, "eligible": False,
                    "reasons": [f"签收已 {days} 天，超过 {window} 天期限"]}
        return {"action": "refund", "amount": amount, "eligible": True,
                "reasons": [f"签收 {days} 天，在 {window} 天期限内"]}


#: 所有故障注入模型：`kind` → 类。键同时是 CLI/API 的 `--kind` 取值。
FAULT_MODELS: dict[str, type[ScriptedChatModel]] = {
    "scripted": ScriptedChatModel,
    "flaky": FlakyScriptedModel,
    "overprivileged": OverprivilegedModel,
    "guesser": GuesserModel,
    "lenient": LenientModel,
    "noncompliant": NoncompliantModel,
    "lucky": LuckyModel,
}


def get_model(role: str, kind: str = "scripted") -> Any:
    """模型工厂。

    `scripted` 是正确基线；其余都是**故意做坏**的对照，用于验证评测框架的分辨率。
    完整清单见 `FAULT_MODELS`。
    """
    try:
        cls = FAULT_MODELS[kind]
    except KeyError:
        raise ValueError(f"未知模型 kind={kind!r}，可选：{', '.join(sorted(FAULT_MODELS))}") from None
    return cls(role=role)
