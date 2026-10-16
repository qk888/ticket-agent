"""端点自检：在**花掉一轮评测之前**回答"这个端点能不能用"。

════════════════════════════════════════════════════════════════
为什么必须有这一层
════════════════════════════════════════════════════════════════

本项目的 agent 只有一种推进方式：**模型发出 `tool_calls`**。
`graph/nodes.py` 的 ReAct 循环里是这么写的：

    calls = getattr(ai, "tool_calls", None) or []
    if not calls:
        break                      # ← 没有工具调用 = 这一轮结束了

也就是说，"模型不产生 tool_calls" 与 "模型认为任务已完成" 在这个循环里
是**同一个分支**。模型若从不发起工具调用，agent 会立刻结束、什么都不做 ——
而它**不会报错**。评测照常跑完，报表照常生成，只是准确率很低。

这个失效模式在换端点时特别危险，因为**接口看起来一切正常**：
HTTP 200、返回了内容、token 也计费了。区别只在响应体里有没有那个
`tool_calls` 字段 —— 而第三方中转/聚合层把它丢掉是常见故障
（有的只转发 `content`，有的把 `tool_calls` 转成了纯文本）。

所以：**换任何新端点，先跑自检再跑评测。**
一轮千级评测要几千次模型调用，自检只要 3 次。

════════════════════════════════════════════════════════════════
检查什么，以及每一项"没通过"意味着什么
════════════════════════════════════════════════════════════════

1. **连通与鉴权** —— 最基础。失败通常是 base_url 拼错（少了 `/v1`）
   或 key 无效/欠费。
2. **tool_calls 是否被保留** —— ★ 最关键的一项。见上。
3. **多轮工具循环** —— 发出 tool_call 之后，把工具结果回填再问一次，
   看它能否继续。有些中转层第一轮保留 tool_calls、第二轮就退化，
   只测一次会漏掉。
4. **usage 是否上报** —— 成本轴依赖 `usage.prompt_tokens/completion_tokens`。
   不上报时 `middleware._extract_usage` 会走字符数估算兜底，
   成本与 token 数随之失真（**会失真但不会报错**）。

★ 这项检查本身不做"通过/不通过"的阻断判断 —— 它只**报告事实**。
  阻断是调用方的事（`cli.py` 的 `report` / `eval` 在端点未自检时会拦，
  除非显式 `LLM_ALLOW_UNVERIFIED=true`）。分开的理由：自检脚本要在
  "端点确实是坏的"这个前提下也能跑完并打印诊断，而不是自己先抛异常。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Endpoint, resolve_endpoint, settings

#: 自检用的工具定义。刻意做成**无副作用**的查询工具（不是 create_refund 之类），
#: 这样自检永远不会动到业务数据 —— 端点自检不该有写权限。
_PROBE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "查询订单当前状态。自检用，无副作用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "订单号"},
                },
                "required": ["order_id"],
            },
        },
    }
]


@dataclass
class Check:
    """一项检查的结果。

    `ok` 为 None 表示**没测**（比如前面一步就失败了，后面的测不下去）。
    ★ 区分 None 与 False 是必要的：把"没测"记成"失败"会让人以为端点有 4 个
      问题，实际可能只有 1 个，修好它其余全过。
    """

    name: str
    ok: bool | None
    detail: str
    ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "ms": self.ms}

    @property
    def mark(self) -> str:
        return {True: "✓", False: "✗", None: "—"}[self.ok]


@dataclass
class EndpointReport:
    """一次端点自检的完整结论。"""

    endpoint: dict[str, Any]  # 已脱敏（无 key）
    checks: list[Check] = field(default_factory=list)
    #: 自检是否足以支撑一次评测（见 `ready` 的定义）
    sample_text: str = ""
    latency_ms: int = 0

    @property
    def ready(self) -> bool:
        """能不能拿它跑评测。

        ★ 判据是"**必需项**全过"，不是"全过"：
        - 连通（1）与 tool_calls（2）是必需 —— 缺任一项，agent 根本推不动。
        - 多轮循环（3）与 usage（4）不是必需：
          前者影响的是"半途退化"，后者影响的是成本数字准不准；
          它们坏了评测仍然产出一个**有意义的**准确率，只是附带说明要读。
          把非必需项也算进 ready，会让"能跑但成本不准"被误判成"不能跑"。
        """
        required = {"连通与鉴权", "tool_calls 保留"}
        got = {c.name: c.ok for c in self.checks if c.name in required}
        return bool(got) and all(got.get(n) is True for n in required)

    @property
    def warnings(self) -> list[str]:
        """非必需项里没过的那些 —— 不影响能不能跑，但影响怎么读报表。"""
        required = {"连通与鉴权", "tool_calls 保留"}
        out = []
        for c in self.checks:
            if c.name not in required and c.ok is False:
                out.append(f"{c.name}：{c.detail}")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "checks": [c.to_dict() for c in self.checks],
            "ready": self.ready,
            "warnings": self.warnings,
            "latency_ms": self.latency_ms,
            "sample_text": self.sample_text,
        }


def _client_for(ep: Endpoint) -> Any:
    """建一个原始 openai client。

    ★ 自检刻意**不走 LangChain**，直接用 openai SDK：
      这一层要回答的是"端点本身的行为"，中间夹一层框架，
      出问题时无法区分是端点丢了 tool_calls 还是框架弄丢了。
    """
    from openai import OpenAI

    return OpenAI(
        api_key=ep.api_key or "not-needed",
        base_url=ep.base_url,
        timeout=settings.request_timeout,
        max_retries=0,  # ★ 自检不重试：要看清端点的**真实**行为，不是重试后的行为
    )


def _err_text(exc: BaseException) -> str:
    """把异常压成一行 —— 自检的失败原因要能直接贴进 issue。"""
    msg = str(exc)
    for marker in ("401", "403", "404", "429", "500", "502", "503"):
        if marker in msg:
            extra = {
                "401": "鉴权失败：key 无效或没配上",
                "403": "被拒绝：key 无权限或余额不足",
                "404": "路径不存在：base_url 十有八九少了 /v1",
                "429": "限流：并发或配额超限（混元官方默认只给 5 并发）",
                "500": "对端内部错误",
                "502": "网关错误：中转层上游不通",
                "503": "服务不可用",
            }[marker]
            return f"{marker} {extra}｜{msg[:200]}"
    return f"{type(exc).__name__}: {msg[:200]}"


def check_endpoint(ep: Endpoint) -> EndpointReport:
    """对端点跑 4 项检查。**不抛异常** —— 失败写成 Check(ok=False)。"""
    rep = EndpointReport(endpoint=ep.redacted())

    if not ep.is_configured:
        missing = "、".join(ep.missing())
        rep.checks.append(Check("连通与鉴权", False, f"配置不全，缺：{missing}"))
        return rep

    try:
        client = _client_for(ep)
    except Exception as exc:  # pragma: no cover - 依赖缺失等
        rep.checks.append(Check("连通与鉴权", False, f"建 client 失败：{_err_text(exc)}"))
        return rep

    model = ep.model

    # ── ① 连通与鉴权 ─────────────────────────────────────
    t0 = time.monotonic()
    try:
        r1 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "只回复两个字：收到"}],
            max_tokens=16,
            temperature=0,
        )
        ms = int((time.monotonic() - t0) * 1000)
        rep.latency_ms = ms
        text = (r1.choices[0].message.content or "").strip()
        rep.sample_text = text[:60]
        rep.checks.append(Check("连通与鉴权", True, f"返回：{text[:30]!r}", ms))
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        rep.checks.append(Check("连通与鉴权", False, _err_text(exc), ms))
        # 连不通就没必要往下测了 —— 后面三项的结果都会是同一个网络错误，
        # 报 4 个错会掩盖"其实只有一个问题"。
        for name in ("tool_calls 保留", "多轮工具循环", "usage 上报"):
            rep.checks.append(Check(name, None, "跳过：上一步未通过"))
        return rep

    # ── ② tool_calls 是否被保留（★ 最关键）───────────────
    t0 = time.monotonic()
    tc = None
    try:
        r2 = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": "帮我查一下订单 12345 现在是什么状态。必须调用工具查，不要直接回答。",
                }
            ],
            tools=_PROBE_TOOLS,
            tool_choice="auto",
            max_tokens=256,
            temperature=0,
        )
        ms = int((time.monotonic() - t0) * 1000)
        msg = r2.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            tc = calls[0]
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "?") if fn else "?"
            args = getattr(fn, "arguments", "") if fn else ""
            rep.checks.append(
                Check("tool_calls 保留", True, f"收到 {len(calls)} 个调用：{name}({str(args)[:60]})", ms)
            )
        else:
            content = (getattr(msg, "content", "") or "").strip()
            rep.checks.append(
                Check(
                    "tool_calls 保留",
                    False,
                    "响应里**没有** tool_calls 字段。agent 靠它推进，缺了就等于"
                    f"一步都走不动（但接口不报错）。实际返回内容：{content[:120]!r}",
                    ms,
                )
            )
    except Exception as exc:
        # ★ 有些端点不认 tools 参数会直接 400 —— 那同样是"不能用"，
        #   只是原因不同（不是丢字段，而是根本不支持）。
        ms = int((time.monotonic() - t0) * 1000)
        rep.checks.append(Check("tool_calls 保留", False, f"带 tools 的请求失败：{_err_text(exc)}", ms))

    # ── ③ 多轮工具循环 ───────────────────────────────────
    if tc is None:
        rep.checks.append(Check("多轮工具循环", None, "跳过：上一项没有拿到 tool_call"))
    else:
        t0 = time.monotonic()
        try:
            fn = getattr(tc, "function", None)
            args = getattr(fn, "arguments", "") if fn else "{}"
            r3 = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": "帮我查一下订单 12345 现在是什么状态。必须调用工具查。",
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": getattr(tc, "id", "call_1"),
                                "type": "function",
                                "function": {
                                    "name": getattr(fn, "name", "get_order_status") if fn else "x",
                                    "arguments": str(args) or "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": getattr(tc, "id", "call_1"),
                        "content": json.dumps(
                            {"order_id": 12345, "status": "shipped"}, ensure_ascii=False
                        ),
                    },
                ],
                tools=_PROBE_TOOLS,
                tool_choice="auto",
                max_tokens=256,
                temperature=0,
            )
            ms = int((time.monotonic() - t0) * 1000)
            m3 = r3.choices[0].message
            text = (getattr(m3, "content", "") or "").strip()
            more = getattr(m3, "tool_calls", None) or []
            if text or more:
                rep.checks.append(
                    Check("多轮工具循环", True, f"回填工具结果后继续应答：{(text or '又发起了工具调用')[:40]!r}", ms)
                )
            else:
                rep.checks.append(
                    Check("多轮工具循环", False, "回填工具结果后返回空响应（既无文本也无 tool_calls）", ms)
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            rep.checks.append(Check("多轮工具循环", False, _err_text(exc), ms))

    # ── ④ usage 上报 ─────────────────────────────────────
    usage = getattr(r1, "usage", None)
    pt = getattr(usage, "prompt_tokens", None) if usage else None
    ct = getattr(usage, "completion_tokens", None) if usage else None
    if pt is None and ct is None:
        rep.checks.append(
            Check(
                "usage 上报",
                False,
                "响应里没有 usage 字段。成本与 token 数会退化成按字符数估算"
                "（**会失真，但不会报错**），依赖 token 的预算熔断也不再精确。",
            )
        )
    else:
        rep.checks.append(Check("usage 上报", True, f"prompt={pt} completion={ct}"))

    return rep


def record_check(ep: Endpoint, rep: EndpointReport) -> Path | None:
    """把自检结论落盘，供**另一个进程**的闸门读取。写不进去返回 None。

    ★ 为什么必须落盘、不能只记在内存里（缺陷 ⑯）：
      `endpoint --check` 是一个进程，`report` / `eval` 是另一个进程。
      内存里记一个"已自检"的布尔值，跑 `report` 时它必然是 False ——
      那这个闸门就等于"每次都要重跑自检"，或者更糟：被绕过。

    ★ 为什么单独抽成一个函数（缺陷 ⑯ 的成因）：
      `cli.cmd_endpoint` 的 `--check` 分支原先直接调 `check_endpoint()`，
      而落盘只写在 `preflight()` 里 —— 于是"用户跑的自检"与"闸门读的记录"
      之间断了一环：自检四项全过、结论写着"可以跑评测"，
      而 `eval` 照旧拦下来说"还没通过自检"，用户唯一出路是
      `LLM_ALLOW_UNVERIFIED=1` —— 恰好把这道防线整个绕过去。
      写记录是**自检的一部分**，不该有两个入口各做一半。

    ★ 文件里不写 api_key；只带一个 `base_url|model` 指纹用于区分端点 ——
      换端点后旧记录自然失效，这正是我们要的。
    """
    try:
        path = settings.eval_dir / ".endpoint_check.json"
        payload = rep.to_dict()
        # 端点指纹：换地址/换模型就让旧记录失效
        payload["fingerprint"] = f"{ep.base_url}|{ep.model}"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        # 自检记录写不进去不该让自检本身失败 —— 它是辅助凭据，不是依赖。
        return None


def preflight(profile: str | None = None, *, verbatim: bool = False) -> EndpointReport:
    """评测前的端点检查入口（供 cli 调用）。检查 + 落盘记录。"""
    ep = resolve_endpoint(profile) if profile else settings.endpoint
    rep = check_endpoint(ep)
    record_check(ep, rep)
    return rep


def last_check_matches(ep: Endpoint) -> bool:
    """磁盘上最近一次自检，是不是**当前这个端点**的、且通过了。"""
    path = settings.eval_dir / ".endpoint_check.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if data.get("fingerprint") != f"{ep.base_url}|{ep.model}":
        return False
    return bool(data.get("ready"))


def render_markdown(rep: EndpointReport) -> str:
    """给人和给报表看的自检小结。"""
    L: list[str] = []
    ep = rep.endpoint
    L.append(f"端点：`{ep['profile']}`　模型：`{ep['model']}`")
    L.append(f"地址：`{ep['base_url']}`　来源：{ep['source']}")
    if ep["price_known"]:
        L.append(
            f"单价：输入 {ep['price_in_per_1k_cny']} 元/千 token、"
            f"输出 {ep['price_out_per_1k_cny']} 元/千 token"
        )
    else:
        L.append("单价：**未声明** → 成本列不计金额（不是 0 元，是不知道）")
    L.append("")
    L.append("| 检查 | 结果 | 说明 |")
    L.append("|---|---|---|")
    for c in rep.checks:
        d = c.detail.replace("|", "\\|")
        L.append(f"| {c.name} | {c.mark} | {d} |")
    L.append("")
    if rep.ready:
        L.append("**结论：可以跑评测。**")
    else:
        L.append("**结论：不能跑评测** —— 必需项未通过，agent 推不动或连不上。")
    for w in rep.warnings:
        L.append(f"- 注意（不阻断）：{w}")
    return "\n".join(L)
