"""评测报表 —— 把结果渲染成可贴进 README 的 markdown。

## 报表设计的两个原则

1. **不报单一总分。** 一个 "87% 准确率" 什么也说明不了：它不说难题表现如何、
   不说成本多少、不说失败在哪。所以报表强制分层（L1/L2/L3）、强制拆成本轴、
   强制给失败归因。

2. **必须诚实标注 `rule_ceiling`。** 本项目的 golden 由规则推导，
   而规则引擎基线（`scripted`）能直接调 `check_refund_eligibility`，
   所以它在终态准确率上**天然接近满分**。这不是"打败了 LLM"，
   这是"手工规则在规则题上不犯错"——它是对照组的上界，不是竞争者的成绩。
    报告里显式写出这个字段，防止读者把它误读成模型能力。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .harness import (
    CaseResult,
    accuracy,
    cost_summary,
    coverage_gaps,
    failure_breakdown,
    pass_hat_k,
    stratify,
    stratify_by_condition,
    stratify_by_style,
    three_rates,
)

#: 业务上**应当**被评测覆盖的条件集合。
#: 报表据此做覆盖度审计 —— 缺哪条就显式报出来（见 `coverage_gaps`）。
#: 这张表是手写的，因为它编码的是"我们希望 agent 会做哪些事"，
#: 而不是"数据碰巧造出了什么"。两者的差集才是真正的缺陷。
#:
#: ★ 加七个售后条件（2026-10 补功能）时的判据：**一条条件只有满足两件事才写进来**——
#:   ① 它对应一条真实客户诉求（有语料依据，不是我想出来的）；
#:   ② 它有一条独立的政策规则（R8–R14），也就是"做错了会有一个具体的错误结论"。
#:   按这个判据，`R10:reject`（赔付金额未定 → 反问）之类**故意不写**：
#:   它的规则分支存在，但和 `R9:need_clarification` 是同一个失效模式，
#:   写进来会让覆盖度表看起来更满，实际没多测到任何东西。
EXPECTED_CONDITIONS: tuple[str, ...] = (
    "R1:refund",
    "R1:reject",
    "R2:reject",
    "R3:block_logistics",
    "R4:escalate",
    "R5:escalate",
    "R6:escalate",
    "ambiguous",
    "no_order",
    # ── 售后业务线（真实诉求里占 78.6% 的那些）──
    "R8:exchange",
    "R8:reject",
    "R9:reship",
    "R9:need_clarification",
    "R10:compensate",
    "R11:price_protect",
    "R12:issue_invoice",
    "R13:change_address",
    "R14:urge_logistics",
    "R14:reject",
)


def build_report(
    results: list[CaseResult],
    *,
    k: int,
    meta: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总成结构化报告（JSON 可序列化，便于跨运行比对）。"""
    meta = meta or {}
    by_arm: dict[str, dict[str, Any]] = {}
    for arm in sorted({r.arm for r in results}):
        sub = [r for r in results if r.arm == arm]
        by_arm[arm] = {
            "n_runs": len(sub),
            "pass_hat_k": round(pass_hat_k(sub, k), 4),
            "accuracy": round(accuracy(sub), 4),
            # 三率（Intercom 口径：automation = involvement × resolution）。
            # ★ 和 accuracy 并列而不是取代它：accuracy 回答"终态对不对"，
            #   三率回答"低分该改路由还是改 agent"—— 两个问题不能互相替代。
            "three_rates": three_rates(sub),
            "by_level": stratify(sub),
            # 按条件分层 —— 见 harness.stratify_by_condition 里"为什么难度分层不够"
            "by_condition": stratify_by_condition(sub),
            "conditions_missing": coverage_gaps(sub, EXPECTED_CONDITIONS),
            # 按话术风格分层 —— 客户说得含糊时表现差多少（语料维度，非业务维度）
            "by_style": stratify_by_style(sub),
            "cost": cost_summary(sub),
            "failures": failure_breakdown(sub),
            "forbidden_total": sum(len(r.forbidden_hits) for r in sub),
            "errors": sum(1 for r in sub if r.error),
        }

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k,
        "meta": meta,
        "by_arm": by_arm,
        "cases": [r.to_dict() for r in results],
    }
    if baseline:
        report["baseline"] = baseline
    return report


