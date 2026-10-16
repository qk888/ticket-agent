# -*- coding: utf-8 -*-
"""用 GitHub API 看仓库文件树 / 文件大小；测试 jsdelivr 通道。"""
import json, sys, urllib.request, urllib.parse, time, os

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

def getjson(url, timeout=40, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def tree(repo, branch="master"):
    for br in ([branch] if branch else []) + ["main", "master"]:
        try:
            r = getjson("https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, br))
            return br, [{"path": i["path"], "size": i.get("size"), "type": i["type"]}
                        for i in r.get("tree", []) if i["type"] == "blob"]
        except Exception as e:
            last = repr(e)[:120]
    return None, {"__err__": last}

if __name__ == "__main__":
    mode = sys.argv[1]
    outf = os.path.join(HERE, sys.argv[2])
    repos = sys.argv[3:]
    res = {}
    for rp in repos:
        if mode == "tree":
            br, t = tree(rp)
            res[rp] = {"branch": br, "files": t}
        time.sleep(1.2)
    with open(outf, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("wrote", outf, os.path.getsize(outf))
