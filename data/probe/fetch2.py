# -*- coding: utf-8 -*-
"""fetch2.py — GitHub Contents API 分块续传下载器（partial-aware）

解决 `http.client.IncompleteRead` 导致的分块重试浪费：
  urllib 在 body 被截断时抛 IncompleteRead，但异常对象带 `e.partial`，
  里面是**已经收全的那部分字节**。直接丢弃重试 = 每次白丢 8MB。
本脚本捕获异常后把 e.partial 落盘，再从新的长度继续，因此不会丢进度。

用法：
  python fetch2.py <owner/repo> <ref> <repo内路径> <本地目标> [chunk_bytes]
"""
import os
import sys
import time
import http.client
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github.raw"}


def total_size(url, timeout=90):
    req = urllib.request.Request(url, headers=dict(UA, **{"Range": "bytes=0-0"}))
    r = urllib.request.urlopen(req, timeout=timeout)
    cr = r.headers.get("Content-Range")
    r.read()
    if cr and "/" in cr:
        return int(cr.split("/")[-1])
    cl = r.headers.get("Content-Length")
    return int(cl) if cl else None


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        return 1
    repo, ref, path, dest = sys.argv[1:5]
    chunk = int(sys.argv[5]) if len(sys.argv) > 5 else 8 * 1024 * 1024

    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, ref)
    tot = total_size(url)
    if tot is None:
        print("FAIL 无法取到总长")
        return 1
    print("total %d" % tot, flush=True)

    pos = os.path.getsize(dest) if os.path.exists(dest) else 0
    if pos > tot:
        print("本地文件比远端大，可能之前写坏，截断重来")
        os.truncate(dest, 0)
        pos = 0
    print("resume at %d (%.1f%%)" % (pos, 100.0 * pos / tot), flush=True)

    fails = 0
    t0 = time.time()
    while pos < tot:
        end = min(pos + chunk - 1, tot - 1)
        req = urllib.request.Request(url, headers=dict(UA, **{"Range": "bytes=%d-%d" % (pos, end)}))
        try:
            r = urllib.request.urlopen(req, timeout=240)
            body = r.read()
            with open(dest, "ab") as f:
                f.write(body)
            pos += len(body)
            fails = 0
        except http.client.IncompleteRead as e:
            # 关键：partial 是已收到的字节，不能丢
            part = e.partial or b""
            if part:
                with open(dest, "ab") as f:
                    f.write(part)
                pos += len(part)
            fails += 1
            print("partial+%d -> %d/%d (%.1f%%)" % (len(part), pos, tot, 100.0 * pos / tot), flush=True)
        except Exception as e:
            fails += 1
            print("fail#%d %r" % (fails, e)[:120], flush=True)
            if fails > 60:
                print("GIVEUP at %d" % pos)
                break
            time.sleep(10)
            continue
        print("pos %d/%d (%.1f%%) %.0fKB/s" % (pos, tot, 100.0 * pos / tot,
                                               pos / 1024.0 / max(time.time() - t0, 0.1)), flush=True)
    print("DONE %d/%d" % (os.path.getsize(dest) if os.path.exists(dest) else 0, tot))
    return 0 if pos >= tot else 2


if __name__ == "__main__":
    sys.exit(main())
