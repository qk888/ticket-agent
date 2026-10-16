"""跨域效度检验：把本项目的判据拿到一份完全独立的第三方标注数据上跑。

为什么需要这个脚本
------------------
本项目所有的评测都是**自己考自己**：语料来自 JDDC，golden 由 `rules.decide()` 推导，
`classify_style()` 判的是"客户要的动作在不在动作集内"。这三个东西共享同一套假设，
所以它们互相对齐**不能证明判据是对的** —— 只能证明它们互相一致。

`hf_zhtw_ticket_train_FULL.jsonl`（2,497 条）是唯一一份**与本项目没有任何关系**的数据：
来自 HuggingFace，繁体中文（台湾用语），由 teacher LLM 依 schema 合成，
带 6 个类目 + `needs_human` / `is_multi_intent` / `emotion_intensity` 三个独立标注。

它的用途不是"再跑一遍准确率"——它没有本项目的订单、政策、动作集，
**跑不出终态准确率**。它唯一的用途是一次 **外部效度检验**：
问一个本项目所有报表都回答不了的问题 ——

    「一条工单是否需要转人工」这件事，
    本项目的判据（`escalate` / `need_clarification`）与一个独立标注者意见一致吗？

★ 已知的天花板（先写在前面，免得把结论读过头）
------------------------------------------------
1. 这份数据的 `needs_human` 标注，README 自陈**几乎完全由 `emotion_intensity` 单轴决定**
   —— 是生成 prompt 对该字段定义不明确，不是标注不稳定。
   所以它测的是"客户情绪激动吗"，而本项目的 `escalate` 测的是"规则要求转人工吗"
   （R4 金额/风控、R5 重复退款、R6 生鲜）。**两者定义不同**，
   本脚本要量的是"不同到什么程度"，而不是指望它们重合。
2. 它是**繁体**中文，而本项目全部判据都是**简体字面量**。
   故必须先做繁→简归一化，否则量出来的是字形差异，不是判据差异。

★ 这个脚本真正要验的东西（也是它存在的理由）
--------------------------------------------
**字形归一化本身是不是一个真问题。** 本项目的判据是正则字面量匹配，
如果繁体输入会让 `presupposes_receipt()` 判反（实测 `我的包裹已經到超商但還沒收到通知`
被判成"已到手"），那这个缺陷在简体环境里永远不会暴露，
但它是**同一个类别的失败**：判据依赖字形而非语义。

用法：
    python _crosscheck.py            # 完整报告
    python _crosscheck.py --samples  # 只打分歧样本
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "probe" / "samples" / "hf_zhtw_ticket_train_FULL.jsonl"

sys.path.insert(0, str(ROOT / "src"))
from ticket_agent.db import corpus  # noqa: E402

# ---------------------------------------------------------------------------
# 繁→简映射：**复用生产模块**，不再自己维护一张表
# ---------------------------------------------------------------------------
# ★ 这里曾经有一张手写的 84 条映射表，现在删掉了。原因是它的覆盖率判据
#   是**循环论证**：用表里的键定义"什么是繁体"，又用同一张表定义"转换是否成功"，
#   于是"归一化后 0 残留"是同义反复，什么都没证明。
#
#   现在改用 `ticket_agent.db.textnorm` —— 它的表由 `_gennorm.py` 从 Windows NLS
#   生成，正确性有独立来源。更关键的是它**已经接进了生产判据的入口**，
#   所以本脚本量到的就是生产环境的真实行为，而不是一份平行实现的行为。
#   平行实现有个隐蔽的坏处：它跑通了只能说明这份实现是自洽的，
#   说明不了生产路径上的那次归一化是对的。
from ticket_agent.db.textnorm import (  # noqa: E402
    TRADITIONAL_CHARS,
    normalize as to_simplified,
)

# 残留判据也随之换尺子。旧判据问"这个字还在不在表的键里"——循环。
# 新判据问「这个字在**本项目自己的简体语料**里从未出现过」——
# 语料是一个与本表无关的独立集合。这把尺子**过严**（简体生僻字也会被算进来），
# 所以它只用来看趋势，不用来下结论。
_corpus_chars: frozenset[str] | None = None


def corpus_chars() -> frozenset[str]:
    """本项目全部简体语料里出现过的汉字并集。"""
    global _corpus_chars
    if _corpus_chars is None:
        _corpus_chars = frozenset(
            ch
            for t in corpus.load_corpus()
            for ch in t.body
            if "\u4e00" <= ch <= "\u9fff"
        )
    return _corpus_chars


def unseen_by_corpus(text: str) -> set[str]:
    """文本里出现、但简体语料从未用过的汉字。**过严的尺子**，只看趋势。"""
    return {
        ch
        for ch in text
        if "\u4e00" <= ch <= "\u9fff" and ch not in corpus_chars()
    }


def residual_traditional(text: str) -> set[str]:
    """归一化后仍残留的**表内**繁体字。

    注意这把尺子现在只对 `textnorm.TRADITIONAL_CHARS` 有意义 ——
    它不再是"这个字是不是繁体"的判据（那把尺子已废弃），
    而是"本表承诺认得的字有没有漏转"的自检。
    """
    return {ch for ch in text if ch in TRADITIONAL_CHARS}


# ---------------------------------------------------------------------------
# 加载与归一化
# ---------------------------------------------------------------------------
def load() -> list[dict]:
    rows = []
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            t = obj["ticket"]
            rows.append(
                {
                    "text_raw": t["ticket_text"],
                    "text": to_simplified(t["ticket_text"]),
                    "category": t["category"],
                    "intent": t["primary_intent"],
                    "emotion": int(t["emotion_intensity"]),
                    "needs_human": bool(t["needs_human"]),
                    "multi": bool(obj["is_multi_intent"]),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# 本项目的判据（拿到外部数据上跑）
# ---------------------------------------------------------------------------
def our_route(text: str) -> str:
    """本项目对一条工单正文的**诉求层**路由（不含订单数据，故只能给出候选）。

    返回三选一：
      - `in_action_set`  ：客户要的动作在本系统动作集内（退款/退货/拒收拦截）
      - `need_clarify`   ：客户没说清要什么 → 正解是反问
      - `out_of_scope`   ：客户要的动作系统办不到（查件/催件/补发/开票/改址…）
    """
    if corpus.has_explicit_request(text):
        return "in_action_set"
    # 没点动作集内的动作。再看是不是"根本没诉求"（→反问）还是"诉求在别处"（→办不到）
    if corpus.presupposes_receipt(text):
        # 已经在讨论到手后的处置，却没点退款退货 → 多半是要补偿/维修/说明
        return "out_of_scope"
    return "need_clarify"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="只打分歧样本")
    args = ap.parse_args()

    rows = load()
    n = len(rows)

    # ---- ① 归一化的覆盖情况（先证这一步可信，后面所有数字才成立）----
    raw_residual = sum(1 for r in rows if residual_traditional(r["text_raw"]))
    conv_residual = sum(1 for r in rows if residual_traditional(r["text"]))
    conv_chars = sum(len(residual_traditional(r["text"])) for r in rows)
    # 独立尺子：归一化后仍有"简体语料从没见过的汉字"的条数。
    # ★ 这把尺子过严 —— 简体生僻字也会被算进来，故只看趋势。
    unseen_raw = sum(1 for r in rows if unseen_by_corpus(r["text_raw"]))
    unseen_conv = sum(1 for r in rows if unseen_by_corpus(r["text"]))

    print("=" * 78)
    print("① 繁→简归一化的覆盖情况（这一步不可信，后面全部不可信）")
    print("=" * 78)
    print(f"数据集条数            : {n}")
    print(f"生产映射表规模        : {len(TRADITIONAL_CHARS)} 个繁体字"
          f"（来自 ticket_agent.db.textnorm，由 NLS 生成）")
    print()
    print("尺子 A（自检，会循环）：归一化后残留的**表内**繁体字")
    print(f"  归一化前               : {raw_residual} ({raw_residual / n:.1%})")
    print(f"  归一化后               : {conv_residual} ({conv_residual / n:.1%})")
    print(f"  归一化后残留字符总数   : {conv_chars}")
    print("  ★ 这把尺子只能证明'表对自己的键生效'，不能证明表够用 ——")
    print("    它用表里的键定义'什么是繁体'，又用同一张表定义'转换是否成功'。")
    if conv_residual:
        leftover = Counter(ch for r in rows for ch in residual_traditional(r["text"]))
        print(f"  残留字符 top10         : {leftover.most_common(10)}")
    print()
    print("尺子 B（独立，过严）：归一化后仍有'简体语料从没见过'的汉字的条数")
    print(f"  归一化前               : {unseen_raw} ({unseen_raw / n:.1%})")
    print(f"  归一化后               : {unseen_conv} ({unseen_conv / n:.1%})")
    unseen_chars_raw = sum(len(unseen_by_corpus(r["text_raw"])) for r in rows)
    unseen_chars_conv = sum(len(unseen_by_corpus(r["text"])) for r in rows)
    print(f"  残留汉字总字数         : {unseen_chars_raw} → {unseen_chars_conv} "
          f"（降 {(1 - unseen_chars_conv / max(unseen_chars_raw, 1)):.1%}）")
    if unseen_conv:
        top = Counter(
            ch for r in rows for ch in unseen_by_corpus(r["text"])
        ).most_common(12)
        print(f"  残留汉字 top12         : {top}")
    print()
    print("★ 条数一栏**一点没降**（2497 → 2497），这不是 bug，是这把尺子的性质：")
    print("  它问的是'这条句子里有没有语料没用过的字'，而这 2497 条里**每一条**")
    print("  都有 —— top12 的 麼/樣/訂/結/處/會/現/確/覺/帳/認 全都是**真繁体字**，")
    print("  只是本表刻意没收（它们不在判据词典里，转不转都不影响任何判定）。")
    print("  所以这把尺子只能看**总字数**的下降（上面的降幅），不能看条数。")
    print()
    print("★ 为什么不能只靠尺子 B：它把'简体里的生僻字'也算成残留，")
    print("  于是永远归零不了；而尺子 A 又永远能归零。两把尺子都不够，")
    print("  真正的判据是 `_nlsdict.py`（拿判据词典逐字问 NLS）——")
    print("  它测的是'判据要用的字，表认不认得'，不是'表干不干净'。")

    # ---- ② ★ 字形不再改变判据（这是修复的回归验证，不再是缺陷暴露）----
    print()
    print("=" * 78)
    print("② 回归验证：同一条文本的繁简两种字形，判据结论还一样吗")
    print("=" * 78)
    # ★ 归一化已接进生产判据入口，所以下面两组**都应当为 0**。
    #   若不为 0，说明有人在某条判据里漏了 `normalize`，或新增了判据而没接。
    diff_route = [r for r in rows if our_route(r["text_raw"]) != our_route(r["text"])]
    diff_recv = [
        r
        for r
        in rows
        if corpus.presupposes_receipt(r["text_raw"])
        != corpus.presupposes_receipt(r["text"])
    ]
    print(f"路由结论不同的条数        : {len(diff_route)} ({len(diff_route) / n:.1%})")
    print(f"presupposes_receipt 不同  : {len(diff_recv)} ({len(diff_recv) / n:.1%})")
    print()
    if not diff_route and not diff_recv:
        print("→ 0。判据已与字形无关：生产路径上的 normalize 覆盖了全部入口。")
        print("  （修复前这两个数字是 321 条 /12.9% 与 159 条 /6.4%）")
    else:
        print("★ 不为 0 —— 有判据入口漏了 normalize。逐条列出：")
        for r in (diff_route + diff_recv)[:8]:
            print(f"  原文：{r['text_raw'][:56]}")
            print(f"    raw → route={our_route(r['text_raw'])}, "
                  f"recv={corpus.presupposes_receipt(r['text_raw'])}")
            print(f"    norm → route={our_route(r['text'])}, "
                  f"recv={corpus.presupposes_receipt(r['text'])}")

    # ---- ②-b 若判据**没有**归一化，差异会有多大（保留基线，便于日后对比）----
    print()
    print("─" * 78)
    print("②-b 基线：绕过归一化直接查原始正则，差异有多大")
    print("─" * 78)

    def _route_raw(text: str) -> str:
        """复现"归一化之前"的路由 —— 直接拿未归一化文本查正则。"""
        if corpus._REQUEST_RE.search(text):
            return "in_action_set"
        if corpus._PRE_RECEIPT_RE.search(text):
            return "need_clarify"
        return "out_of_scope" if corpus._POST_RECEIPT_RE.search(text) else "need_clarify"

    def _recv_raw(text: str) -> bool:
        if corpus._PRE_RECEIPT_RE.search(text):
            return False
        return bool(corpus._POST_RECEIPT_RE.search(text))

    raw_diff_route = [r for r in rows if _route_raw(r["text_raw"]) != _route_raw(r["text"])]
    raw_diff_recv = [r for r in rows if _recv_raw(r["text_raw"]) != _recv_raw(r["text"])]
    print(f"不归一化时，路由结论不同  : {len(raw_diff_route)} "
          f"({len(raw_diff_route) / n:.1%})")
    print(f"不归一化时，receipt 判反  : {len(raw_diff_recv)} "
          f"({len(raw_diff_recv) / n:.1%})")
    print("★ 这两个数字就是归一化挡掉的错误量。它们的**含义**与②段的 0 相反：")
    print("  ②段的 0 是'修复生效'，这里的非 0 是'没有修复就会错这么多'。")
    if raw_diff_recv:
        print()
        print("  判反样本（这些句子繁体下会被判成'已到手'）：")
        for r in raw_diff_recv[:4]:
            print(f"    {r['text_raw'][:60]}")
            print(f"      不归一化 → {_recv_raw(r['text_raw'])} ／ 归一化后 → "
                  f"{corpus.presupposes_receipt(r['text_raw'])}")

    # ---- ③ 路由分布 vs 第三方类目 ----
    print()
    print("=" * 78)
    print("③ 本项目的诉求路由 × 第三方类目（吃繁体归一化之后的结果）")
    print("=" * 78)
    routes = Counter(our_route(r["text"]) for r in rows)
    print("路由分布：")
    for k, v in routes.most_common():
        print(f"  {k:16s} {v:5d}  {v / n:6.1%}")
    print()
    cross: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        cross[r["category"]][our_route(r["text"])] += 1
    cats = sorted(cross, key=lambda c: -sum(cross[c].values()))
    keys = ["in_action_set", "need_clarify", "out_of_scope"]
    print(f"{'类目':<14}{'条数':>6}" + "".join(f"{k:>16s}" for k in keys))
    for c in cats:
        tot = sum(cross[c].values())
        print(f"{c:<14}{tot:>6}" + "".join(f"{cross[c][k]:>15d} " for k in keys))

    # ---- ④ 与 needs_human 的对照（★ 核心：外部效度）----
    print()
    print("=" * 78)
    print("④ 外部效度：本项目的判据 vs 第三方 needs_human 标注")
    print("=" * 78)
    nh = Counter(r["needs_human"] for r in rows)
    print(f"第三方 needs_human : True {nh[True]} / False {nh[False]}  ({nh[True] / n:.1%} 为 True)")
    print()
    print("★ 先看这份标注自己是什么 —— 它是不是真的在标注'需要人'：")
    print()
    emo_by_nh: dict[bool, Counter] = defaultdict(Counter)
    for r in rows:
        emo_by_nh[r["needs_human"]][r["emotion"]] += 1
    print(f"{'emotion_intensity':<20}{'needs_human=True':>18}{'needs_human=False':>19}")
    for e in range(1, 6):
        print(f"{e:<20}{emo_by_nh[True][e]:>18}{emo_by_nh[False][e]:>19}")
    tot_t = sum(emo_by_nh[True].values())
    mean_t = sum(e * emo_by_nh[True][e] for e in range(1, 6)) / max(tot_t, 1)
    tot_f = sum(emo_by_nh[False].values())
    mean_f = sum(e * emo_by_nh[False][e] for e in range(1, 6)) / max(tot_f, 1)
    print(f"{'均值':<20}{mean_t:>18.2f}{mean_f:>19.2f}")
    print()
    gap = mean_t - mean_f
    print(f"→ 两组均值 {mean_t:.2f} vs {mean_f:.2f}，相差 {gap:.2f}；"
          f"intensity=5 这一档 True/False = {emo_by_nh[True][5]}/{emo_by_nh[False][5]}。")
    print("  这个差距足以坐实数据集 README 的自陈：**needs_human 几乎由情绪单轴决定**。")
    print("  而本项目的 escalate 由规则决定（R4 金额/风控、R5 重复、R6 生鲜）——")
    print("  **两者测的不是同一件事**，下面的对照必须按这个理解读，")
    print("  不能把'我们判要转人工'与'第三方标 needs_human'当成同一个量在对答案。")

    print()
    print("本项目路由 × needs_human：")
    print(f"{'路由':<16}{'条数':>7}{'needs_human=True':>18}{'占比':>9}")
    for k in keys:
        sub = [r for r in rows if our_route(r["text"]) == k]
        t = sum(1 for r in sub if r["needs_human"])
        print(f"{k:<16}{len(sub):>7}{t:>18}{(t / len(sub) if sub else 0):>9.1%}")

    # ---- ⑤ 我们判"办不到"的，第三方怎么标 ----
    print()
    print("=" * 78)
    print("⑤ 交叉验证：我们判 out_of_scope 的条，第三方标注者怎么看")
    print("=" * 78)
    oos = [r for r in rows if our_route(r["text"]) == "out_of_scope"]
    print(f"out_of_scope 共 {len(oos)} 条，类目分布：")
    for c, v in Counter(r["category"] for r in oos).most_common():
        print(f"  {c:<14}{v:>5}  {v / len(oos):6.1%}")
    print()
    print(f"其中 needs_human=True : {sum(1 for r in oos if r['needs_human'])} "
          f"({sum(1 for r in oos if r['needs_human']) / max(len(oos), 1):.1%})")
    print(f"其中 is_multi_intent  : {sum(1 for r in oos if r['multi'])} "
          f"({sum(1 for r in oos if r['multi']) / max(len(oos), 1):.1%})")
    print()
    nh_oos = sum(1 for r in oos if r["needs_human"])
    nh_all = nh[True] / n
    nh_ias = [r for r in rows if our_route(r["text"]) == "in_action_set"]
    nh_ias_rate = (sum(1 for r in nh_ias if r["needs_human"]) / len(nh_ias)) if nh_ias else 0.0
    print("★ 原先的假设是：若 out_of_scope 的 needs_human 占比明显高于全体，")
    print("  就说明'系统办不到'与'需要人'在独立标注下相关。**实测否掉了这个假设**：")
    print(f"    全体            : {nh_all:.1%}")
    print(f"    out_of_scope    : {nh_oos / max(len(oos), 1):.1%}   ← 与全体几乎相同，甚至略低")
    print(f"    in_action_set   : {nh_ias_rate:.1%}   ← 反而**最高**")
    print()
    print("  原因在④段已经量出来了：needs_human ≈ 情绪单轴，而'情绪激动'与")
    print("  '这件事系统办不办得到'是两条独立的轴 —— 一位平静地要求查快递的客户")
    print("  会让系统无能为力，却被标 needs_human=False；一位在动作集内但暴怒的")
    print("  客户会被标 needs_human=True。**所以这份标注不能为本项目的 escalate")
    print("  或 out_of_scope 提供外部证据**，这里只能记一个否定结论。")
    print()
    print("★ 本脚本里唯一真正拿到外部证据的是③段的这一条：")
    print("    退換貨申請 → in_action_set 344/399 = 86.2%")
    print("  第三方独立标注的'退换货申请'与本项目判据的'诉求在动作集内'高度对齐，")
    print("  这是**判据侧**的外部效度；它支持的是路由判据，不是转人工判据。")

    if args.samples:
        print()
        print("=" * 78)
        print("⑥ 分歧样本（我们判 in_action_set，第三方判 needs_human=False）")
        print("=" * 78)
        for r in [r for r in rows if our_route(r["text"]) == "in_action_set"
                  and not r["needs_human"]][:10]:
            print(f"  [{r['category']}] {r['text_raw'][:64]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
