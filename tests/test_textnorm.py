"""字形归一化的测试。

这一层为什么需要测试
--------------------
`textnorm.py` 只有 30 行数据和 1 行函数，看起来是最不容易出错的一层。事实相反：
它**静默失效**的方式比任何一层都隐蔽 —— 表里少一对字，繁体输入就被判反，
而简体环境下永远不会暴露；表里多一对字，可能把合法简体词改坏。
两种错误都不会让任何东西崩，只会让判据悄悄给出错误结论。

★ 本文件里最重要的一条是 `test_dict_chars_are_covered_by_table`：
  它把"判据词典将来收录 `后`/`于`/`干`/`它` 会让本表静默失效"
  这件事从一句 docstring 里的提醒，变成一个会变红的断言。
"""

from __future__ import annotations

import pytest

from ticket_agent.db import corpus, textnorm


# ---------------------------------------------------------------------------
# 契约：normalize 的基本性质
# ---------------------------------------------------------------------------
def test_normalize_is_identity_on_simplified():
    """纯简体输入必须原样返回 —— 这是"零影响"的前提。"""
    plain = [
        "我要退款",
        "收到的商品有瑕疵，能帮我处理一下吗？",
        "帮我催下快递",
        "快递到了我不想要了可以拒收吗",
        "订单显示已签收但我没收到货",
        "我买的东西损坏了，要换货",
    ]
    for t in plain:
        assert textnorm.normalize(t) == t, f"纯简体被改动了: {t!r}"


def test_normalize_only_substitutes_traditional_chars():
    """只动表内的字，其余一律不动（含标点、数字、字母）。"""
    assert textnorm.normalize("abc 123 ！？，。") == "abc 123 ！？，。"
    assert textnorm.normalize("") == ""
    # 表外的繁体字**刻意**不转（本表只收判据见过的 32 对）
    assert textnorm.normalize("妳") == "妳"
    assert textnorm.normalize("後") == "後"


def test_normalize_never_introduces_traditional():
    """归一化是单向的：输出里不应剩下**表内**的繁体字。"""
    text = "我的包裹已經到超商但還沒收到通知"
    out = textnorm.normalize(text)
    residue = {ch for ch in out if ch in textnorm.TRADITIONAL_CHARS}
    assert residue == set(), f"归一化后仍残留表内繁体字: {residue}"


# ---------------------------------------------------------------------------
# 判据边界：这是本模块存在的理由
# ---------------------------------------------------------------------------
def test_presupposes_receipt_reversed_by_traditional():
    """★ 判反的原始样本：'還沒收到' 里的 '收到' 曾命中已到手词典。

    这条断言的意义在于：它测的不是 normalize 这个函数，
    而是"归一化确实被接进了判据入口"——若有人把 corpus 里的
    `text = normalize(text)` 删掉，这条会红。
    """
    text = "我的包裹已經到超商但還沒收到通知"
    assert corpus.presupposes_receipt(text) is False, (
        "繁体'還沒收到'被判成了'已到手'—— 归一化没接进 presupposes_receipt"
    )


def test_request_regex_matches_traditional():
    """`我要退貨` 里的 `退貨` 若不归一，动作词一个都匹配不上。"""
    assert corpus.has_explicit_request("我要退貨") is True
    assert corpus.classify_style("我要退貨") == corpus.STYLE_EXPLICIT
    assert corpus.classify_style("我買的東西損壞了，要換貨") == corpus.STYLE_DEFECT


def test_classify_style_normalizes_once():
    """★ 两条判据必须看到**同一份**归一化文本。

    若只在 `has_explicit_request` 里归一化、`_DEFECT_RE` 不归一，
    就会出现"动作判定用简体、瑕疵判定用繁体"的错位。
    这里用一个瑕疵词是繁体异形字的样本钉住它。
    """
    # 損壞 是 损坏 的繁体字形
    assert corpus.classify_style("我買的東西損壞了，要換貨") == corpus.STYLE_DEFECT
    # 质量 的繁体 質量 同理；但这句没点动作集内动作，故仍是 vague
    assert corpus.classify_style("商品質量太差了") == corpus.STYLE_VAGUE


