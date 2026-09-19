# -*- coding: utf-8 -*-
"""
162 点密集标定 (2026-09-16 上午) 的未校准 / 校准后两图

    python tools/uncal_vs_cal.py
    python tools/uncal_vs_cal.py --dir data/dense

产出
----
  data/uncal_162.png   图1: **未校准**散点 —— 装置原始读数 vs 2636B 回读值
  data/cal_162.png     图2: **三常数校准后** —— 上格 点+理想 y=x, 下格 残差放大

口径 (与 data/README.md「误差口径」一致, 别改)
--------------------------------------------
  I_std  2636B **回读值** —— 本工程的基准 (不用设定值)
  I_dev  装置**未校准**读数, 直接取 raw.csv 的 dev_nA 列
  I_cal  装置**校准后**读数 = 式(6): report_table.i_of() (公共实现, 不重定义)
  残差   I_cal − I_std   —— 是"装置 vs 回读值"之差, **不是**装置的绝对准确度

颜色: 只用 正红 #FF0000 / 蓝 #0000FF / 黑 #000000 (用户指定)
  未校准 = 正红   校准后 = 蓝   理想/参考线 = 黑

为什么剔除两个点: 见 dense_report.py 的门注释 —— 它们是两种不同成因的坏点,
不剔会把残差从 pA 量级抬到百 pA 量级, 整张图被带偏。
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
from report_table import i_of          # 式(6) 的公共实现, 复用不重写

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

RED, BLUE, BLACK = '#FF0000', '#0000FF', '#000000'

# 图名落在**正下方**: 用「相对坐标轴固定偏移」定位, 不跟 tight_layout 抢位置
# (用 fig.text + rect 会被 tight_layout 的边缘算出重叠, 试过, 不行)
NOTE = ('注：已剔除 4 次异常重复（3 次装置单次跳变 + 1 次源侧未整定），'
        '每个电流点各剔 1 次，其余重复取均值。')


def caption(ax, title, dy=54, note_dy=None, fontsize=11, note=None):
    """在图下方正中落图名; note_dy 给了就再往下写一行小字注脚。

    note 不给就用本文件的 NOTE (坏点说明); 别的脚本 (如 lowI_error_curve.py)
    传自己的 note 即可复用这个定位方式。
    """
    ax.annotate(title, xy=(0.5, 0), xycoords='axes fraction',
                xytext=(0, -dy), textcoords='offset points',
                ha='center', va='top', fontsize=fontsize, color=BLACK)
    if note_dy is not None:
        ax.annotate(NOTE if note is None else note,
                    xy=(0.5, 0), xycoords='axes fraction',
                    xytext=(0, -note_dy), textcoords='offset points',
                    ha='center', va='top', fontsize=8.5, color=BLACK)

# 坏点, 按 (设定 nA, 第几次) 精确点名剔除 —— 不搞通用闸门。
# 两种成因, 见 dense_report.py 的门注释:
#   ① 源侧: 源表还没整定完, 回读离设定很远
#   ② 装置侧单次跳变: 回读正常, 同一点的另两次正常, 单这一次跳 20~630 pA
#
# ⚠️ 第③类是**孤立**的, 不是系统性次序漂移 —— 查过: 逐次中位数 rep1/2/3 =
#    +0.19/+0.32/-0.09 pA, 相邻两次之差中位 ~-0.06 pA, 162 点里只有 1~3 次超 10 pA。
#    所以**不能**因为"第 3 次差"就整套换口径, 只需点名单次。
BAD = [(14.5, 1, "源侧未整定  回读 9.49 而设定 14.5 (偏 34.5%)"),
       (15.5, 3, "装置单次跳变  本次 +628.10 pA, 另两次 +0.79/+6.06"),
       (26.5, 3, "装置单次跳变  本次 -30.29 pA, 另两次 -0.12/+8.36"),
       (14.0, 3, "装置单次跳变  本次 +22.04 pA, 另两次 +1.51/+6.14")]


def load(path):
    """data/dense/raw.csv -> [(set_nA, rep, I_std, I_dev, I_cal), ...]  (单位 nA)"""
    out = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            try:
                s, rp = float(r['set_nA']), int(r['rep'])
                std = float(r['true_nA'])
                dev = float(r['dev_nA'])
                m, n = int(r['m']), int(r['n'])
                c1, c2 = int(r['ext1']), int(r['ext2'])
            except (KeyError, ValueError):
                continue
            if (s, rp) in {(b[0], b[1]) for b in BAD}:
                continue                      # ← 直接剔除坏点
            cal = i_of(c1, c2, m, n) * 1e9
            out.append((s, rp, std, dev, cal))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/dense')
    ap.add_argument('--out-uncal', default='data/uncal_162.png')
    ap.add_argument('--out-cal', default='data/cal_162.png')
    a = ap.parse_args()

    rows = load(os.path.join(a.dir, 'raw.csv'))
    if not rows:
        sys.exit("没读到 %s/raw.csv" % a.dir)

    # 逐点均值 (每点 3 次 -> 剔坏点后 14.5/15.5 两点只剩 2 次)
    g = {}
    for s, rp, std, dev, cal in rows:
        g.setdefault(s, []).append((std, dev, cal))
    keys = sorted(g, key=lambda k: (k < 0, abs(k)))
    pt = {k: tuple(sum(x[i] for x in g[k]) / len(g[k]) for i in range(3))
          for k in keys}
    npt = len(keys)
    nrep = {k: len(g[k]) for k in keys}

    std = np.array([pt[k][0] for k in keys])
    dev = np.array([pt[k][1] for k in keys])
    cal = np.array([pt[k][2] for k in keys])

    fs = std.max() - std.min()
    d_uncal = (dev - std) * 1e3              # pA
    d_cal = (cal - std) * 1e3                # pA
    rms = math.sqrt(float(np.mean(d_cal ** 2)))
    mx = float(np.max(np.abs(d_cal)))

    print("=" * 78)
    print("162 点密集标定   (%s)" % os.path.join(a.dir, 'raw.csv'))
    print("=" * 78)
    print("  %d 次测量 -> **%d 个电流点** (每点取均值)" % (len(rows), npt))
    print("  已剔除 %d 个坏点:" % len(BAD))
    for s, rp, why in BAD:
        print("      %+g nA 第%d次   %s" % (s, rp, why))
    print("  电流范围 %+.1f ~ %+.1f nA   FS = %.1f nA" % (std.min(), std.max(), fs))
    print("")
    rel_unc = float(np.mean((dev - std) / std * 100))
    print("  【未校准】装置 dev_nA       vs 回读   相对偏差均值 %+.4f %%"
          % rel_unc)
    print("  【校准后】装置 式(6)        vs 回读   残差 RMS %.3f pA   max %.3f pA"
          % (rms, mx))
    print("              -> 线性度 %.5f %%FS  (max|残差|/FS)"
          % (mx * 1e-3 / fs * 100))
    print("")

    # ---------------- 图1: 未校准散点 + 回读值参考线 ----------------
    fig, ax = plt.subplots(figsize=(9.5, 7.6))
    ulim = np.array([std.min(), std.max()]) * 1.06
    # 回读值参考线: 装置读数若等于回读值, 点就落在这条线上。
    # **没有它这张图什么都看不出来** —— ±45 nA 满量程下 1.03% 只有 0.46 nA,
    # 光看散点就是一条笔直的线, 偏了多少完全读不出。
    ax.plot(ulim, ulim, '-', color=BLACK, lw=1.4,
            label='回读值参考线  $I_{dev} = I_{std}$')
    ax.plot(std, dev, 'o', color=RED, ms=5.0, mec=RED, mew=.6, alpha=.85,
            label='装置未校准读数 $I_{dev}$')
    ax.set_xlim(ulim); ax.set_ylim(ulim)
    ax.set_xlabel('2636B 回读电流 $I_{std}$   (nA)      ← 基准', fontsize=11)
    ax.set_ylabel('装置未校准读数 $I_{dev}$   (nA)', fontsize=11)
    ax.grid(alpha=.25, color=BLACK, lw=.6)
    ax.tick_params(labelsize=9.5)
    ax.legend(fontsize=10, loc='upper left', framealpha=.95)
    ax.set_aspect('equal', adjustable='box')
    caption(ax,
            '图 1　装置未校准读数与 2636B 回读值的关系\n'
            '162 个电流点（±5 ~ ±45 nA，步长 0.5 nA，每点 3 次取均值）　'
            '相对回读值偏差 %+.2f %%　2026-09-16' % rel_unc,
            dy=54, note_dy=96)
    fig.tight_layout()
    fig.savefig(a.out_uncal, dpi=150, bbox_inches='tight')
    print("已写", a.out_uncal)

    # ---------------- 图2: 校准后 (点+理想直线 / 残差放大) ----------------
    fig2, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10.0, 10.6), sharex=True,
        gridspec_kw=dict(height_ratios=[1.25, 1], hspace=0.14))

    lim = np.array([std.min(), std.max()]) * 1.06
    ax1.plot(lim, lim, '-', color=BLACK, lw=1.4,
             label='理想线性  $I_{cal} = I_{std}$')
    ax1.plot(std, cal, 'o', color=BLUE, ms=5.0, mec=BLUE, mew=.6, alpha=.85,
             label='校准后装置读数 $I_{cal}$')
    ax1.set_xlim(lim); ax1.set_ylim(lim)
    ax1.set_ylabel('装置校准后读数 $I_{cal}$   (nA)', fontsize=11)
    ax1.grid(alpha=.25, color=BLACK, lw=.6)
    ax1.tick_params(labelsize=9.5)
    ax1.legend(fontsize=10, loc='upper left', framealpha=.95)
    ax1.set_title('(a) 校准后装置读数 vs 理想线性', fontsize=11.5, color=BLACK)

    ax2.plot(std, d_cal, 'o', color=BLUE, ms=4.2, mec=BLUE, mew=.5, alpha=.85,
             label='残差 $I_{cal} - I_{std}$')
    ax2.axhline(0, color=BLACK, lw=1.2, ls='--')
    for s in (rms, -rms):
        ax2.axhline(s, color=BLACK, lw=.9, ls=':',
                    label='±RMS = ±%.2f pA' % rms if s > 0 else None)
    ax2.set_xlabel('2636B 回读电流 $I_{std}$   (nA)      ← 基准', fontsize=11)
    ax2.set_ylabel('残差 $I_{cal} - I_{std}$   (pA)', fontsize=11)
    ax2.grid(alpha=.25, color=BLACK, lw=.6)
    ax2.tick_params(labelsize=9.5)
    ax2.legend(fontsize=9.5, loc='upper left', framealpha=.95, ncol=2)
    ax2.set_title('(b) 残差放大  ——  纵轴单位 pA，是 (a) 的 1000 倍放大',
                  fontsize=11.5, color=BLACK)

    caption(ax2,
            '图 2　三常数校准后的装置读数与理想线性\n'
            '基准 = 2636B 回读值　RMS %.2f pA，max %.2f pA　162 点' % (rms, mx),
            dy=56, note_dy=90)
    fig2.tight_layout()
    fig2.savefig(a.out_cal, dpi=150, bbox_inches='tight')
    print("已写", a.out_cal)


if __name__ == '__main__':
    main()
