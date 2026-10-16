"""语料层的单元测试 —— 这一层此前**零覆盖**。

## 为什么它必须被测

`corpus.classify_style()` 不只是"读数据"，它是**评测集的一个维度**：
报表里「按话术风格」那张表的每一格都来自它。而它是一条规则
（正则），规则就会写错、就会随语料换代而失配 —— 实测就发生过：

    `_REQUEST_RE` 只认"退/换"家族，于是真实语料里 51.5% 的物流类
    句子（「帮我催下快递」）被判成 "客户没说清要什么"，而客户其实
    说得非常清楚，只是要的动作不在本系统的能力范围内。

那次失配没有让任何测试变红，因为**这一层没有测试**。它只表现为
报表上一张悄悄偏掉的分布表。所以这里的断言分两类：

  1. **契约**：函数承诺的事（可复现、跨文件去重、坏行不废整份语料）；
  2. **判据边界**：`explicit` / `vague` 的分界到底划在哪，用**真实
     语料里出现过的原句**钉死 —— 这样判据一旦被改歪，diff 里就能看见。

第 2 类里每一条都写成"句子 → 期望风格"，理由写在断言旁边。换语料
时它们会失效，这是**有意为之**：失效就是提醒"判据该跟着重看一遍"。
"""

from __future__ import annotations

import json
import random

import pytest

from ticket_agent.db import corpus


# --------------------------------------------------------------------------
# 契约 1) 风格分类是可复现的纯函数
# --------------------------------------------------------------------------

def test_classify_style_is_pure_and_repeatable():
    """同一个句子任何时候都得到同一个 style。

    ★ 这是 `classify_style` 存在的**全部理由**（见其 docstring）：手工
    贴标签做不到这点，而标签一旦是手工的，它就不再是数据集的属性，
    而成了某个人的解读。所以"可复现"本身就是要被钉住的契约。
    """
    text = "收到的衣服有瑕疵，能帮我处理换货吗？"
    first = corpus.classify_style(text)
    assert [corpus.classify_style(text) for _ in range(50)] == [first] * 50


def test_style_values_are_the_declared_ones():
    """风格只可能取 `ALL_STYLES` 里的值 —— 报表按它分组，多一个值就多一行。"""
    for text in ("我要退款", "我要投诉", "收到的商品有瑕疵", "", "???"):
        assert corpus.classify_style(text) in corpus.ALL_STYLES


# --------------------------------------------------------------------------
# 契约 2) 判据边界 —— 用真实语料原句钉死
# --------------------------------------------------------------------------

#: (句子, 期望风格, 为什么) —— 句子全部取自实际语料或实测抽样。
STYLE_CASES: tuple[tuple[str, str, str], ...] = (
    # ── explicit：点名了**动作集内可执行**的诉求 ──
    (
        "我要退款",
        corpus.STYLE_EXPLICIT,
        "直接点名退款 —— `decide()` 能产出 refund",
    ),
    (
        "我要退货寄回给你们",
        corpus.STYLE_EXPLICIT,
        "退货走 refund（规则侧不区分退与换）",
    ),
    (
        "快递到了我不想要了可以拒收吗",
        corpus.STYLE_EXPLICIT,
        "拒收对应 R3 的 block_logistics —— 动作集内",
    ),
    # ── explicit：诉求在动作集内（系统有对应工具） ──
    (
        "帮我催下快递",
        corpus.STYLE_EXPLICIT,
        "★ 这条**原本是 vague**，原文写着「系统没有催件动作」。加了 R14 催单"
        "与 `submit_after_sales_action` 之后它合法地改判为 explicit —— 这正是"
        "`classify_style` docstring 预告过的那次改判：判据是「客户要的动作在不在"
        "动作集内」，能力变强了，同一个句子就该判得不一样。"
        "**不是测试写错了，是它守护的那个事实变了。**",
    ),
    (
        "麻烦帮我补发一个",
        corpus.STYLE_EXPLICIT,
        "同上：这条原先的理由只有四个字「没有补发工具」，现在有了 R9 补发。",
    ),
    # ── vague：诉求仍然在动作集之外（系统至今没有对应工具） ──
    (
        "能帮我维修一下吗",
        corpus.STYLE_VAGUE,
        "★ 维修至今不在动作集内（`REQUEST_KINDS` 里没有 repair）。"
        "留这条是为了让「判据的分界线」始终有一条**站在能力之外**的样本 —— "
        "上面两条改判成 explicit 之后，如果不补，这一层就只剩能力内的样本，"
        "分界线再也没被测过（而它恰恰是当初漏判 51.5% 物流工单的原因）。",
    ),
    (
        "你们能不能便宜点，我下次还买",
        corpus.STYLE_VAGUE,
        "议价：不构成任何一种售后诉求，也不是缺陷投诉",
    ),
    (
        "我要投诉",
        corpus.STYLE_VAGUE,
        "投诉只表达不满，没点明动作集内的动作",
    ),
    (
        "我要给差评了",
        corpus.STYLE_VAGUE,
        "同上 —— 这是 L3 的典型输入，正解是反问澄清",
    ),
    # ── defect：动作集内 + 提到质量瑕疵（政策上要走质量流程、要凭证） ──
    (
        "商品质量不是很好我想退",
        corpus.STYLE_DEFECT,
        "★ 「质量不是」是实测漏词：原正则只认「质量不好」，"
        "误杀过真投诉（`我发现这个现在包装质量不是很好哎…是否可以全部退`）",
    ),
    (
        "收到的商品有瑕疵，能帮我处理换货吗？",
        corpus.STYLE_DEFECT,
        "有退换诉求 + 瑕疵描述",
    ),
    # ── 消歧：多义词与子串陷阱 ──
    (
        "那我买8包寄回老家邮政是吗",
        corpus.STYLE_VAGUE,
        "★ 「寄回老家」是寄**往**老家（收件地址），不是退货寄回；"
        "实测正是这句话暴露了朴素的 `寄回` 子串匹配会误判",
    ),
    (
        "显示已签收但我没收到",
        corpus.STYLE_VAGUE,
        "★ 「签收」刻意不进已到手词典，否则会把意思判反",
    ),
)


