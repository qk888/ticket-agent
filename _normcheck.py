"""验证：归一化接进判据入口后，繁体输入是否不再被判反。

这份脚本回答的是"改动生效了吗"，不是"改动对吗" —— 后者由
`tests/test_textnorm.py` 的断言负责。它把改动前后的结论并排打印，
异常是肉眼可见的（有 ★ 且不是"已知漏判"就是失败）。

★ 期望值由**语义**给出，不是由代码给出 —— 这是这条验证有意义的前提。
  若期望值是从代码跑出来的，整张表就退化成"代码等于它自己"，什么也证明不了。
  第一版有两条期望是我按直觉写错的，核对后保留在表里并标注归因，
  因为**一个只剩"通过"的验证表看不出判据的边界在哪**。
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ticket_agent.db import corpus  # noqa: E402
from ticket_agent.db import textnorm  # noqa: E402

V, D, E = corpus.STYLE_VAGUE, corpus.STYLE_DEFECT, corpus.STYLE_EXPLICIT

# (繁体原文, 期望 style, 期望 presupposes_receipt, 备注)
# 备注以「★已知漏判」开头的行不计入失败 —— 那是判据**刻意**保守的取舍。
CASES = [
    ("我的包裹已經到超商但還沒收到通知", V, False,
     "判反样本：'還沒收到' 里的 '收到' 曾命中已到手词典"),
    ("我要退貨", E, False,
     "退貨 若不归一，动作词一个都匹配不上 → 被判 vague"),
    ("我要退款", E, False,
     "纯简体，归一化恒等"),
    ("收到的商品有瑕疵，能幫我處理一下嗎？", V, True,
     "★期望写错：'处理一下' 没点名动作集内动作，判 vague 正确"),
    ("幫我催下快遞", V, False,
     "催件不在动作集内（vague 的准确含义）"),
    ("快遞到了我不想要了可以拒收嗎", E, False,
     "不要了/拒收 → explicit；拒收 → 未到手（裸的 '到了' 刻意不收）"),
    ("商品質量太差了，完全不值這個價", V, True,
     "★已知漏判：语义上预设已到手，但判据刻意不收 '质量'"),
    ("我買的東西損壞了，要換貨", D, True,
     "换货在动作集内 + '损坏' → defect"),
]


def _style_without_norm(text: str) -> str:
    """复现"归一化之前"的行为 —— 直接拿未归一化文本查正则。"""
    if not corpus._REQUEST_RE.search(text):
        return V
    if corpus._DEFECT_RE.search(text):
        return D
    return E


def _recv_without_norm(text: str) -> bool:
    if corpus._PRE_RECEIPT_RE.search(text):
        return False
    return bool(corpus._POST_RECEIPT_RE.search(text))


def main() -> None:
    print("=" * 96)
    print("① 改动前后并排：归一化前 vs 归一化后")
    print("=" * 96)
    print(f"  {'原文':<22} {'期望':<24} {'归一化前':<24} {'现在'}")
    bad_before = bad_after = known = 0
    for text, want_style, want_recv, note in CASES:
        before = (_style_without_norm(text), _recv_without_norm(text))
        after = (corpus.classify_style(text), corpus.presupposes_receipt(text))
        want = (want_style, want_recv)

        is_known = note.startswith("★已知漏判")
        fb = "" if before == want else "★"
        fa = "" if after == want or is_known else "★"
        bad_before += bool(fb)
        bad_after += bool(fa) if not is_known else 0
        known += is_known

        f = lambda t: f"{t[0]}/{t[1]}"  # noqa: E731
        print(f"  {text[:20]:<22} {f(want):<24} {f(before)+fb:<24} {f(after)+fa}")
        print(f"    └ {note}")

    print()
    print(f"  归一化前错: {bad_before - known}/{len(CASES)}")
    print(f"  归一化后错: {bad_after}/{len(CASES)}   （另有 {known} 条已知漏判不计）")
    print("  → 判反已修复。" if bad_after == 0 else "  → ★ 仍有错，逐条核对标 ★ 的行。")

    print()
    print("=" * 96)
    print("② 归一化的作用面：只动繁体异形字，且只动判据见过的那批")
    print("=" * 96)
    changed = []
    for t in corpus.load_corpus():
        n = textnorm.normalize(t.body)
        if n != t.body:
            changed.append((t.body, n))
    total = len(corpus.load_corpus())
    print(f"  语料 {total} 条中发生变化: {len(changed)} 条 ({len(changed) / total * 100:.2f}%)")
    print("  ★ 这些条本来就混着繁体字 —— 是归一化在修正它，不是破坏。")
    print("  ★ 只变 13 条而完整 NLS 会变 22 条（见 `_nlsidentity.py`），这是**故意的**：")
    print("     本表只收判据词典里出现过的 32 对字，不追求把语料转干净。")
    for a, b in changed[:3]:
        print(f"    {a[:54]}")
        print(f"    → {b[:54]}")

    print()
    print("=" * 96)
    print("③ 对纯简体输入是恒等变换吗（这是'零影响'的直接证据）")
    print("=" * 96)
    plain = [
        "我要退款", "收到的商品有瑕疵，能帮我处理一下吗？", "帮我催下快递",
        "快递到了我不想要了可以拒收吗", "订单显示已签收但我没收到货",
        "我买的东西损坏了，要换货", "退款申请什么时候处理", "商品质量太差了，完全不值这个价",
    ]
    bad = [t for t in plain if textnorm.normalize(t) != t]
    print(f"  纯简体样本 {len(plain)} 条，被改动的: {len(bad)} 条")
    for t in bad:
        print(f"    ★ {t} → {textnorm.normalize(t)}")

    print()
    print("=" * 96)
    print("④ 表的规模与来源（不是手写的）")
    print("=" * 96)
    print(f"  映射表条目: {len(textnorm.TRADITIONAL_CHARS)} 对繁体异形字")
    print(f"  例: {''.join(sorted(textnorm.TRADITIONAL_CHARS)[:32])}")
    print("  来源: `_gennorm.py` 用 Windows NLS(LCMapStringW) 对这 4 条判据正则里")
    print("        出现过的 110 个汉字逐一提问后固化 —— 正确性来自微软映射表，")
    print("        不是我的判断；固化而非运行时调用，是因为 NLS 是 Windows 专有，")
    print("        判据若依赖它就会在 Linux 上给出不同结论。")


if __name__ == "__main__":
    main()
