"""成本与预算中间件（难点 #5：多轮 token 累计熔断）。

★ 为什么用 **callback** 而不是 middleware hook 来记账：

LangChain 1.0 的 middleware hook 名（`before_model` / `wrap_model_call` / …）在小版本间
调整过，而 `BaseCallbackHandler.on_llm_end` 是跨版本稳定的。记账属于横切关注点，
用 callback 更稳，也不会因为 hook 签名变化而静默失效 —— 那种失效最危险：
预算熔断悄悄不生效，账单上才发现。

middleware hook 的写法我也保留在下面（`budget_middleware`），供你在 1.0 稳定版上切换。
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from ..config import settings


def _extract_usage(response: LLMResult) -> tuple[int, int]:
    """从 LLMResult 里尽力提取 (input_tokens, output_tokens)。

    不同厂商/版本把用量放在不同位置，这里按可靠性顺序依次尝试。
    """
    # ① message.usage_metadata（LangChain 标准位置）
    try:
        for gen_list in response.generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg is not None else None
                if um:
                    return int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0))
    except Exception:
        pass

    # ② llm_output["token_usage"]（OpenAI 兼容端点的常见位置）
    out = response.llm_output or {}
    tu = out.get("token_usage") or out.get("usage") or {}
    if tu:
        return int(tu.get("prompt_tokens", 0) or 0), int(tu.get("completion_tokens", 0) or 0)

    # ③ 估算兜底：按字符数粗算（宁可高估，不能低于实际而不熔断）
    approx_in = approx_out = 0
    for gen_list in response.generations:
        for gen in gen_list:
            approx_out += len(getattr(gen, "text", "") or "") // 2
    return approx_in, approx_out


class BudgetTracker(BaseCallbackHandler):
    """累计 token 与成本，并在超预算时抛异常熔断。

    每个 run 新建一个实例，挂到 `config={"callbacks": [tracker]}` 上。
    这样并发跑多个 case 时各算各的，不会串账。
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        max_cost_cny: float | None = None,
        on_degrade: Any = None,
    ) -> None:
        super().__init__()
        self.max_tokens = max_tokens if max_tokens is not None else settings.max_tokens_per_ticket
        self.max_cost_cny = (
            max_cost_cny if max_cost_cny is not None else settings.max_cost_per_ticket_cny
        )
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_cny = 0.0
        #: 当前端点的单价没声明 → 成本数字是"不知道"而不是"0 元"。报表会显式标注。
        self.price_unknown = False
        self.model_calls = 0
        self.degraded = False
        self.tripped = False
        self.tripped_reason = ""
        self._on_degrade = on_degrade

    # ── 记账 ──────────────────────────────────────────────
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        tin, tout = _extract_usage(response)
        self.input_tokens += tin
        self.output_tokens += tout
        self.model_calls += 1
        self.cost_cny += self._cost_of(tin, tout)
        self._maybe_degrade()
        self._maybe_trip()

    def _cost_of(self, tin: int, tout: int) -> float:
        """把 token 换算成金额。

        ★ 单价取自**当前生效端点**（`settings.endpoint`），不是配置里的一个全局常量。

        为什么这很重要：换模型往往连端点一起换（DeepSeek → 混元 → 本地），
        而各家单价差一个数量级。单价跟端点分离的话，换端点后成本列会继续
        用旧单价算 —— 数字照常打印、量级照常合理，**只是算的不是这个端点的钱**。
        这与缺陷 ⑨（基准漂移）是同一类问题，漂移源从时钟换成了单价表。

        ★ 单价未声明时返回 0 并标记 `price_unknown=True`，而不是猜一个数：
        猜的数字会让报表显示一个精确的错值，比"不知道"更糟。报表层会
        把 `price_unknown` 显式印出来（见 eval/harness.py 的 cost_summary）。
        """
        ep = settings.endpoint
        if not ep.price_known:
            self.price_unknown = True
            return 0.0
        return (
            tin / 1000 * (ep.price_in or 0.0)
            + tout / 1000 * (ep.price_out or 0.0)
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        # LLM 调用失败不计费，但要记一次调用次数，便于发现重试风暴
        self.model_calls += 1

    # ── 降级与熔断 ────────────────────────────────────────
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def _ratio(self) -> float:
        r_token = self.total_tokens / max(self.max_tokens, 1)
        r_cost = self.cost_cny / max(self.max_cost_cny, 1e-9)
        return max(r_token, r_cost)

    def _maybe_degrade(self) -> None:
        """达到阈值先降级模型，而不是直接熔断 —— 让任务有机会跑完。"""
        if self.degraded:
            return
        if self._ratio() >= settings.degrade_at_ratio:
            self.degraded = True
            if self._on_degrade is not None:
                try:
                    self._on_degrade(self)
                except Exception:
                    pass

    def _maybe_trip(self) -> None:
        if self.tripped:
            return
        if self.total_tokens >= self.max_tokens:
            self.tripped = True
            self.tripped_reason = (
                f"token 超限：{self.total_tokens} >= {self.max_tokens}"
            )
        elif self.cost_cny >= self.max_cost_cny:
            self.tripped = True
            self.tripped_reason = (
                f"成本超限：{self.cost_cny:.4f} 元 >= {self.max_cost_cny} 元"
            )
        if self.tripped:
            raise BudgetExceeded(self.tripped_reason, tracker=self)

    def as_budget(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_cny": round(self.cost_cny, 6),
            "model_calls": self.model_calls,
            "degraded": self.degraded,
            "tripped": self.tripped,
            "tripped_reason": self.tripped_reason,
            # ★ 必须往上传，否则"单价未知"这个事实出了 tracker 就消失了：
            #   下面 cost_cny 会是 0，而 0 在报表里长得跟"免费"一模一样。
            "price_unknown": self.price_unknown,
        }


class BudgetExceeded(Exception):
    """预算熔断。被图节点捕获后走"升级人工"分支，而不是让整个 run 崩掉。"""

    def __init__(self, message: str, tracker: BudgetTracker | None = None) -> None:
        super().__init__(message)
        self.tracker = tracker


def merge_budget(tracker: BudgetTracker, prev: dict[str, Any] | None) -> dict[str, Any]:
    """把本次调用的用量合并进既有预算（节点级别累加）。"""
    prev = prev or {}
    return {
        "input_tokens": int(prev.get("input_tokens", 0)) + tracker.input_tokens,
        "output_tokens": int(prev.get("output_tokens", 0)) + tracker.output_tokens,
        "cost_cny": round(float(prev.get("cost_cny", 0.0)) + tracker.cost_cny, 6),
        "model_calls": int(prev.get("model_calls", 0)) + tracker.model_calls,
        "degraded": bool(prev.get("degraded", False)) or tracker.degraded,
        "tripped": bool(prev.get("tripped", False)) or tracker.tripped,
        "tripped_reason": tracker.tripped_reason or str(prev.get("tripped_reason", "")),
        # ★ 同样要合并 —— 一张工单会跑多个节点，只要有一个节点发现单价未知，
        #   整张工单的成本就是"不知道"，哪怕别的节点都算出了数。
        "price_unknown": bool(prev.get("price_unknown", False)) or tracker.price_unknown,
    }


# ── 备选实现：LangChain 1.0 middleware hook（稳定版可用时切换）──
def budget_middleware(tracker: BudgetTracker) -> Any:
    """返回一个 wrap_model_call 中间件，其记账逻辑与 callback 版一致。

    用法（LangChain >= 1.0）::

        from langchain.agents.middleware import wrap_model_call
        agent = create_agent(model, tools, middleware=[budget_middleware(tracker)])

    注意：hook 签名在不同小版本间有变化，若报 TypeError 请改回 callback 方案。
    """
    try:
        from langchain.agents.middleware import wrap_model_call
    except Exception as exc:  # pragma: no cover - 取决于安装版本
        raise RuntimeError(
            "当前 langchain 版本没有 wrap_model_call；请使用 BudgetTracker callback 方案"
        ) from exc

    @wrap_model_call
    def _mw(request: Any, handler: Any) -> Any:
        if tracker.tripped:
            raise BudgetExceeded(tracker.tripped_reason, tracker=tracker)
        return handler(request)

    return _mw
