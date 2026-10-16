# -*- coding: utf-8 -*-
"""GitHub 仓库搜索 + HF filter 搜索。"""
import json, sys, urllib.request, urllib.parse, time, os

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

def getjson(url, timeout=30, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def gh_search(q, per_page=15):
    u = "https://api.github.com/search/repositories?q=%s&per_page=%d&sort=stars" % (urllib.parse.quote(q), per_page)
    try:
        r = getjson(u)
        return [{"full_name": i["full_name"], "stars": i["stargazers_count"],
                 "size_kb": i["size"], "desc": (i.get("description") or "")[:160],
                 "lang": i.get("language"), "branch": i.get("default_branch"),
                 "pushed": i.get("pushed_at")} for i in r.get("items", [])]
    except Exception as e:
        return {"__err__": repr(e)[:200]}

def gh_code_search(q, per_page=10):
    """GitHub 代码搜索（无需 token 也能用有限次数）。"""
    u = "https://api.github.com/search/code?q=%s&per_page=%d" % (urllib.parse.quote(q), per_page)
    try:
        r = getjson(u)
        return [{"repo": i["repository"]["full_name"], "path": i["path"]} for i in r.get("items", [])]
    except Exception as e:
        return {"__err__": repr(e)[:200]}

def hf_filter(filt, search=None, limit=25):
    u = "https://hf-mirror.com/api/datasets?filter=%s&limit=%d&sort=downloads&direction=-1" % (urllib.parse.quote(filt), limit)
    if search:
        u += "&search=" + urllib.parse.quote(search)
    try:
        r = getjson(u)
        if not isinstance(r, list):
            return {"__err__": str(r)[:200]}
        return [{"id": d.get("id"), "downloads": d.get("downloads"), "likes": d.get("likes"),
                 "desc": (d.get("description") or "")[:250].replace("\n", " ").replace("\t", " ")} for d in r]
    except Exception as e:
        return {"__err__": repr(e)[:200]}

if __name__ == "__main__":
    mode = sys.argv[1]
    outf = os.path.join(HERE, sys.argv[2])
    args = sys.argv[3:]
    res = {}
    for a in args:
        res[a] = gh_search(a) if mode == "gh" else hf_filter(a[4:] if a.startswith("ANY:") else a)
        time.sleep(1.0)
    with open(outf, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("wrote", outf, os.path.getsize(outf))
