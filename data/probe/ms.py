# -*- coding: utf-8 -*-
"""ModelScope：文件清单 + 详情 + 样本下载。"""
import json, sys, os, urllib.request, urllib.error, urllib.parse, time

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))

def getjson(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def files(ns, name, rev="master"):
    u = "https://modelscope.cn/api/v1/datasets/%s/%s/repo/tree?Revision=%s&Recursive=true" % (
        urllib.parse.quote(ns), urllib.parse.quote(name), rev)
    try:
        d = getjson(u)
    except Exception as e:
        return {"__err__": repr(e)[:150]}
    data = (d or {}).get("Data") or {}
    fl = data.get("Files") or []
    return [{"path": f.get("Path"), "size": f.get("Size"), "type": f.get("Type")} for f in fl]

def detail(ns, name):
    u = "https://modelscope.cn/api/v1/datasets/%s/%s" % (urllib.parse.quote(ns), urllib.parse.quote(name))
    try:
        d = getjson(u)
    except Exception as e:
        return {"__err__": repr(e)[:150]}
    dd = (d or {}).get("Data") or {}
    return {"ns": dd.get("Namespace"), "name": dd.get("Name"), "cn": dd.get("ChineseName"),
            "dl": dd.get("Downloads"), "likes": dd.get("Likes"), "license": dd.get("License"),
            "desc": (dd.get("Description") or "")[:600].replace("\n", " "),
            "readme_head": (dd.get("ReadmeContent") or "")[:2500]}

def dl(ns, name, path, dest, nbytes=None, rev="master"):
    u = ("https://modelscope.cn/api/v1/datasets/%s/%s/repo?Revision=%s&FilePath=%s" % (
        urllib.parse.quote(ns), urllib.parse.quote(name), rev, urllib.parse.quote(path)))
    h = dict(UA)
    if nbytes:
        h["Range"] = "bytes=0-%d" % (nbytes - 1)
    req = urllib.request.Request(u, headers=h)
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read(nbytes) if nbytes else r.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    op = "wb" if not nbytes else "wb"
    with open(dest, op) as f:
        f.write(data)
    return {"ok": True, "bytes": len(data), "dest": dest}

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "probe":
        outf = os.path.join(HERE, sys.argv[2])
        pairs = sys.argv[3:]
        res = {}
        for p in pairs:
            ns, name = p.split("/", 1)
            res[p] = {"detail": detail(ns, name), "files": files(ns, name)}
            time.sleep(0.4)
        with open(outf, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("wrote", outf, os.path.getsize(outf))
    elif mode == "dl":
        ns, name, path, dest = sys.argv[2:6]
        n = int(sys.argv[6]) if len(sys.argv) > 6 else None
        print(json.dumps(dl(ns, name, path, dest, n), ensure_ascii=False))
