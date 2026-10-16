"""交付总览：把 multi 净负面那段按 A1/A2 分开写，并补 A2 的净负面幅度与结构。

硬规则：assert 命中数；只改这一段，不碰后面的 ⑫ 段落（那是 A1 的逐条取证，仍然成立）。
"""
import pathlib

p = pathlib.Path(__file__).parent / "交付总览.md"
t = p.read_text(encoding="utf-8")

old = """> ★ **`multi` 是净负面的，这条我不打算藏**：同一批 case 上，
> 多智能体多花 1.74× 模型调用、2.95× 延迟，准确率掉 15 个点；
> 而它多花的钱全花在**重复的只读复核**上（写操作两臂 0.88 vs 0.68，multi 反而更低）。
> `multi` 22 条失败里 **21 条一个动作都没产出**（`observed_action == none`），
> 但**没有一条退错钱、越权为 0** —— 丢的是流程收尾，不是正确性。
> 逐条件对照与三条读法见 README [§7.8](README.md#78-两臂对照-multi-是净负面的)。"""

new = """> ★ **`multi` 是净负面的，这条我不打算藏** —— 但两批样本给出的负面幅度差很远：
>
> - **A1（40 case 旧集）**：多智能体多花 1.74× 模型调用、2.95× 延迟，
>   pass^3 从 90.0% 掉到 65.0%、单次准确率从 96.7% 掉到 85.0%，**净负 25 个点**。
>   它多花的钱全花在**重复的只读复核**上（写操作两臂 0.88 vs 0.68，multi 反而更低）。
>   `multi` 22 条失败里 **21 条一个动作都没产出**（`observed_action == none`），
>   但**没有一条退错钱、越权为 0** —— 丢的是流程收尾，不是正确性。
>
> - **A2（200 case 全诉求集）**：净负面**从 25 个点缩到 3.5 个点**（单次 45.8% vs 42.3%）。
>   按条件拆开后能看到 A1 上看不见的结构：`multi` 在「该驳回 / 该催办」类净胜
>   （`R1:reject` +13、`R2:reject` +9、`R13:reject` +5），
>   在「该起第二个 agent 干活」类净负（`no_order` −11、`R4:escalate` −9、
>   `R9:reship` −9、`R8:reject` −9）。
>   **小样本上只能看到「多智能体没用」，看不到「它在哪儿有用」。**
>
> 逐条件对照与读法见 README [§7.8](README.md#78-两臂对照-multi-是净负面的)。"""

assert t.count(old) == 1, f"multi 净负面段未唯一命中（{t.count(old)}）"
p.write_text(t.replace(old, new), encoding="utf-8")
print("交付总览.md：", len(t.replace(old, new)), "chars")
print("✓ multi 净负面段已按 A1/A2 分开")
