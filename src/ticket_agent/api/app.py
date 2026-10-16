"""HTTP 服务层：FastAPI + SSE 流式 + 租户 token 鉴权。

分层：
- `service.py`  —— 业务逻辑（与框架无关，CLI / 评测 / HTTP 共用）
- `api/app.py`  —— HTTP 外壳（鉴权、参数校验、SSE 编码）

★ 鉴权设计（面试可讲）：
1. **Bearer token → tenant_id 的映射在服务端**。客户端永远不能"指定"自己
   要操作哪个租户 —— 请求体里根本没有 tenant_id 这个字段。
2. 映射放进 `request.state`，路由只从 `Depends(current_tenant)` 取。
   若某个路由忘了加依赖，**它拿不到租户，直接 500**，而不是静默地跨租户读数据。
3. 每个查询都经 `TenantRepo` 过滤（数据层第二道）。
   "纵深防御"在简历里是套话，在这里是两条独立成立的防线。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import PROJECT_ROOT, settings
from ..db.snapshot import diff_snapshots
from ..graph.nodes import get_model_kind, set_model_kind
from ..service import (
    approve_run,
    approve_ticket,
    dumps,
    get_ticket_detail,
    list_tickets,
    pending_human_tickets,
    run_store,
    runtime_info,
    stream_run,
    submit_customer_reply,
)

app = FastAPI(
    title="ticket-agent",
    description="售后工单处置 Agent —— 多租户、可评测、预算受控",
    version="0.1.0",
)

WEB_DIR = PROJECT_ROOT / "web"


# ══════════════════════════════════════════════════════════════
# 控制台（前端）
# ══════════════════════════════════════════════════════════════


@app.get("/", include_in_schema=False)
def console() -> Any:
    """Agent 控制台。

    ★ 为什么把前端挂在同一个服务上，而不是另起一个前端 dev server：
    同源就没有 CORS，也不需要在前端塞一个后端地址配置 ——
    演示时"起一个东西就能看"，比"先 npm run dev 再改 proxy"重要得多。
    前端本身是单个静态 HTML（见 web/index.html 的文件头说明）。

    文件不在时返回 404 而不是 500：`web/` 是被 .gitignore 之外的一部分，
    打包漏了它应该是"控制台没了"，不该把整个服务打成不可用。
    """
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="控制台文件缺失：web/index.html")
    # 不缓存：改完 HTML 刷新就能看到，不用清缓存
    return FileResponse(page, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


# ══════════════════════════════════════════════════════════════
# 鉴权
# ══════════════════════════════════════════════════════════════


def current_tenant(authorization: str = Header(default="")) -> str:
    """从 Bearer token 解析租户。

    ★ 失败一律 401，且**不区分**「token 不存在」与「token 有效但租户已停用」——
    区分会让攻击者能枚举有效 token。
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer token")
    token = authorization[7:].strip()
    tenant = settings.tenant_tokens.get(token)
    if not tenant:
        raise HTTPException(status_code=401, detail="token 无效")
    return tenant


# ══════════════════════════════════════════════════════════════
# 请求体
# ══════════════════════════════════════════════════════════════


class RunRequest(BaseModel):
    ticket_id: int = Field(..., ge=1, description="工单 ID")
    arm: str = Field("multi", pattern="^(single|multi)$", description="单 agent 或多 agent")
    user_input: str = Field("", max_length=2000, description="坐席补充说明")


class CustomerReplyRequest(BaseModel):
    """#2 多轮澄清闭环：客户对 `ask_customer` 提问的答复。"""

    reply: str = Field(..., min_length=1, max_length=2000, description="客户答复原文")
    arm: str = Field("multi", pattern="^(single|multi)$", description="第二轮用哪个臂跑")
    dry_run: bool = Field(
        False,
        description="只落库不重跑（预览这条答复会补齐哪些字段），用于排查" ,
    )


class ApproveRequest(BaseModel):
    decision: str = Field(..., pattern="^(approve_refund|reject|keep_pending)$")
    note: str = Field("", max_length=1000)
    amount: float | None = Field(None, ge=0, le=1_000_000)
    run_id: str = Field("", description="可选的关联 run（仅用于审计留痕）。跨进程审批可以不传")
    override: bool = Field(
        False,
        description=(
            "人工显式越过政策护栏（如 R5 已退过款、R6 生鲜不退）。"
            "默认 false —— 政策禁止的退款不会被批准，会返回 409；"
            "置 true 时放行但会在审计日志里留下 policy_override 记录"
        ),
    )


class ModelKindRequest(BaseModel):
    # ★ 与 agents.scripted.FAULT_MODELS 的 key 保持一致；漏一个就等于
    # 有个故障模型没法通过 HTTP 切换，而它本来是拿来演示评测框架的
    kind: str = Field(..., pattern="^(scripted|flaky|overprivileged|guesser|lenient|noncompliant|lucky|llm)$")


# ══════════════════════════════════════════════════════════════
# 端点
# ══════════════════════════════════════════════════════════════


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "model_kind": get_model_kind()}


@app.get("/meta")
def meta(_: str = Depends(current_tenant)) -> dict[str, Any]:
    """自述：当前模型、角色工具矩阵、阈值。演示时先打这个。"""
    return runtime_info()