@pytest.mark.parametrize("text,expected,why", STYLE_CASES, ids=[c[0][:14] for c in STYLE_CASES])
def test_style_judgement_boundaries(text: str, expected: str, why: str):
    """判据的分界线 —— 每条的 `why` 就是它存在的理由。"""
    assert corpus.classify_style(text) == expected, why


def test_has_explicit_request_is_not_merely_has_a_request():
    """`has_explicit_request` 问的不是"有没有提要求"，而是"要的动作在不在动作集内"。

    ★ 这是判据最容易读错的地方，所以单独钉一条。

    ★★ 判据的**分界线会随能力移动**（这是设计，不是缺陷）：本条原先用
    「帮我催下快递」当反例（"系统没有催件动作"），加了 R14 之后它变成了正例。
    钉一个判据时，要选一个**判据本身不该改变**的样本，否则测试会跟着能力一起漂 ——
    所以反例改用维修：它在可预见的将来都不打算做（不是售后自动化能办的事，
    需要线下的维修网点与报价）。
    """
    assert corpus.has_explicit_request("我要退货") is True
    assert corpus.has_explicit_request("帮我催下快递") is True, "R14 之后这已是能力内的诉求"
    assert corpus.has_explicit_request("能帮我维修一下吗") is False, "维修不在动作集内"


# --------------------------------------------------------------------------
# 契约 3) 事实前提判定（正文与订单状态的相容性靠它）
# --------------------------------------------------------------------------

def test_presupposes_receipt_history_and_transit():
    """「已到手」与「在路上」必须判到相反的结论。

    ★ 这个函数是造数侧相容性约束的判据（`seed.py` 用它决定正文能不能
    关联一笔未签收的订单）。它判反了，评测集里就会出现"客户说收到的
    衣服有瑕疵"配一笔**未付款**订单 —— 实测 84/605（13.9%）就是这样来的。
    """
    assert corpus.presupposes_receipt("收到的衣服有瑕疵") is True
    assert corpus.presupposes_receipt("快递到了我不想要了可以拒收吗") is False


def test_presupposes_receipt_prioritises_not_yet_received():
    """「未到手」的消息更具体，必须压过「已到手」的线索。

    ★ 真实语料里两种线索常常同现：「显示已签收但我没收到」既有"签收"
    也有"没收到"。先查 `_PRE_RECEIPT_RE` 才不会把意思判反。
    """
    assert corpus.presupposes_receipt("显示已签收但我没收到") is False
    assert corpus.presupposes_receipt("我还没收到货，另外包装是破的") is False


def test_presupposes_receipt_false_is_not_a_claim_of_transit():
    """返回 False 是"**没有**假定已到手"，不是"确定了货没到"。

    ★ 这个区别是 docstring 里明写的：一句「我要退款」既没说到手也没说
    在路上。把它当"未到手"用会引入一批新的矛盾样本，所以这里钉住。
    """
    assert corpus.presupposes_receipt("我要退款") is False
    assert corpus.presupposes_receipt("") is False


# --------------------------------------------------------------------------
# 契约 4) 层级映射
# --------------------------------------------------------------------------

def test_level_to_styles_returns_declared_styles():
    for level in ("L1", "L2", "L3"):
        styles = corpus.level_to_styles(level)
        assert styles, f"{level} 必须有候选风格"
        assert set(styles) <= set(corpus.ALL_STYLES)


def test_unknown_level_falls_back_to_all_styles():
    """未知层退回全部风格，而不是抛异常或返回空 —— 空会让 `pick` 恒为 None。"""
    assert set(corpus.level_to_styles("L9")) == set(corpus.ALL_STYLES)


