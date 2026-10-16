"""把 JDDC 真实客服语料预处理成工单正文语料（一次性构建脚本）。

★ 为什么要有这一步，而不是让 `corpus.py` 直接读原始文件：

  `data/probe/samples/ms_ecom_train_FULL.jsonl` 有 **99.8 MB / 226,600 行**，
  而 `corpus.load_corpus()` 是启动路径上的同步调用。直接读它会让每次造数都
  多花十几秒，且原始文件不该进仓库。所以流程拆成两段：

      tools/build_corpus_jddc.py（本脚本，离线跑一次）
          → data/raw/jddc_aftersale.jsonl（净可用语料，几百 KB）
              → corpus.load_corpus()（启动时读小文件）

  好处是**过滤规则可复核**：本脚本把每条被丢掉的句子连同丢弃原因都打印出来，
  而 corpus.py 里只留"读文件"这一件事。

★ 原始语料是什么（两路来源，是同一批数据的两种切法）：

  1. `ms_ecom_train_FULL.jsonl`（226,600 行）—— 上游切好的 (user 问,
     assistant 答) **单轮对**，每行只含一问一答，不携带上下文。
  2. `jddc_multi_train_FULL.txt`（500,000 行）—— **原始上下文版**，形如

         第一句<s>第二句<s>...<s>最后一句 \t 对这一句的回复

     左边是客户到这一轮为止说过的**全部话**，右边才是对**最后一句**的回复
     （文件名 `context_response.txt` 就是这个意思）。★ 所以从它里面只能取
     **第一句**当工单正文 —— 那是会话开场白，天然自足；后面的句子全是
     对话中段，回指上文。

★ 这两路的关系决定了它们必须**一起**用，而不是二选一：

  单轮源 226,600 行里能过闸门的有 6,226 条；多轮源额外提供 4,681 条
  单轮源里没有的可用句（实测 `_multiquality.py`）—— 合计 10,217 条，
  比只用单轮源涨 77%。多轮源独自用不了：它 500,000 行去重后只有
  128,961 条唯一开场白，且大量轮次的开场白是同一句。

  ★ 但**回复的配对必须分源对待**（实测 `_multipair.py`）：多轮行的回复
  属于最后一句，与第一句配不上 —— 新增的 4,681 条里只有 43.8%（来自
  单轮行）有正确回复，另 56.2% 只能留空。可接受的依据是 `bot` 字段在
  运行时**无人读取**（`grep '"bot"'` 全项目只命中本文件一处写入），
  它只用于"漏判率"弱标签统计。**留空好过填错** —— 一个填错的字段
  迟早会被当成对的用，错位的回复会让统计得出反向结论。

★ 这一点直接决定了过滤必须做什么：

  「哦快递」「啥快递」「嗯嗯好的给我快递单号完了」这类句子在原始数据里
  占很大比例，它们是**对话中段**，回指上文，脱离了上下文根本不能当工单
  正文（工单正文是客户**主动发起**时说的第一句）。所以本脚本的闸门不只
  是"有没有售后词"，还包括"这句话能不能独立成立"。

★ 三道闸门（按代价从低到高）：

  0. 基本可用：长度 ≥10 字、不是纯语气词、不是售前咨询
  1. 有售后事由：句中必须有一个"我遇到了问题"的事由，而不是"我在问规则"。
     ★ 关键区分：「为什么加99换购的要运费啊」= 被收了费（售后）；
                「运费多少」= 问规则（售前）。前者是"我遇到了"，后者是"是什么"。
     ★ 裸的「快递/物流」**不算**事由 —— 问快递方式是纯售前，实测占了大头。
  2. 自足性：不以指代/承接词开头（那意味着在回指上文）。
     放宽：以指代词开头但含**强诉求词**的句子仍保留 ——
     「那我申请退款了帮我退一下呢」能独立成立，不该被杀。

★ 已知的残余噪声（不掩盖）：

  客服的回复里出现"动作承诺"（`帮您催下哦`）时，说明这句话在人类读来
  是"有要办的事"的。本脚本用它做**弱标签**量了一下：我判 vague 的句子里，
  仍有 27% 客户的回复是客服去办了 —— 但这**不是纯漏判**，因为客服也可能
  只是出于服务意识主动去办（「我知道可是我一个包裹都没收到啊」→「稍等我查下」）。
  这个数是**上界**。详见 `_leakprobe.py` 的输出。

用法：
    python tools/build_corpus_jddc.py            # 构建
    python tools/build_corpus_jddc.py --dry-run  # 只看统计不写文件
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "probe" / "samples" / "ms_ecom_train_FULL.jsonl"
# ★ 第二路来源（2026-10 接入）。它与 SRC 是**同一批数据的两种切法**：
#   SRC 是上游切好的 (user 问, assistant 答) 单轮对，226,600 行；
#   MULTI_SRC 是**原始上下文版**，500,000 行，形如
#       「第一句<s>第二句<s>...<s>最后一句 \t 对这一句的回复」
#   左边是**上下文**（客户到这一轮为止说过的全部话），右边才是对
#   **最后一句**的回复 —— 文件名 `context_response.txt` 就是这个意思。
#   所以从多轮行里只能取**第一句**当工单正文（它是会话开场白，
#   天然自足、不依赖上文），而同行那个回复**属于最后一句，不属于第一句**。
MULTI_SRC = ROOT / "data" / "probe" / "samples" / "jddc_multi_train_FULL.txt"
DST = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"

MIN_LEN = 10
MAX_LEN = 120  # 超过这个长度就不是"一句话说清诉求"，而是多话题混杂

# ── 闸门 1：售后事由。每条事由带一个"工单标题"，派生用法见文件末尾说明 ──
CAUSES: tuple[tuple[str, str, str], ...] = (
    # (事由键, 工单标题, 正则)
    ("退款", "退款申请", r"退款|退钱|退运费|退个款|申请退|帮我退|能退|要退|想退|退一下|退给我|钱.{0,4}(没|不).{0,3}退"),
    ("退货", "退换货申请", r"退货|退换|换货|换新|换个|换一个|换成|换吗|换一下|不要了|不想要|寄回|退回|拒收|退掉"),
    ("补发", "补发申请", r"补发|补寄|重发|重新发|重寄|再发|发一个|补一下|漏发|少发|缺件|少件|发错|错发|漏了|少给"),
    ("赔付", "补偿诉求", r"赔偿|补偿|赔钱|索赔|差价|优惠券|返现"),
    ("投诉", "客户投诉维权", r"投诉|差评|维权|举报|曝光|315|工商|消协|淘宝介入"),
    # ★ 「质量」不能单独当词用 —— 售前也在说质量。实测混进来的：
    #   「店里哪个质量最好我给新生儿买的」「不知道你们那儿的质量怎么样」
    #   所以这里要求它出现在**问题语境**里（质量差/质量问题/质量太…）。
    #   同理去掉裸的 `坏`/`脏`/`味道`：它们的误召回比真召回多。
    # ★ `质量不是` 是核对漏判时补的：实测「我发现这个现在包装质量不是很好哎
    #   这个五包装是否可以全部退」被踢出了语料 —— 它既是质量投诉又带诉求。
    ("质量", "商品质量问题", r"瑕疵|破损|破洞|破了|划痕|开胶|开线|线头|次品|假货|"
                            r"变质|过期|发霉|异味|坏了|坏的|坏掉|不好用|不能用|没法用|"
                            r"褪色|掉色|掉屑|掉毛|烂了|裂了|碎了|压坏|压扁|"
                            r"质量(问题|差|不好|不行|太|真|有|很|堪忧|不是)"),
    ("物流", "物流异常", r"没收到|未收到|没到|没有收到|收不到|没见|不见了|丢了|丢件|签收|没送|不送|"
                        r"没派|未派|物流.{0,4}(不动|没动|不更新|没更新)|催|崔|加急|快点|尽快|什么时候|多久"),
    ("发票", "发票开具", r"发票|开票"),
    ("改址", "改收货地址", r"改地址|修改地址|改下地址|换地址|改收货|改运费"),
)

# ── 闸门 0 的排除项 ────────────────────────────────────────
PRESALE_RE = re.compile(
    r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜|"
    r"发什么快递|哪家快递|什么快递|发啥快递|发货吗|能到吗|可以到吗|有活动吗|送什么|"
    # ★ 下面这批是实测从 vague 池抽样里抓出来的售前句（问商品属性/规则，
    #   而不是"我遇到了问题"）。加它们的依据是抽样原文，不是想当然：
    #   「店里哪个质量最好我给新生儿买的」「是正品吗」「保质期多久」
    r"哪个质量|哪款|是正品|真的吗|保质期|好吃吗|好用吗|好喝吗|怎么吃|怎么用|"
    r"有什么区别|有货吗|有现货吗|买哪个|选哪个|划算"
)
FILLER_RE = re.compile(
    r"^(嗯|哦|啊|呃|额|好吧|好的|行|是|对|不|在|有|没|可以|谢谢|麻烦|亲|"
    r"什么|怎么|为什么|哪|多少|几个|多久)[啊呢吧吗么了~！!。.？?，,、\s]*$"
)

# ── 闸门 2：自足性 ────────────────────────────────────────
ANAPHORA_RE = re.compile(
    r"^(对|是|不|好|嗯|哦|啊|这|那|它|他|她|其|该|此|还|也|又|就|但|而|"
    r"所以|因此|然后|接着|而且|不过|可是|另外|同时|再|继续|已经|刚刚|刚才)"
)
# 强诉求词：含它就说明这句话即便以指代词开头也能独立成立
STRONG_REQUEST_RE = re.compile(
    r"退款|退货|退钱|退换|换货|换新|换成|补发|补寄|重发|拒收|寄回|退回|"
    r"赔偿|补偿|投诉|差评|发票|开票|改地址|帮我把|帮我退|帮我查|帮我催|帮我问"
)

CAUSE_RE = [(key, title, re.compile(pat)) for key, title, pat in CAUSES]


def clean(s: str) -> str:
    """去 `亲` 前缀、压空白。`亲` 是客服话术，客户不会自称。"""
    s = re.sub(r"^(亲[，,、\s]*)+", "", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def causes_of(text: str) -> list[str]:
    return [key for key, _, rx in CAUSE_RE if rx.search(text)]


def reject_reason(text: str) -> str | None:
    """返回丢弃原因；None 表示通过。顺序即闸门顺序。"""
    if len(text) < MIN_LEN:
        return "太短"
    if len(text) > MAX_LEN:
        return "太长"
    if FILLER_RE.match(text):
        return "纯语气"
    if PRESALE_RE.search(text):
        return "售前咨询"
    if not causes_of(text):
        return "无事由"
    if ANAPHORA_RE.match(text) and not STRONG_REQUEST_RE.search(text):
        return "回指上文"
    return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_single_source(path: Path) -> list[tuple[str, str]]:
    """上游切好的单轮对：`{"messages": [system, user, assistant]}`。

    返回 (客户话术, 客服回复)。这里**不 clean** —— 去重键必须是清洗后的文本，
    统一在主流程里做，否则 `亲，我要退款` 与 `我要退款` 会被当成两条。
    """
    out: list[tuple[str, str]] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = reply = ""
            for m in obj.get("messages") or []:
                if m.get("role") == "user":
                    user = (m.get("content") or "").strip()
                elif m.get("role") == "assistant":
                    reply = (m.get("content") or "").strip()
            if user:
                out.append((user, reply))
    return out


def read_multi_source(path: Path) -> list[tuple[str, str]]:
    """原始上下文版：`第一句<s>第二句<s>...<s>最后一句 \\t 对最后一句的回复`。

    ★ 只取**第一句**当工单正文，因为：

      工单正文是客户**主动发起**时说的第一句话，不含上下文；而第一句正是
      会话开场白，天然自足（「你好货收到了不过有一包破损了」）。
      后面的句子全是对话中段，回指上文，脱离上下文不能当正文 ——
      这与 `reject_reason` 里那道「回指上文」闸门要挡的是同一类东西。

    ★ 回复的配对规则（★ 这是本函数最容易出错的地方）：

      右侧那个回复属于**最后一句**，不属于第一句。实测印证：

          我拍了一组我在拍一组可以嘛<s>那就等你们处理喽 \t 好的亲退了

      「好的亲退了」回的是「等你们处理」，不是「能不能再拍一组」。

      所以：整行只有一个句子的（没有 `<s>`），回复与正文**天然配对**；
      有多个句子的，回复**配不上**，一律留空 —— 见返回值第二项的说明。
      留空不是偷懒，是**不让一个填错的字段冒充正确答案**：`bot` 目前在
      运行时无人读取（只用于"漏判率"弱标签统计），但一旦有人当它对，
      错位的回复会让统计得出反向结论。

    返回 (正文, 回复)，其中多轮行的回复为 `""`。
    """
    out: list[tuple[str, str]] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            left, right = parts[0], parts[1]
            # ★ 这份语料是**空格分词**过的（"买 二份 有没有 少点 呀"），
            #   要先去掉词间空格才是自然中文，否则去重与闸门全部失准。
            single_turn = "<s>" not in left
            first = left.split("<s>")[0].replace(" ", "").strip()
            if not first:
                continue
            reply = right.replace(" ", "").strip() if single_turn else ""
            out.append((first, reply))
    return out


def merge_sources(*sources: list[tuple[str, str]]) -> dict[str, str]:
    """按清洗后的正文合并多路来源，**优先保留配得上回复的那一条**。

    ★ 去重键用清洗后的文本：`亲，我要退款` 与 `我要退款` 是同一条，
      若在去重后才清洗，语料里会留下两条只差客服话术前缀的记录。

    ★ 冲突时的取舍：非空回复 > 空回复。同一句话既出现在单轮源、
      又作为多轮行第一句出现时，取单轮源那份 —— 它的回复是真的。
    """
    best: dict[str, str] = {}
    for src in sources:
        for raw_user, raw_reply in src:
            user = clean(raw_user)
            if not user:
                continue
            reply = clean(raw_reply)
            prev = best.get(user)
            if prev is None or (not prev and reply):
                best[user] = reply
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    for p, label in ((SRC, "单轮源"), (MULTI_SRC, "多轮源")):
        if not p.exists():
            print(f"{label}不存在：{p}")
            print("（它们由子代理下载到 data/probe/samples/，体积过大不进仓库）")
            return 1

    single = read_single_source(SRC)
    multi = read_multi_source(MULTI_SRC)
    print(f"单轮源：{len(single)} 行")
    print(f"多轮源：{len(multi)} 行"
          f"（其中 {sum(1 for _t, r in multi if r)} 行的回复与正文配对）")

    first_reply = merge_sources(single, multi)
    paired = sum(1 for v in first_reply.values() if v)
    print(f"\n合并去重后：{len(first_reply)} 条客户话术"
          f"，其中 {paired} 条有配对回复（{paired / len(first_reply):.1%}）\n")

    dropped: Counter[str] = Counter()
    dumps: dict[str, list[str]] = {}
    kept: list[dict[str, str]] = []
    for text, reply in first_reply.items():
        why = reject_reason(text)
        if why:
            dropped[why] += 1
            dumps.setdefault(why, []).append(text)
            continue
        cs = causes_of(text)
        kept.append(
            {
                # scene 是**派生**字段：JDDC 不带标签，这里取命中的首个事由，
                # 只用于生成工单标题，不参与任何判定（详见文件末尾）。
                "scene": cs[0],
                "user": text,
                "cause_all": ",".join(cs),
                "bot": reply,
            }
        )

    print(f"{'丢弃原因':<10}{'条数':>8}")
    for k, c in dropped.most_common():
        print(f"  {k:<10}{c:>6}")
    print(f"\n净可用：{len(kept)} 条"
          f"（其中 {sum(1 for r in kept if r['bot'])} 条带回复）")

    causes = Counter(c for row in kept for c in row["cause_all"].split(","))
    print(f"\n事由分布（一条可命中多个事由）：")
    n = len(kept)
    for k, c in causes.most_common():
        print(f"  {k:<6}{c:>6}  {c/n*100:5.1f}%")

    lens = sorted(len(r["user"]) for r in kept)
    print(
        f"\n长度：min={lens[0]} p50={lens[len(lens)//2]} p90={lens[int(len(lens)*0.9)]} "
        f"max={lens[-1]} mean={sum(lens)/len(lens):.1f}"
    )

    print("\n被丢弃的抽样（每类 6 条，核对丢得对不对）：")
    for k, c in dropped.most_common():
        print(f"\n  【{k}】共 {c} 条")
        step = max(1, len(dumps[k]) // 6)
        for s in dumps[k][::step][:6]:
            print(f"    - {s[:64]}")

    print("\n净可用抽样 25：")
    step = max(1, len(kept) // 25)
    for i, row in enumerate(kept[::step][:25], 1):
        print(f"  {i:>2}. [{row['scene']}] {row['user'][:60]}")

    if args.dry_run:
        print("\n--dry-run，未写文件。")
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", encoding="utf-8", newline="\n") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n已写出：{DST}")
    print(f"  字节：{DST.stat().st_size}  sha256：{sha256(DST)[:32]}...")
    print("\n★ 记得把 sha256 前 16 位抄进 corpus.CORPUS_SOURCES 的溯源表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
