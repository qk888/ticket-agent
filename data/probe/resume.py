# -*- coding: utf-8 -*-
"""断点续传下载器：ghapi Range 支持 IncompleteRead 重试；也支持 codeload tarball。"""
import os, sys, json, time, urllib.request, urllib.error, http.client

UA = {"User-Agent": "Mozilla/5.0"}

def total_size(url):
    h = dict(UA); h["Accept"] = "application/vnd.github.raw"; h["Range"] = "bytes=0-0"
    for _ in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                cr = r.headers.get("Content-Range") or ""
                if "/" in cr:
                    return int(cr.split("/")[-1])
        except Exception:
            time.sleep(2)
    return None

def resume_ghapi(repo, path, branch, dest, chunk=4_000_000):
    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, branch)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    total = total_size(url)
    got = os.path.getsize(dest) if os.path.exists(dest) else 0
    fails = 0
    t0 = time.time()
    while True:
        if total and got >= total:
            break
        end = got + chunk - 1
        h = dict(UA); h["Accept"] = "application/vnd.github.raw"
        h["Range"] = "bytes=%d-%d" % (got, end)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=180) as r:
                data = r.read()
        except http.client.IncompleteRead as e:
            data = e.partial
        except Exception as e:
            fails += 1
            print("  retry %d: %s" % (fails, repr(e)[:90]), flush=True)
            if fails > 25:
                return {"ok": False, "got": got, "total": total}
            time.sleep(2)
            continue
        if not data:
            break
        with open(dest, "ab") as f:
            f.write(data)
        got += len(data)
        if got // (10 * 1024 * 1024) != (got - len(data)) // (10 * 1024 * 1024):
            print("  %d / %s MB  (%.0fs)" % (got // 1048576, total // 1048576 if total else "?", time.time() - t0), flush=True)
        if len(data) < chunk and total and got < total:
            fails += 1
            if fails > 25:
                return {"ok": False, "got": got, "total": total}
            time.sleep(1)
    return {"ok": True, "got": got, "total": total, "dest": dest}

if __name__ == "__main__":
    repo, branch, path, dest = sys.argv[1:5]
    print(json.dumps(resume_ghapi(repo, path, branch, dest), ensure_ascii=False))
