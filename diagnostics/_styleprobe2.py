"""候选修法量化：`_REQUEST_RE` 该扩到多大？

背景（`_styleprobe.py` 实测）：现有正则只有"退款/退货/换钱/换货"四个家族，
在 JDDC 真实语料上 vague 占 75.9%、defect 仅 0.5%。抽样显示大量被判 vague
的句子其实**说清了要什么**，只是要的不是退钱，而是补发/查件/开票/赔偿 ——
aftersale 的诉求**不止"动钱"和"动货"两种**，还有"要服务动作"。

本脚本不猜，直接把候选家族逐个加上去，看分布怎么变、哪些句子被挪动。
三个候选：
  A = 现状（只有 退/换 家族）
  B = A + "服务动作"家族（补发/重发/催/查/问/开票/改址/退回/拒收）
  C = B + "要结果"家族（赔偿/补偿/给个说法/处理/解决/给答复）

风险提示：扩得越宽，vague 越少，L3（要澄清）能选的话术就越少 ——
这是本脚本要显式量出来的代价，而不是事后才发现。
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

# ── 候选家族 ──────────────────────────────────────────────
A = r"退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下"

# 服务动作：客户要的不是钱，是让客服做一件事
_SERVICE = (
    r"补发|补寄|重新发|重发|再发|发一个|发件|催|加急|"
    r"帮我查|帮我问|帮我联系|帮我催|查一下|查下|问一下|问下|核实|确认下|"
    r"开个发票|开发票|发票|"
    r"改地址|改下地址|修改地址|"
    r"退回|寄回|拒收|拦截"
)
B = A + "|" + _SERVICE

# 要结果：客户要一个处置，但没指明动作
_RESULT = r"赔偿|补偿|给个说法|给说法|处理一下|怎么处理|怎么解决|给个答复|给答复|给我解决|解决一下"
CC = B + "|" + _RESULT

_DEFECT = C._DEFECT_RE.pattern

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
    s = re.sub(r"^(亲[，,、\s]*)+", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def usable(s: str, min_len: int = 10) -> bool:
    return (
        len(s) >= min_len
        and not FILLER_RE.match(s)
        and bool(AFTERSALE_RE.search(s))
        and not PRESALE_RE.search(s)
    )


def classify(text: str, req_pat: re.Pattern[str]) -> str:
    """与 corpus.classify_style 同构，只换诉求正则。"""
    if not req_pat.search(text):
        return C.STYLE_VAGUE
    if re.search(_DEFECT, text):
        return C.STYLE_DEFECT
    return C.STYLE_EXPLICIT


def dist(xs: list[str], req_pat: re.Pattern[str]) -> dict[str, int]:
    return dict(Counter(classify(s, req_pat) for s in xs))


def rates(d: dict[str, int], n: int) -> str:
    return "  ".join(
        f"{k}={d.get(k,0)}（{d.get(k,0)/n*100:.1f}%）"
        for k in (C.STYLE_EXPLICIT, C.STYLE_DEFECT, C.STYLE_VAGUE)
    )


# ── 抽样 ────────────────────────────────────────────────
p = S / "ms_ecom_train_FULL.jsonl"
users: list[str] = []
with p.open(encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        for m in obj.get("messages") or []:
            if m.get("role") == "user":
                t = clean(m.get("content") or "")
                if t:
                    users.append(t)
kept = [s for s in sorted(set(users)) if usable(s)]
n = len(kept)
print(f"样本：JDDC 净可用 {n} 条\n")

pats = {"A 现状": re.compile(A), "B +服务动作": re.compile(B), "C +要结果": re.compile(CC)}
for name, pat in pats.items():
    d = dist(kept, pat)
    print(f"{name:10s} → {rates(d, n)}")

print("\n" + "=" * 70)
print("对照：现有 862 条合成语料（这是当初写正则时的目标分布）")
synth = [t.body for t in C.load_corpus()]
ds = dist(synth, re.compile(A))
print(f"{'A 现状':10s} → {rates(ds, len(synth))}")

# ── 谁被挪动了 ──────────────────────────────────────────
pa, pb = re.compile(A), re.compile(B)
moved = [s for s in kept if classify(s, pa) == C.STYLE_VAGUE and classify(s, pb) != C.STYLE_VAGUE]
print("\n" + "=" * 70)
print(f"★ 从 vague 挪到 explicit/defect 的句子：{len(moved)} 条")
print("（这是在核对'扩正则'捞回来的到底是不是真诉求，不是在看数量）")
step = max(1, len(moved) // 30)
for i, s in enumerate(moved[::step][:30]):
    print(f"  {i+1:>2}. {s}")

still_vague = [s for s in kept if classify(s, pb) == C.STYLE_VAGUE]
print(f"\n★ 扩到 B 后**仍然** vague 的：{len(still_vague)} 条 —— 抽样 25")
print("（这些才是真正'没说清要什么'的，L3 要靠它们）")
step = max(1, len(still_vague) // 25)
for i, s in enumerate(still_vague[::step][:25]):
    print(f"  {i+1:>2}. {s}")
