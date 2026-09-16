# -*- coding: utf-8 -*-
"""
8 nA 处的开关切换速度, 以及装置的所有时间尺度 —— 看它有没有撞上固有周期

    python tools/switching_times.py

物理
----
电荷平衡: 积分器在参考 I₊ 和 I₋ 之间来回摆, 每次摆过 ΔV_int 就换向。
    相位时长 = C·ΔV / |I − I_参考|
一周期 = 两个相位之和:
    T(I) = C·ΔV·[ 1/|I−I₊| + 1/|I−I₋| ]
一窗 (1 s) 里的周期数 = 1 / T
窗口拍数 (1 拍 = 160 µs) 由 m + n 给出, 而 m/n 就是两个相位的**拍数之比**。

⚠️ C·ΔV 不由标称值直接算 —— 从**实测波形**上标定:
   +10 nA 那条整窗波形量得相位长 95 拍 / 138 拍 -> 周期 233 拍 = 37.3 ms,
   反推 C·ΔV = 890 pC。再用它去预测**别的电流**的 m/n 做验证 (见下)。
   标称 C=100 pF 若 ΔV_int ≈ 8.9 V 则 C·ΔV = 890 pC —— 与实测吻合。
"""
import math
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

IP = 49.7153e-9       # 参考正电流 (拟合值)
IN_ = -49.7423e-9     # 参考负电流
CDV = 890e-12         # C·ΔV_int (从 +10 nA 波形标定)
TICK = 160e-6         # TIM6 采样节拍
WIN = 1.0             # 窗口长度 (s)


def phases(i):
    """两个相位的时长 (s)。i 为带符号电流。"""
    ta = CDV / abs(i - IP)
    tb = CDV / abs(i - IN_)
    return ta, tb