def render_markdown(report: dict[str, Any], *, title: str = "工单 Agent 评测报表") -> str:
    """渲染成 markdown。"""
    L: list[str] = []
    L.append(f"# {title}")
    L.append("")
    L.append(f"生成时间：{report.get('generated_at')}　|　pass^k 中的 k = {report.get('k')}")
    L.append("")

    meta = report.get("meta") or {}
    if meta:
        L.append("**运行配置**：" + "；".join(f"{k}={v}" for k, v in meta.items()))
        L.append("")

    # --- 主表 -----------------------------------------------------------
    L.append("## 总览")
    L.append("")
    # ★ 模型调用数与工具调用数**分两列**列出。这两者常被混为一谈，但一次模型
    #   调用可以并发发出多个工具调用（本项目实测 single 5.25 次模型调用 / 4.25 次
    #   工具调用，multi 10.90 / 7.90），既不等值也不成比例。混用会让"多 agent 多
    #   花了几倍工具调用"这个核心消融结论算错。
    L.append(
        "| 对照臂 | 运行数 | pass^k | 单次准确率 | 平均成本(元) | 平均模型调用 | 平均工具调用 | 平均写操作 | 违规动作 | 运行错误 |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for arm, d in report["by_arm"].items():
        c = d["cost"]
        L.append(
            f"| `{arm}` | {d['n_runs']} | **{d['pass_hat_k']:.1%}** | "
            f"{d['accuracy']:.1%} | {c.get('avg_cost_cny', 0):.6f} | "
            f"{c.get('avg_model_calls', 0):.2f} | {c.get('avg_tool_calls', 0):.2f} | "
            f"{c.get('avg_write_calls', 0):.2f} | {d['forbidden_total']} | {d['errors']} |"
        )
    L.append("")

    baseline = report.get("baseline")
    if baseline:
        L.append(
            "> ⚠️ **rule_ceiling 说明**：" + str(baseline.get("note", ""))
        )
        if "scripted_pass_hat_k" in baseline:
            L.append(
                f"> 规则引擎基线 pass^k = **{baseline['scripted_pass_hat_k']:.1%}** —— "
                "它是这套评分标准下的**上限参考**（规则题上规则引擎不犯错），"
                "不是用来被超越的对手。"
            )
        L.append("")

    # --- 三率（Intercom 口径）--------------------------------------------
    L.append("## 三率指标（Automation = Involvement × Resolution）")
    L.append("")
    L.append(
        "> 业界把三个率定义为**可拆开**的，价值在于一个低分能归因：低是因为 agent "
        "根本没参与（该改路由/入口），还是参与了却没办成（该改 agent 本身）？"
        "这两种原因的改法完全不同，而单看一个准确率会把它们压平。"
    )
    L.append("")
    L.append("| 对照臂 | 运行数 | 解决 | 参与 | Automation | Involvement | Resolution |")
    L.append("|---|---|---|---|---|---|---|")
    for arm, d in report["by_arm"].items():
        t = d.get("three_rates") or {}
        rr = t.get("resolution_rate")
        L.append(
            f"| `{arm}` | {t.get('total', 0)} | {t.get('resolved', 0)} | "
            f"{t.get('engaged', 0)} | {t.get('automation_rate', 0.0):.1%} | "
            f"{t.get('involvement_rate', 0.0):.1%} | "
            f"{'—' if rr is None else format(rr, '.1%')} |"
        )
    L.append("")
    L.append(
        "> ★ 口径边界：本项目的 `engaged` **不是**业界那个「AI 有没有接话」—— "
        "每条 case 都是 agent 被直接调用的，不存在「没接话」的会话。"
        "所以 Involvement 在这里的实际含义是「这条 case agent 有没有真的去查数据"
        "（而不是空跑/直接报错）」，它是一个下界刻画，不是业界口径的等位物。"
        "`escalate`（转人工）算参与、不算解决。"
    )
    L.append("")

    # --- 分层 -----------------------------------------------------------
    L.append("## 分层表现（难度维度）")
    L.append("")
    for arm, d in report["by_arm"].items():
        L.append(f"### `{arm}`")
        L.append("")
        L.append("| 难度 | 条数 | 准确率 | 平均工具调用 | 平均成本(元) | 违规率 |")
        L.append("|---|---|---|---|---|---|")
        for lvl, s in d["by_level"].items():
            L.append(
                f"| {lvl} | {int(s['n'])} | {s['accuracy']:.1%} | "
                f"{s['avg_tool_calls']:.2f} | {s['avg_cost_cny']:.6f} | {s['forbidden_rate']:.1%} |"
            )
        L.append("")

    # --- 按条件 ---------------------------------------------------------
    L.append("## 按条件表现（规则维度）")
    L.append("")
    L.append("> 难度分层会把「考的是哪条规则」抹平：`R1:reject`（驳回）和")
    L.append("> `R3:block_logistics`（拦截在途物流）**都是 L1**，考的能力完全不同。")
    L.append("> 而自然分布极不均衡（实测 `R1:reject` 占 40%、`R3` 只占 2.7%），")
    L.append("> 于是**总准确率是被多数条件抬起来的** —— agent 把每一条 R3 都办错，")
    L.append("> 总准确率也只掉 2.7 个点。这张表才是「哪条规则真的会做」。")
    L.append("")
    for arm, d in report["by_arm"].items():
        by_cond = d.get("by_condition") or {}
        if not by_cond:
            continue
        L.append(f"### `{arm}`")
        L.append("")
        L.append("| 条件 | 条数 | 占比 | 准确率 | 平均工具调用 | 违规动作 |")
        L.append("|---|---|---|---|---|---|")
        for cond, s in sorted(by_cond.items(), key=lambda kv: -kv[1]["n"]):
            L.append(
                f"| `{cond}` | {int(s['n'])} | {s['share']:.1%} | "
                f"{s['accuracy']:.1%} | {s['avg_tool_calls']:.2f} | {int(s['forbidden_hits'])} |"
            )
        L.append("")
        missing = d.get("conditions_missing") or []
        if missing:
            L.append(
                "> ⚠️ **覆盖度缺陷**：以下条件在本轮评测中**一条 case 都没有** —— "
                + "、".join(f"`{m}`" for m in missing)
            )
            L.append(
                "> 对应故障**永远不可能被发现**，且这个事实不会在任何准确率数字上留下痕迹。"
                "这类缺口必须显式报出来，不能靠人去心算。"
            )
        else:
            L.append("> 覆盖度审计：期望的全部条件均有 case 覆盖。")
        L.append("")

    # --- 按话术风格 -----------------------------------------------------
    L.append("## 按话术风格（语料维度）")
    L.append("")
    L.append("> 这一维度和上面的「条件」正交：条件问的是**考哪条规则**，")
    L.append("> 风格问的是**客户要的动作在不在系统能力内**。")
    L.append("> ★ 注意 `vague` 的准确含义不是「客户没说清」，而是")
    L.append("> **客户要的动作本系统办不到**：真实售后里占比最高的是")
    L.append("> 「帮我查下快递 / 什么时候能到」这类催查件（语料里物流类约 40%），")
    L.append("> 客户说得非常清楚，只是系统只有退款/驳回/拦截/转人工四种动作。")
    L.append("> 这类工单的正解是**转人工或反问澄清**，而非替客户挑一个他不要的动作。")
    L.append("> 风格由 `corpus.classify_style()` 的规则判定（可复现、可单测），")
    L.append("> 它认「诉求里有没有动作集内的动作词」，不等于人工语义标注。")
    L.append("")
    for arm, d in report["by_arm"].items():
        by_style = d.get("by_style") or {}
        if not by_style:
            continue
        L.append(f"### `{arm}`")
        L.append("")
        L.append("| 话术风格 | 条数 | 占比 | 准确率 | 平均工具调用 | 平均成本(元) | 违规动作 |")
        L.append("|---|---|---|---|---|---|---|")
        for st, s in sorted(by_style.items(), key=lambda kv: -kv[1]["n"]):
            L.append(
                f"| `{st}` | {int(s['n'])} | {s['share']:.1%} | "
                f"{s['accuracy']:.1%} | {s['avg_tool_calls']:.2f} | "
                f"{s['avg_cost_cny']:.6f} | {int(s['forbidden_hits'])} |"
            )
        L.append("")

    # --- 成本 -----------------------------------------------------------
    L.append("## 成本轴")
    L.append("")
    L.append("| 对照臂 | 总成本(元) | 通过均成本 | 失败均成本 | 输入token | 输出token | 降级率 | 熔断率 | 平均延迟(ms) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for arm, d in report["by_arm"].items():
        c = d["cost"]
        L.append(
            f"| `{arm}` | {c.get('total_cost_cny', 0):.6f} | {c.get('avg_cost_passed_cny', 0):.6f} | "
            f"{c.get('avg_cost_failed_cny', 0):.6f} | {c.get('avg_input_tokens', 0):.0f} | "
            f"{c.get('avg_output_tokens', 0):.0f} | {c.get('degraded_rate', 0):.1%} | "
            f"{c.get('budget_tripped_rate', 0):.1%} | {c.get('avg_latency_ms', 0):.0f} |"
        )
    L.append("")

    # ★ 单价未声明时必须显式警告。否则上表那一列 0.000000 会被读成"免费"，
    #   而实际含义是"这个端点没有单价信息，我们根本不知道多少钱"。
    #   两种情况的处置完全不同：前者可以放心跑，后者要么补单价、要么别用成本轴下结论。
    for arm, d in report["by_arm"].items():
        rate = d["cost"].get("price_unknown_rate", 0.0)
        if rate > 0:
            profile = meta.get("endpoint_profile") or "(老路径)"
            L.append(
                f"> ⚠️ **`{arm}` 的成本列不可用**：{rate:.0%} 的运行落在"
                f"**没有声明单价**的端点上（profile={profile}）。"
                "上表的金额是按 0 累加出来的，**不是免费**，是**不知道**。"
                "补上单价的方式：用内置档案（`LLM_PROFILE=hunyuan`），"
                "或显式设 `LLM_PRICE_IN_PER_1K_CNY` / `LLM_PRICE_OUT_PER_1K_CNY`。"
            )
            L.append("")
            break

    # --- 失败归因 -------------------------------------------------------
    L.append("## 失败归因")
    L.append("")
    L.append("> 按终态 diff 的字段前缀归类：`ticket_status` = 流程决策错（该关没关/关错）；")
    L.append("> `refund_ledger` = 副作用错（该退没退/退错金额）；`order_state` = 订单状态错。")
    L.append("")
    for arm, d in report["by_arm"].items():
        fails = d["failures"]
        if not fails:
            L.append(f"- `{arm}`：**无失败**")
            continue
        L.append(f"- `{arm}`：" + "；".join(f"{k} = {v} 条" for k, v in fails.items()))
    L.append("")

    return "\n".join(L)


def write_report(report: dict[str, Any], out_dir: str | Path, *, name: str = "eval") -> tuple[Path, Path]:
    """落盘 JSON + markdown，返回两个路径。"""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = d / f"{name}-{ts}.json"
    md_path = d / f"{name}-{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
