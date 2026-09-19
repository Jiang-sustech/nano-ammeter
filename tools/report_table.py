# -*- coding: utf-8 -*-
"""
按报告表格格式输出某次扫描 (默认 data/lowI_pos, 正向低电流)

    python tools/report_table.py
    python tools/report_table.py --dir data/lowI_pos
    python tools/report_table.py --dir data/dense --min-na 5      # 高电流那批

列:  序号 设定Iₛ 回读I_std 读数1/2/3 平均Īₘ s Δ δ/% u_c

u_c 是什么 (必须写清楚, 否则这一列没有意义)
--------------------------------------------
    u_c(Δ) = sqrt( u_A² + u_ref² + u_syst² )      单位统一为 nA

    u_A    = s/√n                 —— **重复性**: 该点 n 次读数均值的标准误 (A 类)
    u_ref  = 2636B 回读精度 ÷ 2   —— **基准的不确定度** (B 类)
             1 nA 档  ±(0.15% rdg + 240 fA)
             10 nA 档 ±(0.15% rdg + 3 pA)
             100 nA档 ±(0.06% rdg + 40 pA)
             ÷2 是把手册给的"精度极限"(≈95%)当作正态换算成标准不确定度
    u_syst = **装置逐点结构** (B 类)
             该量无法从单个点的 n 次重复里得到 —— n 次重复只反映**重复性**,
             反映不了"换到别的电流点会偏多少"。这里用 **Δ 在整段各点上的
             散布**当它的估计 (段0 实测 0.072 pA)。

**不包含**: 绝对准确度。回读值自己的**系统**偏差被三常数拟合吸收, 残差里
看不见 —— 要分离必须换第三个独立参考 (标准 R + 标准 V)。

⚠️ 因此 u_c 是"Δ 这个差值的合成标准不确定度", **不是装置的准确度**。
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

T = 160e-6
Q = 15596e-18
IP = 49712e-12
IN_ = -49739e-12

# (相对项, 固定项) —— 来自 data/README.md「误差口径」① 的**回读精度**那一列
READBACK = [(1e-9, 0.0015, 240e-15), (10e-9, 0.0015, 3e-12), (1e9, 0.0006, 40e-12)]


def readback_u(i_amp):
    """2636B 回读的标准不确定度 (A)。÷2 把精度极限换算成标准不确定度。"""
    for lim, rel, fix in READBACK:
        if abs(i_amp) <= lim:
            return (rel * abs(i_amp) + fix) / 2.0
    return float('nan')


def i_of(c1, c2, m, n):
    nt = float(m + n)
    return Q * (c2 - c1) / (nt * T) - (m * IP + n * IN_) / nt


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/lowI_pos')
    ap.add_argument('--min-na', type=float, default=0.0, help='只列 |设定| >= 这个 (nA)')
    ap.add_argument('--max-na', type=float, default=1e9, help='只列 |设定| <= 这个 (nA)')
    ap.add_argument('--unit', default='pa', choices=['pa', 'na'],
                    help='表格单位; 默认 pA (低电流段跨 1~4000 pA, 用 nA 会满屏 0.000xxx)')
    ap.add_argument('--syst-pa', type=float, default=0.072,
                    help='u_syst: 该段的**逐点结构** (pA), 默认段0 实测值')
    a = ap.parse_args()

    g = {}
    with open(os.path.join(a.dir, 'raw.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            k = float(r['set_nA'])
            if not (a.min_na <= abs(k) <= a.max_na):
                continue
            g.setdefault(k, []).append(r)
    if not g:
        sys.exit("没有符合条件的点 (--min-na/--max-na 挡住了?)")

    U = 'pA' if a.unit == 'pa' else 'nA'
    K = 1e3 if a.unit == 'pa' else 1.0     # 内部按 nA 算, 打印前乘这个
    u_syst = a.syst_pa * 1e-3          # pA -> nA (内部一律 nA)
    print("=" * 128)
    print("标定数据表   (%s, %d 个电流点, 每点 3 次)   **单位: %s**"
          % (a.dir, len(g), U))
    print("u_c = sqrt(u_A² + u_ref² + u_syst²),  u_syst = %.4g pA (该段逐点结构)"
          % a.syst_pa)
    print("=" * 128)
    print(("%3s %11s %11s %12s %12s %12s %12s %10s %10s %8s %11s %7s")
          % ("序号", "设定Iₛ", "回读I_std", "Iₘ₁", "Iₘ₂", "Iₘ₃",
             "平均Īₘ", "s", "Δ", "δ/%", "u_c", "Δ/u_c"))
    print("-" * 128)
    # 按**幅值**升序 —— 正负两批才逐行对得上。
    # 原来写的是 sorted(g), 对正向表恰好就是幅值升序, 但负向表会变成
    # -4000 -> -1 (幅值降序), 两张表并排看行号对不上。
    for i, k in enumerate(sorted(g, key=abs), 1):
        v = g[k]
        I = [i_of(int(r['ext1']), int(r['ext2']), int(r['m']), int(r['n'])) * 1e9
             for r in v]
        while len(I) < 3:
            I.append(float('nan'))
        Im = sum(I) / len(I)
        s = math.sqrt(sum((x - Im) ** 2 for x in I) / (len(I) - 1))
        std = sum(float(r['true_nA']) for r in v) / len(v)
        d = Im - std
        uA = s / math.sqrt(len(I))
        uc = math.sqrt(uA ** 2 + readback_u(std * 1e-9) ** 2 * 1e18 + u_syst ** 2)
        print(("%3d %11.6g %11.6g %12.6g %12.6g %12.6g %12.6g %10.6g %+10.6g "
               "%+8.2f %11.6g %7.1f")
              % (i, k * K, std * K, I[0] * K, I[1] * K, I[2] * K, Im * K,
                 s * K, d * K, d / std * 100, uc * K,
                 d / uc if uc else float('nan')))
    print("-" * 128)
    print("Δ/u_c > 2  = 该点的偏置在合成不确定度下**显著** (但注意 u_c 不含绝对准确度)")


if __name__ == '__main__':
    main()
