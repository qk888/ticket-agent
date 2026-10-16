"""集中配置。

设计原则：**所有可调参数都在这里**。
评测要做 A/B（阈值 500 vs 800、预算 0.5 元 vs 2 元），参数散落在各处就没法做消融。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════
# 端点档案（多端点支持：云端真实 API / 第三方中转 / 本地大模型）
# ══════════════════════════════════════════════════════════════
#
# ★ 为什么"换模型"要连**单价**一起换，而不是只换 base_url 和 model_name：
#
#   成本轴（graph/middleware.py 的 BudgetTracker）是用单价把 token 换算成金额的。
#   单价写死在配置里、端点换了单价不换，报表的成本列就在说谎 —— 而且是个
#   **看不出来的谎**：数字照常打印，量级也照常合理，只是算的不是这个端点的钱。
#   这与缺陷 ⑨（基准漂移）是同一类问题：漂移源从时钟换成了单价表。
#
#   所以单位价格是**端点档案的一部分**，并且会随报表一起落盘（见 cli._run_meta）。
#   两个端点跑出来的成本只有在单价也被记下来的前提下才可比。
#
# ★ 为什么价格允许是 None（未声明）而不是一律填个数：
#
#   `None` 表示"我不知道这个端点多少钱"。此时成本**不计算**（保持 0）并在报表里
#   标成 `price=未声明`，而不是拿一个猜的数字填进去。填 0 会让成本列显示"免费"，
#   填一个猜的会让它显示一个精确的错数 —— 两者都比"我不知道"更糟。
#   本地模型是例外：它的边际成本确实是 0（见 ollama 档案的说明）。
#
# ★ `price_scope`：这个价到底是**地址**的属性还是**模型**的属性（缺陷 ⑱）
#
#   同一件事在两种端点上是两回事：
#
#     - `"address"`（默认）：价钱由"打到哪台机器"决定，那台机器上换什么模型
#       都一样。自建推理（ollama / vLLM / LM Studio）就是这样 —— 换个模型
#       还是自己的电费，边际成本仍然是 0。所以 `qwen2.5:7b` 与
#       `qwen2.5:7b-instruct` 必须给出同一个价。
#
#     - `"model"`：同一个地址后面挂着**一堆价差很大的模型**。DeepSeek 官方
#       地址上 `deepseek-chat` 与 `deepseek-reasoner` 不同价；本机 hy3 sidecar
#       的地址后面挂着 WorkBuddy 的 16 个模型（`hy3` 名册标 `free=true`，
#       `hy3-x` 标 `x0.05 credits`）。这时"按地址认领单价"等于把某一个模型的
#       价套给同地址的其它模型 —— 成本列会安静地按错的价目表算账。
#
#   这个标记**只影响按地址反查**（老路径认领单价、以及档案没声明价时的兜底）。
#   选了档案就不看它：那时档案名本身就是用户的声明，覆盖地址和模型是他的自由，
#   价跟着档案走（见 `_resolve_endpoint` 档案分支的注释）。
#
# ★ 校验方式：`python -m ticket_agent.cli endpoint` 会把**实测 token 数**按这里的
#   单价算一遍打出来。拿它跟账单对一下，就知道声明值对不对。
ENDPOINT_PROFILES: dict[str, dict[str, Any]] = {
    # ── 默认：DeepSeek 官方 ──────────────────────────────
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "cheap_model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        # 元 / 千 token（= 元/百万 × 0.001）
        "price_in": 0.001,
        "price_out": 0.002,
        # ★ 同一个地址上 `deepseek-chat` 与 `deepseek-reasoner` **不同价**
        #   （reasoner 更贵）。价属于模型，不属于地址（缺陷 ⑱）。
        "price_scope": "model",
        "requires_key": True,
        "note": "当前默认端点。OpenAI 兼容，支持 tool calling。",
    },
    # ── 腾讯混元官方 ─────────────────────────────────────
    "hunyuan": {
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "model": "hunyuan-a13b",
        "cheap_model": "hunyuan-a13b",
        "key_env": "HUNYUAN_API_KEY",
        # a13b 刊例价：输入 0.5 元/百万、输出 2 元/百万（2026-06 官方计费页）
        "price_in": 0.0005,
        "price_out": 0.002,
        # ★ 混元官网上不同型号单价不同（a13b 与其它尺寸不是一个价）。
        "price_scope": "model",
        "requires_key": True,
        "note": (
            "腾讯混元官方，OpenAI 兼容且文档明确支持 Function Calling。"
            "★ 默认并发上限 5（主子账号共享）—— 本评测一轮要跑几千次调用，"
            "直接上会被限流，需要串行或降 --size。"
        ),
    },
    # ── 本机 WorkBuddy 桥接（Hy3 等，走 tools/wb_sidecar.mjs）──
    "hy3": {
        # ★ 这个地址不是云端的，是**本机 sidecar** 的监听口。
        #   Hy3 只认 WorkBuddy 桌面客户端的登录态，没有可直接调用的公网 key，
        #   所以由 sidecar 复用桌面端登录、把它转成标准 OpenAI 接口。
        "base_url": "http://127.0.0.1:8787/v1",
        "model": "hy3",
        "cheap_model": "hy3",
        "key_env": "WORKBUDDY_SIDECAR_TOKEN",
        # ★ 0.0 是**上游自己声明**的，不是我照截图推断的：模型名册里 hy3 带
        #   `"billing": {"credits": "x0.00 credits", "free": true}`，单次调用的
        #   usage 回传 `"credit": 0`，且调用前后额度快照一个数字都没变。
        #   ⚠️ 但 free 是**限时**的（截图写的就是"限时免费"）：上游一旦收费，
        #   credit 就不再是 0，而这里仍按 0 算 —— 成本列会安静地说谎，
        #   与缺陷 ⑭ 同族。届时请显式设 LLM_PRICE_IN/OUT_PER_1K_CNY 覆盖本档案。
        "price_in": 0.0,
        "price_out": 0.0,
        # ★ 同一个 sidecar 地址背后是 WorkBuddy 的 16 个模型，价差是真实存在的：
        #   名册里 `hy3` 标 `billing.free=true`（这就是本档案 0.0 的来源），
        #   而 `hy3-x` 标 `x0.05 credits`、`free: false` —— 明确收费。
        #   若按地址认领，`LLM_MODEL=hy3-x` 会被安静地按 0 元计价（缺陷 ⑱）。
        "price_scope": "model",
        "requires_key": True,  # sidecar 的 bearer 校验（随机 token，见下）
        "note": (
            "Hy3（腾讯混元系，经本机 WorkBuddy 桥接）。★ 必须先起 sidecar："
            "`node tools/wb_sidecar.mjs --token <token>` —— token 会写进 "
            ".workbuddy-sidecar.json，也接受 WORKBUDDY_SIDECAR_TOKEN 环境变量；"
            "把同一个值填进 .env 的 WORKBUDDY_SIDECAR_TOKEN 即可。"
            "★ 它是纯 reasoning 模型（名册 onlyReasoning=true、canDisableThinking=false），"
            "思考 token 计入 max_tokens：端点自检发的 max_tokens=16 会被思考吃光、"
            "正文为空，sidecar 已把上限抬到 2048。"
        ),
    },
    # ── 第三方中转 / 聚合面板（截图里 x0.00 那种）────────
    "relay": {
        # ★ 刻意留空：中转站的 base_url 只有你的面板上有，别人猜不到。
        #   留空而不是填一个示例地址，是为了让它**必须被显式填写** ——
        #   填个示例地址的话，忘了改就会去打一个陌生域名。
        "base_url": "",
        "model": "",
        "cheap_model": "",
        "key_env": "RELAY_API_KEY",
        # ★ 面板标 x0.00 是**计费倍率**，不是"这个模型免费"。
        #   倍率会把上游价乘 0，但上游是谁、上游什么价，面板说了算。
        #   所以默认 None（未声明），要填就把你从面板读到的实际价填进来。
        "price_in": None,
        "price_out": None,
        "requires_key": True,
        "note": (
            "第三方中转。★ 必须先跑 `cli endpoint --profile relay --base-url <你的地址>` 自检："
            "这类转发层丢掉 tool_calls 字段是常见故障，而本项目 agent 的唯一推进方式就是 "
            "tool_calls（丢掉 = 一步都走不动，但接口看起来一切正常）。"
        ),
    },
    # ── 本地大模型（Ollama / vLLM / LM Studio 都是这个形状）──
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b-instruct",
        "cheap_model": "qwen2.5:7b-instruct",
        "key_env": "",
        # ★ 0 是"边际成本为 0"，不是"不要钱"：电费、机器折旧、时间都不在这张表里。
        #   而且本地模型慢 1~2 个数量级，一轮千级评测的时间成本远大于云端的钱。
        #
        # ★ `"address"`：自建推理的价属于**地址**，不属于模型 —— 换个模型还是
        #   自己的电费，边际成本仍然是 0。所以 `qwen2.5:7b` 与
        #   `qwen2.5:7b-instruct` 必须认领到同一个价，断言见
        #   `test_legacy_agrees_with_profile_on_same_endpoint`。
        #   这是唯一一个 `"address"` 的内置档案，也是这条规则存在的理由。
        "price_scope": "address",
        "price_in": 0.0,
        "price_out": 0.0,
        "requires_key": False,
        "note": (
            "本地推理。★ 不需要 API key（requires_key=False），OpenAI 兼容端口由 "
            "Ollama/vLLM/LM Studio 提供。适合单条冒烟，不适合跑整轮评测："
            "本项目单工单平均 10.4 次模型调用，千级规模要让本地机器跑十几小时。"
        ),
    },
    # ── OpenAI 官方 ──────────────────────────────────────
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "cheap_model": "gpt-4o-mini",
        "key_env": "OPENAI_OFFICIAL_API_KEY",
        # ★ 不填价格：我不掌握当前价目，填错比留空更糟（见文件头说明）。
        #   要用这个端点，请自行查官网后填 LLM_PRICE_IN/OUT_PER_1K_CNY。
        "price_in": None,
        "price_out": None,
        "requires_key": True,
        "note": "OpenAI 官方端点。单价未声明 —— 请查官网后填 LLM_PRICE_*_PER_1K_CNY。",
    },
    # ── 完全自定义 ───────────────────────────────────────
    "custom": {
        "base_url": "",
        "model": "",
        "cheap_model": "",
        "key_env": "",
        "price_in": None,
        "price_out": None,
        "requires_key": True,
        "note": "任意 OpenAI 兼容端点。四个值全靠 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 给。",
    },
}


@dataclass(frozen=True)
class Endpoint:
    """解析后**实际生效**的模型端点。

    ★ 为什么要有这个解析层，而不是直接散着读 settings 的几个字段：

    多端点意味着"这个值从哪来"有了分支（档案 / 环境变量 / 内置默认）。
    分支散在调用点上，就会出现"模型用的是 A 的地址、成本用的是 B 的单价"
    这种**局部都自洽、合起来是错的**状态。解析成**一个**对象之后，
    模型构造、成本换算、报表溯源读的是同一份结论。
    """

    profile: str  # 档案名；"" 表示走 OPENAI_* 老路径
    base_url: str
    model: str
    cheap_model: str
    api_key: str
    #: 元 / 千 token；None = 未声明（成本不计算，而不是按 0 算）
    price_in: float | None
    price_out: float | None
    requires_key: bool
    source: str  # 人可读："这些值从哪来"

    @property
    def is_local(self) -> bool:
        """是否指向本机。用于在报表里标注"这个成本 0 是免费的 0"。"""
        host = urlparse(self.base_url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local")

    @property
    def price_known(self) -> bool:
        return self.price_in is not None and self.price_out is not None

    @property
    def is_configured(self) -> bool:
        """能否真的发起调用。缺 key 或缺 base_url 都算没配好。"""
        if not self.base_url:
            return False
        return bool(self.api_key) or not self.requires_key

    def redacted(self) -> dict[str, Any]:
        """给报表 / HTTP 接口用的形态：**不含 api_key**。

        报表会落盘、也会通过 `/info` 返回给调用方。把 key 放进去等于
        让它出现在文件、日志、和别人的终端里 —— 这里只回一个布尔值。
        """
        return {
            "profile": self.profile or "(OPENAI_* 老路径)",
            "base_url": self.base_url,
            "model": self.model,
            "cheap_model": self.cheap_model,
            "has_api_key": bool(self.api_key),
            "requires_key": self.requires_key,
            "is_local": self.is_local,
            "price_in_per_1k_cny": self.price_in,
            "price_out_per_1k_cny": self.price_out,
            "price_known": self.price_known,
            "source": self.source,
        }

    def key_env_options(self) -> list[str]:
        """这个端点会认哪些 API key 环境变量，按优先级从高到低。

        ★ 存在的理由：`missing()` 原先自己拼这句话，结果是
        `API key（环境变量 LLM_API_KEY 或 LLM_API_KEY）` —— 两个选项同名，
        等于什么也没说。根因是老路径与档案路径**读的变量根本不是同一组**，
        而那句文案把两者混成了一句。现在把"认哪些变量"抽成这个方法，
        两条分支各自说实话，也让报错文案有唯一来源。
        """
        if not self.profile:
            # 老路径：`LLM_API_KEY`，以及 OPENAI_* 时代的 `OPENAI_API_KEY`。
            return ["LLM_API_KEY", "OPENAI_API_KEY"]
        key_env = str(ENDPOINT_PROFILES.get(self.profile, {}).get("key_env") or "")
        out = [key_env] if key_env else []
        # 档案专用变量之后才是通用变量（次序同 `_resolve_endpoint` 的取值链）。
        return [*out, "LLM_API_KEY"]

    def missing(self) -> list[str]:
        """列出还缺哪些必填项。缺什么就说什么，不要只说"配置不完整"。"""
        out: list[str] = []
        if not self.base_url:
            out.append("LLM_BASE_URL（或档案里的 base_url）")
        if not self.model:
            out.append("LLM_MODEL（或档案里的 model）")
        if self.requires_key and not self.api_key:
            # ★ 只列出**真的会被读**的变量。去重是必要的：档案没声明 key_env 时
            #   列表里只剩通用变量，若不去重就会又拼出"LLM_API_KEY 或 LLM_API_KEY"。
            names = list(dict.fromkeys(self.key_env_options()))
            out.append("API key（环境变量 " + " 或 ".join(names) + "）")
        return out


# ══════════════════════════════════════════════════════════════
# 端点解析内核 —— `Settings.endpoint` 与 `resolve_endpoint()` 共用**这一份**
# ══════════════════════════════════════════════════════════════
#
# ★ 为什么要抽这一层：这两个入口不是"两个功能"，而是**同一个问题的两次提问**。
#   `endpoint --check` 要回答"我接下来实际会打到哪个端点、它能不能用"；
#   `report` 要回答"我现在实际打到的是哪个端点"。如果它们各算各的，就可能
#   得出两个不同的答案，而**没有任何东西会变红** ——
#
#     自检报告写着"tool_calls 正常、可以跑评测"，
#     真正跑评测时用的却是另一个模型（或另一个 base_url），
#     于是 agent 一步都走不动，而报表照常生成、成本照常累加。
#
#   这不是假想的风险：本文件早先的两个版本正是这样 ——
#   `Settings.endpoint` 读 `os.environ`，而 `resolve_endpoint()` 完全不读，
#   于是 `endpoint --profile hunyuan --check` 自检的是**档案原样**，
#   而 `report` 跑的是"档案 + 当时的 LLM_MODEL 覆盖"。两次都成功，结论无关。
#
# ★ 两个入口的差别**只剩两点**，都通过参数表达：
#     ① 档案名从哪来（`LLM_PROFILE` 环境变量/字段 vs 函数入参）；
#     ② 允不允许显式覆盖（`--base-url` / `--model` / `--api-key`）。
#   **读不读进程环境不是差别** —— 两个入口都照读。理由：自检要认证的就是
#   "在**当前这套环境变量**下实际会跑的那个端点"。若自检忽略环境变量，
#   它认证的是一个跑评测时并不存在的端点，通过与否都不说明问题。


def _as_float(raw: str, label: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{label}={raw!r} 不是数字") from exc


def _price_scope_of(profile: Mapping[str, Any]) -> str:
    """这个档案的单价属于**地址**还是属于**模型**（缺省 `"address"`）。

    ★ 缺省值取 `"address"` 而不是更严格的 `"model"`，是为了保住既有行为：
      `ollama` 这类自建地址上任何模型边际成本都是 0，而老路径靠地址认价
      也正是缺陷 ⑭ 的修法。真正需要区分的档案**显式写** `"model"` ——
      `tests/test_endpoints.py::test_price_scope_is_declared_and_valid`
      把"声明了单价就必须说清归属"变成会红的断言。
    """
    return str(profile.get("price_scope") or "address")


def _profile_covers_model(profile: Mapping[str, Any], model: str) -> bool:
    """档案声称的这个价，是不是给 `model` 的。

    地址级恒真；模型级要求模型名与档案声明的 `model` / `cheap_model` 之一**完全相同**。

    ★ 为什么不做前缀 / 模糊匹配：`"hy3-x".startswith("hy3")` 为真，
      而这两个正是价差最大的两个（免费 vs `x0.05 credits`）——
      "看起来像"恰恰是错账的理由。这里宁可判不覆盖。
    ★ 模型名为空时判**不覆盖**：不知道是哪个模型，就没有依据认领模型价。
    """
    if _price_scope_of(profile) != "model":
        return True
    declared = {str(profile.get(k) or "").strip() for k in ("model", "cheap_model")} - {""}
    return (model or "").strip() in declared


def _price_claim(base_url: str, model: str) -> tuple[str, str]:
    """老路径 / 兜底：这个端点的单价该按哪个档案认领。返回 `(档案名, 说明)`。

    档案名 `""` = 没认领到；说明写清**为什么**，因为两种原因的处置完全不同：
    认不出地址要去查这是谁家的端点，模型对不上则是"我有这个地址的价、但没有
    这个模型的价"。

    ★ 存在的理由（缺陷 ⑭）：老路径（没选档案）没有"我是谁"这个概念，
    于是它的单价只能靠**地址**认领。实测的毛病是 —— 把 `OPENAI_BASE_URL`
    指向本地 ollama（边际成本真的是 0）或混元（输入 0.0005）之后，单价仍是
    `Settings` 字段里的那两个 DeepSeek 价（0.001/0.002），而且
    `price_known=True`：报表那条"成本列不可用"的告警**永远不会响**，
    成本列安静地按 DeepSeek 的价算别人的账。

    判据取"按地址反查"而不是"一律判未声明"，是为了守住另一条更重要的不变式：
    **老路径与档案路径解析出同一个端点时，必须给出同一个价** ——
    否则 `endpoint --check` 认证的端点和 `report` 实跑的端点又变成两回事了
    （那正是缺陷 ⑬ 的形状）。断言见 `test_legacy_agrees_with_profile_on_same_endpoint`。

    ★ 但"地址"不足以确定端点身份（缺陷 ⑱）：同一个地址后面可能挂着一堆价差
    几十倍的模型 —— DeepSeek 的 `deepseek-chat` vs `deepseek-reasoner`，
    本机 sidecar 后面的 16 个模型（`hy3` 免费、`hy3-x` 收 credits）。
    所以档案要用 `price_scope` 声明"这个价属于地址还是属于模型"：
    声明 `"model"` 的必须**模型名也对得上**才认领，否则宁可判未声明。

    认不出来时返回 `""`，让单价落到"未声明"。**先认领再判未知**：
    猜一个数会安静地算错，不知道会打告警 —— 后者的代价是噪音，前者是错账。
    """
    norm = (base_url or "").strip().rstrip("/").lower()
    if not norm:
        return "", ""
    hits = [
        name
        for name, prof in ENDPOINT_PROFILES.items()
        if str(prof.get("base_url") or "").strip().rstrip("/").lower() == norm
    ]
    # 撞上多个档案（理论上不该发生）时判不确定，而不是取第一个 ——
    # 取第一个等于用字典顺序决定单价。
    if len(hits) != 1:
        return "", ""
    name = hits[0]
    prof = ENDPOINT_PROFILES[name]
    # ★ 用与档案分支**同一个**判据（`_profile_covers_model`）——
    #   否则两条路径会各写一份口径，迟早分叉，而分叉的方向恰恰是
    #   "一半说免费、一半说未声明"，比全错更难查。
    if _profile_covers_model(prof, model):
        return name, ""
    # ★ 地址认得、模型对不上：这才是缺陷 ⑱ 要防的那一步。
    #   报出档案名与当前模型名，让"为什么成本列不可用"能当场看懂。
    declared = {
        str(prof.get(k) or "").strip() for k in ("model", "cheap_model")
    } - {""}
    asked = (model or "").strip() or "未指定"
    return "", (
        f"{name} 档案的单价按模型计，当前模型 {asked} 不在它的价目里"
        f"（该档案声明的是 {'、'.join(sorted(declared)) or '无'}）"
    )


def _profile_for_endpoint(base_url: str, model: str = "") -> str:
    """`_price_claim` 的薄封装，只要档案名（取证脚本与日志用）。

    ★ 注意 `model` 省略时等价于"模型未知"：对 `price_scope="model"` 的档案
    会**认不出来**（返回 `""`）—— 这是刻意的，"不知道是哪个模型"就不能认领
    它的价。要复现"按地址认领"的老行为请显式传入模型名。
    """
    return _price_claim(base_url, model)[0]


def _resolve_endpoint(
    cfg: Settings,
    *,
    profile_name: str,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> Endpoint:
    """端点解析的**唯一**实现（见上方注释：为什么不能有第二份）。

    优先级（前者覆盖后者）：
    **显式覆盖 → 进程环境 → 这份 Settings 读到的 `.env` → Settings 字段 → 档案**。

    第三层是缺陷 ⑮ 补的：pydantic 读 `.env` 只填**声明过的字段**，档案专用 key
    （`HUNYUAN_API_KEY` / `WORKBUDDY_SIDECAR_TOKEN` 等）不在其中，于是"写在
    `.env` 里的 key"从来到不了这里。详见 `_dotenv_layer()` 上方注释。

    `environ` 传 `os.environ`；只在测试里需要构造一个隔离的环境映射时才传别的。
    传进来的映射优先级不变（仍是"进程环境"那一层），`.env` 只补它没有的键。
    """
    ov: dict[str, str] = {k: str(v) for k, v in (overrides or {}).items() if v}
    # ★ `environ=None` = 读真实进程环境（默认）；只有测试才会传一个隔离的映射进来。
    #   两个生产入口都显式传 `os.environ`，这里给 `None` 兜底是为了让"忘了传"
    #   不会静默变成"看不见环境变量"——那种默认值会让覆盖悄悄失效。
    env: Mapping[str, str] = os.environ if environ is None else environ
    # ★ 缺陷 ⑮：把这份 Settings 从 `.env` 读到的键**垫在进程环境之下**。
    #
    #   没有这一层时，写在 `.env` 里的档案专用 key（`HUNYUAN_API_KEY` /
    #   `RELAY_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_OFFICIAL_API_KEY` /
    #   `WORKBUDDY_SIDECAR_TOKEN`）到不了这里 —— 它们不是 Settings 的声明字段，
    #   pydantic 读 `.env` 时把它们丢了，而 pydantic 也从不写 `os.environ`。
    #   结果是"文档教的放法不生效"：请求带空 bearer 打出去、报 401，而两边配置
    #   看上去完全一致。详见 `_dotenv_layer()` 上方的注释。
    #
    #   次序：**进程环境赢**。同一键在两处都有时以环境变量为准 —— 与"显式压过
    #   文件"的直觉一致，也让"临时 export 一个 key 覆盖 `.env`"这种调试手法可用。
    env_from_dotenv: frozenset[str] = frozenset()
    fallback: dict[str, str] = getattr(cfg, "_dotenv_fallback", None) or {}
    if fallback:
        # 只补进程环境里没有（或为空）的键；键统一大写：Windows 上 `os.environ`
        # 本身就大写化，而环境变量名按惯例就是大写 —— 不这么做的话，`.env` 里
        # 写 `llm_profile=x` 会被 pydantic 认（它默认大小写不敏感）却被这一层
        # 漏掉，于是又出现"一边认一边不认"。
        filled: dict[str, str] = {}
        for key, val in fallback.items():
            if key.upper() in filled:
                continue
            if (env.get(key) or env.get(key.upper()) or "").strip():
                continue  # 进程环境里已有非空值 → 环境变量赢
            filled[key.upper()] = val
        if filled:
            env = {**env, **filled}
            env_from_dotenv = frozenset(filled)
    profile: dict[str, Any] = ENDPOINT_PROFILES.get(profile_name, {}) if profile_name else {}
    if profile_name and not profile:
        # 未知档案名：不要静默退化成默认端点 —— 那样"我想用 A 却跑了 B"
        # 会毫无痕迹。抛出来，让拼错的名字在第一次调用时就可见。
        known = ", ".join(sorted(ENDPOINT_PROFILES))
        raise ValueError(f"未知端点档案 {profile_name!r}；可用：{known}")

    def env_get(key: str) -> str:
        return (env.get(key) or "").strip()

    def where_from(key: str) -> str:
        """这个键的值是从哪儿来的：进程环境还是 `.env`（缺陷 ⑮）。

        存在的理由：出问题时"我明明在 `.env` 里写了 key"和"解析器真的读到了"
        是两件事，而这两件事在 `source` 里本来长得一模一样。写清楚来源，
        下次再遇到"配了却不生效"就不必重新取证一遍。
        """
        return "（来自 .env）" if key.upper() in env_from_dotenv else ""

    def pick(env_key: str, field_val: str, prof_key: str, override_key: str) -> str:
        return (
            ov.get(override_key, "")
            or env_get(env_key)
            or (field_val or "").strip()
            or str(profile.get(prof_key) or "")
        ).strip()

    def pick_price(env_key: str, field_val: float | None, prof_key: str) -> float | None:
        raw_ov = ov.get(prof_key)
        if raw_ov:
            return _as_float(raw_ov, f"--{prof_key.replace('_', '-')}")
        raw = env.get(env_key)
        if raw is not None and raw.strip() != "":
            return _as_float(raw, env_key)
        if field_val is not None:
            return field_val
        val = profile.get(prof_key)
        return None if val is None else float(val)

    # ★ 老路径（没选档案）：地址与模型沿用 `OPENAI_*` 时代的字段。单价**不由
    #   字段默认值决定**，而是按 `base_url` 反查内置档案认领（缺陷 ⑭）：认得出
    #   就跟着那个端点走，认不出就是"未声明"。
    #
    #   两个被否掉的方案，记在这里免得有人再试一遍：
    #   ① 一律判未声明 —— 那会让老路径指向 DeepSeek 官方时成本列也变成告警，
    #      而那种情况本是可用的；更要紧的是它与 `test_both_entry_points_agree`
    #      的精神相悖：同一个端点应当给出同一个结论。
    #   ② 一概取 `Settings.price_*` 字段 —— 就是修之前的写法，实测把 ollama 的
    #      0 和混元的 0.0005 都算成了 DeepSeek 的 0.001/0.002。
    legacy = not profile_name
    if legacy:
        base_url = pick("LLM_BASE_URL", cfg.openai_base_url, "base_url", "base_url")
        model = pick("LLM_MODEL", cfg.model_name, "model", "model")
        cheap = pick("LLM_CHEAP_MODEL", cfg.cheap_model_name, "cheap_model", "cheap_model") or model
        api_key = (
            ov.get("api_key") or env_get("LLM_API_KEY") or (cfg.openai_api_key or "").strip()
        )
        # ★ 单价这里**读** `LLM_PRICE_*` 环境变量：报表在"成本列不可用"的告警里
        #   就是这么告诉用户的（"或显式设 LLM_PRICE_IN_PER_1K_CNY"）。若老路径
        #   不认它，那条指引就是空头支票 —— 用户照做之后成本列依然是 0。
        #
        #   注意第三参传 `None` 而不是 `cfg.price_input_per_1k_cny`：字段默认值
        #   （0.001/0.002）是 DeepSeek 的价，拿它当"任何未声明端点"的价，
        #   等于替用户猜端点 —— 而这正是缺陷 ⑭ 的根因。要显式声明单价请用
        #   `LLM_PRICE_*` 环境变量，或 `PRICE_*` 字段（后者仍会被读到，
        #   见下方 `declared_in`）。
        price_in = pick_price("LLM_PRICE_IN_PER_1K_CNY", None, "price_in")
        price_out = pick_price("LLM_PRICE_OUT_PER_1K_CNY", None, "price_out")
        # 字段里若**显式**填了价，仍认（老用户靠它声明自定义端点的价）。
        #   ★ 但只认显式填的：`model_fields_set` 只含"被赋过值"的字段，
        #     默认值不算 —— 这是 pydantic 给"用户到底说没说"留下的唯一凭据。
        fields_set = getattr(cfg, "model_fields_set", set())
        if price_in is None and "price_input_per_1k_cny" in fields_set:
            price_in = cfg.price_input_per_1k_cny
        if price_out is None and "price_output_per_1k_cny" in fields_set:
            price_out = cfg.price_output_per_1k_cny
        # 还没有价 → 按地址反查档案认领。认不出就留 None（= 未声明）。
        if price_in is None or price_out is None:
            inferred_name, why = _price_claim(base_url, model)
            inferred = ENDPOINT_PROFILES.get(inferred_name, {}) if inferred_name else {}
            if price_in is None:
                price_in = inferred.get("price_in")
            if price_out is None:
                price_out = inferred.get("price_out")
        else:
            inferred_name, why = "", ""
        requires_key = True
        source = "OPENAI_*（老路径）"
        if env_get("LLM_API_KEY"):
            source += f"，key 来自环境变量 LLM_API_KEY{where_from('LLM_API_KEY')}"
        if inferred_name:
            source += f"，单价按地址认领自 {inferred_name} 档案"
        elif price_in is None or price_out is None:
            source += "，单价未声明（成本列不可用，不是免费）"
            # ★ 缺陷 ⑱：认得上地址但认不上模型时，必须把这一步说出来 ——
            #   否则用户看到的就是"我明明指着 DeepSeek 官方，怎么成本列不可用"。
            if why:
                source += f"：{why}"
    else:
        base_url = pick("LLM_BASE_URL", cfg.llm_base_url, "base_url", "base_url")
        model = pick("LLM_MODEL", cfg.llm_model, "model", "model")
        cheap = pick("LLM_CHEAP_MODEL", cfg.llm_cheap_model, "cheap_model", "cheap_model") or model
        key_env = str(profile.get("key_env") or "")
        # ★ key 的次序：显式覆盖 → **档案专用变量** → LLM_API_KEY → Settings 字段。
        #   专用变量排在通用变量之前，理由是"更具体的选择优先"：用户写了
        #   `HUNYUAN_API_KEY` 就是在说"这是混元的 key"。若让 shell 里某个残留的
        #   `LLM_API_KEY`（比如上一条命令 export 了 DeepSeek 的）盖过它，
        #   请求就会带着错误的 key 打到混元，报 401 —— 而配置看上去完全正常。
        api_key = (
            ov.get("api_key")
            or (env_get(key_env) if key_env else "")
            or env_get("LLM_API_KEY")
            or (cfg.llm_api_key or "").strip()
        )
        price_in = pick_price("LLM_PRICE_IN_PER_1K_CNY", cfg.llm_price_in_per_1k_cny, "price_in")
        price_out = pick_price("LLM_PRICE_OUT_PER_1K_CNY", cfg.llm_price_out_per_1k_cny, "price_out")
        requires_key = bool(profile.get("requires_key", True))
        # ★ `bits` 必须在下面那段反查**之前**建好：反查失败时要往里追加
        #   "为什么没认领"。先建后用地写，是为了让 source 的各段保持固定次序
        #   （档案名 → key → 覆盖 → 反查），而不是按代码执行顺序拼。
        bits = [f"档案 {profile_name}"]
        # ★ 缺陷 ⑱ 的第二步：**档案自己声明的价也要被"模型范围"否掉**。
        #   档案声明价 = "这个档案默认的那个模型，值这个价"，不等于
        #   "这个地址后面任何模型都值这个价"。`LLM_PROFILE=hy3` +
        #   `LLM_MODEL=hy3-x` 时，hy3-x 名册上明写 `x0.05 credits / free:false`，
        #   拿 free 的 0 元记它就是错的账 —— 而修复前两条路径（老路径 / 档案路径）
        #   只改了老路径，于是"两条入口"里有一半仍然是错的。
        #
        #   判据问的是"档案声称的这个价，是不是给**当前模型**的"：
        #   `_price_claim()` 用的是同一个口径，所以两条路径不会分叉。
        #
        #   ⚠️ 用户的显式声明（LLM_PRICE_* / pydantic 字段）**不受影响** ——
        #   那是用户自己说"这个端点这个价"，没有推断成分。
        if _price_scope_of(profile) == "model" and not _profile_covers_model(profile, model):
            declared = sorted(
                {str(profile.get(k) or "").strip() for k in ("model", "cheap_model")} - {""}
            )
            # ★ 文案必须带上"未声明"三个字：`cli.py` 与 `report.py` 里那条
            #   "单价未声明（成本列不可用，不是免费）"是全项目统一的说法，
            #   少这三个字就等于给用户一句"地址是 hy3 档案的、只是没认领出来"，
            #   而真正要问的是"这个模型的价格我不知道"。
            bits.append(
                f"单价未声明（成本列不可用，不是免费）：{profile_name} 档案声明的单价"
                f"（{'、'.join(declared) or '无'}）按模型计，不覆盖当前模型 "
                f"{model or '（未指定）'} —— 请显式设 LLM_PRICE_IN/OUT_PER_1K_CNY"
            )
            price_in = price_out = None  # type: ignore[assignment]
        elif price_in is None or price_out is None:
            # ★ 档案**自己没声明价**时（relay / custom），按最终地址反查一次：
            #   用户选了 `LLM_PROFILE=relay` 又把 `LLM_BASE_URL` 指到本地 ollama 时，
            #   "未声明"是错的 —— 地址已经把端点说清楚了。档案声明了价就不反查：
            #   那时用户选的档案名就是他的声明（覆盖地址是他的自由，价跟着档案走）。
            inferred_name, why = _price_claim(base_url, model)
            inferred = ENDPOINT_PROFILES.get(inferred_name, {}) if inferred_name else {}
            if price_in is None:
                price_in = inferred.get("price_in")
            if price_out is None:
                price_out = inferred.get("price_out")
            if why:
                # ★ 档案没声明价、地址认出来了但模型对不上 ——
                #   "未声明"是对的，但不能让用户以为是自己配错了地址。
                bits.append(f"单价未按地址认领：{why}")
        else:
            inferred_name = ""
        if key_env and env_get(key_env):
            bits.append(f"key 来自环境变量 {key_env}{where_from(key_env)}")
        elif env_get("LLM_API_KEY"):
            bits.append(f"key 来自环境变量 LLM_API_KEY{where_from('LLM_API_KEY')}")
        elif cfg.llm_api_key:
            # ★ 走到这里说明 key 来自 Settings 字段（`.env` / 显式传参）—— 不是
            #   环境变量。`LLM_API_KEY` 是**声明过的**字段，pydantic 会把它从
            #   `.env` 收进 cfg，所以它不需要兜底层也一直能用；而档案专用 key
            #   （`HUNYUAN_API_KEY` 等）不是字段，只能靠兜底层（缺陷 ⑮）。
            bits.append("key 来自 Settings 字段 LLM_API_KEY")
        if any(env_get(k) for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_CHEAP_MODEL")):
            bits.append("部分值被 LLM_* 覆盖")
        source = "；".join(bits)

    return Endpoint(
        profile=profile_name,
        base_url=base_url,
        model=model,
        cheap_model=cheap,
        api_key=api_key,
        price_in=price_in,
        price_out=price_out,
        requires_key=requires_key,
        source=source,
    )


# ══════════════════════════════════════════════════════════════
# `.env` 兜底层（缺陷 ⑮）
# ══════════════════════════════════════════════════════════════
#
# ★ 为什么需要它：pydantic 读 `.env` 只填**自己声明过的字段**（`extra="ignore"`
#   会把其余键直接丢掉），而且**从不写 `os.environ`**；而 `_resolve_endpoint()`
#   的 `env_get()` 读的是环境变量映射。两个环节都不报错、也不告警，于是 `.env`
#   里的 `HUNYUAN_API_KEY` / `RELAY_API_KEY` / `DEEPSEEK_API_KEY` /
#   `OPENAI_OFFICIAL_API_KEY` / `WORKBUDDY_SIDECAR_TOKEN` 这类"档案专用 key"
#   （都不是 Settings 的声明字段）**一路都是空的** —— 请求带着空 bearer 打出去，
#   而两边配置看上去完全正常。`.env.example` 教的正是这种放法。
#
# ★ 为什么不直接把这些键写进 `os.environ`：那会让"读一次 `.env`"变成进程级
#   副作用 —— 测试之间互相串味，且 `Settings(_env_file=None)` 的隔离会失效。
#   解析器本来就有 `environ` 参数可以接受一个映射，垫一层即可。
#
# ★ 为什么用 `python-dotenv` 而不是自己解析：它是 pydantic-settings 内部读
#   `.env` 的**同一个实现**（pyproject 里已显式声明该依赖）。两套读法只要在
#   引号、行尾注释、`export` 前缀这些细节上分歧一次，就会出现"pydantic 认这个值、
#   解析器认那个值"，而两边都不报错 —— 比缺一个 key 更难查。

_UNSET: Any = object()


def _dotenv_layer(target: Any, *, encoding: str = "utf-8") -> dict[str, str]:
    """读一份 `.env`，返回其中**非空**的键值；读不到就返回空字典。

    空值被丢掉而不是当成"显式置空"：`.env.example` 里大量键是
    `OPENAI_API_KEY=`（留白待填）这种形态，把空串当覆盖会让兜底层反过来盖掉
    进程环境里真实的 key。
    """
    if not target:
        return {}
    try:
        raw = dotenv_values(target, encoding=encoding)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if v is not None and v.strip() != ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 必须清空：否则 pydantic 会拒绝 model_name / model_config 这类字段名
        protected_namespaces=(),
    )

    #: 这次构造从 `.env` 读到的键值（缺陷 ⑮）。垫在**进程环境之下**参与解析：
    #: 进程环境优先，`.env` 只补进程环境没有的键。
    #: `Settings(_env_file=None)`（测试隔离）构造出的实例里它是空的 —— 这也正是
    #: 缺陷 ⑮ 能从 133 个测试里逃过去的原因：测试把 `.env` 整个摘掉了，
    #: 于是永远测不出"写在 `.env` 里的 key 到不了解析器"。
    _dotenv_fallback: dict[str, str] = PrivateAttr(default_factory=dict)

    def __init__(self, **values: Any) -> None:
        """★ 覆写只为记下"这次构造读了哪份 `.env`"（缺陷 ⑮）。

        为什么非记不可：`_env_file` 是 `BaseSettings.__init__` 的命名参数，
        **用完即丢**，从不写回 `model_config`（实测三种构造下
        `model_config['env_file']` 都是同一个默认路径），所以"这次读没读 `.env`"
        只有实例自己知道。而解析器必须知道 —— 否则 `Settings(_env_file=None)`
        的测试隔离会失效（解析器会去读开发机的 `.env`，红的理由与代码无关）。

        与 pydantic 保持一致：`.env` 只在**构造时**读一次，进程内改 `.env` 不会
        生效（要换端点请用环境变量 —— `settings.endpoint` 每次现读 `os.environ`）。
        """
        raw = values.get("_env_file", _UNSET)
        super().__init__(**values)
        if raw is _UNSET:
            target: Any = self.model_config.get("env_file")
        elif raw is None:
            target = None
        else:
            target = raw
        self._dotenv_fallback = _dotenv_layer(
            target, encoding=str(self.model_config.get("env_file_encoding") or "utf-8")
        )

    @property
    def dotenv_fallback(self) -> dict[str, str]:
        """这份 Settings 会垫在进程环境之下的 `.env` 键值（诊断用，缺陷 ⑮）。"""
        return dict(self._dotenv_fallback)

    # ── 模型（OpenAI 兼容端点）──────────────────────────────
    #
    # ★ 保留 OPENAI_* 这组名字，而不是改成 LLM_* 一刀切：
    #   `.env.example`、README、以及用户手上已有的 .env 全都写的是 OPENAI_*。
    #   改名会让所有现存配置**静默失效**（extra="ignore" 会把不认识的键丢掉，
    #   于是 key 变空、kind 掉回 scripted，报表依然能跑完并显示 100%）。
    #   所以 OPENAI_* 继续作为"老路径"生效；多端点开关是新的一组键，缺省时
    #   两者等价（profile 为空 → 等价于 deepseek 的取值）。
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-chat"
    # 成本熔断后降级到的便宜模型；留空则降级为"强制结束"
    cheap_model_name: str = "deepseek-chat"
    temperature: float = 0.0
    request_timeout: float = 60.0

    # ── 多端点（新）────────────────────────────────────────
    #: 端点档案名（ENDPOINT_PROFILES 的键）。留空 = 走上面的 OPENAI_* 老路径。
    llm_profile: str = ""
    #: 显式覆盖。留空则取档案里的值；档案也没有就必须给，否则报缺失。
    llm_base_url: str = ""
    llm_model: str = ""
    llm_cheap_model: str = ""
    #: 显式 key。留空则按档案的 key_env 去环境变量里找。
    llm_api_key: str = ""
    #: 显式单价（元 / 千 token）。留空则取档案里的值（可能是 None = 未声明）。
    llm_price_in_per_1k_cny: float | None = None
    llm_price_out_per_1k_cny: float | None = None
    #: 是否允许用未经自检的端点跑评测（见 endpoints.py 的 preflight）。
    llm_allow_unverified: bool = False

    # ── 数据层 ─────────────────────────────────────────────
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'eval_out' / 'ticket_agent.db'}"
    checkpointer: str = "memory"  # memory | sqlite | postgres
    postgres_dsn: str = ""

    # ── 预算与熔断（难点 #5）────────────────────────────────
    max_tokens_per_ticket: int = 120_000
    max_cost_per_ticket_cny: float = 1.50
    # ★ 单价默认 **None = 未声明**，不是 0.001/0.002（缺陷 ⑭）。
    #   给个 DeepSeek 的价当默认值，等于宣称"任何没声明单价的端点都按 DeepSeek
    #   计价"——把 `OPENAI_BASE_URL` 指到本地 ollama（边际成本 0）或混元
    #   （输入 0.0005）时，报表会拿 0.001/0.002 安静地算出成本，而且
    #   `price_known=True`，那条"成本列不可用"的告警永远不会响。
    #   现在：不声明 → `price_known=False` → 报表里的成本列标成不可用并告警
    #   （打告警的代价是噪音，猜一个数的代价是错账）。
    #   要显式声明单价，用 `LLM_PRICE_IN/OUT_PER_1K_CNY`（两条路径都认），
    #   或在这里显式填 —— 填了就会被 `model_fields_set` 认出来。
    price_input_per_1k_cny: float | None = None
    price_output_per_1k_cny: float | None = None
    # 达到预算的百分之多少时先降级模型（0.8 = 80%）
    degrade_at_ratio: float = 0.8

    # ── 业务规则阈值（R4）──────────────────────────────────
    refund_hitl_threshold_cny: float = 500.0
    risk_score_hitl_threshold: float = 0.7
    return_window_days: int = 7
    max_refunds_per_order: int = 1

    # ── 售后非退款线的阈值（R8–R15）────────────────────────
    # ★ 这些值都是**公开的行业惯例**，不是我这个项目拍的：
    #   7 天无理由来自《消费者权益保护法》第 25 条，15 天换货来自三包规定，
    #   运费险赔付上限取常见档位。写在这里是为了让它们可改、可测、可被质疑，
    #   而不是散落在规则分支里的字面量。
    #: R8 换货窗口（天）—— 三包规定：7 日内退货、15 日内换货
    exchange_window_days: int = 15
    #: R10 运费补偿上限（元）—— 超过必须转人工，不能由 agent 直接发钱
    max_compensation_cny: float = 30.0
    #: R11 价保窗口（天）—— 主流电商普遍 7–15 天，取 15
    price_protect_window_days: int = 15
    #: R15 催单冷却（小时）—— 同一订单短时间内不得重复催，防止刷工单
    urge_cooldown_hours: int = 24
    #: 补发申请的最多受理次数（R9）—— 同一订单不得无限补发
    max_reships_per_order: int = 1
    #: 换货申请的最多受理次数（R8）—— 同上
    max_exchanges_per_order: int = 1

    # ── PII 脱敏（业界标配：消息级、不可恢复）────────────────
    #: 审计日志落库前是否脱敏。默认开。
    #: ★ 关掉的**唯一**正当理由是排查问题需要看原文 —— 那时请明确知道
    #:   你在把个人信息写进日志，并且用完就把库删掉。
    pii_redact_audit: bool = True
    #: 工单正文进 LLM prompt 前是否脱敏。默认开。
    #: ★ 关掉意味着把客户的手机号/身份证直接喂给模型 —— 生产环境不要关。
    pii_redact_prompt: bool = True
    #: 脱敏档位：standard（高精度四条）/ strict（追加姓名+地址，误报率高）
    #: ★ strict 实测误报率约 75%（被「9月12号」「开柜号码」这类骗），
    #:   默认不开。要开之前先看 `db/pii.py` 末尾的误报样本。
    pii_profile: str = "standard"

    # ── 评测 ───────────────────────────────────────────────
    eval_out_dir: str = str(PROJECT_ROOT / "eval_out")
    eval_holdout_ratio: float = 0.3
    eval_seed: int = 42
    # 终态比对时浮点容差（分）
    eval_amount_tolerance_cny: float = 0.01

    # ── 服务层 ─────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    tenant_tokens: dict[str, str] = Field(
        default_factory=lambda: {"tok-acme": "acme", "tok-globex": "globex"}
    )

    # ★ 这里曾经有一个 `log_level: str = "INFO"`，已删除（缺陷 ⑧ 的一部分）。
    #   它没有任何消费者：全项目零处 logging/getLogger/structlog 调用。
    #   一个"配置了却不起作用"的开关比没有这个开关更糟 —— 它让人以为
    #   调高 LOG_LEVEL 就能看到更多东西。
    #   本项目的可观测性走的是 **AuditLog 表**（谁在什么租户、对哪张工单、
    #   调了什么工具、成功与否、参数是什么），而不是进程日志。这是刻意的：
    #   工单处置要能事后逐条复盘"这笔钱是谁批的"，那是查询而不是 grep。

    @field_validator("tenant_tokens", mode="before")
    @classmethod
    def _parse_tenant_tokens(cls, v: Any) -> Any:
        """支持 .env 里写成 JSON 字符串。"""
        if isinstance(v, str):
            if not v.strip():
                return {}
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # 退化支持 "tok1:tenant1,tok2:tenant2"
                out: dict[str, str] = {}
                for pair in v.split(","):
                    if ":" in pair:
                        k, _, val = pair.partition(":")
                        out[k.strip()] = val.strip()
                return out
        return v

    @property
    def endpoint(self) -> Endpoint:
        """把端点配置解析成**一个**结论。

        优先级（前者覆盖后者）：**进程环境 → Settings 字段（.env）→ 档案**。
        真正的解析在模块级 `_resolve_endpoint()` 里 —— 与 `resolve_endpoint()`
        **共用同一份实现**（理由见那个函数上方的注释：两份实现会导致"自检的
        端点和实跑的端点不是同一个"）。

        ★ 环境变量的覆盖走 `os.environ` 而不是只读 Settings：因为
        `settings` 是 `lru_cache` 的单例，进程启动后再 `os.environ[...] = ...`
        是不生效的。而"换端点重跑"这个动作天然发生在进程内（跑完 A 端点
        接着跑 B 端点做对比），所以覆盖必须每次现读。

        ★ 代价：这意味着 endpoint 是个**动态**属性 —— 同一个进程里改环境变量
        再取一次，拿到的可能是不同对象。这是刻意的（换端点就该拿到新对象），
        但调用方不要把它缓存到模块级常量里，否则换端点对它无效。
        """
        prof_name = (os.environ.get("LLM_PROFILE") or self.llm_profile or "").strip()
        # ★ 这里刻意**不**传 overrides：`Settings.endpoint` 是"当前生效端点"，
        #   没有"某次调用的临时覆盖"这个语义；覆盖只属于 `resolve_endpoint()`。
        return _resolve_endpoint(self, profile_name=prof_name, environ=os.environ)

    @property
    def eval_dir(self) -> Path:
        p = Path(self.eval_out_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def prompt_dir(self) -> Path:
        return PROJECT_ROOT / "prompt"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def resolve_endpoint(profile: str | None = None, **overrides: str) -> Endpoint:
    """按档案名解析端点，**不改变当前进程的生效端点和环境变量**。

    ★ 为什么需要它：`cli endpoint --profile relay` 想查看某个档案目前解析成什么，
      `cli smoke --profile hunyuan` 想用某个档案跑一次而**不污染**进程环境。
      如果这两个功能靠"设了环境变量再读 settings.endpoint 再改回来"实现，
      一旦中间抛异常，环境就永久停在那个端点上了 —— 后面所有的评测都跑在
      错误的端点上，而报表依然正常。宁可多写一个纯函数，也不要可恢复的环境状态。

    与 `settings.endpoint` 的关系：**同一份解析实现**（`_resolve_endpoint`），
    差别只有两点 ——
      ① 档案名来自入参而不是 `LLM_PROFILE`；
      ② 允许用 `base_url` / `model` / `api_key` 三个显式覆盖（`cli endpoint --base-url`）。
    ★ 它**照读**进程环境（只是不修改）：因为要回答的问题是"以 `LLM_PROFILE=X`
      启动、在**当前这些环境变量**下，实际会打到哪个端点"。若它忽略环境，
      `endpoint --check` 认证的就是一个跑评测时根本不存在的端点 —— 自检通过
      也说明不了任何事。

    ★ `overrides` 在**空档案名**下同样生效。旧实现在 `profile` 为空时直接
      `return settings.endpoint`，于是 `endpoint --base-url http://...`（不带
      `--profile`）会**静默丢掉**那个覆盖值，打印出默认端点 —— 用户看到的是
      "我明明指了地址，它却说要用官方地址"，而没有任何报错。
    """
    name = (profile if profile is not None else settings.llm_profile) or ""
    return _resolve_endpoint(
        settings,
        profile_name=name.strip(),
        environ=os.environ,
        overrides=overrides,
    )


settings = get_settings()
