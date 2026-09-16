# -*- coding: utf-8 -*-
"""8 / 9 nA 两点的 ADC 端点码差 (Δc) 逐个列出。"""
import csv
import sys

sys.stdout.reconfigure(encoding='utf-8')

T_INT = 160e-6
Q = 1.559440e-14          # C/码, 19 点标定
IP = 49.7153e-9           # A
IN_ = -49.7423e-9

rows = []
with open('data/sweep_broad.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        try:
            sp = float(r['set_pA'])
        except (KeyError, ValueError):
            continue
        if abs(sp) not in (8000.0, 9000.0):
            continue
        rows.append(r)

for sp in (8000.0, 9000.0, -8000.0, -9000.0):
    sel = [r for r in rows if float(r['set_pA']) == sp]
    if not sel:
        continue
    print("=" * 96)
    print("设定 %+g nA   (回读 %.6f nA)" % (sp / 1000.0, float(sel[0]['true_nA'])))
    print("=" * 96)
    print("%4s %6s %6s %7s %7s %8s %7s %7s %8s %10s %10s %10s"
          % ("次", "m", "n", "INT1", "INT2", "Δc_int", "EXT1", "EXT2", "Δc_ext",
             "I_ext(nA)", "回读(nA)", "残差(pA)"))
    for r in sel:
        m, n = int(r['m']), int(r['n'])
        i1, i2 = int(r['int1']), int(r['int2'])
        e1, e2 = int(r['ext1']), int(r['ext2'])
        nt = m + n
        dc = e2 - e1
        chg = Q * dc / (nt * T_INT)                       # 电荷项 (A)
        ref = (m * IP + n * IN_) / nt                     # 参考项 (A)
        I = chg - ref
        t = float(r['true_nA']) * 1e-9
        print("%4s %6d %6d %7d %7d %8d %7d %7d %8d %10.4f %10.4f %+10.3f"
              % (r['rep'], m, n, i1, i2, i2 - i1, e1, e2, dc,
                 I * 1e9, t * 1e9, (I - t) * 1e12))
    print("")

# 灵敏度
print("=" * 96)
print("灵敏度 (1 s 窗口, m+n = 6250, T = 160us => nt*T = 1.0 s)")
print("=" * 96)
print("  1 **拍** (m 变 1)     = (I+ - I-)/(m+n) = %.5f nA = %.3f pA"
      % ((IP - IN_) / 6250 * 1e9, (IP - IN_) / 6250 * 1e12))
print("  1 **码** (Δc 变 1)    = q/(nt*T)        = %.6e A  = %.4f pA"
      % (Q / 1.0, Q / 1.0 * 1e12))
print("  -> 码比比拍细 %.0f 倍" % (((IP - IN_) / 6250) / (Q / 1.0)))
