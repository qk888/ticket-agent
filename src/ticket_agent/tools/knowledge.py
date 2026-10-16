"""知识库检索：轻量 RAG，零外部依赖。

设计取舍（面试可讲）：
- **不用向量库**。政策文档只有 7 条规则，用 BM25 风格的词频打分 + 中文 bigram 就够，
  召回质量在这么小的语料上不比 embedding 差，但省掉一个服务和一次网络调用。
- **如果语料涨到几百条**，再换 embedding —— 接口不变，只替换 `search_policies`。
- **中文分词不做**：用字符 bigram + 关键词加权，避免引入 jieba 之类的重依赖，
  在短文本短查询上效果与分词相当。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from sqlalchemy import select

from ..db.models import PolicyArticle
from ..db.session import session_scope

# 规则编号与业务术语的加权词表 —— 命中这些词的条款优先
KEYWORD_BOOST: dict[str, float] = {
    "R1": 3.0, "R2": 3.0, "R3": 3.0, "R4": 3.0, "R5": 3.0, "R6": 3.0, "R7": 3.0,
    "七天": 2.0, "无理由": 2.0, "签收": 2.0, "退款": 2.0, "退货": 2.0,
    "内衣": 2.0, "定制": 2.0, "数字商品": 2.5, "生鲜": 2.5,
    "人工": 2.0, "审批": 2.0, "黑名单": 2.5, "风险": 1.5,
    "拦截": 2.0, "物流": 1.5, "类目": 1.5, "金额": 1.5, "500": 2.0, "0.7": 2.0,
}

_STOP = set("的了呢吗是有和与我你他它这那请问一下帮我想要能不可以怎么如何")


def _tokenize(text: str) -> list[str]:
    """中文 bigram + 英文/数字词 + 规则编号。"""
    text = text.lower()
    tokens: list[str] = []
    # 规则编号
    tokens += re.findall(r"r\d+", text)
    # 英文/数字
    tokens += re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text)
    # 中文 bigram
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in han:
        seg = "".join(ch for ch in seg if ch not in _STOP)
        tokens += [seg[i : i + 2] for i in range(len(seg) - 1)]
        # 保留单字，避免极短查询无命中
        if len(seg) == 1:
            tokens.append(seg)
    return tokens


def _score(query_tokens: Counter[str], doc_tokens: Counter[str], doc_len: int, avg_len: float, k1: float = 1.2, b: float = 0.75) -> float:
    """BM25。"""
    score = 0.0
    for t, qf in query_tokens.items():
        df = doc_tokens.get(t, 0)
        if df == 0:
            continue
        idf = math.log(1 + (1 + 1) / (df + 0.5))  # 简化 IDF（语料极小，df 用词频近似）
        tf = df
        denom = tf + k1 * (1 - b + b * (doc_len / max(avg_len, 1.0)))
        score += idf * (tf * (k1 + 1)) / max(denom, 1e-9) * (1 + 0.3 * math.log1p(qf))
    return score


def search_policies(question: str, top_k: int = 3) -> list[dict[str, Any]]:
    """检索政策条款。返回 [{rule_id, section, content, score}, ...]，按相关性降序。"""
    with session_scope() as s:
        arts = list(s.scalars(select(PolicyArticle)))
        if not arts:
            return [
                {
                    "rule_id": "N/A",
                    "section": "知识库为空",
                    "content": "政策文档尚未导入。请先运行 `python -m ticket_agent.cli seed`。",
                    "score": 0.0,
                }
            ]

        docs = [(a, _tokenize(f"{a.section} {a.content}")) for a in arts]
        lens = [len(t) for _, t in docs]
        avg_len = sum(lens) / max(len(lens), 1)

        q_tokens = Counter(_tokenize(question))
        # 关键词加权：把加权词按权重重复计入 query
        for kw, w in KEYWORD_BOOST.items():
            if kw.lower() in question.lower():
                q_tokens[kw.lower()] += int(w * 3)

        scored: list[tuple[float, PolicyArticle]] = []
        for (art, toks), _ln in zip(docs, lens):
            cnt = Counter(toks)
            base = _score(q_tokens, cnt, len(toks), avg_len)
            # 精确规则号命中直接大幅加分
            boost = 0.0
            for kw, w in KEYWORD_BOOST.items():
                k = kw.lower()
                if k in cnt and k in q_tokens:
                    boost += w * 0.5
            scored.append((base + boost, art))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "rule_id": art.rule_id,
                "section": art.section,
                "content": art.content,
                "score": round(sc, 4),
            }
            for sc, art in scored[:top_k]
        ]
