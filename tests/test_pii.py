"""PII 脱敏的回归测试。

★ 这个文件的存在理由不是"覆盖代码"，是**钉住两条会静默退化的边界**：

1. **误报**：把订单号当银行卡抹掉 —— agent 会失去找订单的唯一钥匙，
   而报表上只表现为"准确率掉了几
   个点"，看不出是脱敏干的。9 条误报样本全部来自真实语料实测。
2. **正样本没命中**：语料里真 PII 只占 0.009%，
   **拿语料跑一遍 0 命中不能证明模块有效** —— 必须自造样本。

★ 还有一个容易漏的：脱敏必须**不可恢复**（脱敏后不得残留原文）。
  只替换了一半（比如只抹中间四位、两头还在）等于没脱。
"""

from __future__ import annotations

import pytest

from ticket_agent.db import pii


# ──────────────────────────────────────────────────────────────
# A. 误报：这些必须**一个都不脱敏**
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", pii.FALSE_POSITIVE_SAMPLES)
def test_false_positive_samples_are_never_redacted(text: str) -> None:
    """真实语料里'看起来像 PII 但其实是别的'—— 脱敏了就是砸业务。"""
    out, found = pii.redact(text)
    assert not found, f"误脱敏：{text!r} 命中了 {[f.kind for f in found]}"
    assert out == text


def test_order_number_survives_even_when_luhn_passes() -> None:
    """最危险的一条：订单号恰好 Luhn 通过。

    实测语料里 `订单号:19627589320267052` 是唯一 Luhn 通过的 16+ 位数字，
    而它**是订单号**。这条守不住，agent
    就查不到这一单 —— 且只表现为准确率掉几个点。
    """
    text = "订单号:19627589320267052 帮我查一下"
    out, found = pii.redact(text)
    assert not found
    assert "19627589320267052" in out


def test_bare_order_number_survives() -> None:
    """裸订单号（前面没有业务词）也必须活。

    ★ 这是本模块最关键的一条边界：实测语料里 366 次长数字出现中
    **344 次是裸的**（94%）。若豁免规则写成'看前面有没有业务词'，
    94% 的订单号会被误抹。豁免靠的是 Luhn，不是上下文。
    """
    for text in (
        "30542042254605106什么时候到",
        "单号2203476352141368什么时候派送呢",
    ):
        out, found = pii.redact(text)
        assert not found, f"裸订单号被误脱敏：{text}"


# ──────────────────────────────────────────────────────────────
# B. 正样本：必须命中（语料 0 命中，所以只能自造）
# ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", pii.TRUE_POSITIVE_SAMPLES)
def test_true_positive_samples_are_redacted(text: str, want: str) -> None:
    out, found = pii.redact(text)
    kinds = {f.kind for f in found}
    assert want in kinds, f"未命中：{text!r} 期望 {want}，实际 {kinds}"
    assert out != text


def test_redaction_is_irreversible() -> None:
    """脱敏后不得残留原文 —— 只抹一半等于没脱。"""
    text = "我叫张三，手机13812345678，邮箱zs@example.com"
    out, _ = pii.redact(text)
    assert "13812345678" not in out
    assert "zs@example.com" not in out


# ──────────────────────────────────────────────────────────────
# C. 校验位：不过校验就不是 PII（防误报的核心手段）
# ──────────────────────────────────────────────────────────────

def test_id_card_requires_valid_checksum() -> None:
    """18 位但校验位不对 → 不当身份证。"""
    bad = "110105194912310021"  # 校验位应为 X，这里给 1
    _, found = pii.redact(bad)
    assert not any(f.kind == "id_card" for f in found)


def test_bank_card_requires_luhn() -> None:
    """16–19 位但 Luhn 不过 → 不当银行卡（语料里 25/26 条都是这种）。"""
    _, found = pii.redact("3239746110956160")  # 实测：是运单号
    assert not any(f.kind == "bank_card" for f in found)


def test_real_bank_card_is_caught() -> None:
    """Luhn 通过的真卡必须被抓到。"""
    out, found = pii.redact("卡号4111111111111111")
    assert any(f.kind == "bank_card" for f in found)
    assert "4111111111111111" not in out


# ──────────────────────────────────────────────────────────────
# D. 档位与开关
# ──────────────────────────────────────────────────────────────

def test_standard_profile_does_not_touch_address() -> None:
    """standard 档不动地址 —— strict 因为实测误报率高才默认不开。"""
    text = "我叫罗光亮，地址是隆回县南岳庙乡老屋场村11组26号"
    _, found = pii.redact(text, profile="standard")
    assert not any(f.kind in ("address", "name") for f in found)


def test_strict_profile_catches_address_and_name() -> None:
    text = "我叫罗光亮，地址是隆回县南岳庙乡老屋场村11组26号"
    out, found = pii.redact(text, profile="strict")
    kinds = {f.kind for f in found}
    assert "address" in kinds
    assert "name" in kinds
    assert "老屋场村11组26号" not in out


def test_unknown_profile_raises() -> None:
    """档位写错必须当场报错，不能静默回落成 standard。"""
    with pytest.raises(ValueError):
        pii.redact("随便一句", profile="nonsense")


def test_contains_pii_matches_redact() -> None:
    """contains_pii 的判断必须与 redact 一致 —— 两者分歧会让
    '要不要转人工' 与 '实际有没有脱敏' 对不上。"""
    for text in ("我的手机13812345678", "订单号20240101123456查询"):
        has = pii.contains_pii(text)
        _, found = pii.redact(text)
        assert has == bool(found)


def test_empty_text_is_identity() -> None:
    out, found = pii.redact("")
    assert out == ""
    assert not found
