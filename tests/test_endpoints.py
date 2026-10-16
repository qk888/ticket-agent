"""端点解析的测试。

这一层为什么需要测试
--------------------
`config.py` 的端点解析曾经有**两份平行实现**：

  - `Settings.endpoint`（`report` / `middleware` / `nodes` 真跑时走的）
  - `resolve_endpoint()`（`endpoint --check` / `smoke` 自检时走的）

前者的取值链读 `os.environ`，后者完全不读。后果不是报错 —— 是：

    `endpoint --profile hunyuan --check` 自检的是**档案原样**的端点，
    `report` 实跑的是"档案 + 当时 LLM_MODEL 覆盖"的端点。
    两次调用都成功、自检报告写着"tool_calls 正常、可以跑评测"、
    报表照常生成、成本照常累加 —— 只是结论建立在两个不同端点上。

这是本项目最典型的一类缺陷：**局部都自洽，合起来是错的，而没有任何东西会变红**。
修法是把优先级逻辑抽成一个内核 `_resolve_endpoint`，两个入口都调它
（差别只剩"档案名从哪来"和"允不允许显式覆盖"两点）。

★ 本文件里最重要的一条是 `test_both_entry_points_agree`：
  它不检查"两个入口各自对不对"，而是**检查它们是否给出同一个答案** ——
  这正是当初那个 bug 的形状。两份平行实现最典型的症状就是各自都能跑、
  答案不一样。

★ 为什么测试要用 `Settings(_env_file=None)` 而非模块级 `settings`：
  模块级 `settings` 会被开发机上的 `.env` 影响。测试若依赖它，
  在一台配了 `.env` 的机器上就会红 —— 而红的理由与代码无关。
  这里要测的是**解析逻辑**，所以输入必须由测试自己钉死。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ticket_agent import config
from ticket_agent.config import ENDPOINT_PROFILES, Settings

#: 本层会用到的全部环境变量。每个测试开头都清掉它们，
#: 这样"开发机上恰好 export 了 LLM_MODEL"不会改变任何断言。
_ENV_KEYS = (
    "LLM_PROFILE",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_CHEAP_MODEL",
    "LLM_API_KEY",
    "LLM_PRICE_IN_PER_1K_CNY",
    "LLM_PRICE_OUT_PER_1K_CNY",
    "DEEPSEEK_API_KEY",
    "HUNYUAN_API_KEY",
    "RELAY_API_KEY",
    "OPENAI_OFFICIAL_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "MODEL_NAME",
    "CHEAP_MODEL_NAME",
    # ★ 缺陷 ⑮：档案专用 key 变量也要清。少了它，开发机上恰好 export 过
    #   `WORKBUDDY_SIDECAR_TOKEN` 就会让 hy3 相关的断言带上一个真实 key。
    "WORKBUDDY_SIDECAR_TOKEN",
)


@pytest.fixture()
def cfg(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """一个不受 `.env` 与本机环境变量影响的 Settings。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    # `resolve_endpoint()` 在调用时读模块级 `settings`，所以替换它即可。
    monkeypatch.setattr(config, "settings", fresh)
    return fresh


# ---------------------------------------------------------------------------
# 契约：各档案解析成什么
# ---------------------------------------------------------------------------
def test_legacy_path_when_no_profile(cfg: Settings) -> None:
    """不选档案 = 老路径，沿用 OPENAI_* 时代的字段。"""
    ep = config.resolve_endpoint("")
    assert ep.profile == ""
    assert ep.model == "deepseek-chat"
    assert ep.base_url == "https://api.deepseek.com/v1"
    assert ep.price_known is True


def test_profile_supplies_defaults(cfg: Settings) -> None:
    """档案提供地址、模型、单价 —— ★ 单价必须跟着档案走。"""
    ep = config.resolve_endpoint("hunyuan")
    assert ep.model == "hunyuan-a13b"
    assert ep.base_url == "https://api.hunyuan.cloud.tencent.com/v1"
    # 混元官方价：输入 0.5 元/百万 = 0.0005 元/千；输出 2 元/百万 = 0.002 元/千。
    # ★ 这条断言的意义是"成本列换了端点就得换单价"：若有人让 hunyuan 档案
    #   继承老路径的 0.001，成本会按 DeepSeek 的价算混元的账。
    assert ep.price_in == 0.0005
    assert ep.price_out == 0.002
    assert ep.requires_key is True