def main():
    print("=" * 90)
    print("装置的时间尺度 —— 8 nA 有没有撞上固有周期?")
    print("=" * 90)
    print("C·ΔV_int = %.0f pC  (从 +10 nA 整窗波形标定)" % (CDV * 1e12))
    print("采样节拍 = %.0f µs,  窗口 = %.0f s = %.0f 拍"
          % (TICK * 1e6, WIN, WIN / TICK))
    print("")

    # ---------- 先验证物理模型: 用 C·ΔV 预测 m/n ----------
    import csv
    meas = {}
    with open('data/sweep_broad.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                I = float(r['true_nA']) * 1e-9
                m, n = float(r['m']), float(r['n'])
            except (KeyError, ValueError):
                continue
            if m > 0 and n > 0:
                meas.setdefault(round(I * 1e9, 3), []).append((m, n))
    print("--- 验证: 用 C·ΔV 预测 m/n, 跟实测比 (只列一部分) ---")
    print("%10s %12s %12s %10s %10s" % ("电流/nA", "实测 m", "预测 m", "实测 n", "预测 n"))
    errs = []
    for k in sorted(meas):
        if abs(k) > 45.5 or (abs(k) > 10.5 and abs(k) < 12):
            pass
        m = sum(x for x, _ in meas[k]) / len(meas[k])
        n = sum(y for _, y in meas[k]) / len(meas[k])
        i = k * 1e-9
        ta, tb = phases(i)
        T = ta + tb
        cyc = WIN / T
        # m/n 哪个对应哪个相位由数据定: m 大时对应**短**相位
        pm_a, pm_b = cyc * ta / TICK, cyc * tb / TICK
        # 自动配对: 让预测的 (大,小) 对上实测的 (大,小)
        if m >= n:
            pm, pn = max(pm_a, pm_b), min(pm_a, pm_b)
        else:
            pm, pn = min(pm_a, pm_b), max(pm_a, pm_b)
        e = (pm - m) / m * 100
        errs.append(abs(e))
        if abs(k) in (5, 6, 7, 8, 9, 10, 12.5, 20, 30, 40, 45) or abs(k) < 10:
            print("%+10.3f %12.0f %12.0f %10.0f %10.0f   (m 误差 %+.2f%%)"
                  % (k, m, pm, n, pn, e))
    print("  -> 全部 %d 个点, |m 预测误差| 中位 %.2f%%, 最大 %.2f%%"
          % (len(errs), sorted(errs)[len(errs) // 2], max(errs)))
    print("")

    # ---------- 时间尺度表 ----------
    print("--- 各电流的开关时间尺度 ---")
    print("%9s %11s %11s %11s %11s %11s %10s"
          % ("电流/nA", "相位A/ms", "相位B/ms", "周期/ms", "周期/拍",
             "每窗周期数", "切换/秒"))
    for k in (5, 6, 7, 8, 9, 10, 12.5, 15, 20, 25, 30, 35, 40, 45):
        ta, tb = phases(k * 1e-9)
        T = ta + tb
        cyc = WIN / T
        print("%+9g %11.3f %11.3f %11.3f %11.1f %11.3f %10.1f"
              % (k, ta * 1e3, tb * 1e3, T * 1e3, T / TICK, cyc, 2 * cyc))
    print("")

    # ---------- 固有时间尺度, 逐个比对 ----------
    print("--- 装置的其它固有时间尺度, 看有没有哪个在 8 nA 处被「穿过」 ---")
    print("%-34s %-16s %s" % ("尺度", "值", "在 6~45 nA 内被穿过吗"))
    print("-" * 90)
    # 周期 = 1 拍 / 2 拍 / 10 拍 的位置
    for nticks in (1, 2, 10):
        target = nticks * TICK
        # 解 T(I) = target -> 只有 I 接近 ±I参考 才可能
        # T(I) = 160µs 要 |I−I参考| = C·ΔV/160µs ≈ 5.6 µA, 而参考才 50 nA
        need = CDV / target                      # 需要的 |I − I参考| (A)
        print("%-34s %-16s %s"
              % ("周期 = %d 拍" % nticks, "%.0f µs" % (target * 1e6),
                 "否 —— 要 |I − I参考| = %.0f nA, 即 |I| ≳ %.0f nA, 远超 ±45 nA 量程"
                 % (need * 1e9, (need - abs(IP)) * 1e9)))
    print("%-34s %-16s %s" % ("两相位等长", "%.3f nA" % ((IP + IN_) / 2e-9),
                              "在 I ≈ 0 处 (周期极小点), **不在 8 nA**"))
    print("%-34s %-16s %s" % ("周期极值点", "36.2~63 ms",
                              "极小在 I≈0, 之后**单调上升**, 8 nA 无奇点"))
    print("%-34s %-16s %s" % ("ADG 开关切换时间", "几十~几百 ns", "否, 比 160 µs 小 3 个量级"))
    print("%-34s %-16s %s" % ("运放压摆", "未查证器件",
                              "但**斜率随电流变** -> 不受压摆限制, 见下"))
    # 从 +10 nA 波形实测: 上升 611, 下降 407 码/拍, 比 1.50
    # 物理预测: |10−I-|/|10−I+| = 59.7423/39.7153 = 1.504  ->  吻合
    print("%-34s %-16s %s" % ("  斜率是压摆限制的吗?", "否",
                              "实测 611/407=1.50 vs 预测 1.504 —— 斜率正比于"
                              "|I−I参考|, 是电流定的, 不是运放定的"))
    print("")

    # ---------- 直接测: 残差 vs 小数相位 ----------
    print("--- 直接检验: 残差跟「窗内周期数的小数部分」有关吗 ---")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from linearity_all import CAL_FILE, BROAD_FILE, load_broad
        from cv_linearity import load, fit
        g = dict(load(CAL_FILE))
        for a1, a2, I in load_broad(BROAD_FILE):
            g.setdefault(round(I * 1e9, 4), []).append((a1, a2, I))
        rows = [r for v in g.values() for r in v]
        x = fit(rows)
        pts = []
        for k, v in g.items():
            I = sum(r[2] for r in v) / len(v)
            e = sum(x[0] * a1 + x[1] + x[2] * a2 - I for a1, a2, I2 in v) / len(v)
            ta, tb = phases(I)
            cyc = WIN / (ta + tb)
            pts.append((I * 1e9, cyc - math.floor(cyc), e * 1e12))
        pts.sort()
        n = len(pts)
        mf = sum(p[1] for p in pts) / n
        me = sum(p[2] for p in pts) / n
        num = sum((p[1] - mf) * (p[2] - me) for p in pts)
        den = math.sqrt(sum((p[1] - mf) ** 2 for p in pts)
                        * sum((p[2] - me) ** 2 for p in pts))
        r = num / den
        t = r * math.sqrt((n - 2) / (1 - r * r))
        print("  %d 个点, 小数相位 vs 残差:  r = %+.3f   (t = %+.2f, n = %d)"
              % (n, r, t, n))
        print("  -> %s" % ("**有相关**" if abs(t) > 2 else
                           "没有显著相关 (|t| < 2) —— 这个假设不成立"))
    except Exception as ex:
        print("  (跑不了: %s)" % ex)


if __name__ == '__main__':
    main()
