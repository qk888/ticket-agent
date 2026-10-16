# 测试

> 264 个用例。改完代码先跑这个，再跑 [docs/DEFECTS.md](DEFECTS.md) 里对应那条缺陷的取证脚本。

## 怎么跑

```bash
python -m pytest tests/ -q
# 263 passed, 1 skipped
```

Windows 控制台如果报 `UnicodeEncodeError`，先设 `PYTHONIOENCODING=utf-8`
（`cli.py` 已经为自己做了这件事，裸 pytest 不会）。

## 一条硬规矩：测试数只能数，不能抄

`pytest --collect-only -q | tail -1` 是唯一可信来源。
README 里手抄的数字过期过三次（231 → 234 → 263），每次都因为加了一个缺陷忘了改一处。
**测试数跟缺陷数一起涨，所以它必须跟着一起数。**

## 三类测试各自防什么

| 类型 | 防什么 | 例子 |
|---|---|---|
| **规则一致性** | golden 和工具层对同一份输入读出不同结论 | `test_rules_consistency.py` 用同一批订单同时问两边 |
| **探针单测** | 探针自己漏报 / 误报 | `test_forbidden_probes.py` 25 条，含阳性与阴性对照 |
| **缺陷回归** | 已修的缺陷复活 | `test_defect33.py` / `test_defect35.py`，各自对应 DEFECTS 里一条 |

## 另一类更值钱的：接线测试

大部分用例测的不是「智能」，是**接线**：

- 声明里有的能力，代码里真的实现了吗（`test_declaration_integrity.py`）
- 政策文本里写的规则编号，代码里真的分支了吗（`test_policy_consistency.py`）
- 探针说的那个违规，在构造的轨迹里真的会被抓吗（`test_forbidden_probes.py`）

**「能力只存在于声明里」是这个项目反复撞到的形状**（见 DEFECTS ⑧）——
声明和实装之间没有任何东西会自动变红，所以只能靠测试钉住。

## 关于那个 skipped

`test_defect33.py` 有一条被 skip：实测千级库里**被多张工单共用的订单数 = 0**
（orders 2353 / tickets 597 / tickets with order_id 438），当前造数策略不产这个形状。

**触发条件不成立 ≠ 缺陷不存在。** 正向复现脚本 `diagnostics/_d33.py` 同样打印
「触发条件不成立」—— 所以这条 skip 是**诚实的 skip，不是掩盖**。

## 已知会变红的两种情况

1. **改了模型但没重建库**：`init_db()` 走 `create_all`，只补缺表，
   **绝不给已存在的表加列**。模型加过列的旧库必须 `init_db(drop=True)`，
   否则报 `table tickets has no column named pending_question`。
2. **改了造数但没换 seed**：`tests/test_service_api.py` 有哨兵断言
   「这次 seed 没造出 R6:escalate 的 case，测试失去意义」。
   它是故意设的红线 —— 看到它先去确认造数改动，不是去放宽断言。
