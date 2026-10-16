"""判定 `_REQUEST_RE` 该收敛到什么 —— 以"动作集内可执行"为判据。

背景（前几轮取证已坐实）：
  - `_REQUEST_RE` 只认"退/换"家族，于是把"要服务动作"的句子全判成 vague。
  - 真实 JDDC 语料里 37.9% 是物流类、补发/赔付/改址/发票合计 15%+。
  - 但这些客户**说得非常清楚**，只是要的不是退款退货。
  - 所以 vague 的现含义是「诉求不在我的动作集里」，不是「客户没说清」。

本脚本要回答的是**新判据该怎么写**，而不是"哪个档 vague 更少"：
  A 现状            = 退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下
  A2 +拦截家族       = A + 拒收|不要了|不想要|寄回|退回|退掉|拦截   ← 对应 R3 的 block_logistics
  B  +要结果(超集)    = A2 + 补发/赔付/发票/改址                    ← 系统**没有**这些动作
  C  +要过程(超集)    = B + 催查问家族                              ← 系统**没有**这些动作

判据：A2 是"动作集内可执行"的上界。B/C 里那些动作系统办不到，
      把它们判成 explicit 会让工单正文点着一个办不到的动作，
      而 golden 由 `rules.decide()` 按订单数据推出（如 R1 退款）→ 语义打架。

用法：python _actionprobe.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"

A = r"退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下"
INTERCEPT = r"拒收|不要了|不想要|寄回|退回|退掉|拦截"
RESULT = r"补发|补寄|重发|重新发|再发|补上|补齐|漏发|少发|发错|赔偿|补偿|赔钱|索赔|差价|返现|退运费|改地址|修改地址|换地址|开发票|开票"
PROCESS = r"帮我查|帮我问|帮我联系|帮我催|帮我处理|帮我看看|帮我看下|帮我解决|查一下|查下|查询|核实|问一下|问下|联系快递|催一下|催下|催催|催件|加急|给个说法|怎么处理|怎么解决|给个答复|给我解决|处理一下|解决一下"
DEFECT = r"瑕疵|破损|破洞|破了|划痕|开胶|开线|线头|次品|假货|变质|损坏|脏|裂|掉色|褪色|质量"


def style_of(text: str, request_re: re.Pattern[str], defect_re: re.Pattern[str]) -> str:
    if not request_re.search(text):
        return "vague"
    return "defect" if defect_re.search(text) else "explicit"


def main() -> int:
    rows: list[dict[str, str]] = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    n = len(rows)
    print(f"样本：{SRC.name}  {n} 条\n")

    cands = {
        "A  现状": A,
        "A2 +拦截": f"{A}|{INTERCEPT}",
        "B  +要结果": f"{A}|{INTERCEPT}|{RESULT}",
        "C  +要过程": f"{A}|{INTERCEPT}|{RESULT}|{PROCESS}",
    }
    d = re.compile(DEFECT)
    dist: dict[str, dict[str, int]] = {}
    print(f"{'候选':<12}{'explicit':>10}{'defect':>9}{'vague':>9}{'vague%':>9}")
    for name, pat in cands.items():
        rx = re.compile(pat)
        c: Counter[str] = Counter(style_of(r["user"], rx, d) for r in rows)
        dist[name] = dict(c)
        print(
            f"{name:<12}{c['explicit']:>10}{c['defect']:>9}{c['vague']:>9}"
            f"{c['vague'] / n * 100:>8.1f}%"
        )

    rx_a = re.compile(A)
    rx_a2 = re.compile(f"{A}|{INTERCEPT}")
    moved = [r["user"] for r in rows if not rx_a.search(r["user"]) and rx_a2.search(r["user"])]
    print(f"\n★ A → A2 从 vague 挪进 explicit/defect：{len(moved)} 条")
    rng = random.Random(7)
    for s in rng.sample(moved, min(15, len(moved))):
        print(f"    {s[:62]}")

    # 反向检查：A2 新纳入的这批里，有多少其实是"办不到的动作"混着
    print("\n★ A2 的 explicit/defect 池里残留的'办不到的动作'（该判 vague 的正则漏网）：")
    rx_b = re.compile(f"{A}|{INTERCEPT}|{RESULT}|{PROCESS}")
    leak = [
        r["user"]
        for r in rows
        if rx_a2.search(r["user"]) and not rx_b.search(r["user"])
    ]
    print(f"    实际为 0 条属预期（A2 ⊂ B），此处只确认集合关系：{len(leak)}")

    # 关键对照：如果按 B/C 判 explicit，会把这些"系统办不到"的句子放进 L1/L2 池
    rx_b_only = re.compile(RESULT)
    only_result = [
        r["user"]
        for r in rows
        if rx_b_only.search(r["user"]) and not rx_a2.search(r["user"])
    ]
    print(f"\n★ 只有'办不到的动作'（补发/赔付/发票/改址）、没有退换拦截词的：{len(only_result)} 条")
    print("    B 档会把这批判成 explicit → 正文点着办不到的动作，golden 却是 R1 退款：")
    for s in rng.sample(only_result, min(12, len(only_result))):
        print(f"    {s[:62]}")

    rx_p_only = re.compile(PROCESS)
    only_process = [
        str(r["user"])
        for r in rows
        if rx_p_only.search(str(r["user"]))
        and not rx_a2.search(str(r["user"]))
        and not rx_b_only.search(str(r["user"]))
    ]
    print(f"\n★ 只有'催查问'的：{len(only_process)} 条")
    for s in rng.sample(only_process, min(12, len(only_process))):
        print(f"    {s[:62]}")

    print("\n★ vague 池（A2 判据）的事由构成 —— 这些是系统办不到、该走澄清/转人工的：")
    rx_b2 = re.compile(f"{A}|{INTERCEPT}")
    v = [r for r in rows if not rx_b2.search(r["user"])]
    cc: Counter[str] = Counter(
        c for r in v for c in str(r.get("cause_all") or "").split(",") if c
    )
    for k, cnt in cc.most_common():
        print(f"    {k:<6}{cnt:>6}  {cnt / len(v) * 100:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
