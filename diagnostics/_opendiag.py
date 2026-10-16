"""诊断：ACME 第一张工单跑 single 之后为什么还停在 open。

`build_eval_cases` 是按"当前还 open 的工单"排的，第一张工单变了
（造数改动之后），所以要看清它是什么条件、agent 到底做了什么。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["DATABASE_URL"] = f"sqlite:///{ROOT / 'eval_out' / 'open60.db'}"

from fastapi.testclient import TestClient  # noqa: E402

from ticket_agent.api.app import app  # noqa: E402
from ticket_agent.db.seed import build_eval_cases, seed_all  # noqa: E402
from ticket_agent.db.session import init_db  # noqa: E402
from ticket_agent.graph import nodes  # noqa: E402

init_db(drop=True)
seed_all(n_customers=40, seed=11)
cases = build_eval_cases(size=40, seed=7)

client = TestClient(app)
tok = "tok-acme"
hdr = {"Authorization": f"Bearer {tok}"}

tickets = client.get("/tickets", headers=hdr).json()
tid = tickets[0]["ticket_id"]
print(f"第一张 ACME 工单：#{tid}")
print(json.dumps(tickets[0], ensure_ascii=False, indent=2)[:800])

match = [c for c in cases if c["tenant_id"] == "acme" and c["ticket_id"] == tid]
print(f"\n对应 case：{json.dumps(match, ensure_ascii=False)[:600] if match else '(不在 case 集里)'}")

nodes.set_model_kind("scripted")
body = client.post("/runs", headers=hdr, json={"ticket_id": tid, "arm": "single"}).json()
print(f"\nrun status = {body.get('status')}")
print(f"actions_taken = {json.dumps(body.get('actions_taken'), ensure_ascii=False)[:400]}")
print(f"observed_action = {body.get('observed_action')}")
print(f"todo/notes = {json.dumps(body.get('work_notes'), ensure_ascii=False)[:400]}")

detail = client.get(f"/tickets/{tid}", headers=hdr).json()
print(f"\n工单状态 = {detail['ticket']['status']}")
print(f"intent = {detail['ticket'].get('intent')}")

