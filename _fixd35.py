"""缺陷 ㉟ 的第二半：verifier 的放行依据里有一个被审对象自己写的字段。

已修的第一半（`_summarize_action` 漏 kind）只让**报表标签**变对，
不改变任何判定 —— 那 24 条 run 里 agent 仍然什么都没办成，golden 仍然要求写。

`_vwhy11.py` 证明 scripted 基线下同批 case 全部办成（act 正确、status 正确、
ver=True），所以 verifier 判据在"agent 真的办事"时不会误放行；
问题只在"agent 没办事却仍 open"时它给了通行证。判据是：

    needs_clarification = basis == "ambiguous" or bool(ticket_snap.get("pending_question"))

`basis` 来自 `classify_order_link` 纯函数（输入侧，agent 改不了）；
`pending_question` 是 agent 用 `ask_customer` **写**进去的 —— 与 `basis`
不同级：一个是不可伪造的输入，一个是被审对象自己的产出。
缺陷 ⑲ 修的是"判据不许读模型自己写的散文"，#2 把依据换成结构化字段，
但字段仍由被审对象书写 —— 同一个病根换了件衣服。

★ 为什么"问过客户"不能当作免检理由：
  政策里有六处会返回 need_clarification（R8/R9/R10/R11 缺凭证/原因/金额/现价），
  那些 case 的正解确实是"先问清再办"。但**问过之后仍然要办成**：
  第二轮客户答复到达时，`apply_customer_reply` 会把缺的字段补上，
  规则重算后同一个 case 必须能办成。所以"问过"只解释第一轮为什么 open，
  不解释终态为什么还是 open。

修法：`pending_question` 只在**这条 case 本来就该澄清**时才是有效依据。
可复现的判据在 case 层，agent 改不了：
  · `condition` 以 `need_clarification` 结尾，或
  · `case["turn2_state"]` 非空（造数时就写好"答复后应该办成什么样"）
两者都来自 `_decide_golden` / `_clarification_turn`，与 agent 无关。
除此之外，`pending_question` 只是 agent 的自造免检券。

★ 这里不引入新字段、不改判定字段集（`TICKET_FIELDS` 保持只有 status）：
  verifier 读的是 case 层事实 + 快照，仍然是"机器断言不依赖模型陈述"。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent
NODES = ROOT / "src" / "ticket_agent" / "graph" / "nodes.py"
HARNESS = ROOT / "src" / "ticket_agent" / "eval" / "harness.py"


def sub(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    assert n == 1, f"{where}：期望命中 1 次，实际 {n} 次"
    return text.replace(old, new)


def main() -> int:
    # ── 1. nodes.py：verifier 判据从"问过"升级为"本该问且问完该办成" ──────
    t = NODES.read_text(encoding="utf-8")

    old_sig = "def verifier_node(state: AgentState) -> dict[str, Any]:"
    new_sig = (
        "def verifier_node(\n"
        "    state: AgentState,\n"
        "    *,\n"
        "    should_clarify: bool | None = None,\n"
        ") -> dict[str, Any]:"
    )
    t = sub(t, old_sig, new_sig, "nodes.py verifier_node 签名")

    t = sub(
        t,
        '    """校验节点：独立复核终态。"""',
        '    """校验节点：独立复核终态。\n'
        "\n"
        "    `should_clarify`：**这条 case 的 golden 是否要求先澄清**（case 层事实，\n"
        "    由造数时的 condition / turn2_state 决定，agent 改不了）。\n"
        "    ★ 缺陷 ㉟：`pending_question` 由 agent 自己写，不能单独当放行依据 ——\n"
        "      否则 agent 问一句就给自己开一张免检券（缺陷 ⑲ 的病根换了件衣服）。\n"
        "      默认 None 表示调用方没给，此时退回只看 `basis`（保守：宁可漏放行）。\n"
        '    """',
        "nodes.py verifier_node docstring",
    )

    old_judge = """        needs_clarification = basis == "ambiguous" or bool(
            ticket_snap.get("pending_question")
        )
        if not needs_clarification:
            problems.append("工单仍为 open，但没有任何需澄清的依据 —— 可能存在漏处置")"""
    new_judge = """        # ★ 缺陷 ㉟：`pending_question` 是 agent 自己写进去的，与 `basis`
        #   （输入侧纯函数）不同级。只有**这条 case 本来就该澄清**时，它才是
        #   有效依据；否则它是"agent 自造的免检券"。
        asked = bool(ticket_snap.get("pending_question"))
        needs_clarification = basis == "ambiguous" or (
            asked and bool(should_clarify)
        )
        if asked and not needs_clarification:
            problems.append(
                "工单仍为 open，且 agent 自行向客户提问（pending_question 非空）—— "
                "但这条 case 的 golden 并不要求澄清，提问不能替代处置"
            )
        if not needs_clarification:
            problems.append("工单仍为 open，但没有任何需澄清的依据 —— 可能存在漏处置")"""
    t = sub(t, old_judge, new_judge, "nodes.py 判据块")

    NODES.write_text(t, encoding="utf-8")
    print(f"nodes.py 改完（{len(t)} 字符）")

    # ── 2. harness.py：把 case 层的 should_clarify 传进 verifier ───────────
    h = HARNESS.read_text(encoding="utf-8")

    old_helper = "def _expected_action(expected_snap: dict[str, Any]) -> str:"
    new_helper = '''def _case_should_clarify(case: dict[str, Any]) -> bool:
    """这条 case 的 golden 是否要求"先澄清再办"（缺陷 ㉟ 的 case 层判据）。

    ★ 为什么这个判据可以信、而 `pending_question` 不能：它来自造数时的
      `_decide_golden` / `_clarification_turn`，写进 case dict 之后 agent
      没有任何路径能改它。`pending_question` 相反 —— 它是 agent 用
      `ask_customer` 写进库的，属于**被审对象自己的产出**。

    两个来源，任一成立即算：
      ① `condition` 以 `need_clarification` 结尾（规则要求先问清）；
      ② `turn2_state` 非空（造数时就算出"客户答复后应该办成什么样"）。
    """
    cond = str(case.get("condition") or "")
    if cond.endswith("need_clarification"):
        return True
    return bool(case.get("turn2_state"))


def _expected_action(expected_snap: dict[str, Any]) -> str:'''
    h = sub(h, old_helper, new_helper, "harness.py 新增 _case_should_clarify")

    # 找 run_ticket / build_graph 的调用点，把 should_clarify 传进去
    print("\n=== harness.py 里调用 verifier / 建图的位置（待接线，先看清楚） ===")
    for i, line in enumerate(h.splitlines(), 1):
        if "build_graph(" in line or "verifier_node(" in line or "run_ticket(" in line:
            print(f"  {i}: {line.strip()[:110]}")
    HARNESS.write_text(h, encoding="utf-8")
    print(f"\nharness.py 改完（{len(h)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
