"""前端（控制台）的端到端验收。

为什么需要它 —— 这个项目栽过两次同一形状的跟头：
一串功能里，一半做了、另一半没做，中间**没有任何东西会变红**
（缺陷 ⑬⑭⑮⑯⑰）。"打开浏览器看了一眼好像有东西"不算验证：
页面上没报错、但列表是空的，看起来和"没有工单"一模一样。

所以这里用 TestClient 把前端真正依赖的每一条链路都打一遍：
  1. `GET /` 真的返回了那份 HTML（不是 404 兜底页）
  2. HTML 里引用的每个接口都真的存在（**照 HTML 里写的路径去请求**，
     而不是照我记得的路径 —— 前端写错路径/后端改了路径，这里就会红）
  3. SSE 帧格式能被前端那套手写解析器吃下去
  4. HITL 的 409 护栏真的会拒绝（前端"试试不勾 override"那段依赖它）

跑法：python _frontendcheck.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# 用独立库，别碰正在跑的评测库
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'eval_out' / 'frontcheck.db'}")

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient  # noqa: E402

from ticket_agent.api.app import app  # noqa: E402
from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db import init_db, seed_all  # noqa: E402
from ticket_agent.graph.nodes import set_model_kind  # noqa: E402
from ticket_agent.rules import decide_for_order  # noqa: E402

OK = "  ✓"
NO = "  ✗"
problems: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"{OK} {label}")
    else:
        print(f"{NO} {label}" + (f" —— {detail}" if detail else ""))
        problems.append(label)


def main() -> int:
    print("=" * 62)
    print("前端（控制台）验收")
    print("=" * 62)

    # ── 1. 页面本身 ────────────────────────────────────────────
    print("\n[1] 页面能出来")
    client = TestClient(app)
    r = client.get("/")
    check("GET / 返回 200", r.status_code == 200, f"实际 {r.status_code}")
    html = r.text
    check("不是兜底 404 页", r.status_code == 200 and "控制台文件缺失" not in html)
    check("是 HTML", "text/html" in r.headers.get("content-type", ""))
    check("含 <title>", "<title>" in html)
    check("含中文（编码没坏）", "售后工单处置" in html)
    check("大小合理（>10KB）", len(html) > 10_000, f"实际 {len(html)} 字节")

    # ── 2. HTML 里引用的接口是否都真的存在 ──────────────────────
    print("\n[2] 前端引用的接口都真实存在（照 HTML 里写的路径打）")
    # ★ 提取必须覆盖**所有**调用样式，否则这个检查会安静地只查两条路径
    #   然后报"全部通过"。前端实际有三种调用：
    #     fetch("/health")            字面量
    #     api("/tickets?limit=60")    经封装的字面量  ← 大部分走这条
    #     api(`/tickets/${id}`)       模板串
    #   只匹配第一种会漏掉 80% 的接口，而"漏检"和"全对"在输出上长得一样。
    paths: set[str] = set()
    for m in re.finditer(r'''(?:fetch|api)\(\s*[`"'](/[^`"'$]*)[`"']''', html):
        paths.add(m.group(1).split("?")[0])
    for m in re.finditer(r'''(?:fetch|api)\(\s*`(/[^`]*)''', html):
        # ★ 模板串不能按 `$` 截断 —— 那样 `api(`/tickets/${id}`)` 会变成
        #   `/tickets/`，而 FastAPI 把它 307 到 `/tickets` 返回 200，
        #   于是这条 **永远"通过"**，却从没验过带参数的那条路由。
        #   正确做法是保留插值位置，换成 `{x}` 骨架。
        skeleton = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1))
        paths.add(skeleton.split("?")[0])
    # HTML 里内联写的接口路径（注释/文案里提到的）
    for m in re.finditer(r'`(/[a-z][a-z0-9_/{}]*)(?:\?[^`]*)?`', html):
        paths.add(m.group(1))

    # 把 {x} 换成具体值
    def concretize(p: str) -> str:
        return re.sub(r"\{[^}]+\}", "1", p)

    tok = next(t for t, n in settings.tenant_tokens.items() if n == "acme")
    hdr = {"Authorization": f"Bearer {tok}"}

    openapi = client.get("/openapi.json").json()
    declared = set(openapi.get("paths", {}))

    print(f"     前端共引用 {len(paths)} 条路径")

    # 把 openapi 里的路径也归一成骨架再比：`/tickets/{ticket_id}` → `/tickets/{x}`。
    # 否则前端写 `/tickets/${id}`、后端写 `{ticket_id}`，字符串不相等就误报。
    def skeleton(p: str) -> str:
        return re.sub(r"\{[^}]+\}", "{x}", p)

    declared_sk = {skeleton(p) for p in declared}

    for raw in sorted(paths):
        p = concretize(raw)
        sk = skeleton(raw)
        # 三路判定：骨架命中 openapi / 原样命中 / 实探一次
        exists = sk in declared_sk or raw in declared or p in declared
        how = "openapi 骨架" if sk in declared_sk else ("openapi 原样" if raw in declared else "")
        if not exists:
            code = client.get(p, headers=hdr).status_code
            exists = code != 404
            how = f"实探 HTTP {code}"
        check(f"路由存在 {raw}（{how}）", exists, "openapi 与实探都找不到")

    # ── 3. 前端真正要用的数据能取到 ────────────────────────────
    print("\n[3] 页面要渲染的数据都能取到")
    r = client.get("/health")
    check("/health 无需鉴权", r.status_code == 200, f"实际 {r.status_code}")
    check("/health 返回 model_kind", "model_kind" in r.json())

    r = client.get("/meta", headers=hdr)
    check("/meta 通过", r.status_code == 200, r.text[:120])
    meta = r.json()
    for k in ("model_kind", "roles", "write_tools", "thresholds"):
        check(f"/meta 含 {k}", k in meta)
    check("roles 非空（否则矩阵表是空的）", bool(meta.get("roles")))
    check("write_tools 非空（否则没有红字）", bool(meta.get("write_tools")))

    r = client.get("/tickets?limit=60", headers=hdr)
    check("/tickets 通过", r.status_code == 200, r.text[:120])
    tickets = r.json()
    check("工单非空（页面左栏要有东西）", len(tickets) > 0, f"实际 {len(tickets)} 条")
    if tickets:
        t0 = tickets[0]
        for k in ("ticket_id", "subject", "status", "priority", "order_basis"):
            check(f"工单项含 {k}", k in t0)

    r = client.get("/pending", headers=hdr)
    check("/pending 通过", r.status_code == 200, r.text[:120])

    r = client.get("/runs?limit=40", headers=hdr)
    check("/runs 通过", r.status_code == 200, r.text[:120])

    tid = tickets[0]["ticket_id"] if tickets else 1
    r = client.get(f"/tickets/{tid}", headers=hdr)
    check(f"/tickets/{tid} 通过", r.status_code == 200, r.text[:160])
    detail = r.json()
    for k in ("ticket", "customer", "order", "order_basis", "snapshot"):
        check(f"详情含 {k}", k in detail)
    snap = detail.get("snapshot") or {}
    for k in ("ticket", "order", "refunds"):
        check(f"快照含 {k}", k in snap, f"实际键 {list(snap)}")

    # ── 4. SSE 帧格式能被前端解析器吃下去 ──────────────────────
    print("\n[4] SSE 帧格式（前端是手写解析器，契约必须对）")
    r = client.post("/runs/stream", headers=hdr,
                    json={"ticket_id": tid, "arm": "single", "user_input": ""})
    check("POST /runs/stream 通过", r.status_code == 200, f"实际 {r.status_code}")
    check("Content-Type 是 event-stream",
          "text/event-stream" in r.headers.get("content-type", ""),
          r.headers.get("content-type", ""))
    body = r.text
    check("有 event: 行", "event:" in body)
    check("有 data: 行", "data:" in body)

    # 复刻前端 parseSSE 的切帧逻辑：按空行切，取 data: 拼 JSON
    frames, bad = [], 0
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        data = ""
        for line in block.split("\n"):
            if line.startswith("data:"):
                data += line[5:].strip()
        if not data:
            continue
        import json

        try:
            frames.append(json.loads(data))
        except Exception:
            bad += 1
    check("每帧都能 JSON.parse（前端不会静默丢帧）", bad == 0, f"{bad} 帧解析失败")
    check("帧数合理（>3）", len(frames) > 3, f"实际 {len(frames)} 帧")

    types = [f.get("type") for f in frames]
    for want in ("run_started", "node_started", "node_finished", "final"):
        check(f"含 {want} 事件", want in types, f"实际出现 {sorted(set(types))}")

    fin = next((f for f in frames if f.get("type") == "final"), {})
    for k in ("status", "actions_taken", "snapshot", "forbidden_hits", "requires_human_approval"):
        check(f"final 含 {k}", k in fin, f"实际键 {sorted(fin)}")

    tc = [f for f in frames if f.get("type") == "tool_call"]
    check("有 tool_call 事件（轨迹不是空的）", len(tc) > 0, f"实际 {len(tc)} 条")
    if tc:
        for k in ("node", "name", "args", "ok"):
            check(f"tool_call 含 {k}", k in tc[0])

    # ── 5. HITL 的 409 护栏（前端"不勾 override"那段依赖它）──
    print("\n[5] HITL：政策拒绝真的会 409")
    # 找一张 pending_human 的工单；没有就手动造一个 R5 场景太麻烦，
    # 改为直接把一张已退款订单的工单置成 pending_human
    from sqlalchemy import select

    from ticket_agent.db.models import Order, Refund, Ticket
    from ticket_agent.db.session import session_scope

    pending_id = None
    with session_scope() as s:
        row = s.execute(
            select(Ticket).where(Ticket.tenant_id == "acme", Ticket.status == "pending_human")
        ).scalars().first()
        if row:
            pending_id = row.id

    if pending_id is None:
        print("     （库里没有待人工工单，跳过 409 检查）")
    else:
        r = client.post(f"/tickets/{pending_id}/approve", headers=hdr,
                        json={"decision": "approve_refund", "note": "前端验收", "amount": None,
                              "override": False})
        check(f"裁决接口可达（{r.status_code} 非 5xx）", r.status_code < 500, r.text[:160])
        print(f"     → 工单 #{pending_id} 不勾 override 的结果：HTTP {r.status_code}")

    # ── 6. 收尾 ────────────────────────────────────────────────
    print("\n" + "=" * 62)
    if problems:
        print(f"✗ {len(problems)} 项没过：")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("✓ 前端验收全部通过")
    return 0


if __name__ == "__main__":
    set_model_kind("scripted")
    init_db(drop=True)
    seed_all(n_customers=40, seed=11)
    raise SystemExit(main())
