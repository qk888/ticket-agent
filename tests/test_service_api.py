"""服务层与 HTTP 层的测试。

这些测试覆盖的是"接线"而不是"智能"：
- 鉴权与租户隔离（这是安全边界，必须有断言而不是靠人肉 review）
- SSE 事件契约（前端就靠这个契约渲染）
- HITL 审批的状态机（只允许 pending_human → resolved/rejected，且不可重复）
- run 摘要里"是否等人"的判定（踩过坑：agent 主动转人工被报成不需要人工）

不做"模型答得对不对"的测试 —— 那是 eval 层的事，靠固定数据集
和 pass^k 衡量，写成 pytest 断言反而会把不确定的东西变成脆弱测试。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ticket_agent.api.app import app
from ticket_agent.config import settings
from ticket_agent.db import build_eval_cases, init_db, seed_all
from ticket_agent.graph.nodes import set_model_kind
from ticket_agent.service import RunRecord, RunStore, run_once

ACME = next(t for t, name in settings.tenant_tokens.items() if name == "acme")
GLOBEX = next(t for t, name in settings.tenant_tokens.items() if name == "globex")


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    set_model_kind("scripted")
    init_db(drop=True)
    seed_all(n_customers=40, seed=11)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── 鉴权 ────────────────────────────────────────────────────────


def test_health_needs_no_auth(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize("hdr", [{}, {"Authorization": "Bearer"}, {"Authorization": "Bearer nope"},
                                 {"Authorization": "nope"}])
def test_bad_auth_is_401(client: TestClient, hdr: dict[str, str]) -> None:
    r = client.get("/meta", headers=hdr)
    assert r.status_code == 401


def test_unknown_token_and_revoked_token_are_indistinguishable(client: TestClient) -> None:
    """401 的响应体必须一致。

    如果"token 不存在"和"租户已停用"给出不同提示，
    攻击者就能靠错误信息枚举出租户是否真实存在。
    """
    a = client.get("/meta", headers=_hdr("definitely-not-a-token"))
    b = client.get("/meta", headers=_hdr(""))
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


# ── 租户隔离 ────────────────────────────────────────────────────


def test_ticket_lists_do_not_overlap(client: TestClient) -> None:
    # /tickets 返回的是裸 list（不是 {"tickets": [...]}）—— 照实际契约写
    a = {t["ticket_id"] for t in client.get("/tickets", headers=_hdr(ACME)).json()}
    b = {t["ticket_id"] for t in client.get("/tickets", headers=_hdr(GLOBEX)).json()}
    assert a and b
    assert a & b == set(), "两个租户看到了同一张工单 —— 租户过滤漏了"


def test_cross_tenant_ticket_read_is_404_not_403(client: TestClient) -> None:
    """跨租户必须 404。403 等于告诉对方"这个 id 存在，只是你没权限"。"""
    other = client.get("/tickets", headers=_hdr(GLOBEX)).json()[0]["ticket_id"]
    r = client.get(f"/tickets/{other}", headers=_hdr(ACME))
    assert r.status_code == 404


def test_tenant_id_is_not_accepted_from_body(client: TestClient) -> None:
    """租户只能来自 token —— 请求体里塞 tenant_id 不应生效。"""
    own = client.get("/tickets", headers=_hdr(ACME)).json()[0]["ticket_id"]
    r = client.post(
        "/runs",
        headers=_hdr(ACME),
        json={"ticket_id": own, "arm": "single", "tenant_id": "globex"},
    )
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme", "请求体里的 tenant_id 竟然生效了"


# ── 运行与 SSE ──────────────────────────────────────────────────


def test_run_returns_summary_and_moves_ticket(client: TestClient) -> None:
    """跑一次必须给出摘要，且工单状态要落到期望终态。

    ★ 不能取 `GET /tickets` 的第一条来测：那个顺序与"这条 case 该不该关单"
      无关，实测造数一改，第一条就变成 `ambiguous` —— 而它的**正确**终态
      就是保持 open（客户名下几笔订单，agent 不该猜是哪一笔）。
      当时的断言写成"状态 != open"，于是把 agent 的正确行为判成了失败。

      正确做法是按 case 的 `expected_state` 挑一条**有确定终态**的工单，
      这样断言的才对得上："跑完 = 落到它该落的终态"。
    """
    cases = build_eval_cases(size=40, seed=11)
    target = next(
        c for c in cases
        if c["tenant_id"] == "acme"
        and c["expected_state"]["ticket"]["status"] != "open"
    )
    tid = target["ticket_id"]

    body = client.post("/runs", headers=_hdr(ACME), json={"ticket_id": tid, "arm": "single"}).json()
    assert body["status"] in {"finished", "awaiting_human"}
    assert "budget" in body and "snapshot" in body

    detail = client.get(f"/tickets/{tid}", headers=_hdr(ACME)).json()
    want = target["expected_state"]["ticket"]["status"]
    got = detail["ticket"]["status"]
    assert got == want, (
        f"{target['case_id']}({target['condition']}) 期望终态 {want!r}，实际 {got!r}"
    )


def test_sse_event_sequence_contract(client: TestClient) -> None:
    """前端依赖这个事件顺序：run_started 打头、done 收尾。"""
    tid = client.get("/tickets", headers=_hdr(ACME)).json()[-1]["ticket_id"]
    with client.stream("POST", "/runs/stream", headers=_hdr(ACME),
                       json={"ticket_id": tid, "arm": "multi"}) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        raw = "".join(r.iter_text())

    names = [ln[len("event: "):] for ln in raw.splitlines() if ln.startswith("event: ")]
    assert names[0] == "run_started"
    assert names[-1] == "done"
    assert "final" in names
    assert names.count("run_started") == 1


def test_run_summary_reports_human_when_agent_escalates() -> None:
    """agent 主动转人工时，摘要必须说"需要人工"。

    踩过的坑：`summary()` 直接读 state 上的 `requires_human_approval`，
    而那个布尔只有**预算熔断**路径才会置真 —— agent 正常调
    `escalate_to_human` 时它是 False，于是状态是 awaiting_human
    却报"不需要人工"，两个字段自相矛盾。
    """
    set_model_kind("scripted")
    cases = build_eval_cases(size=40, seed=11)
    esc = _cases_where(cases, lambda t: t == "pending_human",
                       label="转人工（ticket.status=pending_human）")
    c = esc[0]
    rec = run_once(c["tenant_id"], c["ticket_id"], arm="single")
    assert rec.status == "awaiting_human"
    assert rec.summary()["requires_human_approval"] is True


def test_writes_are_attributed_and_counted() -> None:
    """动作记录不能是空的。

    踩过的坑：`stream_run` 把各节点增量用 `dict.update` 覆盖合并，
    而 `actions_taken` 在 state 上是 operator.add —— 最后一个节点
    （verifier，不调写工具）的空列表把 resolver 的动作抹掉了，
    摘要显示"状态：awaiting_human / 动作：无"。
    """
    set_model_kind("scripted")
    cases = build_eval_cases(size=40, seed=11)
    paid = _cases_where(cases, lambda t: t == "resolved", label="结案（ticket.status=resolved）")
    # 走 multi 臂：它经过 verifier 节点，正是覆盖语义会出问题的那条路径
    rec = run_once(paid[0]["tenant_id"], paid[0]["ticket_id"], arm="multi")
    acts = rec.summary()["actions_taken"]
    assert acts, "case 期望结案，但动作记录为空 —— 合并语义又错了"
    assert any("close_ticket" in a for a in acts)


def _cases_where(cases: list[dict], pred, *, label: str) -> list[dict]:
    """按 golden 终态筛 case；筛不到就明确报错，而不是抛 StopIteration。"""
    hit = [c for c in cases if pred((c["expected_state"].get("ticket") or {}).get("status"))]
    assert hit, f"这次 seed 没造出{label}的 case，测试失去意义（共 {len(cases)} 条）"
    return hit


# ── HITL 审批状态机 ─────────────────────────────────────────────


def test_approve_state_machine(client: TestClient) -> None:
    """只允许 pending_human → 处置，且不可重复裁决。"""
    set_model_kind("scripted")
    cases = build_eval_cases(size=40, seed=11)
    esc = _cases_where(cases, lambda t: t == "pending_human", label="转人工")
    c = esc[0]
    tenant, tid = c["tenant_id"], c["ticket_id"]
    tok = ACME if tenant == "acme" else GLOBEX
    other = GLOBEX if tenant == "acme" else ACME

    run = client.post("/runs", headers=_hdr(tok), json={"ticket_id": tid, "arm": "single"}).json()
    assert run["status"] == "awaiting_human"
    run_id = run["run_id"]

    # 跨租户审批不可达
    assert client.post(f"/runs/{run_id}/approve", headers=_hdr(other),
                       json={"decision": "reject"}).status_code == 404

    ok = client.post(f"/runs/{run_id}/approve", headers=_hdr(tok), json={"decision": "reject"})
    assert ok.status_code == 200

    # 同一个工单不能裁决两次
    again = client.post(f"/runs/{run_id}/approve", headers=_hdr(tok), json={"decision": "reject"})
    assert again.status_code == 409


def _cases_by_condition(cases: list[dict], cond: str) -> list[dict]:
    """按 condition 筛 case；筛不到就明确报错，而不是抛 StopIteration。"""
    hit = [c for c in cases if c.get("condition") == cond]
    assert hit, f"这次 seed 没造出 {cond} 的 case，测试失去意义（共 {len(cases)} 条）"
    return hit


def _reseed() -> list[dict]:
    """重新造一份干净数据并返回评测 case。

    ★ 必须这么做：本模块的 fixture 是 module scope，而前面的测试已经跑过
    agent（把不少工单关掉了），`build_eval_cases` 是按库里"当前还 open 的
    工单"生成的 —— 于是同一个 seed 在文件不同位置会给出**不同的 case 集**，
    条件分布也跟着变（实测从 40 条缩到 21 条，R5 直接消失）。

    ★ 用 60/7 这套：它是 `_hitlcheck.py` / README 指标表用的同一份数据，
    seed=11（40 客户）造出来只有 21 张工单、且**一条 R5 都没有**，
    护栏测试需要 R5/R6/R4 各至少一条。
    """
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    return build_eval_cases(size=40, seed=7)


def _run_to_pending_human(c: dict, tok: str, client: TestClient) -> str:
    """把 case 跑成 pending_human，返回 run_id。"""
    run = client.post("/runs", headers=_hdr(tok), json={"ticket_id": c["ticket_id"], "arm": "single"}).json()
    assert run["status"] == "awaiting_human", f"{c['case_id']} 期望转人工，实际 {run['status']}"
    return run["run_id"]


def test_human_approval_cannot_bypass_policy_guardrail(client: TestClient) -> None:
    """人工审批不能把政策禁止的退款批出去。

    踩过的坑（README §4.2 缺陷 ⑥）：`escalate` 这一个动作名下藏着两种语义，
    旧代码把它们一视同仁，于是人工作业时给一单已退过的订单又退了一次 ——
    47.53 元的图书退成了 95.06 元。人工能压过机器，但不能压过政策。

    正确的分野：
      R4（黑名单/高风险/大额）= **推迟**：政策允许退，人工审完可以批。
      R5（已退过）/ R6（生鲜）= **禁止**：政策不允许这笔钱出去，人工也不能批。
    """
    set_model_kind("scripted")
    cases = _reseed()

    # ── R5：已退过一次 → 人工批准必须被拒 ──
    c5 = _cases_by_condition(cases, "R5:escalate")[0]
    tok5 = ACME if c5["tenant_id"] == "acme" else GLOBEX
    rid5 = _run_to_pending_human(c5, tok5, client)
    blocked = client.post(f"/runs/{rid5}/approve", headers=_hdr(tok5),
                          json={"decision": "approve_refund"})
    assert blocked.status_code == 409, (
        f"R5 工单的人工批准应当被拒（409），实际 {blocked.status_code} —— 政策护栏没生效"
    )
    # 被拒不能留下副作用，也不能把工单静默关掉（否则人工待办队列里就查不到它了）
    detail = client.get(f"/tickets/{c5['ticket_id']}", headers=_hdr(tok5)).json()
    assert detail["ticket"]["status"] == "pending_human", "被拒的操作却把工单关掉了"
    assert detail["snapshot"]["order"]["refund_count"] == 1, "被拒了，钱还是出去了"

    # 同一个 run 显式 override 仍然放行，且必须留下标记 —— 破例是允许的，但必须留痕
    forced = client.post(f"/runs/{rid5}/approve", headers=_hdr(tok5),
                         json={"decision": "approve_refund", "override": True})
    assert forced.status_code == 200, f"显式 override 应当放行，实际 {forced.status_code}"
    assert forced.json()["policy_override"] is True, "override 生效了却没在返回值里标记"

    # ── R6：生鲜完全不退 → 同样被拒 ──
    c6 = _cases_by_condition(cases, "R6:escalate")[0]
    tok6 = ACME if c6["tenant_id"] == "acme" else GLOBEX
    rid6 = _run_to_pending_human(c6, tok6, client)
    assert client.post(f"/runs/{rid6}/approve", headers=_hdr(tok6),
                       json={"decision": "approve_refund"}).status_code == 409, "生鲜订单被批退了"

    # ── R4：推迟不是禁止 → 人工批准必须正常放行，且不得被误标 override ──
    c4 = _cases_by_condition(cases, "R4:escalate")[0]
    tok4 = ACME if c4["tenant_id"] == "acme" else GLOBEX
    rid4 = _run_to_pending_human(c4, tok4, client)
    ok = client.post(f"/runs/{rid4}/approve", headers=_hdr(tok4), json={"decision": "approve_refund"})
    assert ok.status_code == 200, (
        f"R4 是推迟不是禁止，人工批准不该被拦（实际 {ok.status_code}）—— 护栏把正常审批也挡了"
    )
    assert ok.json()["policy_override"] is False, "R4 本就是允许退的，不该记成破例"


def test_no_overdraft_under_any_approval_path(client: TestClient) -> None:
    """不变量：任何路径下，已批准退款总额不得超过订单金额。

    这是缺陷 ⑥ 的直接反面 —— 当时 `acme-t17` 的 47.53 元订单变成了 95.06 元。
    """
    set_model_kind("scripted")
    cases = _reseed()
    esc = _cases_where(cases, lambda t: t == "pending_human", label="转人工")
    checked = 0
    for c in esc:
        tok = ACME if c["tenant_id"] == "acme" else GLOBEX
        run = client.post("/runs", headers=_hdr(tok),
                          json={"ticket_id": c["ticket_id"], "arm": "single"}).json()
        if run["status"] != "awaiting_human":
            continue
        resp = client.post(f"/runs/{run['run_id']}/approve", headers=_hdr(tok),
                           json={"decision": "approve_refund"})
        # 409（政策禁止）与 200（允许）都是合法结局；只要钱没超就是对的
        assert resp.status_code in (200, 409), f"{c['case_id']} 意外状态码 {resp.status_code}"
        snap = client.get(f"/tickets/{c['ticket_id']}", headers=_hdr(tok)).json()["snapshot"]
        approved = sum(r["amount"] for r in snap["refunds"] if r["status"] == "approved")
        amount = float((snap.get("order") or {}).get("amount") or 0.0)
        assert approved <= amount + 0.01, (
            f"{c['case_id']} 超退：已批准 {approved} 元 > 订单 {amount} 元（缺陷 ⑥ 复现）"
        )
        checked += 1
    assert checked, "没有任何转人工的 case 被检查到，这个不变量测试失去意义"


def test_approve_by_ticket_survives_process_boundary(client: TestClient) -> None:
    """缺陷 ⑦：人工裁决不能依赖 RunStore（进程内内存）里的 run。

    ★ 症状：`ticket-agent pending` 列的是库里的工单，而 `approve <run_id>` 要的是
    RunStore 里的 run —— 两者分属不同进程时，最自然的人工流程直接断掉
    （实测 `pending` 列出 1 张工单，`approve <run_id>` 报 `run ... 不存在`）。

    这里用"换一个进程"的等价物来复现：**把 run 从 store 里彻底移除**，
    模拟"这张工单是别的进程跑出来的"。按工单裁决必须照样工作。
    """
    from ticket_agent.service import run_store

    set_model_kind("scripted")
    cases = _reseed()
    c = _cases_by_condition(cases, "R4:escalate")[0]  # R4 = 推迟，人工可以批
    tok = ACME if c["tenant_id"] == "acme" else GLOBEX
    rid = _run_to_pending_human(c, tok, client)

    # 模拟进程边界：run 记录不复存在（换成新进程就是这种情况）
    run_store._runs.pop(rid, None)
    assert client.get(f"/runs/{rid}", headers=_hdr(tok)).status_code == 404, "run 应当已不可见"

    # 按 run 裁决 —— 此时必然失败，且必须是 404（而不是"工单不存在"这类误导信息）
    stale = client.post(f"/runs/{rid}/approve", headers=_hdr(tok), json={"decision": "approve_refund"})
    assert stale.status_code == 404, f"run 已随进程消失，按 run 裁决应 404，实际 {stale.status_code}"

    # ★ 按工单裁决 —— 这才是人工审批的自然主语，必须能工作
    ok = client.post(
        f"/tickets/{c['ticket_id']}/approve",
        headers=_hdr(tok),
        json={"decision": "approve_refund", "note": "跨进程按工单裁决"},
    )
    assert ok.status_code == 200, f"按工单裁决失败：{ok.status_code} {ok.text[:200]}"
    body = ok.json()
    assert body["ticket_id"] == c["ticket_id"]
    assert body["run_id"] is None, "没有关联 run 时不该编造一个 run_id"
    assert body["snapshot"]["ticket"]["status"] == "resolved"

    # 状态真相在库里，不在 RunStore —— 再查工单应当已是终态
    detail = client.get(f"/tickets/{c['ticket_id']}", headers=_hdr(tok)).json()
    assert detail["ticket"]["status"] == "resolved"


def test_approve_by_ticket_is_tenant_scoped(client: TestClient) -> None:
    """按工单裁决同样受租户隔离约束（不能靠 ticket_id 跨租户）。"""
    set_model_kind("scripted")
    cases = _reseed()
    c = _cases_by_condition(cases, "R4:escalate")[0]
    tok = ACME if c["tenant_id"] == "acme" else GLOBEX
    other = GLOBEX if tok is ACME else ACME
    _run_to_pending_human(c, tok, client)

    resp = client.post(f"/tickets/{c['ticket_id']}/approve", headers=_hdr(other),
                       json={"decision": "approve_refund"})
    # 报 404 而不是 403：403 会泄露"这个 ticket_id 存在"
    assert resp.status_code == 404, f"跨租户按工单裁决应 404，实际 {resp.status_code}"


def test_run_store_is_bounded() -> None:
    """内存运行表不能无限涨。"""
    store = RunStore(max_runs=3)
    for i in range(10):
        rec = RunRecord(run_id=f"r{i}", tenant_id="acme", ticket_id=i, arm="single")
        rec.status = "finished"  # 终态才会被淘汰，运行中的永远保留
        store.add(rec)
    assert len(store.list("acme", limit=100)) <= 3
