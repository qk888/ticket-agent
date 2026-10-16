# -*- coding: utf-8 -*-
"""分析 JDDC question_answer.txt / multi_train.txt 的真实结构。"""
import sys, os, io, json, re, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))

def load(p):
    b = open(p, "rb").read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return b.decode(enc), enc
        except Exception:
            continue
    return b.decode("utf-8", "replace"), "utf-8/replace"

def show_head(p, n=12, label=""):
    print("=" * 70)
    print("FILE:", p, " size=", os.path.getsize(p), label)
    t, enc = load(p)
    print("DECODED AS:", enc, " chars=", len(t))
    lines = t.split("\n")
    print("LINES:", len(lines))
    print("--- 前 %d 行，repr 逐行（\\t 显式标出） ---" % n)
    for i, ln in enumerate(lines[:n]):
        print("[%02d] %r" % (i, ln[:600]))
    return t, enc

if __name__ == "__main__":
    for p in sys.argv[1:]:
        if os.path.exists(p):
            show_head(p)
        else:
            print("MISSING", p)
