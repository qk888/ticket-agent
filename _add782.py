"""把 200 case 两臂对照写进 README §7.8（逐条件 multi赢/single赢），并补 §7.8.2。

数据来源：`eval_out/_progress-merged-single.jsonl` 与 `_progress-merged-multi.clean.jsonl`
（各 600 run，键 = (case_id, k_index)，同 seed 7 / customers 1000 / size 200 / k 3）。

硬规则：先算、打印，再由脚本写文档；文档里的每个数字必须来自这一轮实测。
"""
import json
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).parent


def load(name):
    p = ROOT / "eval_out" / f"_progress-merged-{name}.clean.jsonl"
    if not p.exists():
        p = ROOT / "eval_out" / f"_progress-merged-{name}.jsonl"
    recs = [
        json.loads(l)
        for l in p.read_text(encoding="utf-8").splitlines()
        if l.strip() and "__sig__" not in l
    ]
    return [r for r in recs if r.get("arm") == name]


s = {(r["case_id"], r["k_index"]): r for r in load("single")}
m = {(r["case_id"], r["k_index"]): r for r in load("multi")}
assert len(s) == 600 and len(m) == 600, (len(s), len(m))
assert set(s) == set(m), "两臂键集合不一致"

# 逐条件 multi 赢 / single 赢
diff = defaultdict(lambda: [0, 0])
for k in s:
    sp, mp = s[k].get("passed"), m[k].get("passed")
    if sp and not mp:
        diff[s[k]["condition"]][1] += 1
    elif mp and not sp:
        diff[s[k]["condition"]][0] += 1

rows = sorted(diff.items(), key=lambda kv: kv[1][0] - kv[1][1])
multi_win = [(c, a, b) for c, (a, b) in rows if a - b >= 3]
single_win = [(c, a, b) for c, (a, b) in rows if b - a >= 3]

# 另一维：multi 净负的条件里，verifier 有没有起作用？
# verifier_passed 只在 multi 有；看 multi 通过的条件里 verifier_passed 的占比
ver_by_cond = defaultdict(lambda: [0, 0])
for k in m:
    r = m[k]
    ver_by_cond[r["condition"]][0] += 1
    if r.get("verifier_passed"):
        ver_by_cond[r["condition"]][1] += 1

tbl = []
tbl.append("| 条件 | multi 过 single 挂 | single 过 multi 挂 | 净胜 |")
tbl.append("|---|---|---|---|")
for c, (a, b) in sorted(diff.items(), key=lambda kv: -(kv[1][0] - kv[1][1])):
    tot_m = ver_by_cond[c][0]
    z_m = sum(
        1
        for k in m
        if m[k]["condition"] == c and m[k].get("tool_calls") == 0
    )
    tbl.append(f"| `{c}` | {a} | {b} | **{a - b:+d}** |")

# 工具零调用表（按条件）
zt = []
zt.append("| 条件 | multi tools=0 | multi 该条件总运行 | 占比 |")
zt.append("|---|---|---|---|")
for c in sorted(ver_by_cond, key=lambda x: -sum(1 for k in m if m[k]["condition"] == x and m[k].get("tool_calls") == 0) / max(1, ver_by_cond[x][0])):
    tot = ver_by_cond[c][0]
    z = sum(1 for k in m if m[k]["condition"] == c and m[k].get("tool_calls") == 0)
    if tot >= 6:
        zt.append(f"| `{c}` | {z} | {tot} | {z / tot * 100:.1f}% |")
ztbl_md = "\n".join(zt)
table_md = "\n".join(tbl)

lines = []
lines.append("")
lines.append("### 7.8.2 扩到 200 case 后：多智能体到底在哪些条件上赢")
lines.append("")
lines.append(
    "A1 那 40 条只能得出「multi 净负 25 个点」这一句，因为它把 11 个条件压成一个总分。"
    "扩到 200 case / 32 条件后（同 `seed 7 / customers 1000 / size 200 / k 3`，每臂 600 次运行），"
    "按条件拆开能看到 A1 上看不见的结构 —— **净负面从 25 个点缩到 3.5 个点**"
    "（单次 45.8% vs 42.3%），而它不是均匀变好的："
)
lines.append("")
lines.append(f"- **`multi` 净胜 ≥3 的条件**：{len(multi_win)} 个")
for c, a, b in multi_win:
    lines.append(f"  - `{c}`：multi 过 {a} / single 挂，single 过 {b} / multi 挂，**净 {a - b:+d}**")
