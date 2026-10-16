# -*- coding: utf-8 -*-
"""批量搜索 HF 镜像 + ModelScope，结果写 UTF-8 JSON 文件。"""
import json, sys, urllib.request, urllib.parse, time, os

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

def getjson(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def hf_search(kw, limit=20):
    u = ("https://hf-mirror.com/api/datasets?search=%s&limit=%d&sort=downloads&direction=-1"
         % (urllib.parse.quote(kw), limit))
    try:
        r = getjson(u)
        if not isinstance(r, list):
            return {"__err__": str(r)[:200]}
        return [{"id": d.get("id"), "downloads": d.get("downloads"), "likes": d.get("likes"),
                 "lastModified": d.get("lastModified"),
                 "desc": (d.get("description") or "")[:300].replace("\n", " ").replace("\t", " ")}
                for d in r]
    except Exception as e:
        return {"__err__": repr(e)[:200]}

def ms_search(kw, size=20):
    u = ("https://modelscope.cn/api/v1/datasets?Query=%s&PageSize=%d&SortBy=Downloads"
         % (urllib.parse.quote(kw), size))
    try:
        r = getjson(u)
    except Exception as e:
        return {"__err__": repr(e)[:200]}
    data = (r or {}).get("Data") if isinstance(r, dict) else None
    if not isinstance(data, list):
        return {"__err__": str(r)[:300]}
    rows = []
    for d in data:
        dd = d.get("Data") if isinstance(d.get("Data"), dict) else d
        rows.append({"ns": dd.get("Namespace"), "name": dd.get("Name"),
                     "cn": dd.get("ChineseName"), "id": dd.get("Id"),
                     "dl": dd.get("Downloads"), "likes": dd.get("Likes"),
                     "desc": (dd.get("Description") or "")[:300].replace("\n", " ")})
    return rows

if __name__ == "__main__":
    mode = sys.argv[1]
    outf = os.path.join(HERE, sys.argv[2])
    kws = sys.argv[3:]
    res = {}
    for kw in kws:
        res[kw] = hf_search(kw) if mode == "hf" else ms_search(kw)
        time.sleep(0.3)
    with open(outf, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("wrote", outf, os.path.getsize(outf))
