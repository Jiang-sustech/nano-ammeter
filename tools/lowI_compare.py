# -*- coding: utf-8 -*-
"""
低电流段的三条误差链对比 —— 装置 / 设定 / 2636B 回读

    python tools/lowI_compare.py
    python tools/lowI_compare.py --dir data/lowI_pos -o data/lowI_compare.png

为什么要把三条分开
------------------
这一段的三个量**不是一回事**, 混着说一定出错:

    设定 I_set   我们让源表输出多少
    回读 I_std   源表**自己测自己**输出多少   <- 本工程选定的基准
    装置 I_dev   用式(6) 从装置码流算出来多少

于是有两条独立的误差链:

    ① 装置 − 回读 = 我们关心的那个数 (装置的偏差), 但**基准是回读值**
    ② 设定 − 回读 = **源表自己的输出偏差** —— 它的输出没到设定值

**关键**: ② 的存在说明"设定值不是真值"。所以
    装置 − 设定 = ① + ②   ——**把源表的偏差也算进装置头上, 是错的**。

2636B 在 1 nA 档的**回读精度** `±(0.15% + 240 fA)`: 固定项 ±0.24 pA。
所以 ① 里有多少归装置、多少归回读, **分不开** —— 要第三个独立参考。

口径
----
* 残差/偏差都按**逐点均值**(每点 3 次先平均), 与工程其他报告一致
* 相对误差的分母用**回读值**(基准), 所以 "装置−回读 (%)" = ①/I_std
* `|I| >= 1 nA` 的点属于**段1**, 且固件自动切 1 s 窗口 —— 默认不混进来
"""
import argparse
import csv
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

T = 160e-6
Q = 15596e-18       # 固件当前烧的物理常数 (09-16 写入的 162 点拟合值)
IP = 49712e-12
IN_ = -49739e-12


