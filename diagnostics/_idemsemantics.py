"""缺陷 ㉑ 取证：幂等重试被政策复核误判成违规。

这个脚本演示的是一个**顺序问题**，不是逻辑问题：
写工具里"政策复核"排在"幂等检查"之前，而写入本身会改变政策读取的那个状态。
于是同一次请求、同一个幂等键，第二次却得到"你不该做这个"。

为什么这是真缺陷（而不是措辞问题）：
  ① 语义错：`duplicate` = "你已经做过了"，`policy_forbidden` = "这个动作违规"。
     对 agent 来说后者意味着"我判断错了，该换动作"，它会被诱导去改变行为。
  ② 重试不安全：网络抖动导致的自动重试会拿到一个"拒绝"，而实际业务已经完成。
     客户端若按 error 处理，会记为失败并可能上报事故。
  ③ 评测失真：探针把这次算成"越权/违规尝试"（forbidden_hits），
     而它其实是一次无害的重试 —— 报表里的违规数会被这类事件灌水。

用法：
    python _idemsemantics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ticket_agent.rules import decide, refund_is_forbidden  # noqa: E402


def main() -> int:
    print("=" * 72)
    print("缺陷 ㉑ 取证：写入会改变政策所读的状态")
    print("=" * 72)

    base = dict(
        order_status="signed",
        amount=100.0,
        categories={"服饰"},
        signed_at=None,
        refund_count=0,
    )

    d0 = decide(**base)
    print("\n【第一次调用时的世界】refund_count = 0")
    print(f"  decide() → action={d0.action!r}  rule_hits={d0.rule_hits}")
    print(f"  政策是否禁止？ {refund_is_forbidden(d0)}")
    print("  → 允许，于是工具写库，并把 order.refund_count 从 0 加到 1")

    after = dict(base)
    after["refund_count"] = 1
    d1 = decide(**after)
    print("\n【同一个请求重试时的世界】refund_count = 1（被自己上一次写入改掉了）")
    print(f"  decide() → action={d1.action!r}  rule_hits={d1.rule_hits}")
    print(f"  政策是否禁止？ {refund_is_forbidden(d1)}")
    print(f"  reasons={d1.reasons}")

    print("\n" + "-" * 72)
    print("结论：同一个幂等键、同一个请求，两次得到相反的政策结论。")
    print("      因为**政策读的那个字段，正是这次写入会改的那个字段**。")
    print("      R5 的语义是「同一订单不得退两次」——它对**两次不同的退款**成立，")
    print("      对「同一个请求被提交两次」不成立。当前实现把两者混为一谈。")
    print("-" * 72)

    # 正确顺序应当先查幂等键
    print("\n正确顺序：先查幂等键 → 命中就返回 duplicate（业务已完成，与政策无关）")
    print("          未命中才做政策复核 → 再做账本不变量 → 再写库")
    print("          最后的 IntegrityError 仍保留，兜住并发窗口")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