def test_local_endpoint_needs_no_key(cfg: Settings) -> None:
    """本地端点不需要 key，且**已配置**（不能因为没 key 就报缺项）。"""
    ep = config.resolve_endpoint("ollama")
    assert ep.is_local is True
    assert ep.requires_key is False
    assert ep.is_configured is True
    assert ep.missing() == []
    # ★ 本地的 0 是"边际成本为 0"，与 relay 的 None（"不知道"）是两件事：
    #   前者可以进成本合计，后者只能提示"成本列不可用"。
    assert ep.price_known is True


def test_relay_has_no_declared_price(cfg: Settings) -> None:
    """中转站单价**未声明**，而不是 0。

    面板上那个 `x0.00` 是**计费倍率**不是"免费"：倍率把上游价乘 0，
    但上游是谁、上游什么价，由面板说了算。把倍数当价格填进来，
    报表就会用"0 元"冒充"不知道多少钱"。
    """
    ep = config.resolve_endpoint("relay")
    assert ep.price_in is None
    assert ep.price_known is False
    # 地址与模型留空 → 必须被报为缺项，而不是静默用一个陌生的默认地址去打
    assert ep.is_configured is False
    assert len(ep.missing()) >= 2


def test_unknown_profile_raises_instead_of_falling_back(cfg: Settings) -> None:
    """拼错档案名要早失败。

    静默退回默认端点的后果是"我想用 A 却跑了 B"，而报表依然正常 ——
    那种失败没有任何痕迹，所以宁可抛。
    """
    with pytest.raises(ValueError, match="未知端点档案"):
        config.resolve_endpoint("no-such-profile")


