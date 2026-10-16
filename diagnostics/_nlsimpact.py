"""决定性检查：语料里混入的繁体字，是否已经让现有报表的判据结论出错。

为什么这份脚本必须先跑
----------------------
`_nlsidentity.py` 推翻了一个我原来的假设：**JDDC 语料不是纯简体** ——
6,638 条里有 22 条混着繁体字（`貨`/`妳`/`什麽`/`還沒`/`這`/`發`/`領`/`沒`/`壞`…）。

这有两种截然不同的解释，必须用数据分开：

  甲、这些繁体字都在判据**不关心**的位置（名词、语气词）→ 现有报表没错，
      归一化是纯增益，可以放心加。
  乙、其中某些繁体字正好落在判据的敏感位（否定词、诉求动词、到手状态词）→
      **现有千级报表里已经藏着判错的工单**，而且是造数时就配错了订单，
      那 100% 的准确率里有一部分是"判据错 + 数据也跟着错"后的自洽。

不做这个检查就加归一化，等于把一个已知错误留在历史报表里，还附送一层新代码盖住它。
所以要逐个回答三个问题：

  ① 这 22 条里，归一化前后 `classify_style` / `presupposes_receipt` 有几个变了？
  ② 现有 589 张工单的正文里，有多少含繁体字？它们现在被判成什么？
  ③ 繁体数据集归一化后，还剩多少汉字是**本项目简体语料从未见过**的？
     （这才是归一化后的真缺口，比原来的 713 个小得多才有意义。）
"""

import ctypes
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import corpus  # noqa: E402
from ticket_agent.db import session as db_session  # noqa: E402
from ticket_agent.db import models  # noqa: E402

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
LCID_ZH_CN = 0x0804
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CJK = re.compile(r"[\u4e00-\u9fff]")


def to_simplified(text: str) -> str:
    n = _kernel32.LCMapStringW(LCID_ZH_CN, LCMAP_SIMPLIFIED_CHINESE, text, -1, None, 0)
    if n == 0:
        raise OSError(ctypes.get_last_error())
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(LCID_ZH_CN, LCMAP_SIMPLIFIED_CHINESE, text, -1, buf, n) == 0:
        raise OSError(ctypes.get_last_error())
    return buf.value


def judge(text: str) -> tuple[str, bool]:
    return corpus.classify_style(text), corpus.presupposes_receipt(text)


def main() -> None:
    print("=" * 78)
    print("① 语料里那 22 条：归一化前后判据结论变了吗")
    print("=" * 78)
    tickets = corpus.load_corpus()
    affected = []
    for t in tickets:
        s = to_simplified(t.body)
        if s != t.body:
            before = judge(t.body)
            after = judge(s)
            affected.append((t.body, s, before, after))

    print(f"  受影响条数: {len(affected)} / {len(tickets)}")
    style_changed = [r for r in affected if r[2][0] != r[3][0]]
    recv_changed = [r for r in affected if r[2][1] != r[3][1]]
    print(f"  ★ classify_style 变了    : {len(style_changed)}")
    print(f"  ★ presupposes_receipt 变了: {len(recv_changed)}")
    print()
    if style_changed or recv_changed:
        print("  逐条（★ 标注发生变化的那一维）：")
        for body, simp, before, after in affected:
            marks = []
            if before[0] != after[0]:
                marks.append(f"style {before[0]}→{after[0]}")
            if before[1] != after[1]:
                marks.append(f"receipt {before[1]}→{after[1]}")
            flag = "★" if marks else " "
            print(f"   {flag} {body[:44]}")
            if marks:
                print(f"       {simp[:44]}    {'; '.join(marks)}")
    else:
        print("  → 甲成立：22 条全部只是字形变化，判据结论一个都没变。")

    print()
    print("=" * 78)
    print("② 语料里哪些繁体字真的出现在判据敏感位")
    print("=" * 78)
    # 把每条受影响文本的差异字符列出来，看它们是不是判据词典里的字
    vocab = {
        "_REQUEST_RE": corpus._REQUEST_RE.pattern,
        "_DEFECT_RE": corpus._DEFECT_RE.pattern,
        "_PRE_RECEIPT_RE": corpus._PRE_RECEIPT_RE.pattern,
        "_POST_RECEIPT_RE": corpus._POST_RECEIPT_RE.pattern,
    }
    for name, pat in vocab.items():
        print(f"  {name:<18} {pat[:100]}")

    print()
    print("=" * 78)
    print("③ 现有库里 589 张工单：正文含繁体字的有几张，它们现在被判成什么")
    print("=" * 78)
    with db_session.session_scope() as s:
        rows = s.query(models.Ticket).all()
        ticket_rows = [(t.id, t.subject or "", t.body or "") for t in rows]
    print(f"  工单数: {len(ticket_rows)}")
    mixed = []
    for tid, subj, body in ticket_rows:
        s = to_simplified(body)
        if s != body:
            mixed.append((tid, subj, body, s, judge(body), judge(s)))
    print(f"  正文含繁体字的工单: {len(mixed)} ({len(mixed) / max(len(ticket_rows), 1) * 100:.2f}%)")
    if mixed:
        print()
        for tid, subj, body, simp, before, after in mixed[:30]:
            marks = []
            if before[0] != after[0]:
                marks.append(f"style {before[0]}→{after[0]}")
            if before[1] != after[1]:
                marks.append(f"receipt {before[1]}→{after[1]}")
            flag = "★" if marks else " "
            print(f"   {flag} #{tid} {body[:50]}")
            if marks:
                print(f"        {'; '.join(marks)}")

    print()
    print("=" * 78)
    print("④ 归一化后还剩多少「本项目简体语料从未见过」的汉字")
    print("=" * 78)
    seen: set[str] = set()
    for t in tickets:
        seen |= set(t.body)
    print(f"  参照集（归一化后的语料字符集）: {len(seen)} 个汉字")
    rows2 = [
        json.loads(l)
        for l in open("data/probe/samples/hf_zhtw_ticket_train_FULL.jsonl", encoding="utf-8")
        if l.strip()
    ]
    unknown: dict[str, int] = {}
    for r in rows2:
        for ch in to_simplified(r["ticket"]["ticket_text"]):
            if CJK.match(ch) and ch not in seen:
                unknown[ch] = unknown.get(ch, 0) + 1
    print(f"  ★ 归一化后仍未见过的汉字: {len(unknown)}  （归一化前是 713）")
    if unknown:
        items = sorted(unknown.items(), key=lambda kv: -kv[1])
        print(f"    全部: {''.join(c for c, _ in items)}")
        print()
        print(f"    top40: {' '.join(f'{c}{n}' for c, n in items[:40])}")


if __name__ == "__main__":
    main()