def device_pA(c1, c2, m, n):
    nt = float(m + n)
    return (Q * (c2 - c1) / (nt * T) - (m * IP + n * IN_) / nt) * 1e12


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/lowI_pos')
    ap.add_argument('--all', action='store_true',
                    help='连 |I| >= 1 nA 的点一起列 (它们属段1 且走 1 s 窗口)')
    ap.add_argument('--panel', default='both', choices=['both', 'rel', 'abs'],
                    help='both=两格; rel=**只要相对误差那张**; abs=只要绝对误差那张')
    ap.add_argument('--logy', action='store_true',
                    help='纵轴也取对数 (相对误差跨 3000 倍时看得清低端)')
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args()
    if a.out is None:
        # 文件名带上**数据目录** —— 正负两批各存一目录, 图也得各存一张。
        # 原来固定叫 data/lowI_compare.png, 跑负向那批会把正向的图盖掉。
        tag = os.path.basename(os.path.normpath(a.dir)) or 'out'
        a.out = 'data/%s_compare%s%s.png' % (
            tag, '' if a.panel == 'both' else '_' + a.panel,
            '_logy' if a.logy else '')

    g = {}
    with open(os.path.join(a.dir, 'raw.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            k = float(r['set_nA'])
            # ⚠️ **必须取 abs** —— 2026-09-16 加负向批次时才发现: 原来写的是
            # `k >= 1.0`, 对负数据**恒为假**(−1.0 不 >= 1.0), 于是 −1/−2/−4 nA
            # 这些**段1、走 1 s 窗口**的点会悄悄混进段0 的统计里, 把
            # "装置−回读" 的均值和散布一起带偏。判据本来是按**幅值**分的。
            if (not a.all) and abs(k) >= 1.0:
                continue
            g.setdefault(k, []).append(r)

    pts = []
    for k in sorted(g):
        v = g[k]
        dev = [device_pA(int(r['ext1']), int(r['ext2']), int(r['m']), int(r['n']))
               for r in v]
        std = [float(r['true_nA']) * 1e3 for r in v]
        pts.append(dict(
            set=k * 1e3,
            std=sum(std) / len(std),
            dev=sum(dev) / len(dev),
            sd=math.sqrt(sum((x - sum(dev) / len(dev)) ** 2 for x in dev)
                         / max(len(dev) - 1, 1)),
            win=v[0]['t_ms']))

    print("=" * 92)
    print("低电流段: 装置 / 设定 / 回读 三条误差链")
    print("=" * 92)
    print("基准 = 2636B **回读值**。1 nA 档回读精度 ±(0.15% + 240 fA) -> 固定项 ±0.24 pA。")
    print("")
    hdr = ("%9s %11s %11s %9s %9s %9s %9s %8s"
           % ("设定pA", "回读pA", "装置pA", "装置−回读", "  %", "设定−回读", "  %", "窗口"))
    print(hdr)
    print("-" * 92)
    e1, e1p, e2, e2p = [], [], [], []
    for p in pts:
        d1 = p['dev'] - p['std']; d2 = p['set'] - p['std']
        e1.append(d1); e1p.append(d1 / p['std'] * 100)
        e2.append(d2); e2p.append(d2 / p['std'] * 100)
        print("%9.3g %11.4f %11.4f %+9.3f %+8.2f%% %+9.3f %+8.2f%% %7sms"
              % (p['set'], p['std'], p['dev'], d1, d1 / p['std'] * 100,
                 d2, d2 / p['std'] * 100, p['win']))

    def ms(v):
        m = sum(v) / len(v)
        return m, math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    print("-" * 92)
    m1, s1 = ms(e1); m2, s2 = ms(e2)
    print("  **装置 − 回读**: 均值 %+.3f ± %.3f pA" % (m1, s1))
    print("  **设定 − 回读**: 均值 %+.3f ± %.3f pA   <- 这一段是**源表自己**的偏差"
          % (m2, s2))
    print("  装置 − 设定     : 均值 %+.3f pA  (= 上面两条之差)"
          % (ms([p['dev'] - p['set'] for p in pts])[0]))
    print("")
    print("  ⚠️ 回读固定项 ±0.24 pA -> 装置−回读 里有一部分**可能归回读**,")
    print("     分不开。要分离必须有第三个独立参考 (标准 R + 标准 V)。")

    # ---------------- 图 ----------------
    x = np.array([p['set'] for p in pts])
    want = {'both': [('abs', e1, e2, 'pA'), ('rel', e1p, e2p, '%')],
            'abs': [('abs', e1, e2, 'pA')],
            'rel': [('rel', e1p, e2p, '%')]}[a.panel]
    fig, axs = plt.subplots(len(want), 1, figsize=(12, 4.8 * len(want)),
                            sharex=True, squeeze=False)
    for axx, (kind, y1, y2, lab) in zip(axs[:, 0], want):
        axx.plot(x, y1, 'o-', color='C3', ms=6, lw=1.6,
                 label='装置(式6) − 回读   ← 我们关心的')
        axx.plot(x, y2, 's--', color='C0', ms=6, lw=1.6,
                 label='设定 − 回读        ← 源表自己的输出偏差')
        if kind == 'abs':
            axx.axhline(0, color='k', lw=1, ls='--')
            for s in (0.24, -0.24):
                axx.axhline(s, color='C0', ls=':', lw=1.2)
            axx.text(x[0], 0.24, ' 2636B 回读固定项 ±0.24 pA', fontsize=8,
                     color='C0', va='bottom')
            axx.set_title('低电流段误差链 (基准 = 2636B 回读值)')
        else:
            if a.logy:
                axx.set_yscale('log')
                axx.set_title('相对回读值的误差 —— **纵轴对数**')
            else:
                axx.axhline(0, color='k', lw=1, ls='--')
                axx.set_title('相对回读值的误差')
        axx.set_xscale('log')
        axx.set_ylabel('误差 (%s)' % lab)
        axx.grid(alpha=.3, which='both')
        axx.legend(fontsize=9)
    axs[-1, 0].set_xlabel('设定电流 (pA)')
    fig.suptitle('装置 / 设定 / 回读 三条误差链   (%d 个点, |I| < 1 nA, 横轴对数)'
                 % len(pts), fontsize=12, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(a.out, dpi=150)
    print("")
    print("已写", a.out)


if __name__ == '__main__':
    main()
