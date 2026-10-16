"""抽样看多轮源**额外**能提供的那 4,681 条，逐条判质量。

★ 为什么不能只看数量：

  多轮源的"每行第一句"是个**假设** —— 假设它是"客户主动发起时的第一句话"。
  如果上游按"客户连续发言 + 客服一次回复"成对切，那每行开头也可能落在
  对话中段（客户正说到一半）。若新增的几千条多半是这种半截话，接进去
  只是把语料池灌稀，指标会变好看而内容更假。

  所以本脚本**按质量分档打印原文**，让"要不要接"这个决定基于读到的句子。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
MULTI = ROOT / "data" / "probe" / "samples" / "jddc_multi_train_FULL.txt"
SINGLE = ROOT / "data" / "probe" / "samples" / "ms_ecom_train_FULL.jsonl"
import json


def load_multi_first_turns(path: Path) -> list[str]:
    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            first = line.split("\t")[0].split("<s>")[0].replace(" ", "").strip()
            if first:
                out.append(first)
    return out


def load_single_users(path: Path) -> list[str]:
    out = []
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


def keep(text: str) -> bool:
    if not (10 <= len(text) <= 120):
        return False
    if PRESALE_RE.search(text):
        return False
    if not CAUSE_RE.search(text):
        return False
    if LEAD_RE.match(text) and not re.search(r"退款|退货|换货|补发|投诉|赔偿|补偿", text):
        return False
    return True


def main() -> int:
    mu = set(load_multi_first_turns(MULTI))
    su = set(load_single_users(SINGLE))

    new_kept = sorted(t for t in mu if t not in su and keep(t))
    print(f"多轮源额外可用句：{len(new_kept):,}\n")

    # ── 分档：按"是不是一眼看着像半截话" ──────────────────────
    # ★ 判据是**结构**不是语义：句首代词/连词在中文里是回指的强信号。
    #   带强诉求词的例外（"那我申请退款"能独立成立），与生产脚本一致。
    halves = [t for t in new_kept if LEAD_RE.match(t)]
    standalone = [t for t in new_kept if not LEAD_RE.match(t)]
    print(f"  看着自足（句首不是代词/连词）：{len(standalone):,}  "
          f"({len(standalone) / len(new_kept):.1%})")
    print(f"  句首带代词/连词（可能半截）：  {len(halves):,}  "
          f"({len(halves) / len(new_kept):.1%})")

    # ── 逐档抽样打印原文（25 条） ────────────────────────────
    print("\n" + "=" * 78)
    print("新增可用句抽样 25（多轮源独有）")
    print("=" * 78)
    step = max(1, len(new_kept) // 25)
    for i, s in enumerate(new_kept[::step][:25], 1):
        mark = "半截?" if LEAD_RE.match(s) else "     "
        print(f"  {i:>2}. {mark} {s[:66]}")

    print("\n" + "=" * 78)
    print("★ 最需要警惕的一档：句首代词/连词、且无强诉求词（本不该过闸门）")
    print("=" * 78)
    if halves:
        step = max(1, len(halves) // 12)
        for i, s in enumerate(halves[::step][:12], 1):
            print(f"  {i:>2}. {s[:66]}")
    else:
        print("  （空 —— 闸门把它们全挡住了）")

    # ── 与现有语料的**长度/首字**分布对照 ────────────────────
    cur = []
    with (ROOT / "data" / "raw" / "jddc_aftersale.jsonl").open(encoding="utf-8") as f:
        for line in f:
            cur.append(json.loads(line)["user"])
    print("\n长度中位数：现有语料 {} / 新增 {}".format(
        sorted(len(x) for x in cur)[len(cur) // 2],
        sorted(len(x) for x in new_kept)[len(new_kept) // 2],
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
