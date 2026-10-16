"""最终定性：24 条 verifier 放行零动作 = 两个独立缺陷，都要修。

至此证据链闭合（每一环都只用可复现的判据，不靠猜）：

  E1 `_vwhy10.py`：`seed_all(n_customers=1000, seed=11)` +
     `build_eval_cases(size=200, seed=7)` 连跑两次**逐位相同** → 重建确定性。
  E2 `_vwhy12.py`：跑批指纹 `{"customers": 1000, "k": 3, "kind": "llm",
     "seed": 7, "size": 200, "split": "all"}` 与我用的参数**一致**；
     `_vwhy11.py` 却只命中 6/23 条 → 说明"命中数"不是这条判据的度量：
     同一批 case 里，**scripted 基线下它们本来就该办成**（6 条 act 全部正确），
     只有 llm 那次跑挂了。所以"case 不在批里"是**我拿错了 k 与 arm 的组合**
     （24 条是 600 run 的命中，不是 200 case 的命中）。
  E3 `_vwhy5.py`：24/24 条 golden 都要求离开 open 或要求写 → 放行无正当理由。
  E4 `_vwhy6.py`：`_summarize_action` 只认 `create_refund / block_order /
     escalate_to_human / close_ticket` 四个名字；`submit_after_sales_action`
     **不在列** → 轨迹里真写了售后动作也会被判 `'none'`。
  E5 `_vwhy8.py`：scripted 基线的审计里有
     `submit_after_sales_action.urge ok=True args={...'action_id': 1}`，
     而 `observed='resolve'`（来自 close_ticket）→ E4 成立且与 agent 无关。
  E6 `_vwhy11.py`：同一批 case 上 scripted 全部办成（act=reject/resolve/
     escalate、status 正确、ver=True）→ **verifier 判据本身没有放过它们**。

所以 24 条的真实形状是**两个缺陷叠在一起**：

  缺陷 ㉟（报表汇总）：`_summarize_action` 不认 `submit_after_sales_action`
    的 kind。后果：agent 明明写了赔付/补发/开票，报表写 `observed_action=none`；
    面试时看到"零动作"会去查 agent，而真相是**汇总函数少认一个工具**。
    修法：从 trace 的 `args.kind` 取售后类型，映射成同义标签
    （exchange→exchange / reship→reship / compensate→compensate /
     issue_invoice→invoice / urge→urge / price_protect→price_protect /
     change_address→change_address），并**保持"只认成功的那次"**（缺陷 ⑫ 的修法）。
    顺带：`_expected_action` 也从 golden 反推，两边对齐后报表才可比。

  缺陷 ㊱（verifier 判据）：`pending_question` 由 agent 自己写，
    却是"工单仍 open"的放行依据之一。缺陷 ⑲ 修的是"不许读模型散文"，
    #2 把依据换成了结构化字段 —— 但那个字段仍由**被审对象**书写。
    llm 那 24 条正是：agent 问了一句（或问了个 golden 不需要的问题），
    然后什么都不做，verifier 看到"问了客户"就放行。
    修法：放行必须同时满足"这个提问是 golden 认可的"。
    可复现的判据 = **case 层的 `turn2_state`/`customer_reply`**（造数时确定性
    写好，agent 改不了）：只有当这条 case 本来就该澄清（`condition` 以
    `need_clarification` 结尾或 turn2_state 非空）时，`pending_question`
    才是有效依据。否则它是"agent 自造的免检券"。
    这个判据与 `basis` 同级：都是输入侧的确定性事实。

验证方式（都已具备）：
  · `_vwhy6.py` 的 `_summarize_action` 单测表 + 新增 submit 各 kind 用例；
  · 新探针/断言用 `_vwhy11.py` 的 6 条（scripted 下 act 正确）当阴性对照；
  · 阳性样本：llm 那 24 条（`_progress-merged-*.jsonl` 里的原始记录）。
"""
from __future__ import annotations

print(__doc__)
