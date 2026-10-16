"""验证服务层 + HTTP 层：鉴权、SSE、HITL 审批、跨租户隔离。"""
import sys, json
sys.path.insert(0, "src")

# ★ Windows 控制台默认 GBK，直接 print 中文会变乱码或抛 UnicodeEncodeError。
# 这与 cli.py 里 _force_utf8_console() 是同一个坑（_hitlcheck.py 也已加）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient
from ticket_agent.db import init_db, seed_all, build_eval_cases
from ticket_agent.api.app import app

init_db(drop=True)
print("seed:", seed_all(n_customers=60, seed=7))
cases = build_eval_cases(size=40, seed=7)

c = TestClient(app)
TOK = {"Authorization": "Bearer tok-acme"}
TOK2 = {"Authorization": "Bearer tok-globex"}

print("\n--- /health (no auth) ---")
print(c.get("/health").status_code, c.get("/health").json())

print("\n--- /meta requires auth ---")
print("no token ->", c.get("/meta").status_code)
print("bad token ->", c.get("/meta", headers={"Authorization": "Bearer nope"}).status_code)
m = c.get("/meta", headers=TOK).json()
print("model_kind:", m["model_kind"], "| roles:", {k: len(v) for k, v in m["roles"].items()})

print("\n--- tenant isolation ---")
a = c.get("/tickets", headers=TOK).json()
g = c.get("/tickets", headers=TOK2).json()
print(f"acme tickets={len(a)} globex tickets={len(g)}")
a_ids = {t["ticket_id"] for t in a}
g_ids = {t["ticket_id"] for t in g}
print("overlap:", a_ids & g_ids, "(must be empty)")
# acme token 读 globex 的工单必须 404
if g_ids:
    gt = sorted(g_ids)[0]
    print(f"acme reading globex ticket {gt} ->", c.get(f"/tickets/{gt}", headers=TOK).status_code, "(want 404)")

print("\n--- SSE stream ---")
tid = None
for t in a:
    if t["status"] == "open":
        tid = t["ticket_id"]; break
print("target ticket:", tid)
events = []
with c.stream("POST", "/runs/stream", headers=TOK, json={"ticket_id": tid, "arm": "multi"}) as r:
    print("content-type:", r.headers.get("content-type"))
    cur = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            cur = line[7:]
            events.append(cur)
print("event sequence:", events)

print("\n--- non-stream run + detail ---")
r = c.post("/runs", headers=TOK, json={"ticket_id": tid, "arm": "single"})
print("status:", r.status_code)
d = r.json()
print("keys:", sorted(d.keys()))
print("final_answer:", (d.get("final_answer") or "")[:100])
print("actions:", d.get("actions_taken"))
print("budget:", d.get("budget"))
det = c.get(f"/tickets/{tid}", headers=TOK).json()
print("ticket status now:", det["ticket"]["status"], "| basis:", det["order_basis"])

print("\n--- run listing + cross-tenant run read ---")
runs = c.get("/runs", headers=TOK).json()
print("acme runs:", len(runs))
rid = runs[0]["run_id"]
print("acme reads own run ->", c.get(f"/runs/{rid}", headers=TOK).status_code)
print("globex reads acme run ->", c.get(f"/runs/{rid}", headers=TOK2).status_code, "(want 404)")

print("\n--- HITL: find a pending_human case ---")
esc = [x for x in cases if x["expected_state"]["ticket"]["status"] == "pending_human"]
print("pending_human cases:", len(esc))
if esc:
    case = esc[0]
    print("case:", case["case_id"], "tenant:", case["tenant_id"])
    hdr = TOK if case["tenant_id"] == "acme" else TOK2
    rr = c.post("/runs", headers=hdr, json={"ticket_id": case["ticket_id"], "arm": "multi"}).json()
    print("run status:", rr.get("status"), "| requires_human:", rr.get("requires_human_approval"))
    rid2 = rr["run_id"]
    print("pending queue:", len(c.get("/pending", headers=hdr).json()))
    print("condition:", case.get("condition"))
    # ① 普通人工批准：若政策禁止（R5 已退过 / R6 生鲜）必须 409。
    #    这正是缺陷 ⑥ —— 旧代码这里返回 200，把 47.53 元的单退成了 95.06 元。
    ap = c.post(f"/runs/{rid2}/approve", headers=hdr,
                json={"decision": "approve_refund", "note": "人工复核通过"})
    print("approve ->", ap.status_code, "(409 = 政策护栏拦下，200 = 允许退)")
    if ap.status_code == 409:
        print("   detail:", ap.json().get("detail"))
        # ② 显式 override：仍然可以破例，但必须留痕
        ap = c.post(f"/runs/{rid2}/approve", headers=hdr,
                    json={"decision": "approve_refund", "note": "人工复核后确认破例", "override": True})
        print("approve with override ->", ap.status_code,
              "| policy_override:", ap.json().get("policy_override"))
    if ap.status_code == 200:
        j = ap.json()
        print("performed:", [p["action"] for p in j["performed"]])
        print("snapshot:", json.dumps(j["snapshot"], ensure_ascii=False))
    # ③ 再裁决一次必须 409（工单已 resolved，状态机拦截）
    ap2 = c.post(f"/runs/{rid2}/approve", headers=hdr, json={"decision": "reject"})
    print("second approve ->", ap2.status_code, "(want 409)", ap2.json().get("detail"))
    # 跨租户裁决必须 404
    other = TOK2 if case["tenant_id"] == "acme" else TOK
    print("cross-tenant approve ->", c.post(f"/runs/{rid2}/approve", headers=other,
          json={"decision": "keep_pending"}).status_code, "(want 404)")

print("\n--- eval verify endpoint ---")
for case in cases[:2]:
    hdr = TOK if case["tenant_id"] == "acme" else TOK2
    other = TOK2 if case["tenant_id"] == "acme" else TOK
    r1 = c.get(f"/eval/cases/{case['case_id']}/verify", headers=hdr)
    r2 = c.get(f"/eval/cases/{case['case_id']}/verify", headers=other)
    print(f"{case['case_id']} own-token={r1.status_code} other-token={r2.status_code} (want 200/404)")
    if r1.status_code == 200:
        j = r1.json()
        print(f"    passed={j['passed']} diffs={j['diffs']}")
print("\nALL SERVICE CHECKS DONE")