@app.get("/tickets")
def tickets(
    tenant: str = Depends(current_tenant),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return list_tickets(tenant, limit=limit)


@app.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    try:
        return get_ticket_detail(tenant, ticket_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/pending")
def pending(tenant: str = Depends(current_tenant)) -> list[dict[str, Any]]:
    """人工待办队列（R4/R5/R6 转人工的工单）。"""
    return pending_human_tickets(tenant)


@app.post("/tickets/{ticket_id}/customer-reply")
def customer_reply(
    ticket_id: int,
    req: CustomerReplyRequest,
    tenant: str = Depends(current_tenant),
) -> dict[str, Any]:
    """客户答复到达（#2）：落库并以答复为输入重跑第二轮。

    ★ 与 `POST /runs` 的分工：`/runs` 是**第一轮**（客户原话进来），
    这里是**第二轮**（答复进来）。两者共用同一条图执行路径，差别只在
    输入是什么 —— 多一条专用端点，是为了让"问→等→答→办"这四段在
    HTTP 层也能分开审计，而不是塞进 `/runs` 的 `user_input` 里分不出来。
    """
    try:
        return submit_customer_reply(
            tenant,
            ticket_id,
            req.reply,
            arm=req.arm,
            dry_run=req.dry_run,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _sse(event: dict[str, Any]) -> str:
    """按 SSE 规范编码一帧。

    `event:` + `data:` 双行，客户端可以按事件名分流；
    空行结束一帧（少了它前端 onmessage 永远不触发 —— 最常见的 SSE 坑）。
    """
    name = event.get("type", "message")
    return f"event: {name}\ndata: {dumps(event)}\n\n"


@app.post("/runs/stream")
def runs_stream(req: RunRequest, tenant: str = Depends(current_tenant)) -> StreamingResponse:
    """跑一张工单，SSE 流式返回每个节点的进展。"""

    def gen() -> Iterator[str]:
        try:
            for ev in stream_run(tenant, req.ticket_id, arm=req.arm, user_input=req.user_input):
                yield _sse(ev)
        except LookupError as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 —— 生成器内异常必须转成事件，否则连接直接断
            yield _sse({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 缓冲，否则 SSE 会被攒成一坨再发 —— 流式的意义就没了
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/runs")
def runs(req: RunRequest, tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    """非流式跑完一张工单。"""
    last: dict[str, Any] = {}
    for ev in stream_run(tenant, req.ticket_id, arm=req.arm, user_input=req.user_input):
        if ev["type"] in ("final", "error", "run_started"):
            last = ev
        if ev["type"] == "final":
            return ev
        if ev["type"] == "error":
            raise HTTPException(status_code=500, detail=ev.get("error", "run failed"))
    return last


@app.get("/runs")
def list_runs(
    tenant: str = Depends(current_tenant),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    return [r.summary() for r in run_store.list(tenant, limit=limit)]


@app.get("/runs/{run_id}")
def get_run(run_id: str, tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    rec = run_store.get(run_id)
    if rec is None or rec.tenant_id != tenant:
        # ★ 跨租户读他人 run 报 404 而非 403：403 会泄露"这个 run_id 存在"
        raise HTTPException(status_code=404, detail="run 不存在")
    return rec.summary()


@app.post("/runs/{run_id}/approve")
def approve(run_id: str, req: ApproveRequest, tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    """人工裁决（HITL）。只有 pending_human 的工单可裁决。

    ★ 人工裁决**不能**绕过政策护栏：批准一笔 R5（已退过款）或 R6（生鲜不退）
    的退款会返回 409（`PolicyViolation` 是 `ValueError` 子类，由下面的映射兜住）。
    确需破例时传 `override: true`，此时会放行并在审计里留下 `policy_override`。
    """
    try:
        return approve_run(
            run_id,
            tenant,
            decision=req.decision,  # type: ignore[arg-type]
            note=req.note,
            amount=req.amount,
            override=req.override,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/tickets/{ticket_id}/approve")
def approve_by_ticket(
    ticket_id: int, req: ApproveRequest, tenant: str = Depends(current_tenant)
) -> dict[str, Any]:
    """人工裁决（★ 按工单定位，不依赖 run）。

    ★ 为什么需要它：run 记录活在 `RunStore`（进程内内存），而工单活在库里。
    在"生成 run 的进程"与"处理审批的进程"不是同一个的部署里
    （CLI 两条命令、多副本 API、agent worker 与 web 分离），
    只认 run_id 的审批接口会直接报"run 不存在"，而工单其实好端端地
    躺在待人工队列里。人工审批的自然主语是**哪张工单要判**，不是哪次运行。

    可选 `run_id` 字段：传了就记进审计与 override 记录（能对上是被哪次
    agent 运行转过来的人工），不传则只记工单 —— 护栏与留痕不受影响。
    """
    try:
        return approve_ticket(
            tenant,
            ticket_id,
            decision=req.decision,  # type: ignore[arg-type]
            note=req.note,
            amount=req.amount,
            override=req.override,
            run_id=req.run_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/admin/model-kind")
def set_kind(req: ModelKindRequest, _: str = Depends(current_tenant)) -> dict[str, Any]:
    """切换模型种类：scripted（离线可跑）/ llm（真实模型）/ flaky / overprivileged。

    flaky/overprivileged 是**故意留的故障模型** —— 用来演示评测框架
    真的能抓到错误（一个从不失败的评测框架没有价值）。
    """
    set_model_kind(req.kind)
    return {"ok": True, "model_kind": get_model_kind()}


@app.get("/eval/cases/{case_id}/verify")
def verify_case(case_id: str, tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    """按 case_id 复核一条 case 的当前终态与期望终态的差异（调试用）。"""
    from ..db import build_eval_cases

    cases = build_eval_cases(size=500, split="all")
    match = next((c for c in cases if c["case_id"] == case_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} 不存在")
    if match["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail=f"case {case_id} 不存在")
    from ..db.snapshot import export_snapshot
    from ..db.session import session_scope

    with session_scope() as s:
        actual = export_snapshot(s, tenant, match["ticket_id"])
    ok, diffs = diff_snapshots(actual, match["expected_state"])
    return {"case_id": case_id, "passed": ok, "diffs": diffs, "actual": actual, "expected": match["expected_state"]}
