"""端点解析八项实测 —— 合流后的回归检查。

★ 这个脚本存在的理由是一次真 bug：`resolve_endpoint()`（自检用）与
`Settings.endpoint`（真跑用）曾是**两份平行实现**，取值链不一样 ——
前者完全不读进程环境变量。后果不是报错，而是：

    `endpoint --profile hunyuan --check` 自检的是 A 端点，
    `report` 实跑的是 B 端点，两次调用都成功、报表照常生成、
    成本照常累加，只是结论建立在两个不同端点上。

这类故障没有任何东西会变红 —— 所以修完之后必须有一样东西**专门盯着它**。

第 7 项就是当初唯一红的那一项：设了 `LLM_MODEL` 之后，
`resolve_endpoint('hunyuan').model` 必须跟着变。它若红，说明两个入口
又各自长出了一份取值链。

★ 第 8 项盯的是缺陷 ⑭（老路径的单价不跟端点走）。它和第 7 项同族：
**单价与端点身份必须来自同一个来源**。不同的是这次漂移的不是解析路径，
而是**单价表** —— 老路径换端点（换成免费的本地的、或另一家价目的），
单价却还停在 DeepSeek 的 0.001/0.002，而且 `price_known=True`，
于是报表那条"成本列不可用"的告警永远不会响。
这一项若红，表现就是"成本列安静地按别人的价目表算账"。

用法：
    python _endpointcheck.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# ★ Windows 控制台默认 GBK，打不出 ⑭ 这类字符（UnicodeEncodeError 直接把脚本打断）。
#   本项目所有实测脚本都以中文输出，统一在这里把 stdout 定成 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ticket_agent.config import ENDPOINT_PROFILES, Settings, resolve_endpoint, settings  # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}\n         实际={got!r}\n         期望={want!r}")
    if not ok:
        FAILED.append(label)


def banner(n: str, title: str) -> None:
    print(f"\n{n}. {title}")


def main() -> int:
    print("=" * 72)
    print("端点解析八项实测（resolve_endpoint 与 settings.endpoint 已合流）")
    print("=" * 72)

    # ── 1. 老路径：不指定档案，走 OPENAI_* 时代的字段 ─────────────────
    banner("1", "老路径（无档案）")
    ep = resolve_endpoint("")
    check("model", ep.model, "deepseek-chat")
    check("base_url", ep.base_url, "https://api.deepseek.com/v1")
    check("price_in", ep.price_in, 0.001)
    check("price_out", ep.price_out, 0.002)
    check("price_known", ep.price_known, True)

    # ── 2. 混元档案：单价必须跟着档案走，不能沿用 OPENAI_* 的 ──────────
    banner("2", "hunyuan 档案")
    ep = resolve_endpoint("hunyuan")
    check("model", ep.model, "hunyuan-a13b")
    check("base_url", ep.base_url, "https://api.hunyuan.cloud.tencent.com/v1")
    # ★ 混元官方价：输入 0.5 元/百万 = 0.0005 元/千；输出 2 元/百万 = 0.002
    check("price_in", ep.price_in, 0.0005)
    check("price_out", ep.price_out, 0.002)
    check("price_known", ep.price_known, True)
    check("requires_key", ep.requires_key, True)

    # ── 3. 本地 ollama：不需要 key，成本真为 0（不是"不知道"）─────────
    banner("3", "ollama 本地档案")
    ep = resolve_endpoint("ollama")
    check("model", ep.model, "qwen2.5:7b-instruct")
    check("base_url", ep.base_url, "http://localhost:11434/v1")
    check("is_local", ep.is_local, True)
    check("requires_key", ep.requires_key, False)
    check("is_configured", ep.is_configured, True)
    check("missing", ep.missing(), [])
    check("price_known", ep.price_known, True)

    # ── 4. 第三方中转：地址与模型必须显式给，单价应当"未声明"──────────
    banner("4", "relay 第三方中转档案")
    ep = resolve_endpoint("relay")
    check("is_configured", ep.is_configured, False)
    check("price_in", ep.price_in, None)
    check("price_known", ep.price_known, False)
    print(f"         缺项={ep.missing()}")

    # ── 5. 脱敏：key 绝不能出现在打印结果里 ──────────────────────────
    banner("5", "脱敏（redacted）")
    ep = resolve_endpoint("hunyuan", api_key="SECRET-DO-NOT-PRINT")
    d = ep.redacted()
    check("has_api_key", d.get("has_api_key"), True)
    check("结果里含 SECRET 字样", any("SECRET" in str(v) for v in d.values()), False)

    # ── 6. 未知档案名：必须早失败，而不是静默退回老路径 ────────────────
    banner("6", "未知档案名")
    try:
        resolve_endpoint("no-such-profile")
        check("抛 ValueError", False, True)
    except ValueError as exc:
        check("抛 ValueError", True, True)
        print(f"         消息={exc}")
    check("可用档案", sorted(ENDPOINT_PROFILES), sorted(
        ["custom", "deepseek", "hunyuan", "hy3", "ollama", "openai", "relay"]))

    # ── 7. ★ 环境变量覆盖：两个入口必须给出同一个答案 ─────────────────
    banner("7", "环境变量覆盖（当初红的就是这一项）")
    os.environ["LLM_MODEL"] = "from-env"
    try:
        a = resolve_endpoint("hunyuan").model      # 自检入口
        b = settings.endpoint.model                # 真跑入口
        check("resolve_endpoint 认环境变量", a, "from-env")
        check("settings.endpoint 认环境变量", b, "from-env")
        # ★ 这一条才是关键：不是"各自对不对"，而是"两者是否一致"。
        #   两份平行实现最典型的症状就是各自都能跑、答案不一样。
        check("两个入口答案一致", a, b)
    finally:
        del os.environ["LLM_MODEL"]

    # ── 8. ★ 单价必须跟端点一起走（缺陷 ⑭）───────────────────────────
    banner("8", "单价跟端点走（缺陷 ⑭：老路径曾经不跟）")

    # 8a. 老路径指向本地 ollama —— 边际成本 0，不能按 DeepSeek 的 0.001 算
    os.environ["LLM_BASE_URL"] = "http://localhost:11434/v1"
    os.environ["LLM_MODEL"] = "qwen2.5:7b"
    try:
        legacy = resolve_endpoint("")
        profiled = resolve_endpoint("ollama")
        check("老路径地址已生效", legacy.base_url, "http://localhost:11434/v1")
        # ★ 关键：两个入口在**同一个端点**上必须给出同一个价。
        #   与第 7 项同源 —— 错的不是某一侧，而是两侧不一致。
        check("老路径与 ollama 档案同价", (legacy.price_in, legacy.price_out),
              (profiled.price_in, profiled.price_out))
        check("本地的 0 是边际成本 0", legacy.price_in, 0.0)
        check("price_known", legacy.price_known, True)
    finally:
        del os.environ["LLM_BASE_URL"]
        del os.environ["LLM_MODEL"]

    # 8b. 老路径指向**认不出来**的自建端点 —— 必须判"未声明"，不是套默认价
    os.environ["LLM_BASE_URL"] = "http://10.0.0.9:8000/v1"
    os.environ["LLM_MODEL"] = "my-selfhosted-model"
    try:
        ep = resolve_endpoint("")
        check("price_in 未声明", ep.price_in, None)
        check("price_known 为 False", ep.price_known, False)
        print(f"         source={ep.source}")
        # ★ 告警链路的起点：middleware 会据此把成本列标成"不可用"
        check("source 里说明是未声明而非免费", "未声明" in ep.source, True)
    finally:
        del os.environ["LLM_BASE_URL"]
        del os.environ["LLM_MODEL"]

    # 8c. 显式声明仍然认（否则用户没有出路）
    os.environ["LLM_BASE_URL"] = "http://10.0.0.9:8000/v1"
    os.environ["LLM_MODEL"] = "my-selfhosted-model"
    os.environ["LLM_PRICE_IN_PER_1K_CNY"] = "0.004"
    os.environ["LLM_PRICE_OUT_PER_1K_CNY"] = "0.008"
    try:
        ep = resolve_endpoint("")
        check("显式声明的单价被认", (ep.price_in, ep.price_out), (0.004, 0.008))
        check("price_known 变 True", ep.price_known, True)
    finally:
        del os.environ["LLM_BASE_URL"]
        del os.environ["LLM_MODEL"]
        del os.environ["LLM_PRICE_IN_PER_1K_CNY"]
        del os.environ["LLM_PRICE_OUT_PER_1K_CNY"]

    # 8d. ★ 根因守卫：字段默认值不能是 DeepSeek 的价
    bare = Settings(_env_file=None)
    check("PRICE_INPUT_PER_1K_CNY 默认 None", bare.price_input_per_1k_cny, None)
    check("PRICE_OUTPUT_PER_1K_CNY 默认 None", bare.price_output_per_1k_cny, None)
    #   默认值若进了 model_fields_set，"显式声明"那扇门就形同虚设
    check("默认值不算显式声明",
          "price_input_per_1k_cny" in bare.model_fields_set, False)

    # ── 收尾 ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("八项全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
