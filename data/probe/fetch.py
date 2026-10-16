# -*- coding: utf-8 -*-
"""带 Range 支持的下载工具：探测可用下载通道 + 只拉前 N 字节做样本。"""
import json, sys, os, urllib.request, urllib.error, time

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

CHANNELS = {
    # 通道名 -> URL 模板（{repo} {branch} {path}）
    "jsdelivr": "https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}",
    "ghraw":    "https://raw.githubusercontent.com/{repo}/{branch}/{path}",
    "ghapi":    "https://api.github.com/repos/{repo}/contents/{path}?ref={branch}",
    "ghpage":   "https://github.com/{repo}/raw/{branch}/{path}",
    "ghmedia":  "https://media.githubusercontent.com/media/{repo}/{branch}/{path}",
}

def probe(repo, branch, path, nbytes=300000):
    """对每个通道尝试 Range 请求前 nbytes 字节。"""
    out = {}
    for name, tpl in CHANNELS.items():
        url = tpl.format(repo=repo, branch=branch, path=urllib.parse.quote(path) if False else path)
        h = dict(UA)
        h["Range"] = "bytes=0-%d" % (nbytes - 1)
        if name == "ghapi":
            h["Accept"] = "application/vnd.github.raw"
            h["Range"] = "bytes=0-%d" % (nbytes - 1)
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read(nbytes)
            out[name] = {"ok": True, "code": r.status, "got": len(b), "head": b[:120].decode("utf-8", "replace")}
        except urllib.error.HTTPError as e:
            out[name] = {"ok": False, "code": e.code, "err": str(e)[:80]}
        except Exception as e:
            out[name] = {"ok": False, "err": repr(e)[:100]}
        time.sleep(0.3)
    return out

if __name__ == "__main__":
    repo, branch, path = sys.argv[1], sys.argv[2], sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 300000
    r = probe(repo, branch, path, n)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