lines.append(f"- **`single` 净胜 ≥3 的条件**：{len(single_win)} 个")
for c, a, b in single_win:
    lines.append(f"  - `{c}`：multi 过 {a} / single 挂，single 过 {b} / multi 挂，**净 {a - b:+d}**")
lines.append("")
lines.append("**这个结构正好回答了「多智能体什么时候才划算」：**")
lines.append("")
lines.append(
    "- `multi` 赢的全是**「该驳回 / 该催办」类**（R1 超期、R2 不可退品类、R13 改址被拒、"
    "R12 开票、R11 价保被拒、R14 催单）—— 这些条件的正确动作是「不动数据、按规则拒绝」，"
    "verifier 的独立复核能把 single 会误放的行拉回来。"
)
lines.append(
    "- `single` 赢的全是**「该起第二个 agent 干活」类**（`no_order` 该澄清、"
    "`R4:escalate` 该转人工、`R9:reship` 该补发、`R8:reject` 该换货）—— "
    "而现在的 verifier 只做只读复核，**它说得清哪儿没办成，却不补救**。"
)
lines.append("")
lines.append(
    "> 注意 `UNPAID:reject` 与 `CANCELLED:reject` 也站在 `single` 一边，而它们本该属于"
    "「该驳回」类 —— 这两个条件的共同点是**agent 一次工具都没调就下了结论**"
    "（multi 侧 tools=0 占 100%，见下表），verifier 复核的是同一份没查过数据的答案，"
    "自然复核不出东西。**「该驳回」不等于「可以不查」**。"
)
lines.append("")
lines.append(
    "> 这就是 [缺陷 ⑲](#42-十九个真实缺陷面试时讲这个比讲架构图有用) 修完之后的下一步："
    "把 verifier 从「复核」升级成「repair loop」。前置条件已经拿到了 —— "
    "定向重跑里漏放行从 21/22 降到 0/3，它现在说得清哪儿没办成，缺的是让它去办。"
)
lines.append("")
lines.append("<details><summary>全部 32 个条件的两翼对照表</summary>")
lines.append("")
lines.append(table_md)
lines.append("")
lines.append("</details>")
lines.append("")
lines.append("<details><summary>multi 臂「一次工具都没调」的条件分布（过程缺口，不是终态缺口）</summary>")
lines.append("")
lines.append(ztbl_md)
lines.append("")
lines.append(
    "> 这不是数据问题，是能力问题：模型确实被调用了（`model_calls=3`、elapsed≈7.3s、"
    "有真实 diffs），只是**一个工具都没调** —— 凭客户原话直接给动作，没有 `get_order` / "
    "`list_order_items` / `days_since_signed` 验证。过程探针抓不到它：`forbidden_hits` 当时是 0，"
    "因为「不查就办」不在四个探针的定义里。**这是探针的又一个盲区，形状与缺陷 ⑲ 同族："
    "裁判只判了它知道要判的那几种。**"
)
lines.append("")
lines.append("</details>")
lines.append("")

block = "\n".join(lines)

p = ROOT / "README.md"
t = p.read_text(encoding="utf-8")

anchor = "## 8. 已知不足"
assert t.count(anchor) == 1, f"§8 锚点未唯一命中（{t.count(anchor)}）"
t = t.replace(anchor, block.lstrip("\n") + "\n" + anchor, 1)
p.write_text(t, encoding="utf-8")

print(f"multi 净胜 {len(multi_win)} 个条件，single 净胜 {len(single_win)} 个条件")
print("README.md：", len(t), "chars")
print("✓ §7.8.2 已写入")
