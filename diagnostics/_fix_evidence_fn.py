"""一次性修复：nodes.py 里 `corpus_says_evidence` → `detect_evidence_type`（#60）。

为什么用脚本而不是 edit 工具：那个函数的 docstring 里含全角引号
（“凭证已提供”），逐字符匹配已经失配过一次。而它又是文件**最后一个**函数，
所以按 `def corpus_says_evidence(` 定位后直接截断到文件尾再追加新实现，
一个字符都不用逐行匹配 —— 全角引号这个坑就绕过去了。

跑法：
    python _fix_evidence_fn.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = ROOT / "src" / "ticket_agent" / "graph" / "nodes.py"

NEW_TAIL = '''#: 凭证关键词 → `rules.EVIDENCE_TYPES` 受控词表。**顺序即优先级**。
#:
#: ★ 为什么顺序有意义：#60 的判据是「凭证要能证明**问题本身**」。
#:   一句话里同时出现「照片」和「运单号」（"照片发了，运单号 xxx"）时
#:   要取更强的那个，所以按序命中即停。
#:
#: ★ 为什么把「订单号 / 运单号」从原词表里踢出：它们不是凭证，是**业务钥匙**。
#:   PII 脱敏那一轮量过 —— 语料里 16–19 位的「疑似卡号」有 25/26 条
#:   Luhn 校验不通过，原文全是订单号 / 运单号。订单号是 agent 找订单的
#:   唯一入口，把它当凭证，等于用「有订单号」去证明「东西坏了」。
_EVIDENCE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inspection_report", ("检测报告", "质检报告", "检验报告")),
    ("video", ("视频", "开箱录")),
    ("photo", ("照片", "图片", "拍了", "拍给你", "拍个", "实拍")),
    ("screenshot", ("截图",)),
    ("chat_log", ("聊天记录", "对话记录", "沟通记录")),
    ("tracking_proof", ("运单号", "快递单号", "物流轨迹", "签收异常", "快递单")),
)


def detect_evidence_type(text: str) -> str:
    """这段话里的凭证属于受控词表里的哪一类？认不出就返回空串。

    ★ 它只负责「认出**是哪一类**」，不负责「够不够格」—— 后者是
      `rules.check_evidence` 的事。拆成两个函数是因为「认得出」与
      「够不够格」是两种不同的失败：前者是「没说清交的是什么」，
      要客户重发；后者是「交了但证明不了这件事」，要客户补对类型那一份。
      合成一个布尔会把两者压成同一种「没凭证」，改法就跟着错。
    """
    from ..db.corpus import normalize

    t = normalize(text)
    for ev_type, keys in _EVIDENCE_KEYWORDS:
        if any(k in t for k in keys):
            return ev_type
    return ""
'''

# 注释里的旧名 → 新名（含反引号包裹的引用）
COMMENT_RENAMES = [
    (
        "（`classify_cause` / `extract_amount` / `corpus_says_evidence`），",
        "（`classify_cause` / `extract_amount` / `detect_evidence_type`），",
    ),
    (
        "且与 `corpus_says_evidence` 同源",
        "且与 `detect_evidence_type` 同源",
    ),
]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    marker = "def corpus_says_evidence(text: str) -> bool:"
    if marker not in text:
        print("✗ 找不到旧函数定义 —— 已经改过了？")
        return 1
    if text.count(marker) != 1:
        print(f"✗ 旧函数定义出现 {text.count(marker)} 次，锚点不唯一")
        return 1

    head = text[: text.index(marker)]

    for old, new in COMMENT_RENAMES:
        n = head.count(old)
        head = head.replace(old, new)
        print(f"  注释改名 {n} 处: {old[:34]}")

    left = head.count("corpus_says_evidence")
    if left:
        print(f"✗ 注释里还剩 {left} 处 corpus_says_evidence，需要人工看：")
        for i, line in enumerate(head.splitlines(), 1):
            if "corpus_says_evidence" in line:
                print(f"    {i}: {line}")
        return 1

    out = head.rstrip("\n") + "\n\n\n" + NEW_TAIL
    TARGET.write_text(out, encoding="utf-8")
    print(f"✓ 已写入 {TARGET}（{len(out)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())