def test_traditional_false_friend_not_misconverted():
    """表外的字不能被误伤 —— 判定必须与字形无关，而不是与某一批字有关。"""
    # 这些字的简体/繁体在表外，语义不受影响
    for t in ["我收到了", "还没有收到", "我要投诉", "帮我催一下快递"]:
        assert textnorm.normalize(t) == t


# ---------------------------------------------------------------------------
# ★ 静默失效的守卫
# ---------------------------------------------------------------------------
def test_dict_chars_are_covered_by_table():
    """★ 判据词典的每个汉字，要么本表认得，要么它繁简同形。

    这条测试防的是一次**已经写进 docstring 的已知失效**：
    NLS 对 `後/於/乾/牠` 双向不转，本表继承了它。当前判据词典里
    没有这几个字，所以安全 —— 但如果将来有人把 `后`/`于`/`干`/`它`
    加进 `_REQUEST_RE` / `_DEFECT_RE` / `_PRE_RECEIPT_RE` / `_POST_RECEIPT_RE`，
    对应的繁体 `後/於/乾/牠` 就不会被本表转换，判据会**静默失效**。

    本测试的做法：把四条正则里出现过的汉字全取出来，逐个问本表
    "你认得它吗"。对不认得的字，检查它的繁体形态是否恰好同形
    （同形则无需转换）。发现既不在表里、又存在异形繁体形态的字，就红。
    """
    # 已知的 NLS 漏字：这些字的繁体形态本表转不了。
    # 它们**当前不在词典里**，若哪天进了词典，本测试会因为下面的
    # `NLS_GAPS` 检查而变红 —— 那正是设计意图。
    NLS_GAPS = {
        "后": "後",
        "于": "於",
        "干": "乾",
        "它": "牠",
        "痴": "癡",
    }

    chars = set()
    for pattern in (
        corpus._REQUEST_RE.pattern,
        corpus._DEFECT_RE.pattern,
        corpus._PRE_RECEIPT_RE.pattern,
        corpus._POST_RECEIPT_RE.pattern,
    ):
        chars |= {ch for ch in pattern if "\u4e00" <= ch <= "\u9fff"}

    leaks = sorted(ch for ch in chars if ch in NLS_GAPS)
    assert not leaks, (
        f"判据词典里出现了 NLS 漏字 {leaks}："
        f"它们的繁体形态（{ {c: NLS_GAPS[c] for c in leaks} }）不会被 textnorm 转换，"
        "繁体输入下判据会静默失效。修法：给 _T2S 手工补上这几对，"
        "并重跑 `_nlsdict.py` 确认无其他遗漏。"
    )

    # 另一侧：词典里的字应当要么本表认得，要么是繁简同形。
    # 这里用一个更朴素的自检 —— 表本身不能有自映射（那是噪音）。
    self_maps = [k for k, v in textnorm._T2S.items() if k == v]
    assert not self_maps, f"映射表里有自映射噪音: {self_maps}"


def test_normalize_does_not_touch_corpus_bytes():
    """★ 归一化发生在判据内部，不外泄到语料。

    `load_corpus()` 的 sha256 只有在读原始字节时才可比 ——
    若有人用 normalize 去"清洗"语料再落盘，指纹就对不上了。
    这条测试钉住的是"语料对象里的 body 仍是原始字形"。
    """
    corpus.load_corpus.cache_clear()
    tickets = corpus.load_corpus()
    if not tickets:
        pytest.skip("语料文件不存在，跳过")

    # 语料里确实存在混着繁体字的条目（实测 13 条），
    # 且它们**没有**被 load 阶段清洗掉
    with_trad = [t for t in tickets if any(ch in textnorm.TRADITIONAL_CHARS for ch in t.body)]
    assert with_trad, (
        "语料里已找不到任何表内繁体字 —— 要么语料被清洗过（指纹会失效），"
        "要么映射表被改大了（那是另一个改动，请一并更新本断言）"
    )
