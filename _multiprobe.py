"""量一下 `jddc_multi_train_FULL.txt`（原始多轮）比现有单轮源多榨出多少可用正文。

★ 为什么要单独量：

  现在 `data/raw/jddc_aftersale.jsonl` 只用了 `ms_ecom_train_FULL.jsonl`
  （226,600 行，**已经切成单轮**）。而 `jddc_multi_train_FULL.txt` 是同一批
  数据的**原始多轮版**（500,000 行），多出来的 273,400 行去哪了没人知道 ——
  可能被上游切分时丢掉了，也可能只是没被切出来。

  ★ 关键的结构差异：多轮版的每一行用 `<s>` 分隔轮次，形如

      第一轮第一句<s>第一轮第二句<s>...<s>第N轮最后一句	客服回复

  而**只有每一行的第一句**是"客户主动发起时的第一句话" —— 那正是工单正文
  要的东西（工单正文不含上下文）。后面的句子全是对话中段，回指上文。

  所以这份文件的价值等于：**每个会话恰好贡献一句候选**，而候选总数
  从 226,600 涨到 500,000。本脚本量的是"多出来的候选里，有多少能过闸门"。

★ 本脚本不做判断，只打印对照；要不要接入由数字决定。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parent
MULTI = ROOT / "data" / "probe" / "samples" / "jddc_multi_train_FULL.txt"
SINGLE = ROOT / "data" / "probe" / "samples" / "ms_ecom_train_FULL.jsonl"
CUR = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"


def load_multi_first_turns(path: Path) -> list[str]:
    """取每行的第一句（`<s>` 分隔），去掉分词空格。"""
    out: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            body = line.split("\t")[0]  # 制表符左边是客户，右边是客服
            first = body.split("<s>")[0]
            # ★ 这份语料是**空格分词**过的（"买 二份 有没有 少点 呀"），
            #   要去掉词间空格才是自然中文。只去空格，不去标点。
            first = first.replace(" ", "").strip()
            if first:
                out.append(first)
    return out


def load_single_users(path: Path) -> list[str]:
    out: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in row.get("messages") or []:
                if m.get("role") == "user":
                    out.append((m.get("content") or "").strip())
                    break
    return out


def main() -> int:
    print("=" * 78)
    print("原始多轮源 vs 现有单轮源：候选量对照")
    print("=" * 78)

    multi = load_multi_first_turns(MULTI)
    single = load_single_users(SINGLE)
    print(f"\n多轮源第一句候选 : {len(multi):>9,}")
    print(f"单轮源 user 候选 : {len(single):>9,}")
    print(f"现有语料         : {sum(1 for _ in CUR.open(encoding='utf-8')):>9,}")

    # ── 去重对照 ──────────────────────────────────────────────
    mu, su = set(multi), set(single)
    print(f"\n去重后 多轮 {len(mu):,} / 单轮 {len(su):,} / 交集 {len(mu & su):,}")
    print(f"★ 多轮源额外提供 {len(mu - su):,} 条单轮源里没有的句子")

    # ── 复刻 build_corpus_jddc.py 的闸门，看两边通过率 ──────────
    # ★ 这里刻意**照抄**生产脚本的词表，而不是 import 它 ——
    #   本脚本是"要不要改生产脚本"的决策依据，若它 import 生产脚本，
    #   改动生产脚本会让本脚本的结论跟着一起动，就失去了对照的意义。
    CAUSE_RE = re.compile(
        r"退款|退钱|退运费|申请退|帮我退|能退|要退|想退|退一下|退给我|"
        r"退货|退换|换货|换新|换个|换一个|换成|换吗|换一下|不要了|不想要|寄回|退回|拒收|退掉|"
        r"补发|补寄|重发|重新发|重寄|再发|发一个|漏发|少发|缺件|少件|发错|错发|漏了|少给|"
        r"赔偿|补偿|赔钱|索赔|差价|优惠券|返现|"
        r"投诉|差评|维权|举报|曝光|315|工商|消协|介入|"
        r"瑕疵|破损|破洞|破了|划痕|开胶|开线|线头|次品|假货|变质|过期|发霉|异味|坏了|坏的|坏掉|"
        r"不好用|不能用|没法用|褪色|掉色|掉屑|掉毛|烂了|裂了|碎了|压坏|压扁|"
        r"质量(问题|差|不好|不行|太|真|有|很|堪忧|不是)|"
        r"没收到|未收到|没到|没有收到|收不到|没见|不见了|丢了|丢件|签收|没送|不送|"
        r"没派|未派|催|崔|加急|快点|尽快|什么时候|多久|"
        r"发票|开票|改地址|修改地址|改下地址|换地址|改收货|改运费"
    )
    PRESALE_RE = re.compile(
        r"有货|现货|尺码|颜色|优惠|包邮|推荐|哪个好|多少钱|怎么卖|能便宜|"
        r"发什么快递|哪家快递|什么快递|发啥快递|发货吗|能到吗|可以到吗|有活动吗|送什么|"
        r"哪个质量|哪款|是正品|真的吗|保质期|好吃吗|好用吗|好喝吗|怎么吃|怎么用|"
        r"有什么区别|有货吗|有现货吗|买哪个|选哪个|划算"
    )
    LEAD_RE = re.compile(r"^(那|这|这个|那个|它|他|她|哦|嗯|啊|呃|额|唉|诶|哎|哈|咦)")

    def keep(text: str, *, strict_lead: bool) -> bool:
        if not (10 <= len(text) <= 120):
            return False
        if PRESALE_RE.search(text):
            return False
        if not CAUSE_RE.search(text):
            return False
        if strict_lead and LEAD_RE.match(text) and not re.search(
            r"退款|退货|换货|补发|投诉|赔偿|补偿", text
        ):
            return False
        return True

    mm = [t for t in mu if keep(t, strict_lead=True)]
    ss = [t for t in su if keep(t, strict_lead=True)]
    print(f"\n过闸门 多轮 {len(mm):,} / 单轮 {len(ss):,}")
    print(f"★ 多轮额外产出 {len(set(mm) - set(ss)):,} 条单轮源里没有的可用句")

    # ── 首句 vs 句中为什么差这么多：抽看被丢的 ────────────────
    print("\n被丢的候选抽样（多轮源，前 12 条）：")
    dropped = [t for t in mu if not keep(t, strict_lead=True)]
    step = max(1, len(dropped) // 12)
    for i, s in enumerate(dropped[::step][:12], 1):
        reasons = []
        if not (10 <= len(s) <= 120):
            reasons.append(f"长度{len(s)}")
        if PRESALE_RE.search(s):
            reasons.append("售前")
        if not CAUSE_RE.search(s):
            reasons.append("无事由")
        if LEAD_RE.match(s) and not re.search(r"退款|退货|换货|补发|投诉|赔偿|补偿", s):
            reasons.append("回指")
        print(f"  {i:>2}. [{'/'.join(reasons)}] {s[:58]}")

    # ── 现有语料里有多少能被多轮源覆盖（确认同源） ────────────
    cur_texts = []
    with CUR.open(encoding="utf-8") as f:
        for line in f:
            cur_texts.append(json.loads(line)["user"])
    cov = sum(1 for t in cur_texts if t in mu)
    print(f"\n现有语料 {len(cur_texts):,} 条中，{cov:,} 条能在多轮源里逐字找到"
          f"（{cov / len(cur_texts):.1%}）")

    # ── 长度分布对照 ─────────────────────────────────────────
    print("\n长度分布（过闸门后）：")
    for name, arr in (("多轮", mm), ("单轮", ss)):
        if not arr:
            continue
        n = len(arr)
        arr_s = sorted(len(x) for x in arr)
        print(f"  {name}: min={arr_s[0]} 中位={arr_s[n // 2]} "
              f"p90={arr_s[int(n * 0.9)]} max={arr_s[-1]}")

    print("\n风格分布由 corpus.classify_style 判定（见接入后 _stylebias.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
