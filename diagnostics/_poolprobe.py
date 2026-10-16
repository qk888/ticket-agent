"""真正能用的池子有多大？—— 加"售后事由"与"自足性"两道闸门后重新量化。

由来（`_replyprobe.py` 实测，推翻了我上一版假设）：
  - 想用"客服回复里的反问"给 style 打标 → **反问率 0.0%**。原因是 JDDC 是
    **去标点**语料，客服回复里根本没有问号；而抽样里明明有真实反问
    （「那亲要吗要的话可以按399给亲不要的话可以退回」）→ 该信号源不可用。
  - 但这一轮暴露了**更严重的问题**：所谓"净可用 6,328 条"里混着大量
    **根本不是售后**的句子 ——「山东新泰的发什么快递」「听说现在快递有的都
    停运了」「需要专职的售后客服么」「请再帮我催一下快递好吧无语了」里
    前后两类是完全不同的东西。根因：`AFTERSALE_RE` 把「快递|物流|运单」
    当成了售后词，而**问快递方式是纯售前咨询**。
  - 另外大量句子是**对话中段**（「对的那个消息我发错了」「我也邮过快递最好
    发邮政」「嗯嗯好的给我快递单号完了」），脱离上下文不成立，却过了长度闸门。

本脚本加两道闸门重新量化，**不下结论、不写进 corpus**：
  闸门 A · 售后事由：句中必须有一个"我遇到了问题"的事由，而不是"我在问规则"。
     ★ 关键区分：「为什么加99换购的要运费啊」= 被收了费（售后）；
                「运费多少」= 问规则（售前）。前者是"我遇到了"，后者是"是什么"。
  闸门 B · 自足性：不以指代/连接词开头（那意味着在回指上文）。
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

FILLER_RE = re.compile(
    r"^(嗯|哦|啊|呃|额|好吧|好的|行|是|对|不|在|有|没|可以|谢谢|麻烦|亲|"
    r"什么|怎么|为什么|哪|多少|几个|多久)[啊呢吧吗么了~！!。.？?，,、\s]*$"
)

# ── 闸门 A：售后事由（★ 注意：不含裸的"快递/物流"，那两个词售前也天天说）──
CAUSE = {
    "要退钱": r"退款|退钱|退运费|退差价|返现|返钱|钱.{0,4}没|没.{0,3}退",
    "要退货": r"退货|退换|换货|换新|换个|换一个|退掉|不要了|退回去|寄回|退回",
    "要补发": r"补发|补寄|重发|重新发|重寄|再发|发一个|漏发|少发|缺件|少件|发错|错发|漏了|少给",
    "要赔付": r"赔偿|补偿|赔|索赔|差价|优惠券|返现",
    "要投诉": r"投诉|差评|维权|举报|曝光|315|工商|消协",
    "质量事由": r"质量|瑕疵|破损|破洞|破了|坏了|坏了|裂|开胶|开线|线头|次品|假货|"
                r"变质|过期|发霉|异味|味道|不好用|不能用|坏了|划痕|脏|褪色|掉色",
    "物流异常": r"没收到|未收到|没到|没有收到|收不到|没见|不见了|丢了|丢件|"
                r"签收|没送|不送|没派|未派|物流.{0,4}(不动|没动|不更新|没更新)|"
                r"催|加急|快点|多久|几天|什么时候",
    "要发票": r"发票|开票",
    "要改址": r"改地址|修改地址|改下地址|换地址|改收货",
}
CAUSE_RE = {k: re.compile(v) for k, v in CAUSE.items()}

PRESALE_RE = re.compile(
    r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜|"
    r"发什么快递|哪家快递|什么快递|发货吗|能到吗|可以到吗|支持.{0,3}吗$"
)
# ── 闸门 B：自足性（开头即回指/承接 → 是对话中段）──
ANAPHORA_RE = re.compile(
    r"^(对|是|不|好|嗯|哦|啊|这|那|它|他|她|其|该|此|还|也|又|就|但|而|"
    r"所以|因此|然后|接着|而且|不过|可是|另外|同时|另外|再|继续|已经|刚刚|刚才)"
)


def clean(s: str) -> str:
    s = re.sub(r"^(亲[，,、\s]*)+", "", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def gate_a(s: str) -> list[str]:
    """命中的事由名（可能多个）。空 = 没有可执行的售后事由。"""
    return [k for k, rx in CAUSE_RE.items() if rx.search(s)]


def gate_b(s: str) -> bool:
    return not ANAPHORA_RE.match(s)


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
        if u:
            pairs.append((u, a))

uniq = sorted({u for u, _ in pairs})
print(f"客户话术去重后：{len(uniq)}\n")

s1 = [s for s in uniq if len(s) >= 10 and not FILLER_RE.match(s) and not PRESALE_RE.search(s)]
print(f"闸门 0（旧标准：长度≥10 + 非语气 + 非售前词）      → {len(s1)}")
s2 = [s for s in s1 if gate_a(s)]
print(f"闸门 A（+ 必须有售后事由，而非只是提到快递）      → {len(s2)}")
s3 = [s for s in s2 if gate_b(s)]
print(f"闸门 B（+ 不以指代/承接词开头，即自足、能独立成立）→ {len(s3)}")

print("\n" + "=" * 70)
print("被闸门 A 滤掉的（提到售后词但**没有事由**）—— 抽 22，核对滤得对不对")
drop_a = [s for s in s1 if not gate_a(s)]
step = max(1, len(drop_a) // 22)
for i, s in enumerate(drop_a[::step][:22], 1):
    print(f"  {i:>2}. {s}")

print("\n" + "=" * 70)
print("被闸门 B 滤掉的（有事由但在回指上文）—— 抽 22")
drop_b = [s for s in s2 if not gate_b(s)]
step = max(1, len(drop_b) // 22)
for i, s in enumerate(drop_b[::step][:22], 1):
    print(f"  {i:>2}. {s}")

print("\n" + "=" * 70)
print("★ 两道闸门都通过的池子 —— 抽 30，这才是要接进 corpus 的东西")
step = max(1, len(s3) // 30)
for i, s in enumerate(s3[::step][:30], 1):
    print(f"  {i:>2}. {s}")

print("\n" + "=" * 70)
print("事由分布（只统计通过全部闸门的句子）：")
cnt = Counter()
for s in s3:
    for k in gate_a(s):
        cnt[k] += 1
for k, c in cnt.most_common():
    print(f"  {k:8s} {c:>5}  {c/len(s3)*100:5.1f}%")

print("\n" + "=" * 70)
print("现有 corpus 的 style 分布对照（分类器要在这个新池子上重新校验）：")
print(f"  新池子（{len(s3)} 条）:", end=" ")
d = Counter(C.classify_style(s) for s in s3)
print({k: f"{d.get(k,0)}（{d.get(k,0)/len(s3)*100:.1f}%）" for k in ("explicit", "defect", "vague")})
synth = [t.body for t in C.load_corpus()]
d2 = Counter(C.classify_style(s) for s in synth)
print(f"  现有合成（{len(synth)} 条）:", {k: f"{d2.get(k,0)}（{d2.get(k,0)/len(synth)*100:.1f}%）" for k in ("explicit", "defect", "vague")})
