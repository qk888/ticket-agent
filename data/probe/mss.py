# -*- coding: utf-8 -*-
"""ModelScope 批量搜索（关键词写在源码里，避免命令行编码问题）。"""
import json, os, sys, time, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

KWS = [
    "客服对话", "电商客服", "售后", "工单", "投诉", "退换货",
    "客服意图", "意图识别 电商", "售后服务 对话", "客户服务 对话",
    "退款", "物流 客服", "多轮对话 客服", "投诉 处理",
]

def ms_search(kw, page=20):
    u = "https://modelscope.cn/api/v1/datasets?Query=%s&PageSize=%d&SortBy=Downloads" % (
        urllib.parse.quote(kw), page)
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"__err__": repr(e)[:120]}
    out = []
    for it in (d.get("Data") or []):
        out.append({"ns": it.get("Namespace"), "name": it.get("Name"),
                    "cn": it.get("ChineseName"), "dl": it.get("Downloads"),
                    "likes": it.get("Likes"),
                    "desc": (it.get("Description") or "")[:180].replace("\n", " ")})
    return out

if __name__ == "__main__":
    res = {}
    for k in KWS:
        res[k] = ms_search(k)
        time.sleep(0.6)
    with open(os.path.join(HERE, "ms_search2.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("wrote ms_search2.json")
