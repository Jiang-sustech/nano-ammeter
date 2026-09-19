# -*- coding: utf-8 -*-
"""把结果行的 RAW 段代回式(6), 拿到比屏幕上 1 pA 分辨率更细的值

    python tools/raw_recalc.py

**为什么要这个**: 结果行的 `I=` / `X=` 只保留到 0.001 nA = **1 pA**。
50 s 窗下装置的重复性是 ~0.016 pA, 也就是屏幕分辨率比噪声粗 60 倍 ——
光看屏幕上的数会把 pA 量级的结构整个抹掉。
RAW 段里的 m/n/EXT1/EXT2 是**未取整**的原始量, 代回式(6) 就能拿到细值。

常数用固件里烧的那组 (162 点标定), 与分析脚本 report_table.i_of() 一致。
"""
import sys

T = 160e-6
Q = 15596e-18
IP = 49712e-12
IN_ = -49739e-12


def i_of(c1, c2, m, n):
    nt = float(m + n)
    return Q * (c2 - c1) / (nt * T) - (m * IP + n * IN_) / nt


# 用户 2026-09-17 21:01~21:17 连测的 11 次 (m, n, EXT1, EXT2)
ROWS = [
    (156216, 156284, 31269, 51185),
    (156354, 156146, 30903, 54801),
    (155956, 156544, 31192, 33088),
    (155653, 156847, 31226, 35391),
    (155320, 157180, 30887, 44310),
    (154977, 157523, 31243, 57598),
    (156329, 156171, 30988, 38684),
    (156317, 156183, 30741, 35575),
    (156303, 156197, 30613, 31742),
    (156294, 156206, 30270, 47550),
    (156277, 156223, 31171, 17921),
]


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    print("=" * 84)
    print("RAW 段代回式(6) —— 精确值")
    print("=" * 84)
    print("%3s %12s %12s %9s %9s %8s %8s"
          % ("#", "装置(pA)", "屏幕值(pA)", "m", "n", "EXT1", "EXT2"))
    print("-" * 84)
    vals = []
    for i, (m, n, c1, c2) in enumerate(ROWS, 1):
        i_pa = i_of(c1, c2, m, n) * 1e12
        shown = round(i_pa)          # 屏幕 = 四舍五入到 1 pA
        vals.append(i_pa)
        print("%3d %12.4f %12.1f %9d %9d %8d %8d"
              % (i, i_pa, shown, m, n, c1, c2))
    print("-" * 84)

    # 屏幕值 vs 精确值 —— 证明分辨率确实丢了信息
    print("")
    print("## 屏幕值的量化误差")
    err = [vals[i] - round(vals[i]) for i in range(len(vals))]
    print("   最大 |精确 − 屏幕| = %.3f pA   (理论 ±0.5 pA)"
          % max(abs(e) for e in err))

    # 用户要的: 10 pA 以内
    print("")
    print("## |I| <= 10 pA 的点")
    sel = [(i + 1, v) for i, v in enumerate(vals) if abs(v) <= 10.0]
    if not sel:
        print("   (没有)")
        return
    for i, v in sel:
        print("   第 %2d 次: %+8.4f pA" % (i, v))
    m = sum(v for _, v in sel) / len(sel)
    sd = (sum((v - m) ** 2 for _, v in sel) / (len(sel) - 1)) ** 0.5
    print("   ----")
    print("   均值 %+.4f pA   单次 sd %.4f pA   均值标准误 %.4f pA   n=%d"
          % (m, sd, sd / len(sel) ** 0.5, len(sel)))
    print("   (屏幕分辨率 1 pA 远粗于这里的 sd —— 所以必须用 RAW 重算)")


if __name__ == '__main__':
    main()
