"""取证：把「判为 vague 的句子」按事由摊开，看它们究竟是不是真含糊。

背景（见 corpus.py 的模块文档）：`classify_style` 现在的 vague 判据是
「没有出现退/换家族的动作词」。但新语料的事由分布显示真实售后里
**37.9% 是物流类**，而物流句里一个退/换词都不会有 —— 于是它们全被判 vague。

这里要回答的问题不是"词典够不够长"，而是：
**如果把《所有说清了要什么》的动作都算进来，vague 里还剩什么？**

残余的才是真含糊。如果残余里大部分仍是"客户其实说清楚了、只是我的动作集没有这个动作"，
那么 vague 这个名字本身就是错的 —— 它实际含义是"诉求在我的动作集之外"。

★ 这个脚本只打印，不改任何代码。它的输出直接决定 classify_style 怎么改。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "raw" / "jddc_aftersale.jsonl"

# ── 三档候选：请求词家族的覆盖范围 ──────────────────────────
# A = 现状（只认退/换）
A = r"退款|退货|退钱|退换|换货|换新|换一个|换个|能退|要退|想退|申请退|帮我退|退一下"

# B = A + 补/赔/拦截/地址/发票 —— 这些都是**点名了要什么**的动作
B = A + (
    r"|补发|补寄|重发|重新发|重寄|再发|发一个|漏发|少发|发错|错发|少给|补上|补齐"
    r"|赔偿|补偿|赔钱|索赔|差价|返现|退运费|退个运费"
    r"|拒收|寄回|退回|拦截|退掉|不要了|不想要|申请退|退我"
    r"|改地址|修改地址|改下地址|换地址|改收货地址"
    r"|开发票|开个发票|要发票|开票"
)

# C = B + "要服务动作/要答复" —— 客户点了名要做某件事，但动作是"你去查/你去催"
#     ★ 这一档是本次争议所在：它们算不算"说清了要什么"？
C = B + (
    r"|帮我查|帮我问|帮我联系|帮我催|帮我处理|帮我看看|帮我看下|帮我解决"
    r"|查一下|查下|查询|核实|问一下|问下|联系快递|催一下|催下|催催|催件|加急"
    r"|给我个说法|给个说法|怎么处理|怎么解决|给个答复|给我解决|处理一下|解决一下"
)

CANDS = {"A 现状": A, "B +要结果": B, "C +要过程": C}

# 质量瑕疵描述（政策 R2 相关，单独一个维度，不进请求词家族）
DEFECT_RE = re.compile(
    r"瑕疵|破损|破洞|破了|划痕|开胶|开线|线头|次品|假货|变质|过期|发霉|异味|"
    r"损坏|脏|裂|掉色|褪色|质量|掉屑|掉毛|漏气|不好用|不能用|没法用"
)

# 投诉/差评**刻意留在 vague 一侧**：它们表达的是不满，没点名要什么处置。
# 语料自身的证据支持这一点（维权投诉 1明确/106含糊、差评 1明确/97含糊）。
COMPLAINT_RE = re.compile(r"投诉|差评|维权|举报|曝光|工商|消协|315|淘宝介入")


def main() -> int:
    if not SRC.exists():
        print(f"语料不存在：{SRC}\n先跑 tools/build_corpus_jddc.py")
        return 1

    rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"语料：{len(rows)} 条\n")

    dist: dict[str, Counter] = {}
    vague_pools: dict[str, list[dict]] = {}
    for name, pat in CANDS.items():
        rx = re.compile(pat)
        c: Counter[str] = Counter()
        pool: list[dict] = []
        for r in rows:
            t = r["user"]
            if not rx.search(t):
                c["vague"] += 1
                pool.append(r)
            elif DEFECT_RE.search(t):
                c["defect"] += 1
            else:
                c["explicit"] += 1
        dist[name] = c
        vague_pools[name] = pool

    print(f"{'候选':<10}{'explicit':>10}{'defect':>9}{'vague':>9}{'vague%':>9}")
    n = len(rows)
    for name, c in dist.items():
        print(
            f"{name:<10}{c['explicit']:>10}{c['defect']:>9}{c['vague']:>9}"
            f"{c['vague']/n*100:>8.1f}%"
        )

    # ── 关键问题：vague 里有多少其实是"物流类、客户说得挺清楚" ──
    print("\n" + "=" * 70)
    for name in CANDS:
        pool = vague_pools[name]
        by_cause: dict[str, int] = defaultdict(int)
        no_cause = 0
        for r in pool:
            cs = [x for x in (r.get("cause_all") or "").split(",") if x]
            if cs:
                for x in cs:
                    by_cause[x] += 1
            else:
                no_cause += 1
        print(f"\n【{name}】vague={len(pool)}  事由分布（一条可多命中）：")
        for k, v in sorted(by_cause.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<6}{v:>6}  {v/len(pool)*100:5.1f}%")
        print(f"    (无事由)  {no_cause:>4}  {no_cause/len(pool)*100:5.1f}%")

    # ── 摊开看：最终候选 C 的 vague 池按事由抽样 ──────────────
    print("\n" + "=" * 70)
    print("【候选 C 判为 vague 的句子】按首个事由分组抽样 —— 肉眼判定它们是否真含糊：")
    pool = vague_pools["C +要过程"]
    groups: dict[str, list[str]] = defaultdict(list)
    for r in pool:
        cs = (r.get("cause_all") or "").split(",")
        groups[cs[0] or "(无事由)"].append(r["user"])
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        v = groups[k]
        print(f"\n  ── {k}（{len(v)} 条）")
        step = max(1, len(v) // 10)
        for s in v[::step][:10]:
            print(f"     {s[:70]}")

    # ── 反向：C 判为 explicit/defect 里，有没有误报（其实没说清） ──
    print("\n" + "=" * 70)
    print("【候选 C 判为 explicit 的句子】抽样 20 —— 看有没有『其实没说清』被误判：")
    rxC = re.compile(C)
    ex = [r["user"] for r in rows if rxC.search(r["user"]) and not DEFECT_RE.search(r["user"])]
    step = max(1, len(ex) // 20)
    for i, s in enumerate(ex[::step][:20], 1):
        print(f"  {i:>2}. {s[:70]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