# ---------------------------------------------------------------------------
# 优先级：覆盖 → 环境 → 字段 → 档案
# ---------------------------------------------------------------------------
def test_priority_override_beats_env_beats_field_beats_profile(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四级优先级的完整链条，逐级验证。"""
    # 第 4 级：档案（没有任何覆盖时）
    assert config.resolve_endpoint("hunyuan").model == "hunyuan-a13b"

    # 第 3 级：Settings 字段（.env）
    monkeypatch.setattr(cfg, "llm_model", "from-field")
    assert config.resolve_endpoint("hunyuan").model == "from-field"

    # 第 2 级：进程环境
    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert config.resolve_endpoint("hunyuan").model == "from-env"

    # 第 1 级：显式覆盖
    assert config.resolve_endpoint("hunyuan", model="from-override").model == "from-override"


def test_price_env_var_works_on_legacy_path(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 老路径也必须认 `LLM_PRICE_*` 环境变量。

    因为报表在"成本列不可用"的告警里就是这么指引用户的
    （"或显式设 LLM_PRICE_IN_PER_1K_CNY"）。若老路径不认它，
    那条指引就是空头支票 —— 用户照做之后成本列依然是 0。
    """
    monkeypatch.setenv("LLM_PRICE_IN_PER_1K_CNY", "0.003")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1K_CNY", "0.006")
    ep = config.resolve_endpoint("")
    assert ep.price_in == 0.003
    assert ep.price_out == 0.006
    assert ep.price_known is True


def test_bad_price_string_raises_with_the_variable_name(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写错单价要报出**是哪个变量**错了，而不是一个裸的 ValueError。

    ★ 这条断言的是报错的可操作性：报表那头只会说"成本列不可用"，
      真正的原因（某个环境变量写了非数字）必须在这一层就说清楚。
    """
    monkeypatch.setenv("LLM_PRICE_IN_PER_1K_CNY", "五毛")
    with pytest.raises(ValueError, match="LLM_PRICE_IN_PER_1K_CNY"):
        config.resolve_endpoint("")


# ---------------------------------------------------------------------------
# ★ 缺陷 ⑭：老路径的单价不跟端点走
# ---------------------------------------------------------------------------
def test_legacy_agrees_with_profile_on_same_endpoint(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 老路径与档案路径解析出**同一个端点**时，单价必须相同。

    这是缺陷 ⑭ 的形状：老路径不选档案，单价只能从 `Settings.price_*` 字段拿，
    而那个字段的默认值是 DeepSeek 的 0.001/0.002 —— 于是把
    `OPENAI_BASE_URL` 指到本地 ollama（边际成本 0）或混元（输入 0.0005）时，
    报表会拿 DeepSeek 的价去算别人的账，而且 `price_known=True`，
    那条"成本列不可用"的告警永远不会响。

    ★ 这条断言的是"两个入口在同一个端点上必须给同一个价"，
      与 `test_both_entry_points_agree` 同源：错的不是某一侧，而是两侧不一致。
    """
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MODEL_NAME", "qwen2.5:7b")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b-instruct")
    legacy = config.resolve_endpoint("")
    profiled = config.resolve_endpoint("ollama")
    # 本地端点的边际成本是 0 —— 老路径也必须给出 0，而不是 DeepSeek 的 0.001
    assert (legacy.price_in, legacy.price_out) == (profiled.price_in, profiled.price_out)
    assert legacy.price_in == 0.0
    assert legacy.price_known is True


def test_legacy_unknown_endpoint_declares_no_price(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 认不出来的端点 → 单价**未声明**，不是套一个默认价。

    这是缺陷 ⑭ 的另一半：老路径原先**没有任何机制**表达"这个端点我不知道单价"，
    因为 `Settings.price_*` 的默认值非 None，永远走不到未声明分支。
    现在认不出来就判未声明，让报表打告警 ——
    "猜一个数会安静地算错，不知道会打告警：后者的代价是噪音，前者是错账"。

    ★ 这里用 `LLM_BASE_URL` 而不是 `OPENAI_BASE_URL`：前者是 `resolve_endpoint()`
      **运行时**读的覆盖变量，后者是 pydantic 在建 `Settings` 那一刻读的字段。
      fixture 已经建好了 Settings，所以只有前者能在本测试里生效 ——
      两条名字各自有效的路径另有测试覆盖（见下一条）。
    """
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.9:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "my-selfhosted-model")
    ep = config.resolve_endpoint("")
    assert ep.price_in is None
    assert ep.price_out is None
    assert ep.price_known is False
    # ★ 文案要说清"不是免费"：0 在报表里长得跟"免费"一模一样
    assert "未声明" in ep.source


def test_legacy_openai_base_url_env_is_also_honored(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ `.env` 风格写 `OPENAI_BASE_URL` 的老用户也不能被漏掉。

    这条与上一条是**同一个缺陷**的两条入口：老路径的地址既可能来自
    `.env` 里的 `OPENAI_BASE_URL`（pydantic 建 `Settings` 时读），
    也可能来自进程环境里的 `LLM_BASE_URL`（`resolve_endpoint()` 运行时读）。
    两条都必须走到"按地址认领、认不出就未声明"这同一个结论上。
    """
    monkeypatch.setenv("OPENAI_BASE_URL", "http://10.0.0.9:8000/v1")
    monkeypatch.setenv("MODEL_NAME", "my-selfhosted-model")
    rebuilt = Settings(_env_file=None)  # 重建：pydantic 此时才读 OPENAI_BASE_URL
    monkeypatch.setattr(config, "settings", rebuilt)
    ep = config.resolve_endpoint("")
    assert ep.base_url == "http://10.0.0.9:8000/v1"
    assert ep.price_known is False


def test_legacy_declared_price_field_still_honored(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 但用户**显式声明**的单价仍然认 —— 只是不再认字段默认值。

    判据是 `model_fields_set`：它只含"被赋过值"的字段，默认值不算。
    这是 pydantic 给"用户到底说没说"留下的唯一凭据。

    ★ 地址刻意选一个**认不出来**的：否则按地址认领会给出价，
      这条测试就验不到"显式声明"这条路径本身了。
    """
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.9:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "my-selfhosted-model")
    declared = Settings(_env_file=None, price_input_per_1k_cny=0.004, price_output_per_1k_cny=0.008)
    monkeypatch.setattr(config, "settings", declared)
    ep = config.resolve_endpoint("")
    assert (ep.price_in, ep.price_out) == (0.004, 0.008)
    assert ep.price_known is True
    # 对照：不显式填就该是未声明 —— 否则"填了才认"这条规则没有区分力
    defaulted = Settings(_env_file=None)
    monkeypatch.setattr(config, "settings", defaulted)
    assert config.resolve_endpoint("").price_known is False


def test_default_price_fields_are_none_not_deepseek(
    cfg: Settings,
) -> None:
    """★ 字段默认值必须是 None —— 这是缺陷 ⑭ 的根因所在。

    只要默认值是 0.001/0.002，老路径就永远 `price_known=True`，
    那条"成本列不可用"的告警就**永远不会响**。
    """
    assert cfg.price_input_per_1k_cny is None
    assert cfg.price_output_per_1k_cny is None
    # 而且默认值不能进 `model_fields_set`，否则"显式声明"这扇门形同虚设
    assert "price_input_per_1k_cny" not in cfg.model_fields_set


def test_profile_with_no_price_infers_from_address(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 选了档案但档案没声明价、用户又把地址指向已知端点 → 按地址认领。

    场景：`LLM_PROFILE=relay` 是"要 key、不知道单价"的通用档案，用户把
    `LLM_BASE_URL` 指到本地 ollama。这时"未声明"是错的 —— 地址已经把端点
    说清楚了。注意**档案声明了价就不反查**：那时档案名就是用户的声明，
    覆盖地址是他的自由，价跟着档案走。
    """
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b")
    ep = config.resolve_endpoint("relay")
    assert (ep.price_in, ep.price_out) == (0.0, 0.0)
    # 档案说需要 key，就还是需要 key —— 单价认领不改变鉴权口径
    assert ep.requires_key is True


# ---------------------------------------------------------------------------
# ★ 回归守卫：本次真正的 bug
# ---------------------------------------------------------------------------
def test_both_entry_points_agree(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """两个入口必须给出**同一个答案**。

    ★ 这是本文件的核心。当初 `Settings.endpoint` 读环境变量、而
      `resolve_endpoint()` 不读，于是自检认证的端点不是实跑的端点。
      两份实现各自都"能跑通"，只是答案不同 —— 所以这里比的不是
      "各自对不对"，而是 **`a == b`**。

    ★ 前提：**档案名必须一致**。档案名从哪来是两个入口唯一**合法**的差别
      （`settings.endpoint` 读 `LLM_PROFILE`，`resolve_endpoint()` 读入参），
      所以要让 `LLM_PROFILE=hunyuan`，否则比的是"档案 hunyuan"和"老路径"，
      那本来就该不同。

    ★ 也要注意：`resolve_endpoint("")` 与 `settings.endpoint`（`LLM_PROFILE`
      为空）**不是**同一个入口 —— 那时前者走老路径，后者也走老路径，可以比；
      但若 `LLM_PROFILE=hunyuan`，则前者传 `""` 会走老路径、后者走档案，
      比出来的差异是**配置差异**不是**实现差异**。写测试时这一点很容易搞混。
    """
    monkeypatch.setenv("LLM_PROFILE", "hunyuan")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")

    a = config.resolve_endpoint("hunyuan")  # 自检入口（档案名从入参来）
    b = cfg.endpoint  # 真跑入口（档案名从 LLM_PROFILE 来）

    assert a.profile == b.profile == "hunyuan"
    assert a.model == b.model == "from-env"
    assert a.base_url == b.base_url == "https://example.invalid/v1"
    # 逐字段比对：只要两者对任一字段有分歧，就是又长出了第二份实现。
    for field_name in ("profile", "model", "cheap_model", "base_url", "price_in", "price_out"):
        assert getattr(a, field_name) == getattr(b, field_name), (
            f"两个解析入口在 {field_name} 上给出不同答案："
            f"resolve_endpoint={getattr(a, field_name)!r}、"
            f"settings.endpoint={getattr(b, field_name)!r}"
        )


def test_both_entry_points_agree_on_legacy_path(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """老路径（无档案）上两个入口同样必须一致。

    ★ 单独测一次的理由：老路径与档案路径在内核里是**两个分支**，
      只测档案路径会漏掉"老路径又长出一份实现"。
      `endpoint --base-url ...`（不带 `--profile`）走的正是这一支。
    """
    monkeypatch.setenv("LLM_MODEL", "legacy-model")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1K_CNY", "0.007")

    a = config.resolve_endpoint("")  # 自检入口，显式"不要档案"
    b = cfg.endpoint  # 真跑入口，LLM_PROFILE 也为空

    assert a.profile == b.profile == ""
    assert a.model == b.model == "legacy-model"
    assert a.price_in == b.price_in == 0.007


def test_overrides_take_effect_without_profile(cfg: Settings) -> None:
    """★ 不带 `--profile` 时，`--base-url` 这类覆盖**也必须生效**。

    旧实现在档案名为空时直接 `return settings.endpoint`，于是
    `endpoint --base-url http://...` 会**静默丢掉**那个覆盖值，
    打印出默认端点 —— 用户看到的是"我明明指了地址，它却说要用官方地址"，
    而没有任何报错。
    """
    ep = config.resolve_endpoint("", base_url="http://127.0.0.1:9999/v1", model="my-model")
    assert ep.base_url == "http://127.0.0.1:9999/v1"
    assert ep.model == "my-model"


def test_endpoint_property_reads_env_live(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings.endpoint` 必须是**动态**的 —— 进程内换端点要立刻生效。

    ★ 理由：`settings` 是 `lru_cache` 的单例，"跑完 A 端点接着跑 B 端点做对比"
      这个动作天然发生在**同一个进程内**。若 endpoint 被缓存成模块级常量，
      第二次跑仍会用 A 端点，而报表看不出区别。
    """
    monkeypatch.setenv("LLM_MODEL", "first")
    assert cfg.endpoint.model == "first"
    monkeypatch.setenv("LLM_MODEL", "second")
    assert cfg.endpoint.model == "second", "endpoint 被缓存了 —— 换端点对它无效"


# ---------------------------------------------------------------------------
# 报错文案：缺项提示必须点名**真的会被读**的变量
# ---------------------------------------------------------------------------
def test_missing_key_message_names_distinct_variables(cfg: Settings) -> None:
    """缺 key 的提示里，两个候选变量名不能是同一个。

    ★ 这是实测撞出来的：老路径下提示曾显示
    `API key（环境变量 LLM_API_KEY 或 LLM_API_KEY）` —— 等于什么也没说。
    根因是 `missing()` 自己拼这句话，把"老路径读 LLM_API_KEY / OPENAI_API_KEY"
    与"档案路径读 <档案>_API_KEY / LLM_API_KEY"混成了一句。
    用户看到同名提示，唯一能做的就是挨个试。
    """
    for name in (None, "hunyuan", "relay", "openai", "deepseek"):
        ep = config.resolve_endpoint(name) if name else cfg.endpoint
        opts = ep.key_env_options()
        assert opts, f"{name}: 必须至少给出一个 key 变量名"
        assert len(opts) == len(set(opts)), f"{name}: 候选变量重复了 {opts}"
        # 提示语里的名字必须与 key_env_options() 一致（唯一来源）
        if ep.requires_key and not ep.api_key:
            msg = next(m for m in ep.missing() if m.startswith("API key"))
            for opt in opts:
                assert opt in msg, f"{name}: 提示语漏了 {opt}：{msg}"


def test_legacy_path_key_options_include_openai_name(cfg: Settings) -> None:
    """老路径必须点出 `OPENAI_API_KEY`。

    ★ 老路径的 key 来自 `cfg.openai_api_key`（即 OPENAI_API_KEY），
    而 `LLM_API_KEY` 是后加的通用变量 —— 两个都要说，因为两个都真的会读。
    """
    ep = cfg.endpoint
    assert ep.profile == ""
    assert ep.key_env_options() == ["LLM_API_KEY", "OPENAI_API_KEY"]


def test_profile_key_env_outranks_generic(cfg: Settings) -> None:
    """档案专用变量排在通用变量**之前** —— 次序即优先级。"""
    ep = config.resolve_endpoint("hunyuan")
    assert ep.key_env_options()[0] == "HUNYUAN_API_KEY"


def test_every_requiring_profile_has_a_named_key_env(cfg: Settings) -> None:
    """需要 key 的档案都必须在 `ENDPOINT_PROFILES` 里声明 `key_env`。

    ★ 没声明也不会报错（会退回通用变量），但用户就少了一条明确的指引。
    这条测试把"忘了声明"变成一个会红的断言。
    """
    for name, prof in config.ENDPOINT_PROFILES.items():
        if prof.get("requires_key", True) is False:
            continue
        if not prof.get("base_url"):
            continue  # relay / custom 全靠 LLM_* 填，档案里本就没有默认值
        assert prof.get("key_env"), f"档案 {name} 需要 key 却没声明 key_env"


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------
def test_redacted_never_leaks_key(cfg: Settings) -> None:
    """打印/日志里绝不能出现 key 本体。

    ★ `endpoint` 子命令会把解析结果打在终端上给人看，也会进报表的 meta。
      一旦这里漏出去，key 就进了聊天记录、issue、截图。
    """
    secret = "sk-SECRET-MUST-NOT-PRINT"
    ep = config.resolve_endpoint("hunyuan", api_key=secret)
    d = ep.redacted()
    assert d["has_api_key"] is True
    assert secret not in repr(d)
    # 逐值检查，而不是只看 repr —— 免得某个嵌套结构把 key 藏在子字典里
    for value in d.values():
        assert secret not in str(value)


def test_every_profile_has_a_note(cfg: Settings) -> None:
    """每个档案都要有一句"什么时候用它"。

    ★ 档案表是给用户选的，`endpoint --list` 会把 note 打出来。
      没有 note 的档案等于让人凭 base_url 猜它是什么。
    """
    for name, prof in ENDPOINT_PROFILES.items():
        assert prof.get("note"), f"档案 {name} 没有 note"
        assert "requires_key" in prof, f"档案 {name} 没声明 requires_key"


def test_price_scope_is_declared_and_valid(cfg: Settings) -> None:
    """★ 每个**声明了单价**的档案都要说清价的归属（缺陷 ⑱）。

    判据只有两个合法值，且都必须显式写出来：

      - `"address"`：价属于地址（自建推理）—— 换模型还是自己的电费。
      - `"model"`：价属于模型 —— 同地址上不同模型价不同，**必须模型名也对上**
        才能按地址认领。

    ★ 为什么要求**显式声明**而不是给个默认值了事：缺陷 ⑱ 的成因就是
      "默认按地址认领"这个隐式行为 —— 一旦某个云端地址被建了档案、
      又忘了想清"这个价是地址的还是模型的"，成本列就会安静地算错账。
      强制写出来，等于强制在那一步做一次判断。

    ★ 未声明单价的档案（relay / custom / openai 的 `price_in is None`）
      不受约束：它们本来就不会按地址认领任何价。
    """
    for name, prof in ENDPOINT_PROFILES.items():
        if prof.get("price_in") is None and prof.get("price_out") is None:
            continue  # 没声明单价 → 谈不上"价属于谁"
        scope = prof.get("price_scope")
        assert scope in ("address", "model"), (
            f"档案 {name} 声明了单价却没写 price_scope（或写了非法值 {scope!r}）；"
            f"合法值是 'address' 或 'model' —— 见 config.py 档案表上方的缺陷 ⑱ 说明"
        )


# ---------------------------------------------------------------------------
# 缺陷 ⑮：`.env` 里的**档案专用** key 必须能到解析器
# ---------------------------------------------------------------------------
def test_dotenv_key_for_profile_reaches_resolver(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.env` 里写的 `HUNYUAN_API_KEY` 必须真的进到 `resolve_endpoint()`。

    ★ 缺陷 ⑮ 的形状（三层叠加，每一层都不报错）：
      1. pydantic 读 `.env` 时**只填自己声明过的字段**，未声明的被
         `extra="ignore"` 直接丢掉（`HUNYUAN_API_KEY` 不在 `Settings` 里）；
      2. pydantic 读 `.env` **从不写 `os.environ`**；
      3. `_resolve_endpoint` 的 `env_get` 读的是 `os.environ`。

      → 结果是用户按 `.env.example` 的指引把 key 填进 `.env`，
        `endpoint` 打印 `has_api_key: false`、请求带空 bearer 打过去。
        **而两层都不报错、也不告警。**

    ★ 修法是解析器再垫一层"与这份 Settings 同一份 `.env`"。
      测试必须真的构造一个带 `.env` 的 Settings —— 正是因为这个缺陷
      只在"读了 `.env`"时出现，而 fixture 用的 `_env_file=None` 天然把它藏起来。
    """
    env_file = tmp_path / ".env"
    env_file.write_text("HUNYUAN_API_KEY=sk-from-dotenv\n", encoding="utf-8")

    fresh = Settings(_env_file=env_file)  # type: ignore[call-arg]
    monkeypatch.setattr(config, "settings", fresh)

    ep = config.resolve_endpoint("hunyuan")
    assert ep.api_key == "sk-from-dotenv", (
        "`.env` 里的档案专用 key 没到解析器 —— 就是缺陷 ⑮ 复发了"
    )
    assert "来自 .env" in ep.source, "source 该说清 key 是从 .env 来的"


def test_dotenv_does_not_override_process_env(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.env` 垫在**进程环境之下**：环境变量里已有的非空值赢。

    ★ 优先级是"显式覆盖 → 进程环境 → .env → Settings 字段 → 档案"。
      若 `.env` 反过来盖住进程环境，`HUNYUAN_API_KEY=xxx python ...` 这种
      临时换 key 的用法会静默失效 —— 而又一次不报错。
    """
    env_file = tmp_path / ".env"
    env_file.write_text("HUNYUAN_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("HUNYUAN_API_KEY", "sk-from-process")

    fresh = Settings(_env_file=env_file)  # type: ignore[call-arg]
    monkeypatch.setattr(config, "settings", fresh)

    ep = config.resolve_endpoint("hunyuan")
    assert ep.api_key == "sk-from-process"
    assert "来自 .env" not in ep.source


def test_dotenv_empty_value_does_not_override(cfg: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`.env` 里的 `KEY=`（留白待填）不能盖掉真实的值。

    ★ `.env.example` 里大量是 `OPENAI_API_KEY=`（空）这种形态。
      把空串当成"这里声明了一个值"，垫上去就会把进程环境里真的 key 抹掉。
    """
    env_file = tmp_path / ".env"
    env_file.write_text("HUNYUAN_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("HUNYUAN_API_KEY", "sk-from-process")

    fresh = Settings(_env_file=env_file)  # type: ignore[call-arg]
    monkeypatch.setattr(config, "settings", fresh)

    assert config.resolve_endpoint("hunyuan").api_key == "sk-from-process"


def test_env_file_none_disables_dotenv_entirely(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings(_env_file=None)` 必须**一点 `.env` 都不读**。

    ★ 这是整个测试套件的隔离基础：fixture 用 `_env_file=None` 造 Settings，
      好让开发机上真实的 `.env` 不影响任何断言。
      缺陷 ⑮ 的修复若在这里出错（比如解析器无条件读项目根的 `.env`），
      133 个测试会因为各自机器上 `.env` 的内容而随机变红 ——
      "红的理由与代码无关"，正是本文件开头那段 docstring 要避免的。
    """
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr(config, "settings", fresh)
    assert fresh.dotenv_fallback == {}, "`_env_file=None` 的 Settings 不该带着 .env 兜底层"


# ---------------------------------------------------------------------------
# 缺陷 ⑯：自检通过了，闸门却打不开
# ---------------------------------------------------------------------------
def test_recorded_check_opens_the_eval_gate(cfg: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`endpoint --check` 写下的记录，必须能让 `eval` 的闸门放行。

    ★ 缺陷 ⑯ 的形状：`preflight()` 里做检查 + 落盘，而 `cli.cmd_endpoint`
      的 `--check` 分支**直接调 `check_endpoint()`**、绕过了落盘那半。
      于是：自检四项全过、终端上写着"可以跑评测"，
      而 `eval` 照旧拦下来说"还没通过自检"。
      用户唯一的出路是设 `LLM_ALLOW_UNVERIFIED=1` ——
      那恰好把这道防"端点丢 tool_calls"的防线整个绕过去：
      闸门没坏，但也没用了，而且**没有任何东西会变红**。

    ★ 这条测试盯的是"两个入口是否给出同一个答案"，
      与 `test_both_entry_points_agree` 是同一种形状 ——
      本项目最典型的一类缺陷都是这样：局部都自洽，合起来是错的。
    """
    from ticket_agent import endpoints as ep_mod

    monkeypatch.setattr(cfg, "eval_out_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(ep_mod.settings, "eval_out_dir", str(tmp_path), raising=False)

    ep = config.resolve_endpoint("hunyuan", api_key="sk-x")
    rep = ep_mod.EndpointReport(
        endpoint=ep.redacted(),
        checks=[
            ep_mod.Check("连通与鉴权", True, "ok"),
            ep_mod.Check("tool_calls 保留", True, "ok"),
        ],
    )
    assert rep.ready is True

    # 闸门一开始必须是关的
    assert ep_mod.last_check_matches(ep) is False, "凭空就该是没自检过"

    path = ep_mod.record_check(ep, rep)
    assert path is not None, "记录该写得进去（tmp_path 是可写的）"
    assert ep_mod.last_check_matches(ep) is True, (
        "写下的记录读不回来 —— 自检与闸门之间断了一环，也就是缺陷 ⑯"
    )

    # 换端点（不同 model）旧记录必须失效，否则闸门形同虚设
    other = config.resolve_endpoint("hunyuan", api_key="sk-x", model="another-model")
    assert ep_mod.last_check_matches(other) is False, "换模型后旧自检记录该失效"


# ---------------------------------------------------------------------------
# 缺陷 ⑱：按地址反查时，"地址"不足以确定端点身份
# ---------------------------------------------------------------------------
def test_model_scoped_price_is_not_claimed_by_a_different_model(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 价按模型计的地址上，**换个模型就不能认领那个价**（缺陷 ⑱）。

    形状：把 `OPENAI_BASE_URL` 指向 DeepSeek 官方，模型填 `deepseek-reasoner`。
    地址认得出来（`deepseek` 档案），但那个档案声明的价是 `deepseek-chat` 的 ——
    官网价目里 reasoner 更贵。修复前这里会安静地给出 chat 的 0.001/0.002，
    且 `price_known=True`，那条"成本列不可用"的告警**永远不会响**。

    ★ 与缺陷 ⑭ 是同一族的复发：⑭ 的教训是"单价必须跟端点走"，
      而这里暴露的是"端点身份"被截断成了"地址"。
    """
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-reasoner")
    ep = config.resolve_endpoint("")
    assert ep.price_in is None, "chat 的价不能套到 reasoner 头上"
    assert ep.price_known is False
    # ★ 文案必须说清"是模型对不上，不是地址认不出" —— 否则用户会去改地址，
    #   而地址本来就是对的。
    assert "未声明" in ep.source
    assert "按模型计" in ep.source and "deepseek-reasoner" in ep.source


def test_model_scoped_price_still_claimed_for_its_own_model(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个地址、**档案声明的那个模型** → 照旧认领（上一条的对照组）。

    ★ 没有这一条，上一条可以靠"干脆永远不认领"通过 ——
      而那会让老路径指向 DeepSeek 官方时成本列也变成告警，
      与 `test_both_entry_points_agree` 的精神相悖。
    """
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    ep = config.resolve_endpoint("")
    assert (ep.price_in, ep.price_out) == (0.001, 0.002)
    assert ep.price_known is True
    assert "认领自 deepseek 档案" in ep.source


def test_address_scoped_price_ignores_model_name(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 价属于**地址**的端点，模型名不同也必须认领（缺陷 ⑱ 的反向守卫）。

    自建推理（ollama / vLLM / LM Studio）换什么模型都是自己的电费，
    边际成本仍然是 0。若把"模型名对不上就判未声明"写死，
    `test_legacy_agrees_with_profile_on_same_endpoint` 会立刻变红 ——
    那条测试里老路径写 `qwen2.5:7b`、档案写 `qwen2.5:7b-instruct`，
    两者必须给出同一个价。所以判据只能是档案自己声明的 `price_scope`。
    """
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "some-other-local-model:13b")
    ep = config.resolve_endpoint("")
    assert (ep.price_in, ep.price_out) == (0.0, 0.0)
    assert ep.price_known is True


def test_hy3_sidecar_price_is_model_scoped(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ hy3 sidecar 的地址后面挂着 16 个模型，价必须按模型认（缺陷 ⑱ 的实例）。

    名册里 `hy3` 标 `billing.free=true`（档案据此填 0.0），
    而 `hy3-x` 标 `x0.05 credits`、`free: false` —— 明确收费。
    按地址认领会把 0 元套给所有模型，成本列安静地算错账。

    ★ 顺便守住另一头：`LLM_PROFILE=hy3` 且模型就是 hy3 时，价仍是 0 ——
      修复不能把"限时免费"一起修掉。
    """
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:8787/v1")

    monkeypatch.setenv("LLM_MODEL", "hy3-x")
    other = config.resolve_endpoint("")
    assert other.price_known is False, "收费的 hy3-x 不能白拿 free 的价"
    assert "hy3" in other.source and "未声明" in other.source

    monkeypatch.setenv("LLM_MODEL", "hy3")
    same = config.resolve_endpoint("")
    assert (same.price_in, same.price_out) == (0.0, 0.0)
    assert same.price_known is True


def test_profile_path_price_is_also_model_scoped(
    cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ **档案路径**也必须被同一把尺子量（缺陷 ⑱ 的第二步，也是最容易漏的一半）。

    修复前的分叉：老路径改成按"地址 + 模型"认领后确实对了，
    而档案路径因为"用户已经选了档案，档案声明了价"就**直接用**——
    于是 `LLM_PROFILE=hy3` + `LLM_MODEL=hy3-x` 仍安静地按 free 记 0 元。
    同一处缺陷，两条入口给出**互相矛盾**的答案，比两条都错更危险：
    `endpoint --check` 会走档案路径、`report` 走另一条，用户看到哪一个取决于
    他当时敲了哪条命令。

    ★ 所以这里断言的不只是价，还断言两条入口**结论一致** ——
      判据只有一处（`_profile_covers_model`），两处调用。
    """
    monkeypatch.setenv("LLM_MODEL", "hy3-x")

    # 两条入口各按各自的方式指向**同一个** sidecar 地址：
    # 档案路径靠 `LLM_PROFILE=hy3`，老路径靠 `--base-url` 覆盖（CLI 的写法）。
    # ★ `resolve_endpoint` 的第三个位是 `**overrides`（关键字展开），不是
    #   一个 dict —— 传 dict 进去会被当成一个名叫 `overrides` 的键，
    #   静默不生效，而测试会以"价格不对"的样子报错，指向错误的行。
    profile_side = config.resolve_endpoint("hy3", api_key="sk-x")
    legacy_side = config.resolve_endpoint(
        "", base_url="http://127.0.0.1:8787/v1"
    )
    for ep in (profile_side, legacy_side):
        assert ep.price_known is False, f"{ep.source} —— 收费模型不该认领 free 的价"
        assert "未声明" in ep.source
        assert ep.price_in is None and ep.price_out is None

    # 换回档案声明的那个模型 → 认领，且两条入口给出同一个数
    monkeypatch.setenv("LLM_MODEL", "hy3")
    a = config.resolve_endpoint("hy3", api_key="sk-x")
    b = config.resolve_endpoint("", base_url="http://127.0.0.1:8787/v1")
    assert (a.price_in, a.price_out) == (0.0, 0.0)
    assert (b.price_in, b.price_out) == (a.price_in, a.price_out)
