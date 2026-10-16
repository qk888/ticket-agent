"""量化「工单正文」与「订单状态」的语义冲突。

## 为什么要测这个

`level` 在造数循环里是**随机抽的**（`seed.py:301`），正文照这个 level 选；
但 `build_eval_cases` 又根据**订单状态重新推导**一次 level（`seed.py:698-708`）。
两者不是同一来源 —— 于是可能出现：

    订单 shipped（还没签收）→ case 判为 L2（要算物流/时效）
    正文却是「收到的衣服有瑕疵，我想换货」← 客户不可能对没签收的货说"收到的"

这不是语料的错，也不是订单的错，是**两个独立抽取步骤之间缺一个约束**。
但它是评测集的有效性问题：一条自相矛盾的样本，考不出"读懂客户"，
只会让报错归因变模糊。

## 怎么测

不看 level，只看**正文里的时间锚点**与**订单的物流状态**是否相容：

    正文暗示"已到手"（收到/打开/用了/吃了/里面的…）
        → 订单必须 signed_at 不为空
    正文暗示"在途/没到"（还没收到/什么时候到/发货了吗/催…）
        → 订单应当 status=shipped 或 signed_at 为空

冲突率就是这两个方向上的违例占比。输出同时打印抽样原文，
因为**数字要能被读懂**，否则"12% 冲突"没法判断该不该修。
"""

from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, "src")

from ticket_agent.db import corpus  # noqa: E402
from ticket_agent.db import seed  # noqa: E402

# ── 时间锚点词典 ──────────────────────────────────────────────
# ★ 这两个正则都**刻意保守**：只收几乎不可能有歧义的表达。
#   比如不收「东西」——它既可以是"收到的东西"也可以是"要买的东西"。
#
# ★ 它们是**独立于 corpus.presupposes_receipt 的第二把尺子**。
#   相容性约束是按 presupposes_receipt 实施的（那是生产路径上的判据），
#   所以只测它等于自己批自己的卷子。这里再用一套更宽的正则复核一遍：
#   凡是这把宽尺子也认为"说了已到手"的句子，都该落在已签收的订单上；
#   收紧后仍剩下的违例，才是真正的漏网。
RECEIVED_RE = re.compile(
    r"收到|到了|到手|打开|拆开|拆了|里面|用了|吃了|喝了|试了|穿上|"
    r"发来|寄来|送来|发过来|寄过来|给我发的是|包装|签收|到货|"
    r"漏发|少发|发错|碎了|坏了|破损|变质"
)
IN_TRANSIT_RE = re.compile(
    r"还没收到|没有收到|没收到|一直没|什么时候能到|什么时候到|多久能到|什么时候发货|"
    r"怎么还没|催|加急|物流|运单|快递单号|显示已签收|在途|派送|揽件|"
    r"还没发货|没发货|发货了没|发了吗|什么时候能收到"
)


def main() -> None:
    cases = seed.build_eval_cases(size=605, seed=7)
    print(f"case 数: {len(cases)}\n")

    # 复原订单状态：从 initial_state 里读
    stats: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    strict: Counter[str] = Counter()

    for c in cases:
        order = (c.get("initial_state") or {}).get("order") or {}
        status = order.get("status") or "?"
        # ★ 判据是 `status == "signed"`，不是 `signed_at is not None` ——
        #   `initial_state["order"]` 只留 4 个字段（status/refund_count/
        #   amount/category），**根本不含 signed_at**。第一版探针拿它判，
        #   于是每一单都被当成"未签收"，冲突率虚报成 62%。
        signed = status == "signed"
        body = c["body"]

        has_recv = bool(RECEIVED_RE.search(body))
        has_trans = bool(IN_TRANSIT_RE.search(body))
        state = "signed" if signed else f"{status}/unsig"

        # ★ 判据修正 1：**否定语境优先**。
        #   `RECEIVED_RE` 里收了 `签收`/`收到`/`到了`，而中文里它们大量出现在
        #   **否定**或**未完成**的语境中：「这单我**不签收**啦直接退回」、
        #   「我**没收到**货」、「**还没收到**吗」。上一版直接拿 `has_recv`
        #   判 A 类，于是这些句子全被算成"正文说已到手"，A 类虚高。
        #   这与生产判据 `corpus.presupposes_receipt` 的取舍是同一条：
        #   **先查"未到手"再查"已到手"** —— 前者更具体，后者含歧义。
        #   两把尺子在这里必须一致，因为"有没有否定"是语言学事实，不是判据选择。
        recv_now = has_recv and not has_trans

        # ★ 判据修正 2：**未关联订单本身不是冲突**（这是上一版最大的判据错误）。
        #   L3 的 `order_basis` 只有 `ambiguous`（名下多笔，要澄清）与
        #   `no_order`（一笔都没有，转人工）两种 —— **"库里查不到可处置的订单"
        #   就是 L3 的定义**。客户在正文里提物流（真实语料 40.7% 提物流），
        #   而库里正好没有他的订单，这不是造数矛盾，恰恰是**要考的那个场景**：
        #   正解是"先问清是哪一笔"，与 golden 完全一致。
        #   上一版把这类全算成 C 类冲突（5.0% → 10.4%），是在拿一把与设计
        #   不符的尺子量，量出来的不是缺陷。
        #   真正该报的是：**本该有订单的层（L1/L2）却没关联上**。
        unlinked_is_conflict = c["level"] != "L3"

        if order and recv_now and not signed:
            kind = "A 正文说已到手，订单未签收"
        elif order and has_trans and signed:
            kind = "B 正文说在途/催件，订单已签收"
        elif not order and unlinked_is_conflict and (recv_now or has_trans):
            kind = "C 本应有订单却没关联（L1/L2）"
        else:
            kind = "OK"
        stats[kind] += 1
        if kind != "OK":
            samples.setdefault(kind, []).append(f"[{c['level']} {state}] {body[:60]}")

        # 生产判据（corpus.presupposes_receipt）下的违例 —— 这是"约束有没有生效"
        strict["A'" if (order and corpus.presupposes_receipt(body) and not signed) else "ok"] += 1

    total = sum(stats.values())
    for k, v in stats.most_common():
        print(f"{k:36s} {v:4d}  {v / total:6.1%}")
    conflict = total - stats["OK"]
    print(f"\n宽尺子（独立正则）合计冲突: {conflict}/{total} = {conflict / total:.1%}")
    a2 = strict["A'"]
    print(f"生产判据（presupposes_receipt）违例: {a2}/{total} = {a2 / total:.1%}\n")

    for k in sorted(samples):
        print(f"── {k} 抽样 ──")
        for s in samples[k][:10]:
            print("   ", s)
        print()

    # 另一件要核的事：level 与订单状态是否真的一一对应
    # ★ 判据只能看 `status` —— `initial_state["order"]` 里没有 signed_at，
    #   第一版拿它判，结果所有带订单的 case 都被归成 "shipped/unsig"。
    print("── level × 订单状态 ──")
    cross: Counter[tuple[str, str]] = Counter()
    for c in cases:
        order = (c.get("initial_state") or {}).get("order") or {}
        st = order.get("status") if order else "no_order"
        cross[(c["level"], st or "?")] += 1
    for (lv, st), n in sorted(cross.items()):
        print(f"   {lv} × {st:14s} {n:4d}")


if __name__ == "__main__":
    main()
