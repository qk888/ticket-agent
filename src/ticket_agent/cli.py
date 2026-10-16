"""命令行入口。

设计取舍：CLI 不做业务逻辑，只做「参数解析 + 调 service + 打印」。
所以每个命令都能在别的上下文里被复用（CI 跑 eval、cron 跑对账）。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import settings
from .db import build_eval_cases, init_db, seed_all, session_scope
from .db import corpus
from .eval import accuracy, cost_summary, failure_breakdown, pass_hat_k, run_suite, stratify
from .graph.nodes import get_model_kind, set_model_kind
from .service import (
    approve_run,
    approve_ticket,
    get_ticket_detail,
    list_tickets,
    pending_human_tickets,
    run_once,
    runtime_info,
    stream_run,
)

app = typer.Typer(add_completion=False, help="售后工单处置 Agent")


def _force_utf8_console() -> None:
    """把标准流切到 UTF-8。

    ★ 这不是"顺手加的一行"，是必要的：中文 Windows 的控制台编码默认是
    GBK（cp936），而下面的输出里有 `✓ ✗ ▶` 这类 GBK 编不出来的字符。
    不切就会在**打印报表时**抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`，
    而那时评测已经跑完、报表也已落盘，命令却以退出码 1 结束 ——
    等于"活儿干完了，但看起来失败了"，CI 里尤其误导。

    `errors="replace"` 是第二道保险：即使某个终端环境仍拒绝某个字符，
    最坏也只显示成 `?`，不会再让整个命令崩掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 被重定向到非文本流时无 reconfigure
            pass


_force_utf8_console()

console = Console()


def _print(obj: Any) -> None:
    console.print_json(json.dumps(obj, ensure_ascii=False, default=str))


def _run_meta(*, arm: str, kind: str, size: int, seed: int) -> dict[str, Any]:
    """报表的运行配置 + **语料溯源** + **端点溯源**。

    ★ 为什么语料指纹必须进 meta，而不只是写个文件名：

    指标只有在"读的是同一批字节"时才可比。只写 `ecom_aftersale.jsonl`
    不足以固定这件事 —— 文件会被替换、会被重新下载、也可能被手工改过。
    这和缺陷 ⑨（基准漂移）是同一类问题，只是漂移源从时钟换成了数据文件：
    时钟漂移让同一个故障跑出 2/1/2，数据漂移会让准确率悄悄变一个数。

    所以报表里带上 sha256 前 16 位与唯一条数，换语料就能在 diff 里看见。

    ★ 端点是**第二个漂移源**，而且更隐蔽：换模型往往连单价一起变了，
    但成本列的代码没变、量级也照常合理 —— 于是"这次比上次便宜"这种结论
    会建立在两套单价上。所以端点、模型、单价、以及"单价是否已知"
    必须和语料指纹一样进 meta。kind=scripted 时不写端点（它不调模型）。
    """
    st = corpus.corpus_stats()
    meta: dict[str, Any] = {
        "arm": arm,
        "kind": kind,
        "size": size,
        "seed": seed,
        "corpus_sha256": st["sha256"][:16],
        "corpus_unique": st["unique_phrasings"],
        "corpus_ok": st["exists"],
    }
    if kind == "llm":
        ep = settings.endpoint
        meta["endpoint_profile"] = ep.profile or "(老路径 OPENAI_*)"
        meta["endpoint_model"] = ep.model
        meta["endpoint_base_url"] = ep.base_url
        # ★ 单价未知时显式写 None 而不是省略这个键 —— 省略了读者会以为
        #   "这份报表没有成本列"，写 None 才是"成本列里的数字不可用"。
        meta["price_in_per_1k"] = ep.price_in
        meta["price_out_per_1k"] = ep.price_out
        meta["price_known"] = ep.price_known
    return meta


# ── 数据 ────────────────────────────────────────────────────────


@app.command("seed")
def cmd_seed(
    customers: int = typer.Option(60, help="客户数"),
    seed: int = typer.Option(7, help="随机种子"),
    drop: bool = typer.Option(False, "--drop", help="先删表重建"),
) -> None:
    """生成合成业务数据。"""
    init_db(drop=drop)
    _print(seed_all(n_customers=customers, seed=seed))


# ── 运行 ────────────────────────────────────────────────────────


@app.command("tickets")
def cmd_tickets(
    tenant: str = typer.Option("acme"),
    limit: int = typer.Option(20),
) -> None:
    """列出工单。"""
    rows = list_tickets(tenant, limit=limit)
    t = Table(title=f"{tenant} 的工单（{len(rows)}）")
    for col in ("ticket_id", "subject", "status", "order_basis", "order_id"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            str(r["ticket_id"]), r["subject"][:24], r["status"],
            r["order_basis"], str(r["order_id"]),
        )
    console.print(t)


@app.command("run")
def cmd_run(
    ticket_id: int,
    tenant: str = typer.Option("acme"),
    arm: str = typer.Option("multi"),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="逐节点播报"),
) -> None:
    """跑一张工单。"""
    if stream:
        for ev in stream_run(tenant, ticket_id, arm=arm):
            kind = ev["type"]
            if kind == "run_started":
                console.print(f"[bold]▶ run {ev['run_id']}[/bold] ticket={ticket_id} arm={arm}")
            elif kind == "node_started":
                console.print(f"  [cyan]→ {ev['node']}[/cyan]")
            elif kind == "tool_call":
                mark = "[green]✓[/green]" if ev.get("ok") else "[red]✗[/red]"
                console.print(f"      {mark} {ev['name']}({json.dumps(ev.get('args') or {}, ensure_ascii=False)[:80]}) [{ev.get('ms')}ms]")
            elif kind == "node_finished":
                if ev.get("note"):
                    console.print(f"      [dim]{str(ev['note'])[:160]}[/dim]")
                if ev.get("forbidden_hits"):
                    console.print(f"      [red]越权：{ev['forbidden_hits']}[/red]")
            elif kind == "final":
                _show_final(ev)
            elif kind == "error":
                console.print(f"[red]错误：{ev['error']}[/red]")
        return

    rec = run_once(tenant, ticket_id, arm=arm)
    _show_final(rec.summary())


def _show_final(ev: dict[str, Any]) -> None:
    b = ev.get("budget") or {}
    body = (
        f"状态：{ev.get('status')}\n"
        f"结论：{(ev.get('final_answer') or '（无）')[:200]}\n"
        f"需要人工：{ev.get('requires_human_approval')}\n"
        f"动作：{ev.get('actions_taken') or '无'}\n"
        f"越权：{ev.get('forbidden_hits') or '无'}\n"
        f"用量：{b.get('input_tokens', 0)}→{b.get('output_tokens', 0)} tok / "
        f"{b.get('model_calls', 0)} 次调用 / ¥{b.get('cost_cny', 0):.4f}"
        + ("  [yellow]（已降级）[/yellow]" if b.get("degraded") else "")
        + ("  [red]（预算熔断）[/red]" if b.get("tripped") else "")
    )
    # ★ 失败原因必须显示出来：早前这里只印上面几行，于是脚本里
    #   `_aggregate` 签名改坏后 CLI 只报"状态：failed / 结论：（无）"，
    #   真正抛了什么异常完全看不到，白查了一轮。
    if ev.get("error"):
        body += f"\n[red]错误：{ev['error']}[/red]"
    console.print(Panel(body, title="处置结果", expand=False))


@app.command("pending")
def cmd_pending(tenant: str = typer.Option("acme")) -> None:
    """人工待办队列。"""
    rows = pending_human_tickets(tenant)
    if not rows:
        console.print("[dim]队列为空[/dim]")
        return
    t = Table(title=f"{tenant} 待人工（{len(rows)}）")
    for col in ("ticket_id", "subject", "order_no", "amount"):
        t.add_column(col)
    for r in rows:
        t.add_row(str(r["ticket_id"]), r["subject"][:24], str(r["order_no"]), str(r["amount"]))
    console.print(t)
    console.print(
        "[dim]裁决：[/dim]ticket-agent approve --ticket <ticket_id> "
        "[dim]（按工单裁决，跨进程可用；加 --override 可显式破例）[/dim]"
    )


@app.command("approve")
def cmd_approve(
    run_id: str = typer.Argument("", help="关联的 run_id（可选；跨进程审批可留空）"),
    tenant: str = typer.Option("acme"),
    ticket: int = typer.Option(None, "--ticket", "-t", help="★ 按工单裁决（跨进程可用）"),
    decision: str = typer.Option("approve_refund", help="approve_refund|reject|keep_pending"),
    note: str = typer.Option(""),
    amount: float = typer.Option(None, help="退款金额，缺省按订单金额"),
    override: bool = typer.Option(
        False, "--override", help="显式越过政策护栏（R5 已退过款 / R6 生鲜不退）；会记入审计"
    ),
) -> None:
    """人工裁决一条转人工的工单。

    ★ 批准退款时会走政策护栏：R5/R6 这类「政策禁止」的工单会被拒绝（退出码 1），
    而不是静默地把钱退出去。确需破例请显式加 --override。

    ★★ 定位方式（缺陷 ⑦ 的修法）：`ticket-agent pending` 列的是**库里的工单**，
    所以最自然的人工流程是 `approve --ticket 17`。而 run 记录活在进程内内存里，
    `approve <run_id>` 只在"同一个进程刚跑完这张工单"时可用 ——
    跨进程时它会报 `run ... 不存在`，那时候请改用 `--ticket`。
    """
    try:
        if ticket is not None:
            # 按工单裁决：跨进程可用（状态真相在库里，不在 RunStore）
            _print(
                approve_ticket(
                    tenant,
                    ticket,
                    decision=decision,  # type: ignore[arg-type]
                    note=note,
                    amount=amount,
                    override=override,
                    run_id=run_id,
                )
            )
            return
        if not run_id:
            _print(
                {
                    "ok": False,
                    "error": "必须提供 run_id 或 --ticket 之一。"
                    "跨进程/不确定 run 时请用 `approve --ticket <id>`"
                    "（`ticket-agent pending` 列出的就是工单号）。",
                }
            )
            raise typer.Exit(code=1)
        _print(
            approve_run(
                run_id,
                tenant,
                decision=decision,  # type: ignore[arg-type]
                note=note,
                amount=amount,
                override=override,
            )
        )
    except LookupError as exc:
        # run 不在本进程内存里 —— 这不是"工单不存在"，而是定位方式选错了
        _print(
            {
                "ok": False,
                "error": str(exc),
                "hint": "run 记录保存在进程内内存里，换一个进程就查不到。"
                "请改用 `ticket-agent approve --ticket <工单号>`（工单号见 pending 输出）。",
            }
        )
        raise typer.Exit(code=1) from exc
    except (ValueError, PermissionError) as exc:
        _print({"ok": False, "error": str(exc)})
        raise typer.Exit(code=1) from exc


@app.command("show")
def cmd_show(ticket_id: int, tenant: str = typer.Option("acme")) -> None:
    """看一张工单的完整上下文（含终态快照）。"""
    _print(get_ticket_detail(tenant, ticket_id))


# ── 评测 ────────────────────────────────────────────────────────


def _ensure_endpoint_ready(kind: str) -> None:
    """kind=llm 时的前置闸门：端点必须配好，且**必须自检过**。

    ★ 为什么值得在跑评测前拦一道：

    用 llm 跑一轮千级评测要几千次模型调用、十几分钟、真金白银。而端点配错
    或中转层丢 `tool_calls` 的典型表现是——**接口一切正常**（HTTP 200、有内容、
    计费了），只是工具有一次都没被调用过，agent 每张工单都立刻结束。
    这种故障跑完一整轮才在准确率上显形，而那时钱已经花了。

    `endpoint --check` 只要 3 次请求就能分辨。所以这里强制它先跑过。

    ★ 逃生开关是 `LLM_ALLOW_UNVERIFIED=1`（`llm_allow_unverified`）：
    有些场景确实没法先自检（比如刚从别的机器搬来配置、或自检被限流打断，
    但你确信端点是对的）。给一条明确的路，比让人绕过整个闸门好。
    """
    if kind != "llm":
        return

    from .endpoints import last_check_matches

    ep = settings.endpoint
    missing = ep.missing()
    if missing:
        console.print("[red]端点没配好：[/red]" + "、".join(missing))
        console.print("[dim]跑 `endpoint --list` 看内置档案，或 `endpoint` 看当前解析结果。[/dim]")
        raise typer.Exit(code=1)

    if last_check_matches(ep):
        return
    if settings.llm_allow_unverified:
        console.print(
            "[yellow]⚠ 端点未自检，但 LLM_ALLOW_UNVERIFIED 已开，继续跑。[/yellow]"
        )
        return

    console.print(
        f"[red]端点 {ep.model} @ {ep.base_url} 还没通过自检。[/red]\n"
        "先跑 `python -m ticket_agent.cli endpoint --check`（3 次请求，几秒钟）。\n"
        "[dim]它专门验一件事：这个端点会不会保留 tool_calls —— 不保留的话 "
        "agent 一步都走不动，但接口不会报错，你会白跑一整轮。\n"
        "确知端点没问题、要跳过自检：设 LLM_ALLOW_UNVERIFIED=1。[/dim]"
    )
    raise typer.Exit(code=1)


@app.command("eval")
def cmd_eval(
    size: int = typer.Option(40),
    k: int = typer.Option(1, help="pass^k 的 k"),
    arms: str = typer.Option("single,multi"),
    kind: str = typer.Option("scripted"),
    seed: int = typer.Option(7),
    customers: int = typer.Option(60),
    reseed: bool = typer.Option(True, "--reseed/--no-reseed"),
    report: bool = typer.Option(False, "--report", help="落盘 JSON+Markdown 报告"),
) -> None:
    """跑评测并打印指标。"""
    _ensure_endpoint_ready(kind)

    if reseed:
        init_db(drop=True)
        seed_all(n_customers=customers, seed=seed)

    set_model_kind(kind)
    cases = build_eval_cases(size=size, seed=seed)
    console.print(f"评测集 {len(cases)} 条（模型 kind={kind}，pass^{k}）")

    reports = {}
    for arm in [a.strip() for a in arms.split(",") if a.strip()]:
        results = run_suite(cases, arm=arm, kind=kind, k=k, progress=False)
        acc = accuracy(results)
        phat = pass_hat_k(results, k)
        st = stratify(results)
        cs = cost_summary(results)
        fb = failure_breakdown(results)

        console.print(f"\n[bold cyan]═══ arm = {arm} ═══[/bold cyan]")
        console.print(f"终态准确率 {acc:.3f}   pass^{k} {phat:.3f}")
        t = Table()
        for col in ("层级", "n", "准确率", "pass^k", "平均工具数", "越权率"):
            t.add_column(col)
        for lvl, m in st.items():
            t.add_row(
                lvl, str(m["n"]), f"{m['accuracy']:.3f}", f"{m.get('pass_hat_k', 0):.3f}",
                f"{m['avg_tool_calls']:.2f}", f"{m['forbidden_rate']:.2f}",
            )
        console.print(t)
        console.print(
            f"成本：均 ¥{cs['avg_cost_cny']:.5f} / 条（通过 ¥{cs['avg_cost_passed_cny']:.5f}，"
            f"失败 ¥{cs['avg_cost_failed_cny']:.5f}）；均 {cs['avg_model_calls']:.1f} 次调用；"
            f"降级率 {cs['degraded_rate']:.2f}；熔断率 {cs['budget_tripped_rate']:.2f}"
        )
        # ★ 单价未声明时必须说出来（缺陷 ⑰ 同一处）：上面那几个 cost 数字
        #   在单价未知时全是 0，而 0 在报表里跟"免费"长得一模一样。
        if cs.get("price_unknown_rate"):
            console.print(
                f"[yellow]⚠ 有 {cs['price_unknown_rate']:.0%} 的 case 端点单价未声明 —— "
                "上面成本列是 0，但它表示'不知道'，不是'不要钱'。[/yellow]"
            )
        if fb:
            console.print(f"[yellow]失败归因：{fb}[/yellow]")

        if report:
            from .eval import build_report, render_markdown, write_report

            rep = build_report(
                results,
                k=k,
                meta=_run_meta(arm=arm, kind=kind, size=len(cases), seed=seed),
            )
            json_path, md_path = write_report(rep, settings.eval_out_dir, name=f"eval-{arm}")
            console.print(f"报告已写入 {json_path.name} / {md_path.name}")
            reports[arm] = rep

    if report and reports:
        console.print("\n[dim]rule_ceiling 提示：scripted 基线直调 check_refund_eligibility，[/dim]")
        console.print("[dim]而 golden 由同一套规则推导，故它在终态准确率上是**上界而非对手**。[/dim]")


@app.command("report")
def cmd_report(
    size: int = typer.Option(40),
    k: int = typer.Option(3),
    seed: int = typer.Option(7),
    customers: int = typer.Option(60),
    kind: str = typer.Option("scripted", help="scripted|llm|flaky|…"),
) -> None:
    """出正式报告（两臂 + pass^k + 分层 + 成本轴）。"""
    _ensure_endpoint_ready(kind)

    init_db(drop=True)
    seed_all(n_customers=customers, seed=seed)
    cases = build_eval_cases(size=size, seed=seed)

    from .eval import build_report, render_markdown, write_report  # noqa: F401

    all_results = {}
    for kind in (kind,):
        set_model_kind(kind)
        for arm in ("single", "multi"):
            results = run_suite(cases, arm=arm, kind=kind, k=k, progress=True)
            all_results[arm] = results
            # ★ rule_ceiling 必须写进报告里，而不是只放在 README 里口头说明。
            #   scripted 基线直调 check_refund_eligibility，而 golden 由同一套规则推导，
            #   所以它在终态准确率上是**上界**。不写清楚，读者会把它当成"模型成绩"。
            ceiling_note = (
                f"模型 kind={kind}：脚本化规则基线。golden 由 rules.decide 推导，"
                "而该基线直接调用同一套规则，故其终态准确率是这套评分标准下的**上界参考**，"
                "不是被超越的对手。要看模型能力须用 kind=llm（先跑 `endpoint --check` 自检）。"
            )
            rep = build_report(
                results, k=k,
                meta=_run_meta(arm=arm, kind=kind, size=len(cases), seed=seed),
                baseline={"note": ceiling_note, "scripted_pass_hat_k": pass_hat_k(results, k)},
            )
            j, md = write_report(rep, settings.eval_out_dir, name=f"final-{kind}-{arm}-k{k}")
            # ★ 别读 rep["overall"] —— build_report 是按臂分桶的 by_arm 结构，
            #   没有 overall 键。这里踩过一次 KeyError，报告已落盘但命令以退出码 1 结束。
            d = rep["by_arm"][arm]
            console.print(
                f"[green]✓[/green] {arm:6s} acc={d['accuracy']:.3f} "
                f"pass^{k}={d['pass_hat_k']:.3f} → {md.name}"
            )

    if len(all_results) == 2:
        s, m = all_results["single"], all_results["multi"]
        cs, cm = cost_summary(s), cost_summary(m)
        console.print(
            f"\n[bold]两臂对照[/bold]：same acc={accuracy(s):.3f} vs {accuracy(m):.3f}；"
            f"成本 {cs['avg_cost_cny']:.6f} vs {cm['avg_cost_cny']:.6f} "
            f"（multi/single = {cm['avg_cost_cny'] / max(cs['avg_cost_cny'], 1e-9):.1f}×）；"
            f"模型调用 {cs['avg_model_calls']:.1f} vs {cm['avg_model_calls']:.1f}；"
            f"工具调用 {cs.get('avg_tool_calls', 0):.1f} vs {cm.get('avg_tool_calls', 0):.1f}"
            f"（写操作同为 {cs.get('avg_write_calls', 0):.2f}）"
        )


# ── 端点 ────────────────────────────────────────────────────────


@app.command("endpoint")
def cmd_endpoint(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="端点档案名"),
    check: bool = typer.Option(False, "--check", help="真的发请求自检（会消耗极少 token）"),
    list_profiles: bool = typer.Option(False, "--list", help="列出全部档案"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="临时覆盖地址"),
    model: Optional[str] = typer.Option(None, "--model", help="临时覆盖模型名"),
) -> None:
    """查看/自检模型端点（真实 API、第三方中转、本地模型）。

    ★ 换任何新端点，先跑 `--check` 再跑评测。
    """
    from .config import ENDPOINT_PROFILES, resolve_endpoint
    from .endpoints import check_endpoint, last_check_matches, record_check, render_markdown

    if list_profiles:
        t = Table(title="端点档案")
        for col in ("档案", "默认模型", "地址", "单价（元/千 token）", "单价口径", "说明"):
            t.add_column(col, overflow="fold")
        for name, p in ENDPOINT_PROFILES.items():
            pin, pout = p.get("price_in"), p.get("price_out")
            price = "未声明" if pin is None else f"入 {pin} / 出 {pout}"
            # ★ 缺陷 ⑱：同一个地址后面可能挂着价差很大的多个模型。
            #   这一列说清"这个价是按地址算还是按模型算" —— 按模型算的档案，
            #   老路径必须模型名也对得上才会认领它（否则判"未声明"）。
            scope = "按模型" if str(p.get("price_scope") or "address") == "model" else "按地址"
            t.add_row(
                name,
                str(p.get("model") or "（须填写）"),
                str(p.get("base_url") or "（须填写）"),
                price,
                scope,
                str(p.get("note", ""))[:70],
            )
        console.print(t)
        return

    overrides: dict[str, str] = {}
    if base_url:
        overrides["base_url"] = base_url
    if model:
        overrides["model"] = model
    ep = resolve_endpoint(profile, **overrides) if (profile or overrides) else settings.endpoint

    _print(ep.redacted())

    missing = ep.missing()
    if missing:
        console.print(f"[yellow]还缺：[/yellow]{'、'.join(missing)}")
        console.print("[dim]填 .env 或设环境变量后再自检。[/dim]")
        return

    if not check:
        # 不真发请求时，至少把"上次自检记录是否匹配当前端点"讲清楚 ——
        # 否则用户会以为"看过配置了"等于"验证过了"。
        cached = last_check_matches(ep)
        console.print(
            f"上次自检：{'[green]通过且匹配当前端点[/green]' if cached else '[yellow]没有 / 端点已变[/yellow]'}"
        )
        console.print("[dim]加 --check 真发 3 次请求自检。[/dim]")
        return

    console.print("[dim]自检中（最多 3 次请求，不重试）…[/dim]")
    rep = check_endpoint(ep)
    console.print()
    console.print(Panel(render_markdown(rep), title="端点自检", border_style="cyan"))

    # ★ 自检完必须落盘（缺陷 ⑯）：`report` / `eval` 在**另一个进程**里
    #   靠这份记录判断端点验没验过。不写的话，这里四项全过、上面写着
    #   "可以跑评测"，而 `eval` 照旧拦下来说"还没通过自检" ——
    #   用户唯一出路是 LLM_ALLOW_UNVERIFIED=1，那恰好绕过了这道防线。
    path = record_check(ep, rep)
    if rep.ready:
        if path:
            console.print(f"[green]已记下这次自检[/green]（{path.name}），`eval` / `report` 不会再拦。")
        else:
            console.print(
                "[yellow]自检通过，但记录写不进 eval_out/ —— "
                "跑 eval 时会被要求重跑自检。[/yellow]"
            )


@app.command("smoke")
def cmd_smoke(
    ticket: Optional[int] = typer.Option(None, "--ticket", help="指定工单号，默认取第一条"),
    tenant: str = typer.Option("acme"),
    show_trace: bool = typer.Option(True, "--trace/--no-trace"),
) -> None:
    """单条冒烟：用当前端点真跑一张工单，打印工具调用轨迹。

    ★ 这是"接线通不通"的判据，**不是**效果判据。
      判据是轨迹里有没有 `get_ticket` / `check_refund_eligibility` 这类工具调用：
      有 = 模型在真的驱动 agent；没有 = 端点丢了 tool_calls（见 endpoints.py）。
    """
    if not settings.endpoint.is_configured:
        console.print("[red]端点没配好：[/red]" + "、".join(settings.endpoint.missing()))
        raise typer.Exit(code=1)

    set_model_kind("llm")
    t = ticket
    if t is None:
        rows = list_tickets(tenant, limit=1)
        if not rows:
            console.print("[red]库里没有工单，先跑 `seed`。[/red]")
            raise typer.Exit(code=1)
        t = int(rows[0]["ticket_id"])

    console.print(f"工单 {t}，端点 {settings.endpoint.model} @ {settings.endpoint.base_url}")
    events = stream_run(tenant, int(t))
    names: list[str] = []
    for ev in events:
        et = ev.get("event") or ev.get("type")
        if et in ("tool", "tool_call"):
            name = ev.get("name") or (ev.get("tool") or {}).get("name")
            if name:
                names.append(str(name))
        if show_trace:
            console.print(f"[dim]{json.dumps(ev, ensure_ascii=False, default=str)[:300]}[/dim]")

    console.print()
    if names:
        console.print(f"[green]✓ 轨迹里有 {len(names)} 次工具调用[/green]：{names}")
        console.print("[dim]接线是通的 —— 模型在真的驱动 agent。[/dim]")
    else:
        console.print(
            "[red]✗ 轨迹里一次工具调用都没有。[/red]\n"
            "最可能的原因：这个端点没有保留 tool_calls 字段。\n"
            "跑 `endpoint --check` 看第 2 项，那里会分辨'没保留'与'不支持 tools'。"
        )


# ── 运维 ────────────────────────────────────────────────────────


@app.command("info")
def cmd_info() -> None:
    """当前配置、模型、角色工具矩阵。"""
    _print(runtime_info())


@app.command("model-kind")
def cmd_model_kind(kind: Optional[str] = typer.Argument(None)) -> None:
    """查看或切换模型种类（scripted|llm|flaky|overprivileged）。"""
    if kind is None:
        console.print(f"当前：{get_model_kind()}")
        return
    set_model_kind(kind)
    console.print(f"已切换到：{get_model_kind()}")


@app.command("serve")
def cmd_serve(
    host: str = typer.Option(settings.api_host),
    port: int = typer.Option(settings.api_port),
    reload: bool = typer.Option(False),
    seed: int | None = typer.Option(
        None, "--seed", help="起服务前顺带灌数据（客户数），库里没数据时用"
    ),
) -> None:
    """起 HTTP 服务（含控制台前端，浏览器打开 http://host:port/ 即可）。

    ★ 这里必须自己建表 —— 踩过的真实缺陷：
    `serve` 原先只调 `uvicorn.run(...)`，不碰数据库。于是"新克隆仓库 →
    照文档起服务 → 打开页面"看到的是**每个列表都 500**（no such table:
    tickets）。服务本身是"起来了"（/health 正常返回），所以这个洞在
    "服务能不能起"这个检查里是隐形的 —— 又一段流程被拆成两半：
    起服务的人假设库已经建好，建库的人假设你会先跑 seed。

    `init_db()` 是幂等的（`create_all`），已有库不会被动到。
    """
    import uvicorn

    init_db(drop=False)

    # 库里没数据时给一句人话，而不是让用户对着空列表猜
    try:
        from .db.models import Ticket

        with session_scope() as s:
            from sqlalchemy import func, select

            n = s.execute(select(func.count()).select_from(Ticket)).scalar() or 0
        if n == 0:
            typer.echo(
                "⚠ 数据库里还没有工单，控制台会是空的。\n"
                f"  先灌数据：  ticket-agent seed --customers 60 --seed 7\n"
                f"  或直接起带数据的服务：  ticket-agent serve --seed 60"
            )
    except Exception:  # noqa: BLE001 —— 自检失败不该拦住起服务
        pass

    if seed:
        seed_all(n_customers=seed, seed=7)
        typer.echo(f"✓ 已灌入 {seed} 个客户的演示数据")

    typer.echo(f"\n控制台：http://{host}:{port}/   （浏览器直接打开）")
    typer.echo(f"接口文档：http://{host}:{port}/docs\n")
    uvicorn.run("ticket_agent.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
