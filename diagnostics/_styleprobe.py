"""验证：现有 `corpus.classify_style` 的规则在 JDDC 真实话术上还准不准。

为什么必须验：现有的两条正则（`_REQUEST_RE` / `_DEFECT_RE`）是照着
`ecom_aftersale.jsonl` 那种**带标点的合成句**写的。JDDC 的真实客户话术
是**无标点、口语、常有错别字**的（"我的东西没到嗯你看下"），同一套正则
可能大面积失配 —— 失配的表现是"本来能明确归类的句子被归成 vague"，
而 vague 在业务上对应 L3（要澄清），于是**评测集分布会被悄悄改写**。

所以这里不是"跑一下看看"，而是逐条打印判定依据，人工核对误判。
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

print("=== 现有规则（对着 862 条合成语料写的）===")
print(f"_REQUEST_RE = {C._REQUEST_RE.pattern}")
print(f"_DEFECT_RE  = {C._DEFECT_RE.pattern}")

# 复用 probe2 的过滤器
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


# 1) 抽出净可用话术
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
uniq = sorted(set(users))
kept = [s for s in uniq if usable(s)]
print(f"\n净可用：{len(kept)} 条（从 {len(uniq)} 条去重话术里筛出）")

# 2) 用现有 classify_style 分类
print("\n=== 现有 classify_style 在 JDDC 上的分布 ===")
styles = Counter(C.classify_style(s) for s in kept)
print(f"{dict(styles)}")
for k, v in styles.most_common():
    print(f"  {k:9s} {v:5d}  {v/len(kept)*100:5.1f}%")

print("\n=== 对照：在现有 862 条合成语料上的分布 ===")
synth = list(C.load_corpus())
synth_styles = Counter(t.style for t in synth)
print(f"{dict(synth_styles)}")
for k, v in synth_styles.most_common():
    print(f"  {k:9s} {v:5d}  {v/len(synth)*100:5.1f}%")

# 3) 失配取证：被判 vague 的句子里，有多少其实明显有诉求
print("\n=== ★ 重点：被判 vague 的 JDDC 句子抽样 ===")
print("（vague 在业务上 = L3 要澄清。如果这些句子其实说清了要什么，")
print("  说明 _REQUEST_RE 失配，会把不该算 L3 的工单算成 L3）")
vague = [s for s in kept if C.classify_style(s) == C.STYLE_VAGUE]
print(f"共 {len(vague)} 条，随机抽 30：")
step = max(1, len(vague) // 30)
for i, s in enumerate(vague[::step][:30]):
    has_defect = bool(C._DEFECT_RE.search(s))
    print(f"  {i+1:>2}. [defect_re={has_defect}] {s}")

print("\n=== 对照：被判 explicit / defect 的抽样 ===")
for st in (C.STYLE_EXPLICIT, C.STYLE_DEFECT):
    xs = [s for s in kept if C.classify_style(s) == st]
    print(f"\n-- {st}（{len(xs)} 条）随机 12 --")
    stp = max(1, len(xs) // 12)
    for s in xs[::stp][:12]:
        print(f"   {s}")

# 4) 哪些"诉求词"在 JDDC 里出现了但 _REQUEST_RE 没覆盖
print("\n=== ★ _REQUEST_RE 漏掉的诉求表达（在 vague 句里高频出现） ===")
cand = re.compile(
    r"退|换|赔|补|修|要|给|处理|解决|发|寄|查|问|说|看|弄|搞|来|到|买|拍|下单|申请|要求|希望|必须|赶紧|马上|尽快"
)
cnt = Counter()
for s in vague:
    for w in set(re.findall(r"[退换赔补修要处解发寄查问说看弄搞来到买拍申有希必赶马尽][\u4e00-\u9fa5]?", s)):
        cnt[w] += 1
print(f"vague 句 {len(vague)} 条中，字符 bigram 高频前 25：")
for w, c in cnt.most_common(25):
    print(f"  {w!r}: {c}")
