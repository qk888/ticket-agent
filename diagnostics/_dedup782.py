"""README 里 7.8.2 被插入了两次（第一次跑失败时 anchor 已替换过一次？不，是脚本跑了两次都成功）。
这里用幂等方式：保留第一份完整区块，删除第二份重复的。

判据：`### 7.8.2` 到下一个 `## ` 之间的区块若出现两次，删掉后一份。
"""
import pathlib

p = pathlib.Path(__file__).parent / "README.md"
t = p.read_text(encoding="utf-8")

MARK = "### 7.8.2 扩到 200 case 后：多智能体到底在哪些条件上赢"
n = t.count(MARK)
print(f"7.8.2 标记出现 {n} 次")
if n <= 1:
    print("无需处理")
    raise SystemExit(0)

# 找第二份的起点与终点（终点 = 下一个 "## " 标题行）
first = t.index(MARK)
second = t.index(MARK, first + 1)
# 从 second 往前回退到最近的换行，往后找到下一个 "## " 行
start = t.rfind("\n", 0, second) + 1
end = t.find("\n## ", second)
if end == -1:
    end = len(t)
else:
    end = end + 1
dup = t[start:end]
print(f"重复区块 {len(dup)} chars，首行: {dup.splitlines()[0][:60]!r}，末行: {dup.splitlines()[-1][:60]!r}")
assert dup.count(MARK) == 1
assert dup.count("<details>") == dup.count("</details>"), "重复区块 details 不平衡"
t = t[:start] + t[end:]
p.write_text(t, encoding="utf-8")
print("README.md：", len(t), "chars")
print("✓ 已删除重复区块")
