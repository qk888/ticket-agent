"""修复 `_adddefect32.py` 被跑了两次造成的三处重复：
1. 缺陷 ㉜ 正文被插了两遍；
2. EXPECTED_DEFECTS 被加了两次（32→33→34，实际应该是 32）；
3. §4.2 标题已经是"三十二个"，与 32 个缺陷标记一致，不用改。

做法：删掉重复的那份缺陷正文，把 EXPECTED_DEFECTS 直接钉回 32。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).parent

# 1) 删重复正文
p = ROOT / "README.md"
t = p.read_text(encoding="utf-8")
MARK = "**缺陷 ㉜：过程探针全都假设"
n = t.count(MARK)
print(f"缺陷 ㉜ 标记出现 {n} 次")
if n == 2:
    first = t.index(MARK)
    second = t.index(MARK, first + 1)
    start = t.rfind("\n", 0, second) + 1
    end = t.find("\n## ", second)
    if end == -1:
        end = len(t)
    else:
        end += 1
    dup = t[start:end]
    assert dup.count(MARK) == 1, "待删区块标记数不对"
    t = t[:start] + t[end:]
    p.write_text(t, encoding="utf-8")
    print(f"已删除重复正文 {len(dup)} chars")
elif n == 1:
    print("正文只一份，无需删")
else:
    raise SystemExit(f"意外的出现次数：{n}")

# 2) EXPECTED_DEFECTS 钉回 32
p = ROOT / "_readmecheck.py"
t = p.read_text(encoding="utf-8")
m = re.search(r"EXPECTED_DEFECTS\s*=\s*(\d+)", t)
cur = int(m.group(1))
t = re.sub(r"EXPECTED_DEFECTS\s*=\s*\d+", "EXPECTED_DEFECTS = 32", t, count=1)
p.write_text(t, encoding="utf-8")
print(f"EXPECTED_DEFECTS {cur} → 32")
