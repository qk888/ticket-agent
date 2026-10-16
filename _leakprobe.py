"""定量测出"我的分类器漏了多少"，而不是继续手写词典。

由来（三轮取证的共同模式）：
  _styleprobe   → 扩 40 个词，vague 仍 65%
  _replyprobe   → 想用"客服反问"当标签，但 JDDC 去标点，反问率 0.0%
  _poolprobe    → 加两道闸门后抽样，又发现 `掉屑`/`换成`/`啥时侯` 三个漏词
  ★ 每一轮我手写词典，抽样都能找出新漏词。这说明**再扩一轮也还是漏**，
    问题不在词典长度，在于"靠我看抽样补词"这个方法本身没有收敛判据。

★ 换成可测量的办法：用**客服的真实回复**当弱标签。
  JDDC 每行是一对 (客户说, 客服答)。客服答里出现"帮您催/我帮您查/给您申请"
  这类**动作承诺**，说明客服把这句话读成了"要办的事" —— 那么在读它的
  人类看来，客户大概率说清了要什么。于是：

    漏判率 ≈ P(客服去办了 | 我的分类器判 vague)

  这个数我编不出来，只能从数据里读。它给出的是**误差量级**，不是"精确对错"：
  客服也可能只是安抚（"实在抱歉呢"）。所以下面同时打印**反例抽样**，
  让"这段噪声有多大"也看得见，而不是只报一个好看的比例。

本脚本做三件事：
  ① 把三处已知漏词补进词典（`换成`/`掉屑`/错别字`啥时侯`，以及"催"的错写`崔`）
  ② 放宽闸门 B：以指代词开头但含强诉求词的句子**放回池子**（它们能独立成立）
  ③ 量出最终池子的漏判率与反例，并给出风格的最终分布
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

S = ROOT / "data" / "probe" / "samples"
P = S / "ms_ecom_train_FULL.jsonl"

# ── 词典（已补入三处漏词；错别字按变体并列）────────────────────
# 诉求词：客户指名要客服做的动作。★ 必须成对覆盖"动钱/动货/要服务动作"三类。
REQUEST_RE = re.compile(
    # 动钱
    r"退款|退钱|退运费|退个款|申请退|帮我退|能退|要退|想退|退一下|退掉|退给我|"
    # 动货
    r"退货|退换|换货|换新|换个|换一个|换成|换吗|换一下|不要了|不想要|寄回|退回|拒收|"
    # 要服务动作（这是上一版最缺的一类）
    r"补发|补寄|重发|重新发|重寄|再发|发一个|补一下|"
    r"催|(崔)|加急|快点|尽快|"
    r"帮我查|帮我问|帮我联系|帮我催|帮我看看|帮我处理|帮我办|查一下|查下|问一下|问下|核实|确认下|"
    r"开.{0,4}发票|开发票|发票|开票|"
    r"改地址|修改地址|改下地址|换地址|改收货|"
    # 要结果
    r"赔偿|补偿|赔钱|给个说法|给说法|处理一下|怎么处理|怎么解决|给个答复|给答复|给我解决|解决一下|给个回复"
)
DEFECT_RE = re.compile(
    r"瑕疵|破损|破洞|破了|坏|裂|开胶|开线|线头|次品|假货|变质|过期|发霉|异味|"
    r"掉屑|掉毛|掉色|褪色|少|缺|漏|脏|划痕|质量|不好用|不能用|没法用|有问题"
)
CAUSE_RE = re.compile(
    r"退款|退钱|退货|退换|换货|换新|换成|换吗|不要了|补发|补寄|重发|漏发|少发|缺件|少件|发错|错发|"
    r"赔偿|补偿|投诉|差评|维权|发票|"
    r"质量|瑕疵|破损|破洞|破了|坏|裂|开胶|开线|掉屑|掉毛|变质|过期|发霉|异味|划痕|脏|褪色|"
    r"没收到|未收到|没到|没有收到|收不到|没见|不见了|丢了|丢件|签收|没送|不送|没派|未派|"
    r"物流.{0,4}(不动|没动|不更新|没更新)|催|崔|加急|改地址|改运费"
)
PRESALE_RE = re.compile(
    r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜|"
    r"发什么快递|哪家快递|什么快递|发啥快递|发货吗|能到吗|可以到吗"
)
FILLER_RE = re.compile(
    r"^(嗯|哦|啊|呃|额|好吧|好的|行|是|对|不|在|有|没|可以|谢谢|麻烦|亲|"
    r"什么|怎么|为什么|哪|多少|几个|多久)[啊呢吧吗么了~！!。.？?，,、\s]*$"
)
ANAPHORA_RE = re.compile(
    r"^(对|是|不|好|嗯|哦|啊|这|那|它|他|她|其|该|此|还|也|又|就|但|而|"
    r"所以|因此|然后|接着|而且|不过|可是|另外|同时|再|继续|已经|刚刚|刚才)"
)
# 客服的"动作承诺"（弱标签：客服把这句话读成了要办的事）
ACT_RE = re.compile(
    r"帮您|给您|为您|已为您|已经帮|马上|立刻|尽快|这就|去帮|安排|申请|处理|"
    r"反馈|催|补发|补寄|重发|登记|核实|联系|加急|备注|转|跟进|查一下|查下"
)
SOOTHE_RE = re.compile(r"抱歉|不好意思|对不起|理解")


def clean(s: str) -> str:
    s = re.sub(r"^(亲[，,、\s]*)+", "", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def classify(s: str) -> str:
    if not REQUEST_RE.search(s):
        return "vague"
    return "defect" if DEFECT_RE.search(s) else "explicit"


def keep(s: str) -> bool:
    if len(s) < 10 or FILLER_RE.match(s) or PRESALE_RE.search(s):
        return False
    if not CAUSE_RE.search(s):
        return False
    # 闸门 B 放宽：以指代词开头，但含强诉求词的，仍能独立成立 → 放回
    if ANAPHORA_RE.match(s) and not REQUEST_RE.search(s):
        return False
    return True


# ── 读 ────────────────────────────────────────────────────
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

# 正文去重时，回复取第一条（同一句话不同轮次回复可能不同，取任一即可）
seen: dict[str, str] = {}
for u, a in pairs:
    if u not in seen:
        seen[u] = a

pool = [(u, a) for u, a in seen.items() if keep(u)]
n = len(pool)
print(f"客户话术去重：{len(seen)}  →  过闸门：{n}\n")

d = Counter(classify(u) for u, _ in pool)
for k in ("explicit", "defect", "vague"):
    print(f"  {k:9s} {d.get(k,0):>5}  {d.get(k,0)/n*100:5.1f}%")

# ── ★ 漏判率：客服到底有没有去办 ───────────────────────────
print("\n" + "=" * 70)
print("★ 漏判率测量：客服回复里出现'动作承诺'的比例")
print("  （客户说清了要什么 → 客服去办；我判 vague 却有动作承诺 = 我漏了）")
for sty in ("explicit", "defect", "vague"):
    sel = [a for u, a in pool if classify(u) == sty]
    if not sel:
        continue
    act = sum(1 for a in sel if ACT_RE.search(a))
    print(f"  我判 {sty:9s}（{len(sel):>5} 条）→ 客服动作承诺率 {act/len(sel)*100:5.1f}%")

print("\n" + "=" * 70)
print("★ 我判 vague 但客服**去办了**的 —— 抽 25，这些是候选漏判")
miss = [(u, a) for u, a in pool if classify(u) == "vague" and ACT_RE.search(a)]
print(f"共 {len(miss)} 条（占 vague 的 {len(miss)/d.get('vague',1)*100:.1f}%）")
step = max(1, len(miss) // 25)
for i, (u, a) in enumerate(miss[::step][:25], 1):
    print(f"  {i:>2}. 客户：{u[:50]}")
    print(f"      客服：{a[:50]}")

print("\n" + "=" * 70)
print("★ 我判 vague 且客服**只是安抚**的 —— 抽 20（这些判对了：客户确实没说清）")
ok = [(u, a) for u, a in pool if classify(u) == "vague" and SOOTHE_RE.search(a) and not ACT_RE.search(a)]
print(f"共 {len(ok)} 条")
step = max(1, len(ok) // 20)
for i, (u, a) in enumerate(ok[::step][:20], 1):
    print(f"  {i:>2}. 客户：{u[:50]}")
    print(f"      客服：{a[:50]}")

print("\n" + "=" * 70)
print("★ 最终池子抽样 30")
step = max(1, n // 30)
for i, (u, _) in enumerate(pool[::step][:30], 1):
    print(f"  {i:>2}. {u[:70]}")
