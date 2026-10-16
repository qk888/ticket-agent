#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 codeload.github.com 的 tar.gz 通道整仓下载并抽指定文件（绕开 api.github.com 限流）。

用法:
    python targz.py <owner/repo> <branch> <dest_dir> [member_substr ...]

例:
    python targz.py zhangbo2008/JDDC_for_train_gpt_data main out_jddc extract_train.json
    python targz.py Dikea/Dialog-System-with-Task-Retrieval-and-Seq2seq master out_jddc multi_train.txt

不带 member_substr 时把整仓解到 dest_dir。
"""
import io
import sys
import tarfile
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def main():
    repo, branch, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    wants = sys.argv[4:]
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
    print(f"[targz] GET {url}", flush=True)
    req = urllib.request.Request(url, headers=UA)
    resp = urllib.request.urlopen(req, timeout=300)
    total = resp.headers.get("Content-Length")
    print(f"[targz] status={resp.status} content-length={total}", flush=True)
    raw = resp.read()
    print(f"[targz] downloaded {len(raw)} bytes, opening tar", flush=True)
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    n = 0
    for m in tf.getmembers():
        if not m.isfile():
            continue
        if wants and not any(w in m.name for w in wants):
            continue
        data = tf.extractfile(m).read()
        out = f"{dest}/{m.name.split('/', 1)[-1].replace('/', '__')}"
        with open(out, "wb") as f:
            f.write(data)
        print(f"[targz] wrote {out} ({len(data)} bytes)", flush=True)
        n += 1
    print(f"[targz] done, {n} file(s)", flush=True)


if __name__ == "__main__":
    main()
