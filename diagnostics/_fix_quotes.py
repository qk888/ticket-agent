"""一次性修复：demo_menu.py 里被误写成 ASCII 引号的中文引号。

根因：写文件时把「中文引号」打成了 ASCII `"`，于是它被 Python 当成
字符串定界符，把字符串从中间截断 —— `print("...内置"脚本化模型"，...")`
报 SyntaxError: invalid syntax. Perhaps you forgot a comma?

改法：把这些位置的 ASCII `"` 换成全角引号 “ ”。全角引号在源码里
是普通字符，不参与定界 —— 中文语境下本来就该用它。
"""

import io

PATH = "demo_menu.py"

# (原文, 改后) —— 每条都必须唯一命中，否则说明我对文件的判断已经过期
FIXES = [
    ('写的是"命令"，但命令解决不了', "写的是“命令”，但命令解决不了"),
    ('"我该在哪个窗口、先敲哪一条、跑完怎么知道成了"。', "“我该在哪个窗口、先敲哪一条、跑完怎么知道成了”。"),
    ('用的内置"脚本化模型"，不需要', "用的内置“脚本化模型”，不需要"),
    ('亲手扮演那个"人工"。', "亲手扮演那个“人工”。"),
    ('政策结论是"不该退"，上面会', "政策结论是“不该退”，上面会"),
    ('这就是"物理护栏而不是提示词约定" ——', "这就是“物理护栏而不是提示词约定” ——"),
    ('再试一次"强行破例"（加', "再试一次“强行破例”（加"),
    ('扮演"人工"裁决一笔退款', "扮演“人工”裁决一笔退款"),
]

text = io.open(PATH, encoding="utf-8").read()

for old, new in FIXES:
    n = text.count(old)
    assert n == 1, f"命中 {n} 次（期望 1）：{old!r}"
    text = text.replace(old, new)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)

# 复查：还有没有"引号两侧都是非 ASCII"的可疑位置
import re

left = 0
for i, ln in enumerate(text.splitlines(), 1):
    s = ln.strip()
    if s.startswith("#") or s.startswith('"""'):
        continue
    for m in re.finditer(r'"', ln):
        p = m.start()
        prev = ln[p - 1] if p > 0 else ""
        nxt = ln[p + 1] if p + 1 < len(ln) else ""
        if prev and nxt and ord(prev) > 127 and ord(nxt) > 127:
            left += 1
            print(f"  残留 {i}| {ln.strip()}")
            break

print(f"已修 {len(FIXES)} 处；残留可疑 {left} 处")
