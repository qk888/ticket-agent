"""验证「同地址多模型」风险在加了 hy3 档案之后是否已经成真。

缺陷 ⑭ 的同族问题：`_profile_for_endpoint(base_url)` **只按地址反查**、
不看模型。档案表里一旦出现「一个地址挂多个模型」的档案，反查就会把
某一个模型的单价套给同地址的其它模型。

hy3 档案正好是这种形状 —— sidecar 背后是 WorkBuddy 的**16 个模型**：

    hy4-preview, hy3, hy3-x, deepseek-v4.1-flash, glm-5.3, glm-5.3-flash,
    glm-5.2, glm-5.1, glm-5v-turbo, minimax-m3, minimax-m2.7, kimi-k3-1,
    kimi-k2.8-preview, kimi-k2.7, kimi-k2.6, deepseek-v4-pro

名册里 `hy3` 是 `billing.free=true`（本档案据此填 0.0/0.0），
而 `hy3-x` 是 `billing: {credits: "x0.05 credits", free: false}` —— 明确收费。
它们的**地址完全相同**：都是 `http://127.0.0.1:8787/v1`。

本脚本不发任何网络请求，只问解析器：同一个地址、不同模型名，会拿到什么单价。

用法：
    $env:PYTHONIOENCODING="utf-8"; python _hy3priceprobe.py
"""

from __future__ import annotations

from ticket_agent.config import (
    ENDPOINT_PROFILES,
    Settings,
    _profile_for_endpoint,
    _resolve_endpoint,
)


def ask(cfg: Settings, profile_name: str):
    """★ 用 `_resolve_endpoint` 而不是 `resolve_endpoint`：后者只吃字符串覆盖、
    内部固定用模块级 `settings`（会被开发机 `.env` 影响）；前者能传自己的 cfg。
    `environ={}` 是显式的空环境 —— 隔离掉进程环境，否则本机真 export 过的
    变量会混进结论。"""
    return _resolve_endpoint(cfg, profile_name=profile_name, environ={})

SIDECAR = "http://127.0.0.1:8787/v1"

# 名册里的 16 个 id，逐个问一遍
CATALOG = [
    "hy3",
    "hy3-x",
    "hy4-preview",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "glm-5.1",
    "glm-5v-turbo",
    "minimax-m3",
    "minimax-m2.7",
    "kimi-k3-1",
    "kimi-k2.8-preview",
    "kimi-k2.7",
    "kimi-k2.6",
]


def main() -> int:
    print("=" * 78)
    print("一、按地址反查只认地址，不看模型")
    print("=" * 78)
    hit = _profile_for_endpoint(SIDECAR)
    print(f"_profile_for_endpoint({SIDECAR!r}) = {hit!r}")
    print("   → 无论请求里写的是 hy3 还是 hy3-x 还是 deepseek-v4-pro，")
    print("     反查结果都是同一个字符串。这是设计如此，也是风险的来源。")

    print()
    print("=" * 78)
    print("二、老路径（只填 OPENAI_BASE_URL/MODEL_NAME，不选档案）")
    print("=" * 78)
    print(f"{'model':<22} {'price_in':>10} {'price_out':>10} {'known':>6}  来源")
    print("-" * 78)
    for model in CATALOG:
        cfg = Settings(
            _env_file=None,
            openai_base_url=SIDECAR,
            model_name=model,
            # 老路径单价字段留默认（None）—— 这正是缺陷 ⑭ 修复后的形态
        )
        ep = ask(cfg, "")
        print(
            f"{model:<22} {str(ep.price_in):>10} {str(ep.price_out):>10} "
            f"{str(ep.price_known):>6}  {ep.source}"
        )

    print()
    print("=" * 78)
    print("三、档案路径（LLM_PROFILE=hy3），只改 LLM_MODEL")
    print("=" * 78)
    print(f"{'model':<22} {'price_in':>10} {'price_out':>10} {'known':>6}")
    print("-" * 78)
    for model in ("hy3", "hy3-x", "deepseek-v4-pro"):
        cfg = Settings(_env_file=None, llm_model=model)
        ep = ask(cfg, "hy3")
        print(f"{model:<22} {str(ep.price_in):>10} {str(ep.price_out):>10} {str(ep.price_known):>6}")

    print()
    print("=" * 78)
    print("四、档案表里各档案声明的模型（看有没有『一址多型』的其它例子）")
    print("=" * 78)
    for name, prof in ENDPOINT_PROFILES.items():
        print(f"{name:<10} {str(prof.get('base_url')):<46} model={prof.get('model')!r}")

    print()
    print("=" * 78)
    print("判据")
    print("=" * 78)
    cfg = Settings(_env_file=None, openai_base_url=SIDECAR, model_name="hy3-x")
    ep = ask(cfg, "")
    if ep.price_known and ep.price_in == 0.0:
        print("★ 风险已成立：老路径把 hy3-x（名册标注 x0.05 credits、free=false）")
        print("  也认成了 0.0/0.0，且 price_known=True —— 那条『成本列不可用』")
        print("  的告警不会响。这正是缺陷 ⑭ 的形状，只是换了一条路径复发。")
        print("  但注意：hy3-x 是**同一个 sidecar** 的模型，而 sidecar 目前只暴露")
        print("  默认模型；真要用 hy3-x 得先确认 sidecar/上游是否按次计费。")
        return 1
    print(f"未复现（hy3-x 拿到 price_in={ep.price_in}, known={ep.price_known}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
