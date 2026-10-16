# -*- coding: utf-8 -*-
"""HF 镜像：批量拉数据集详情（文件清单/大小/下载量）。"""
import json, sys, os, urllib.request, urllib.parse, time

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

def getjson(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def detail(rid):
    try:
        d = getjson("https://hf-mirror.com/api/datasets/" + rid)
    except Exception as e:
        return {"__err__": repr(e)[:150]}
    sib = d.get("siblings") or []
    return {"id": d.get("id"), "downloads": d.get("downloads"), "likes": d.get("likes"),
            "storage": d.get("usedStorage"), "lastModified": d.get("lastModified"),
            "tags": d.get("tags"), "files": [s.get("rfilename") for s in sib],
            "desc": (d.get("description") or "")[:400].replace("\n", " ")}

if __name__ == "__main__":
    outf = os.path.join(HERE, sys.argv[1])
    ids = sys.argv[2:]
    res = {}
    for i in ids:
        res[i] = detail(i)
        time.sleep(0.35)
    with open(outf, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("wrote", outf, os.path.getsize(outf))
