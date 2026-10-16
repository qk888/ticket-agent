"""接线的第三段：两处 run_ticket 调用都要传 should_clarify。

第二段（`_fixd35c.py`）已把 `build_graph` / `run_ticket` 改好，但 harness 的
接线只匹配到 1 处（turn-1），因为 turn-2 的调用缩进不同（16 空格 vs 12）。
`assert n == 2` 当场拦住，**harness.py 一个字都没被写坏** —— 这正是
"先 assert 命中数再落盘"那条铁律在起作用。

本段分别处理两处，各自 assert 命中 1 次。

★ turn-2 也要传同一个值：第二轮的 golden 是 `turn2_state`，而
  `_case_should_clarify` 的第二个来源正是 `turn2_state` 非空 ——
  也就是说两轮 case 的 should_clarify 恒为 True，verifier 会允许
  "第一轮 open + 有 pending_question"。但**终态判据是 turn2_state**
  （要求办成），所以"问过"仍然不能替代"办成"。

用法：python _fixd35d.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"


def sub(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    assert n == 1, f"{where}：期望命中 1 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    h = HARNESS.read_text(encoding="utf-8")

    # turn-1（12 空格缩进）
    h = sub(
        h,
        """            checkpointer=False,
        )""",
        """            checkpointer=False,
            # ★ 缺陷 ㉟：把 case 层的"该不该澄清"送进 verifier，让放行依据
            #   从"agent 自己写过的 pending_question"换成"golden 要求先澄清"。
            should_clarify=_case_should_clarify(case),
        )""",
        "harness.run_case turn-1",
    )

    # turn-2（16 空格缩进）
    h = sub(
        h,
        """                checkpointer=False,
            )""",
        """                checkpointer=False,
                # ★ 缺陷 ㉟：第二轮同样传 case 层的判据。两轮 case 的
                #   `should_clarify` 恒为 True（turn2_state 非空），所以
                #   verifier 允许"第一轮 open + 有 pending_question"，
                #   但终态判据是 turn2_state（要求办成）——
                #   "问过"依然不能替代"办成"。
                should_clarify=_case_should_clarify(case),
            )""",
        "harness.run_case turn-2",
    )
    HARNESS.write_text(h, encoding="utf-8")
    print(f"harness.py 两处调用都已接线（{len(h)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