def test_l3_maps_only_to_vague():
    """L3 只配 vague —— 这条映射有语义依据，不是随手配的。

    ★ L3 的 `order_basis` 只有 `ambiguous`（保持 open、反问澄清）与
    `no_order`（转人工）两种来源，共同点正是"系统当前办不了、必须先澄清"，
    这正是 `vague` 的含义（诉求不在动作集内）。
    """
    assert corpus.level_to_styles("L3") == (corpus.STYLE_VAGUE,)


# --------------------------------------------------------------------------
# 契约 5) 载入：跨文件去重、坏行不废整份语料
# --------------------------------------------------------------------------

def test_load_corpus_dedupes_and_skips_bad_lines(tmp_path, monkeypatch):
    """跨文件去重 + 坏行跳过。

    ★ 两件事都必须成立，且理由不同：
      去重 —— 同一句话重复会让它在评测集里占比超过应有的权重；
      跳过坏行 —— 一行脏数据废掉整份语料，等于数据集多了一个静默的
               失效模式（表现为"评测集突然变小了"，而不是报错）。
    """
    shared = "我要退款，重复句子"
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text(
        "\n".join(
            [
                json.dumps({"scene": "退款", "user": shared}, ensure_ascii=False),
                json.dumps({"scene": "退款", "user": "我要退货"}, ensure_ascii=False),
                # ★ 这一行必须是**真的**非法 JSON（截断的），不能是
                #   `json.dumps("不是合法 json")` —— 那样它反而是合法行，
                #   测的就不是"坏行跳过"了。第一版正是这么写的，于是
                #   断言失败而失败原因不在被测代码上。
                '{"scene": "退款", "user": "截断的',
            ]
        ),
        encoding="utf-8",
    )
    b.write_text(
        json.dumps({"scene": "退款", "user": shared}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_FILES", ("a.jsonl", "b.jsonl"))
    # lru_cache 会把上一次的结果带过来 —— 换语料必须显式清掉，
    # 否则测的是缓存的旧语料（这类"测试通过但测了个空气"最费时间）。
    corpus.load_corpus.cache_clear()
    try:
        bodies = [t.body for t in corpus.load_corpus()]
        assert bodies.count(shared) == 1, "跨文件必须去重"
        assert "我要退货" in bodies
        assert len(bodies) == 2, "坏行应被跳过，而不是废掉整份语料"
    finally:
        corpus.load_corpus.cache_clear()


def test_load_corpus_missing_files_returns_empty(tmp_path, monkeypatch):
    """语料全缺失时返回空元组，不抛异常。

    ★ 造数路径依赖这个行为：`pick()` 返回 None → 退回内置模板，
    于是"没下语料"这件事表现为报表上的 `source=builtin`，而不是崩溃。
    """
    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_FILES", ("不存在.jsonl",))
    corpus.load_corpus.cache_clear()
    try:
        assert corpus.load_corpus() == ()
    finally:
        corpus.load_corpus.cache_clear()


# --------------------------------------------------------------------------
# 契约 6) pick：谓词筛空时返回 None，而不是硬塞
# --------------------------------------------------------------------------

def test_pick_returns_none_when_predicate_filters_everything(tmp_path, monkeypatch):
    """谓词把池子筛空时必须返回 None。

    ★ "重抽 N 次"的写法在池子被筛得只剩几条时会退化成硬塞，而且调用方
    看不出约束到底生效没有。这里钉住"筛空即 None"：拿到 None 是一条
    可处理的事实，拿到一句勉强合格的句子则是看不见的谎言。
    """
    p = tmp_path / "c.jsonl"
    p.write_text(
        json.dumps({"scene": "退款", "user": "我要退款"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(corpus, "CORPUS_DIR", tmp_path)
    monkeypatch.setattr(corpus, "CORPUS_FILES", ("c.jsonl",))
    corpus.load_corpus.cache_clear()
    try:
        rng = random.Random(0)
        assert corpus.pick(rng, "L1") is not None
        assert corpus.pick(rng, "L1", predicate=lambda _s: False) is None
        assert corpus.pick(rng, "L1", predicate=lambda s: s == "我要退款") is not None
    finally:
        corpus.load_corpus.cache_clear()


def test_corpus_stats_shape():
    """`corpus_stats()` 的字段是报表与 `_run_meta` 的接口。

    ★ `_run_meta` 把 `sha256` 的前 16 位写进每次跑的 meta，理由是
    "指标只有在读的是同一批字节时才可比"。字段名变了而消费点没跟着改，
    指标就会静默失去可比性 —— 所以契约在这里显式列一遍。
    """
    st = corpus.corpus_stats()
    for key in ("files", "exists", "sha256", "unique_phrasings", "by_style",
                "by_scene", "by_business_level", "per_file", "sources"):
        assert key in st, f"corpus_stats() 缺字段 {key}"
    assert st["unique_phrasings"] == sum(st["by_style"].values())
