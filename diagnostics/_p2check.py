"""#2 施工自检：models/snapshot/registry 三层的导入与注册表自洽性。

用法：python _p2check.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./eval_out/_p2check.db")

from ticket_agent.config import settings  # noqa: E402
from ticket_agent.db import snapshot  # noqa: E402
from ticket_agent.db.models import Ticket  # noqa: E402
from ticket_agent.tools import ROLE_TOOL_MATRIX, WRITE_TOOLS  # noqa: E402
from ticket_agent.tools.registry import (  # noqa: E402
    ARG_SCHEMAS,
    _WRITE_TOOLS_FN,
    registry,
)

FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL.append(msg)


print("== models 层 ==")
cols = {c.name for c in Ticket.__table__.columns}
check("pending_question" in cols, "Ticket.pending_question 已加")
check("customer_reply" in cols, "Ticket.customer_reply 已加")

print("== snapshot 层 ==")
check(
    "pending_question" in snapshot.TICKET_OBSERVED_FIELDS,
    "pending_question 进 TICKET_OBSERVED_FIELDS（只观测不判定）",
)
check(
    "pending_question" not in snapshot.TICKET_FIELDS,
    "pending_question **不**进 TICKET_FIELDS（不参与终态判定）",
)
check(
    "customer_reply" in snapshot.TICKET_OBSERVED_FIELDS,
    "customer_reply 进 TICKET_OBSERVED_FIELDS",
)
check(
    snapshot._observed_ticket_fields()[:1] == snapshot.TICKET_FIELDS,
    "判定字段仍在观测字段之前（顺序稳定，指纹可比）",
)

print("== registry 层 ==")
check("ask_customer" in ROLE_TOOL_MATRIX["resolver"], "resolver 有 ask_customer")
check("ask_customer" in ROLE_TOOL_MATRIX["single"], "single 有 ask_customer")
check("ask_customer" not in ROLE_TOOL_MATRIX["triage"], "triage **无** ask_customer（只读侦察）")
check("ask_customer" not in ROLE_TOOL_MATRIX["verifier"], "verifier **无** ask_customer（只读复核）")
check("ask_customer" in WRITE_TOOLS, "ask_customer 在 WRITE_TOOLS（不进只读缓存、计入写调用）")
check("ask_customer" in ARG_SCHEMAS, "ask_customer 在 ARG_SCHEMAS（否则 _to_structured 抛 KeyError）")
check(
    "ask_customer" in [f.__name__ for f in _WRITE_TOOLS_FN],
    "ask_customer 在 _WRITE_TOOLS_FN",
)
tools = {t.name for t in registry.get_tools("resolver")}
check("ask_customer" in tools, "resolver 实际拿到的工具列表里有 ask_customer")
check("ask_customer" not in {t.name for t in registry.get_tools("verifier")}, "verifier 实际拿不到")

print("== 权限裁剪仍然生效 ==")
try:
    registry.get("ask_customer", "verifier")
    check(False, "verifier 取 ask_customer 应抛 PermissionError")
except PermissionError:
    check(True, "verifier 取 ask_customer 抛 PermissionError（物理裁剪生效）")

print()
if FAIL:
    print(f"✗ {len(FAIL)} 项未通过")
    sys.exit(1)
print("✓ 全部通过")
