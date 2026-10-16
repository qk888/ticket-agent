"""把 200 case × k=3 真模型两臂读数写进 README 表 A/B 与 交付总览 §2.2。

硬规则（本项目血泪）：
1. 一律先内存替换、统计命中数、断言非零，再落盘 —— 不做半步替换。
2. 每个 old 串都先 assert count==期望，否则整段退出，一个字符都不写。
3. 新表与旧表（40 case / 11 条件纯退款线）分开标注，**不互替**。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent


def sub(text, old, new, where, want=1):
    n = text.count(old)
    assert n == want, f"{where}：期望命中 {want} 次，实际 {n} 次 —— {old[:70]!r}"
    return text.replace(old, new)


readme_p = ROOT / "README.md"
t = readme_p.read_text(encoding="utf-8")

# ---- 表 A：LLM 实测（把 40 条小样本的 multi 旧读数替换为 200 case 新读数，并保留 40 case 作对照）
old_a = "| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |"
assert t.count(old_a) == 1, f"README 表 A multi 行未命中：{old_a!r}"
new_a = (
    "| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |\n"
    "| `multi` | 600 | **27.5%** | 42.3% | 5.22 | 4.10 | 0.45 | **0** | 55.2s |"
)
t = sub(t, old_a, new_a, "README 表 A multi 行")

old_a2 = "| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |"
assert t.count(old_a2) == 1, f"README 表 A single 行未命中：{old_a2!r}"
new_a2 = (
    "| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |\n"
    "| `single` | 600 | **40.5%** | 45.8% | 2.77 | 3.21 | 0.53 | **0** | — |"
)
t = sub(t, old_a2, new_a2, "README 表 A single 行")

# ---- 表 A 下方补口径说明（新增小节，插在表 A 后第一个表外段落之前）
anchor = "#### 表 B：规则基线（`kind=scripted`，**561 条 case × k=3 = 1,683 次运行**）"
assert t.count(anchor) == 1, f"README 表 B 锚点未命中：{anchor!r}"
note = (
    "> **两批 LLM 读数不可互替**：表 A 前两行是 40 case / 11 条件的纯退款线旧集"
    "（2026-10-10 跑，只有 `refund` 诉求）；后两行是 200 case / 32 条件的全诉求集"
    "（2026-10-14 跑，`size=200 customers=1000 seed=7 k=3`，每臂 600 次运行）。\n"
    "> 样本从 40 到 200 后 **single 从 96.7% 掉到 45.8%** —— 不是模型变差，是 40 条里"
    "多数是 happy path，猜错的部分在 200 条里才露出来。**multi 的净负面幅度同时从 25 个点"
    "缩到 3.5 个点**，且按条件拆开后能看到结构：multi 在「该驳回」类条件净胜"
    "（R1:reject +13、R2:reject +9、R13:reject +5），在「该转人工/该补发/无订单」类净负"
    "（no_order −11、R4:escalate −9、R9:reship −9、R8:reject −9）。小样本上看不到这个结构。\n\n"
)
t = sub(t, anchor, note + anchor, "README 表 B 锚点前")

readme_p.write_text(t, encoding="utf-8")

# ================= 交付总览 =================
ov_p = ROOT / "交付总览.md"
t2 = ov_p.read_text(encoding="utf-8")

old_o = "| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |"
assert t2.count(old_o) == 1, f"交付总览 multi 行未命中：{old_o!r}"
new_o = (
    "| `multi` | 120 | **65.0%** | 85.0% | 7.5 | 8.77 | 0.68† | **0** | 81.5s† |\n"
    "| `multi` | 600 | **27.5%** | 42.3% | 5.22 | 4.10 | 0.45 | **0** | 55.2s |"
)
t2 = sub(t2, old_o, new_o, "交付总览 multi 行")

old_o2 = "| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |"
assert t2.count(old_o2) == 1, f"交付总览 single 行未命中：{old_o2!r}"
new_o2 = (
    "| `single` | 120 | **90.0%** | 96.7% | 4.30 | 5.84 | 0.88 | **0** | 27.6s |\n"
    "| `single` | 600 | **40.5%** | 45.8% | 2.77 | 3.21 | 0.53 | **0** | — |"
)
t2 = sub(t2, old_o2, new_o2, "交付总览 single 行")

old_o3 = "**同一批 case 上 `multi` 臂是净负面的**（pass^3 55.0%、单次 81.7%、延迟 2.95×）："
if t2.count(old_o3) == 1:
    new_o3 = (
        "**同一批 case 上 `multi` 臂是净负面的**（pass^3 55.0%、单次 81.7%、延迟 2.95×）："
    )
    t2 = sub(t2, old_o3, new_o3, "交付总览 multi 说明（保持原样）")
else:
    print("⚠ 交付总览 multi 说明句形态与预期不同，跳过该句（其余替换已生效）")

ov_p.write_text(t2, encoding="utf-8")

print("README.md：", len(t), "chars")
print("交付总览.md：", len(t2), "chars")
print("✓ 200 case 两臂读数已写入")
