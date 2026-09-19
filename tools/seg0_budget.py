# -*- coding: utf-8 -*-
"""段0 (+0.5 pA) 偏置的来源排查 —— 拆开式(6)的两项, 并做量纲排除

    python tools/seg0_budget.py --dir data/lowI_pos

式(6) 是两项之差:

    I = q*dC/(N*T)  -  (m*I+ + n*I-)/N
        [___ T1 ___]    [____ T2 ____]

     T1 "电荷项"  —— 窗口内的净电荷 q*dC, 除以窗口时间
     T2 "参考项"  —— 两个参考电流按各自驻留拍数的加权平均

**为什么要拆开**: 一个恒定偏置要么住在 T1 里, 要么住在 T2 里, 要么两项都有。
拆开后能看出它挂在哪个量上 —— 从而知道该去查哪一段代码, 而不是泛泛地
说"装置有个偏置"。

量纲排除 (不看数值就能做的推理)
------------------------------
窗口长度 N 从 50 s (312500 拍) 换成 10 s (62500 拍), 实测偏置**不变**。
凡是"随 N 变化"的机制, 都要求偏置跟着变 5 倍 —— 全被排除:

    每拍计数偏差 d      I_err = d*(I+ - I-)/N   -> N 增 5 倍则减 5 倍   ✗
    每窗固定电荷 Q0     I_err = Q0/(N*T)        -> N 增 5 倍则减 5 倍   ✗
    C_INT 定值偏差      I_err ∝ I, I->0 时归零                     ✗
    T_INT 定值偏差      I_err ∝ I, I->0 时归零                     ✗
    端点码阈值定值偏差  两个阈值同时平移, dC 不变, 占空比不变            ✗
    ----------------------------------------------------------------
    连续电流 I_leak     I_err = I_leak          -> 与 N 无关         ✓
    每次翻转注入 Q_sw   I_err = Q_sw * f_sw(I)  -> 段0内 f_sw 近似常数 ✓

**后两项在段0内都给出"与 N 无关的常数"** —— 单靠换窗口分不开它们。
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_table import i_of

T = 160e-6
Q = 15596e-18
IP = 49712e-12
IN_ = -49739e-12


def t1_of(c1, c2, n_tot):
    return Q * (c2 - c1) / (n_tot * T) * 1e12


def t2_of(m, n):
    return (m * IP + n * IN_) / (m + n) * 1e12


def ms(v):
    mu = sum(v) / len(v)
    sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
    return mu, sd


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/lowI_pos')
    a = ap.parse_args()

    g = {}
    with open(os.path.join(a.dir, 'raw.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            if abs(float(r['set_nA'])) >= 1.0:      # 段1 不进这个分析
                continue
            g.setdefault(float(r['set_nA']), []).append(r)
    if not g:
        sys.exit("没读到段0 的点 (|I| < 1 nA)")

    print("=" * 104)
    print("段0 偏置拆项   (%s)" % a.dir)
    print("=" * 104)
    print("基准 = 2636B 回读值。 T1 = q*dC/(N*T)  T2 = (m*I+ + n*I-)/N")
    print("")
    print("%10s %8s %7s %10s %10s %9s %10s %10s %9s"
          % ("设定pA", "档位A", "窗ms", "回读pA", "装置pA", "偏置pA",
             "T1 pA", "T2 pA", "m/N"))
    print("-" * 104)

    rows = []
    for k in sorted(g, key=abs):
        v = g[k]
        n_tot = int(v[0]['m']) + int(v[0]['n'])
        dev = sum(i_of(int(r['ext1']), int(r['ext2']), int(r['m']),
                       int(r['n'])) for r in v) / len(v) * 1e12
        std = sum(float(r['true_nA']) for r in v) / len(v) * 1e3
        t1 = sum(t1_of(int(r['ext1']), int(r['ext2']),
                       int(r['m']) + int(r['n'])) for r in v) / len(v)
        t2 = sum(t2_of(int(r['m']), int(r['n'])) for r in v) / len(v)
        rows.append(dict(k=k * 1e3, rng=v[0]['rng_A'], win=v[0]['t_ms'],
                         std=std, dev=dev, off=dev - std, t1=t1, t2=t2,
                         frac=int(v[0]['m']) / float(n_tot)))
        r = rows[-1]
        print("%10.4g %8s %7s %10.4f %10.4f %+9.4f %10.4f %10.4f %9.6f"
              % (r['k'], r['rng'], r['win'], r['std'], r['dev'], r['off'],
                 r['t1'], r['t2'], r['frac']))

    print("-" * 104)

    # 按窗口长度分组 —— 这是量纲排除的关键对照
    byw = {}
    for r in rows:
        byw.setdefault(r['win'], []).append(r)
    print("")
    print("## 按窗口长度分组 (量纲排除的关键对照)")
    for w in sorted(byw, key=lambda x: -float(x)):
        v = byw[w]
        mu, sd = ms([x['off'] for x in v])
        print("   窗 %6s ms  %d 点  偏置 %+.4f ± %.4f pA    T1 %+.3f  T2 %+.3f"
              % (w, len(v), mu, sd, sum(x['t1'] for x in v) / len(v),
                 sum(x['t2'] for x in v) / len(v)))
    if len(byw) > 1:
        ws = sorted(byw, key=lambda x: -float(x))
        a_, b_ = byw[ws[0]], byw[ws[-1]]
        na = float(ws[0]) * 25.0 / 4.0          # ms -> 拍 (T_int = 160us)
        nb = float(ws[-1]) * 25.0 / 4.0
        print("   -> N 相差 %.1f 倍。若偏置是「每拍」或「每窗」量, 应差 %.1f 倍;"
              % (na / nb, na / nb))
        print("      实测两边 %+.4f vs %+.4f pA  (差 %.0f%%)"
              % (sum(x['off'] for x in a_) / len(a_),
                 sum(x['off'] for x in b_) / len(b_),
                 abs(sum(x['off'] for x in a_) / len(a_)
                     - sum(x['off'] for x in b_) / len(b_))
                 / abs(sum(x['off'] for x in b_) / len(b_)) * 100))

    print("")
    print("## 档位")
    rngs = sorted({r['rng'] for r in rows})
    print("   本段用到: %s" % " ".join(rngs))
    if len(rngs) == 1:
        print("   -> **整段同一个档位**。所以「回读档位差异」在本段内不可分辨 ——")
        print("      它对偏置的贡献是个**常数**, 会被整个算进「装置的偏置」。")
        print("      要分离必须**主动改档位**再测同一个电流 (smu_set 有 force_range)。")

    print("")
    print("## 结论: 能排除什么")
    print("   ✗ 每拍计数偏差 / 每窗固定电荷 / C_INT / T_INT / 阈值平移")
    print("     —— 全都随 N 变, 与「两个窗口偏置相同」矛盾")
    print("   ✓ 只剩两类与 N 无关的: 连续漏电流、每次翻转的电荷注入")
    print("   ⚠️ 这两类**在本段内都表现为常数**, 靠换窗口分不开")
    print("   ⚠️ 回读档位差异(若存在)也表现为常数, 同样分不开")


if __name__ == '__main__':
    main()
