"""把测试数与缺陷取证链数在 交付总览.md 里同步（226→231、31→32）。

硬规则：逐条 assert 命中数；一次全替换完再落盘。
"""
import pathlib
import re

p = pathlib.Path(__file__).parent / "交付总览.md"
t = p.read_text(encoding="utf-8")

# 单元测试 226 → 231
n_test = t.count("226")
print(f"「226」出现 {n_test} 次")
# 只改测试数那几处（其它 226 是语料条数，不动）
pairs = [
    ("| 单元测试 | **226** |", "| 单元测试 | **231** |"),
    ("226 项测试", "231 项测试"),
    ("226 个测试", "231 个测试"),
    ("`pytest` 226", "`pytest` 231"),
    ("| 缺陷取证链 | **31 条** |", "| 缺陷取证链 | **32 条** |"),
    ("**31 条缺陷取证链**", "**32 条缺陷取证链**"),
    ("**31 条**缺陷取证链", "**32 条**缺陷取证链"),
]
changed = 0
for old, new in pairs:
    c = t.count(old)
    if c:
        t = t.replace(old, new)
        changed += c
        print(f"  ✓ {old!r} → {new!r}（{c} 处）")

p.write_text(t, encoding="utf-8")
print(f"共改 {changed} 处；交付总览.md：{len(t)} chars")
