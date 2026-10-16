"""探针：Windows NLS 自带的繁简映射能否当「独立尺子」。

存在理由
--------
`_crosscheck.py` 第一版的覆盖率判据是**循环论证**：它用 `_T2S` 的键定义「什么是繁体」，
又用同一张表定义「转换是否成功」，于是「归一化后 0 残留」是同义反复。
要打破这个循环，需要一把**不由我书写**的尺子。Windows 的 NLS（LCMapStringW）就是：
它的映射表来自微软，它的正确性不由本项目的任何假设决定。

这份探针不产出结论，只回答三个能不能：
① Python 能不能调用它；
② 它自己的往返（繁→简→繁）是否一致 —— 不一致的字说明该映射**多对一**，
   单向转换不可逆，那么「用残留率衡量覆盖率」这个思路本身就有上限；
③ 它会不会把简体常见字乱转（`面条` → 繁体写成 `麵條` 是对的多音字处理，
   但如果它把简体输入也改掉，就说明不能无条件当判据用）。
"""

import ctypes
import sys

sys.stdout.reconfigure(encoding="utf-8")

LCMAP_SIMPLIFIED_CHINESE = 0x02000000
LCMAP_TRADITIONAL_CHINESE = 0x04000000
LCID_ZH_CN = 0x0804

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def nls_convert(text: str, flag: int, lcid: int = LCID_ZH_CN) -> str:
    """调 LCMapStringW 做字形映射。返回转换结果，失败抛 OSError。"""
    n = _kernel32.LCMapStringW(lcid, flag, text, -1, None, 0)
    if n == 0:
        raise OSError(f"LCMapStringW 长度探测失败: {ctypes.get_last_error()}")
    buf = ctypes.create_unicode_buffer(n)
    if _kernel32.LCMapStringW(lcid, flag, text, -1, buf, n) == 0:
        raise OSError(f"LCMapStringW 转换失败: {ctypes.get_last_error()}")
    return buf.value


def to_simplified(text: str) -> str:
    return nls_convert(text, LCMAP_SIMPLIFIED_CHINESE)


def to_traditional(text: str) -> str:
    return nls_convert(text, LCMAP_TRADITIONAL_CHINESE)


def main() -> None:
    print("=" * 78)
    print("① 基本可用性：数据集里的真实句子")
    print("=" * 78)
    samples = [
        "我的包裹已經到超商但還沒收到通知",
        "我要退貨",
        "收到的商品有瑕疵，能幫我處理一下嗎？",
        "品質破損瑕疵",
        "客訴投訴",
        "帳號/系統問題",
        "我同時買了好幾樣衣服配件，結果分開寄來，時間差超多",
    ]
    for t in samples:
        s = to_simplified(t)
        print(f"  繁 {t}")
        print(f"  简 {s}")

    print()
    print("=" * 78)
    print("② 往返一致性：繁→简→繁 能不能回到原文")
    print("=" * 78)
    rows = [
        "我的包裹已經到超商但還沒收到通知",
        "我要退貨",
        "品質破損瑕疵",
        "帳號系統問題",
        "我同時買了好幾樣衣服配件",
    ]
    for t in rows:
        s = to_simplified(t)
        back = to_traditional(s)
        mark = "往返一致" if back == t else "往返不一致"
        print(f"  [{mark}] {t}")
        if back != t:
            print(f"            → {s} → {back}")

    print()
    print("=" * 78)
    print("③ 反向风险：简体常见字会不会被 NLS 当成繁体改掉")
    print("=" * 78)
    # 全用简体；如果转换后 != 原文，说明 NLS 对它们做了额外处理
    simplified = [
        "面条", "后面", "头发", "干活", "千万", "发展", "日历", "里程",
        "我收到了", "还没有收到", "退货", "换货", "品质", "破损", "瑕疵",
        "订单显示已签收", "商品质量太差", "我要投诉", "帮我催一下快递",
    ]
    for t in simplified:
        s = to_simplified(t)
        mark = "原样" if s == t else f"被改成 {s}"
        print(f"  {t:<16} → {s:<16} [{mark}]")

    print()
    print("=" * 78)
    print("④ 判据词典里的每一个词，转换后是否还认得出来")
    print("=" * 78)
    # 这些是生产判据的字面量。它们在繁体输入下的行为决定要不要修生产代码。
    words = [
        "退款", "退货", "退钱", "退换", "换货", "换新", "能退", "要退",
        "瑕疵", "破损", "破洞", "开胶", "开线", "线头", "次品", "假货",
        "变质", "损坏", "掉色", "褪色", "质量",
        "拒收", "不签收", "没签收", "未签收", "还没收到", "收不到",
        "收到了", "拆开", "用了", "到手", "碎了", "寄回来", "退回去",
    ]
    changed = []
    for w in words:
        t = to_traditional(w)
        if t != w:
            changed.append((w, t))
    print(f"  {len(changed)}/{len(words)} 个词在繁体字形下字面不同：")
    for w, t in changed:
        print(f"    {w} → {t}")
    same = [w for w in words if to_traditional(w) == w]
    print(f"  另外 {len(same)} 个词繁简同形（判据天然认得）：{'、'.join(same)}")


if __name__ == "__main__":
    main()
