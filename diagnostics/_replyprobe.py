"""★ 换一个信号源：用"客服的真实回复"给客户话术打标，而不是靠我的正则。

由来（`_styleprobe2.py` 实测）：
  A 现状        → explicit=1490（23.5%）  defect=32（0.5%）   vague=4806（75.9%）
  B +服务动作   → explicit=2102（33.2%）  defect=46（0.7%）   vague=4180（66.1%）
  C +要结果     → explicit=2146（33.9%）  defect=53（0.8%）   vague=4129（65.2%）
扩了 40 多个词，仍然 vague 65%，且抽样核对出约 24% 的漏判（「给我安排发货吧」
「可以改运费吗」「能崔下派送么」——连"催"写成"崔"都漏）。继续堆词是打地鼠，
而且**堆出来的规则无法自证对错**：我凭什么说「补发」算说清了、「改运费」不算？

★ ms_ecom_train_FULL.jsonl 每行是一对 (user, assistant)。助手回复是**真人客服当年
真实做出的反应**，而"要不要反问"恰恰是 style 这个维度唯一要区分的事：
  - 客服直接办 / 确认已办        → 客户说清了（explicit）
  - 客服反问「您是要退还是换」    → 客户没说清（vague）
  - 客服要照片/凭证              → 客户提了质量问题（defect）
这是从对话里**读出**的，不是我从 9 条模板里**想出来**的。

本脚本先只看数据、不下结论：回复到底长什么样、有多少是模板复读、
三个信号词家族各占多少。分布合不合理再决定要不要用它替换正则。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.db import corpus as C  # noqa: E402

S = ROOT / "data" / "probe" / "samples"
P = S / "ms_ecom_train_FULL.jsonl"

AFTERSALE_RE = re.compile(
    r"退款|退货|退换|退钱|换货|换新|售后|质保|保修|维修|"
    r"坏了|坏掉|破损|瑕疵|划痕|开线|开胶|裂|漏|次品|假货|变质|发霉|过期|"
    r"错发|发错|少发|漏发|多发|发漏|缺件|少件|"
    r"没收到|未收到|没到|不见|丢了|丢件|签收|物流|快递|运单|配送|驿站|"
    r"补偿|赔偿|投诉|差评|维权|发票|价保|保价|退运费|运费"
)
PRESALE_RE = re.compile(r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜")
FILLER_RE = re.compile(
    r"^(嗯|哦|啊|呃|额|好吧|好的|行|是|对|不|在|有|没|可以|谢谢|麻烦|亲|"
    r"什么|怎么|为什么|哪|多少|几个|多久)[啊呢吧吗么了~！!。.？?，,、\s]*$"
)


def clean(s: str) -> str:
    s = re.sub(r"^(亲[，,、\s]*)+", "", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def usable(s: str, min_len: int = 10) -> bool:
    return (
        len(s) >= min_len
        and not FILLER_RE.match(s)
        and bool(AFTERSALE_RE.search(s))
        and not PRESALE_RE.search(s)
    )


# ── 从"客服回复"里读出三个信号 ──────────────────────────────
# 反问：客服在向客户要信息才能往下走
_ASK = re.compile(r"请问|麻烦您|您是要|您需要|您看|方便|是否|哪个|哪种|多少|几天|什么时候|订单号|提供一下|告知")
# 办结：客服确认已经在办或已办完
_ACT = re.compile(
    r"帮您|给您|为您|已为您|已经帮|马上|立刻|尽快|这就|去帮|安排|申请|处理|"
    r"反馈|催|补发|补寄|重发|登记|核实|联系|加急|备注|转|跟进"
)
# 凭证：客服要图/要证据 → 客户提了质量问题
_PROOF = re.compile(r"照片|图片|拍照|拍个|截图|凭证|证据|视频|寄回|退回|检测")

QMARK = re.compile(r"[？?]")


def signals(reply: str) -> tuple[bool, bool, bool]:
    return (
        bool(QMARK.search(reply) and _ASK.search(reply)),
        bool(_ACT.search(reply)),
        bool(_PROOF.search(reply)),
    )


# ── 读一遍 ──────────────────────────────────────────────
pairs: list[tuple[str, str]] = []
with P.open(encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        u = a = ""
        for m in obj.get("messages") or []:
            if m.get("role") == "user":
                u = clean(m.get("content"))
            elif m.get("role") == "assistant":
                a = clean(m.get("content"))
        if u and a and usable(u):
            pairs.append((u, a))

print(f"售后池（带客服回复的）：{len(pairs)} 对\n")

ask = act = proof = 0
for _, a in pairs:
    s1, s2, s3 = signals(a)
    ask += s1
    act += s2
    proof += s3
n = len(pairs)
print("客服回复的三个信号命中率：")
print(f"  反问（要信息）      {ask:>6}  {ask/n*100:5.1f}%")
print(f"  办结（确认在办）    {act:>6}  {act/n*100:5.1f}%")
print(f"  凭证（要图/寄回）   {proof:>6}  {proof/n*100:5.1f}%")

print("\n" + "=" * 70)
print("★ 客服回复的模板化程度（去重后 top 20）")
print("（如果 top 20 就覆盖大半，说明回复是模板复读，信号不可信）")
ctr = Counter(a for _, a in pairs)
print(f"唯一回复数：{len(ctr)} / {n}（{len(ctr)/n*100:.1f}%）")
for i, (t, c) in enumerate(ctr.most_common(20), 1):
    print(f"  {i:>2}. [{c:>5}] {t[:60]}")

print("\n" + "=" * 70)
print("★ 与现有正则的交叉：客服到底有没有反问")
print("（关键问题：被判 vague 的句子，客服真的反问了么？）\n")
syn = re.compile(C._REQUEST_RE.pattern)
for style in (C.STYLE_EXPLICIT, C.STYLE_DEFECT, C.STYLE_VAGUE):
    sel = [(u, a) for u, a in pairs if C.classify_style(u) == style]
    if not sel:
        continue
    r = sum(1 for _, a in sel if signals(a)[0]) / len(sel)
    act_r = sum(1 for _, a in sel if signals(a)[1]) / len(sel)
    print(f"  正则判 {style:9s}（{len(sel):>5} 条）→ 客服反问率 {r*100:5.1f}%  办结率 {act_r*100:5.1f}%")

print("\n" + "=" * 70)
print("★ 逐对sample（正则判 vague 但客服**没反问**的 —— 这些就是漏判）")
noask = [(u, a) for u, a in pairs if C.classify_style(u) == C.STYLE_VAGUE and not signals(a)[0]]
print(f"共 {len(noask)} 条，抽 20：")
step = max(1, len(noask) // 20)
for i, (u, a) in enumerate(noask[::step][:20], 1):
    print(f"  {i:>2}. 客户：{u[:52]}")
    print(f"      客服：{a[:52]}")

print("\n" + "=" * 70)
print("★ 反过来：正则判 explicit 但客服**反问**了 —— 这些是我判宽了")
wide = [(u, a) for u, a in pairs if C.classify_style(u) == C.STYLE_EXPLICIT and signals(a)[0]]
print(f"共 {len(wide)} 条，抽 15：")
step = max(1, len(wide) // 15)
for i, (u, a) in enumerate(wide[::step][:15], 1):
    print(f"  {i:>2}. 客户：{u[:52]}")
    print(f"      客服：{a[:52]}")
