# -*- coding: utf-8 -*-
"""第二轮搜索：GitHub 仓库搜索（中文关键词写在源码里，避免命令行编码问题）。"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ghsearch import gh_search, hf_filter

HERE = os.path.dirname(os.path.abspath(__file__))

GH_QUERIES = [
    "电商客服 数据集",
    "客服对话 数据集",
    "售后 工单 数据集",
    "淘宝客服 语料",
    "京东客服 数据集",
    "ECDT e-commerce dialogue corpus",
    "chinese customer service dataset",
    "工单 意图识别 中文",
    "客户服务 大模型 数据",
    "退换货 数据集 中文",
    "投诉 数据集 中文",
    "online shopping dialogue chinese dataset",
]

HF_QUERIES = [
    "customer service chinese",
    "e-commerce dialogue chinese",
    "customer support intent",
    "ticket classification",
    "after-sales",
    "complaint chinese",
    "dialogue summarization chinese",
]

if __name__ == "__main__":
    out = {}
    for q in GH_QUERIES:
        out["GH::" + q] = gh_search(q)
        time.sleep(2.0)
    for q in HF_QUERIES:
        out["HF::" + q] = hf_filter("", search=q) if False else None
        u = "https://hf-mirror.com/api/datasets?search=%s&limit=25&sort=downloads&direction=-1" % __import__("urllib.parse", fromlist=["quote"]).quote(q)
        import urllib.request
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            out["HF::" + q] = [{"id": x.get("id"), "downloads": x.get("downloads"), "likes": x.get("likes"),
                                "desc": (x.get("description") or "")[:200].replace("\n", " ")} for x in d]
        except Exception as e:
            out["HF::" + q] = {"__err__": repr(e)[:120]}
        time.sleep(1.0)
    with open(os.path.join(HERE, "gh_r2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("wrote gh_r2.json")
