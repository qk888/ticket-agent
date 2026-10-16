# -*- coding: utf-8 -*-
"""通用下载器：优先 jsdelivr，回退 GitHub raw API；支持 Range 前 N 字节。"""
import os, sys, json, urllib.request, urllib.error, time

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")

def fetch(rel_url, dest, nbytes=None, timeout=90):
    """rel_url: 相对 https:// 的完整 URL 或直接给 URL。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    h = dict(UA)
    if nbytes:
        h["Range"] = "bytes=0-%d" % (nbytes - 1)
    req = urllib.request.Request(rel_url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(nbytes if nbytes else None)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data), dest

def _ghapi_chunked(url, dest, total=None, chunk=8_000_000, timeout=180):
    """ghapi 单次返回被截断时，用 Range 分块拼接。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    got = 0
    with open(dest, "wb") as f:
        while True:
            end = got + chunk - 1
            h = dict(UA)
            h["Accept"] = "application/vnd.github.raw"
            h["Range"] = "bytes=%d-%d" % (got, end)
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if not data:
                break
            f.write(data)
            got += len(data)
            if len(data) < chunk:
                break
            if total and got >= total:
                break
            time.sleep(0.3)
    return got

def gh_fetch(repo, branch, path, dest, nbytes=None):
    errors = []
    for name, url in [
        ("jsdelivr", "https://cdn.jsdelivr.net/gh/%s@%s/%s" % (repo, branch, path)),
        ("ghapi", "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, branch)),
    ]:
        try:
            if name == "ghapi" and nbytes is None:
                # 大文件：分块 Range 拼接
                total = None
                try:
                    hm = dict(UA)
                    hm["Accept"] = "application/vnd.github.raw"
                    hm["Range"] = "bytes=0-0"
                    with urllib.request.urlopen(urllib.request.Request(url, headers=hm), timeout=60) as r:
                        cr = r.headers.get("Content-Range") or ""
                        if "/" in cr:
                            total = int(cr.split("/")[-1])
                except Exception:
                    pass
                got = _ghapi_chunked(url, dest, total)
                if total and got < total:
                    raise RuntimeError("incomplete %d/%d" % (got, total))
                return {"ok": True, "channel": "ghapi-chunked", "bytes": got, "dest": dest}
            h = dict(UA)
            if nbytes:
                h["Range"] = "bytes=0-%d" % (nbytes - 1)
            if name == "ghapi":
                h["Accept"] = "application/vnd.github.raw"
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read(nbytes if nbytes else None)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            return {"ok": True, "channel": name, "bytes": len(data), "dest": dest}
        except Exception as e:
            errors.append("%s: %s" % (name, repr(e)[:120]))
            time.sleep(0.5)
    return {"ok": False, "errors": errors}

def hf_fetch(repo_id, filename, dest, nbytes=None):
    url = "https://hf-mirror.com/datasets/%s/resolve/main/%s" % (repo_id, filename)
    h = dict(UA)
    if nbytes:
        h["Range"] = "bytes=0-%d" % (nbytes - 1)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read(nbytes if nbytes else None)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return {"ok": True, "bytes": len(data), "dest": dest}

if __name__ == "__main__":
    kind = sys.argv[1]
    if kind == "gh":
        repo, branch, path, dest = sys.argv[2:6]
        n = int(sys.argv[6]) if len(sys.argv) > 6 else None
        print(json.dumps(gh_fetch(repo, branch, path, dest, n), ensure_ascii=False))
    elif kind == "hf":
        rid, fn, dest = sys.argv[2:5]
        n = int(sys.argv[5]) if len(sys.argv) > 5 else None
        print(json.dumps(hf_fetch(rid, fn, dest, n), ensure_ascii=False))
    elif kind == "url":
        url, dest = sys.argv[2:4]
        n = int(sys.argv[4]) if len(sys.argv) > 4 else None
        print(json.dumps(fetch(url, dest, n), ensure_ascii=False))
