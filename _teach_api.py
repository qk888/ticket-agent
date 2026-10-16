"""自己动手测一遍前后端 —— 教学版。

这个脚本分四段，每段都是**只读**或**可回滚**的，不会污染评测数据：

  第 1 段  后端 CLI：起服务前先确认它自己知道该建表
  第 2 段  后端 API：不起浏览器，直接用 Python 打 12 个接口
  第 3 段  人工审批：扮演"人工"裁决一笔退款（HITL 的核心动作）
  第 4 段  前端：打开网页控制台你能点什么、每下点在调什么接口

用法：
    python _teach_api.py            # 第 1+2+3 段（不起服务，直接打 app）
    python _teach_api.py --serve    # 额外起一个真 HTTP 服务（127.0.0.1:8199）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "eval_out" / "teach.db"

# ★ 必须在 import ticket_agent 之前设好，否则会连到评测库
os.environ["DATABASE_URL"] = f"sqlite:///{DB.as_posix()}"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from ticket_agent.api.app import app as fastapi_app  # noqa: E402
from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.db.seed import seed_all  # noqa: E402
from ticket_agent.config import settings  # noqa: E402


def h(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main() -> int:
    serve = "--serve" in sys.argv

    # ── 第 0 段：造一份你自己的数据 ────────────────────────────
    h("第 0 段：造数据（独立库 teach.db，评测库一个字节都不会动）")
    if DB.exists():
        DB.unlink()
    init_db(drop=True)
    stats = seed_all(n_customers=40, seed=7)
    print("造好了：", {k: v for k, v in stats.items() if k != "text_source_corpus"})
    print(f"库文件：{DB}")

    # ★ 注意是 Bearer，不是裸 token（我第一次就写错了，拿到 401）
    client = TestClient(
        fastapi_app,
        headers={"authorization": "Bearer tok-acme"},
        raise_server_exceptions=False,
    )

    # ── 第 1 段：先打两个"体检"接口 ───────────────────────────
    h("第 1 段：/health 和 /meta —— 服务活着吗？它认哪个模型？")
    r = client.get("/health")
    print("GET /health →", r.status_code, r.json())
    r = client.get("/meta")
    meta = r.json()
    print("GET /meta →", r.status_code, "字段：", list(meta))
    print(f"   当前模型类型：{meta.get('model_kind')}  模型名：{meta.get('model_name')}")
    print(f"   有 API key 吗：{meta.get('has_api_key')}")
    print("   ★ model_kind=scripted 表示用内置假模型（不花钱、不需要 key）；")
    print("     =llm 才会真调远程模型。这两个字决定了你接下来看到的是真跑还是演示。")
    print("   401 是什么？token 没带或带错格式 —— 必须是 Authorization: Bearer tok-acme。")
    assert r.status_code == 200, "401 说明 token 格式错了（要 Bearer 前缀）"

    # ── 第 2 段：列工单 → 看一张 → 跑一张 ─────────────────────
    h("第 2 段：核心三连 —— 列表 / 详情 / 处置")
    r = client.get("/tickets")
    tickets = r.json()
    if isinstance(tickets, dict):
        tickets = tickets.get("tickets", [])
    print(f"GET /tickets → {r.status_code}，共 {len(tickets)} 张工单")
    first = tickets[0]
    tid = first["ticket_id"]
    print(f"   第一张：id={tid} status={first.get('status')} intent={first.get('intent')}")
    print(f"           order_basis={first.get('order_basis')}  ← 它怎么找到关联订单的")

    r = client.get(f"/tickets/{tid}")
    detail = r.json()
    print(f"GET /tickets/{tid} → {r.status_code}，字段：{list(detail)[:10]}")
    body = detail.get("ticket", detail).get("body") or detail.get("body") or ""
    print("   客户原话：", str(body)[:70], "…")

    print(f"\nPOST /runs/stream（ticket_id={tid}, arm=single）…")
    with client.stream(
        "POST", "/runs/stream", json={"ticket_id": tid, "arm": "single"}
    ) as resp:
        raw = list(resp.iter_lines())
    events = [json.loads(line[6:]) for line in raw if line.startswith("data: ")]
    print(f"   SSE 事件 {len(events)} 条，类型序列：", [e.get("type") or e.get("event") for e in events][:14])
    final = events[-1] if events else {}
    print("   最后一条：", str({k: v for k, v in final.items() if k in ("status", "action", "passed", "state")})[:200])
    print("   ★ SSE = 服务器一边算一边把日志推给你，比「转圈等三秒再看结果」更能看清它在想什么。")

    # ── 第 3 段：多轮澄清（第 2 轮客户答复）──────────────────
    h("第 3 段：多轮澄清 —— 客户答复之后接着办")
    r = client.post(
        f"/tickets/{tid}/customer-reply",
        json={"reply": "我要退货，鞋子磨脚，照片我发你了", "arm": "single", "dry_run": True},
    )
    print(f"POST /tickets/{tid}/customer-reply（dry_run）→ {r.status_code}")
    print("   ", str(r.json())[:300])
    print("   ★ dry_run=True 只落库不重跑 —— 用来看「这句答复会补齐哪些字段」。")

    # ── 第 4 段：人工审批（HITL）─────────────────────────────
    h("第 4 段：人工审批 —— 这个项目最该看的动作")
    # 教学数据用 60 客户那档（40 客户档实测 0 张待人工，别在这上面浪费时间）
    init_db(drop=True)
    seed_all(n_customers=60, seed=7)
    r = client.get("/pending")
    pending = r.json()
    if isinstance(pending, dict):
        pending = pending.get("pending", [])
    print(f"重新灌了 60 客户数据。GET /pending → {r.status_code}，{len(pending)} 张待人工")
    if pending:
        pt = pending[0]
        pid = pt["ticket_id"]
        print(f"   第一张：id={pid} status={pt.get('status')} intent={pt.get('intent')}")
        print(f"\nPOST /tickets/{pid}/approve（decision=reject，最安全的一次）…")
        r = client.post(f"/tickets/{pid}/approve", json={"decision": "reject", "note": "教学演练"})
        print("   →", r.status_code, str(r.json())[:240])
        print("   ★ decision 只能是 approve_refund / reject / keep_pending 三选一。")
        print("   ★ 把 reject 换成 approve_refund 再试一次：")
        print("     如果政策不允许（超限/已退过），会拿到 409 + 政策原因 ——")
        print("     这就是缺陷 ⑥ 修好后的样子：人工也绕不过账本不变量。")
        print("     想要硬来得显式 override=true，且会在审计日志留 policy_override。")
    else:
        print("   还是没有。换 seed：把上面 seed_all(n_customers=60, seed=7) 的 7 改成 11 或 13。")

    # ── 第 5 段：网页控制台 ──────────────────────────────────
    h("第 5 段：网页控制台怎么用")
    print("起服务（菜单 [6] 或下面这条命令）：")
    print('   ticket-agent serve --host 127.0.0.1 --port 8000 --seed 60')
    print("然后浏览器打开 http://127.0.0.1:8000/ ，Token 填 tok-acme")
    print("\n页面上每个按钮对应哪个接口：")
    print("   ┌──────────────────────┬─────────────────────────────────┐")
    print("   │ 页面动作             │ 实际在调                        │")
    print("   ├──────────────────────┼─────────────────────────────────┤")
    print("   │ 打开页面             │ GET /            （拿前端文件）  │")
    print("   │ 出现工单列表         │ GET  /tickets                    │")
    print("   │ 点一张工单看详情     │ GET  /tickets/{id}               │")
    print("   │ 「处置该工单」        │ POST /runs/stream（SSE 直播）    │")
    print("   │ 日志一行行长出来     │ 同上，一条 SSE 事件 = 一行日志    │")
    print("   │ 「人工审批」按钮      │ POST /tickets/{id}/approve       │")
    print("   │ 切换单/多智能体      │ POST /admin/model-kind           │")
    print("   └──────────────────────┴─────────────────────────────────┘")
    print("\n★ 你可以在浏览器按 F12 → Network，点一下按钮就看它发什么请求。")
    print("  前端没有魔法，全部是上面这些接口。")

    # ── 第 6 段：真的起一个服务 ─────────────────────────────
    if serve:
        h("第 6 段：起真 HTTP 服务（127.0.0.1:8199，教学库）")
        import threading
        import time

        import uvicorn

        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=8199, log_level="info")
        server = uvicorn.Server(config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        time.sleep(2.0)
        print("服务起来了：http://127.0.0.1:8199/  （Token 填 tok-acme）")
        print("浏览器打开后，回到这里按 Ctrl+C 停掉。")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n停了。")
    else:
        print("\n想真的起服务？加 --serve 再跑一遍：python _teach_api.py --serve")
        print("（或者直接 ticket-agent serve --port 8000，那个用的是演示库 demo.db）")

    print("\n★ 教学库在这里，想清掉随时删：", DB)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
