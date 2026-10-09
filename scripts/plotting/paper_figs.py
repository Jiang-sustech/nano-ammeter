# -*- coding: utf-8 -*-
"""
按《论文制图规范》出四张图 -> image_paper/

    python scripts/plotting/paper_figs.py

规范见 scripts/lib/paper_style.py (字号/图幅/配色) 与 image_paper/IMAGE_SPEC.md (正文)。
数据加载**不重写** —— 直接复用既有脚本的 loader 与坏点表, 保证和之前几版口径一致。
"""
import math
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, NullFormatter, ScalarFormatter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 脚本按用途分家后, 让本目录 + scripts 根 + 各子目录(lib/smu/plotting/analysis/misc)都在 import 路径上
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ('lib', 'smu', 'plotting', 'analysis', 'misc'):
    sys.path.insert(0, os.path.join(_ROOT, _sub))

from report_table import i_of                                    # noqa: E402
import uncal_vs_cal                                              # noqa: E402
import lowI_error_curve                                          # noqa: E402
import rel_accuracy                                              # noqa: E402
import paper_style as ps                                         # noqa: E402

ps.apply()                       # ⚠️ 必须在所有 import 之后 —— 旧脚本在模块级改过 rcParams

DENSE = 'data/dense/raw.csv'
LOWI = 'data/lowI_pos/raw.csv'
OUT = 'image_paper'


def _save(fig, name):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    fig.savefig(p)               # 不用 bbox_inches='tight' —— 那会改掉图幅尺寸
    plt.close(fig)
    print("已写", p)


# ============================ 图 1  未校准散点 ============================
def fig01():
    rows = uncal_vs_cal.load(DENSE)
    g = {}
    for s, rp, std, dev, cal in rows:
        g.setdefault(s, []).append((std, dev))
    keys = sorted(g, key=lambda k: (k < 0, abs(k)))
    std = np.array([sum(x[0] for x in g[k]) / len(g[k]) for k in keys])
    dev = np.array([sum(x[1] for x in g[k]) / len(g[k]) for k in keys])

    fig, cap_y = ps.figure(9.7, cap_lines=1)
    ax = fig.add_subplot(1, 1, 1)

    lim = np.array([std.min(), std.max()]) * 1.06
    ax.plot(lim, lim, '-', color=ps.BLACK, lw=ps.LW_MAIN,
            label='回读值参考线  $I_{dev} = I_{std}$')
    ax.plot(std, dev, 'o', color=ps.RED, ms=ps.MS, mec=ps.RED, mew=ps.MEW,
            alpha=.85, label='装置未校准读数 $I_{dev}$')

    ax.set_xlim(lim); ax.set_ylim(lim)
    # 画布框 宽:高 = 2:1 (aspect = y单位/x单位 的画布长度比 = 0.5)。
    # 代价: 理想线不再是 45°, 偏离量不能按像素直读 —— 靠参考线对比。
    ax.set_aspect(0.5, adjustable='box')
    ax.set_xlabel('2636B 回读电流 $I_{std}$   (nA)')
    ax.set_ylabel('装置未校准读数 $I_{dev}$   (nA)')
    ps.grid(ax)
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y, '装置未校准读数与回读值的关系')
    _save(fig, 'fig01_uncal.png')


# ============================ 图 2  校准后 ============================
def fig02():
    rows = uncal_vs_cal.load(DENSE)
    g = {}
    for s, rp, std, dev, cal in rows:
        g.setdefault(s, []).append((std, cal))
    keys = sorted(g, key=lambda k: (k < 0, abs(k)))
    std = np.array([sum(x[0] for x in g[k]) / len(g[k]) for k in keys])
    cal = np.array([sum(x[1] for x in g[k]) / len(g[k]) for k in keys])
    # 原稿另有一格残差散布图。2026-09-21 用户要求**删去** —— ±45 nA 这张
    # 只保留"三常数校准后的数据 + 回读值那条直线"。残差请见图 4 下格。

    fig, cap_y = ps.figure(10.5, cap_lines=1)
    ax = fig.add_subplot(1, 1, 1)

    lim = np.array([std.min(), std.max()]) * 1.06
    ax.plot(lim, lim, '-', color=ps.BLACK, lw=ps.LW_MAIN,
            label='理想线性  $I_{cal} = I_{std}$')
    ax.plot(std, cal, 'o', color=ps.BLUE, ms=ps.MS, mec=ps.BLUE, mew=ps.MEW,
            alpha=.85, label='校准后装置读数 $I_{cal}$')
    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel('2636B 回读电流 $I_{std}$   (nA)')
    ax.set_ylabel('装置校准后读数 $I_{cal}$   (nA)')
    ps.grid(ax)
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y, '三常数校准后的装置读数与回读值')
    _save(fig, 'fig02_cal.png')


# ============================ 图 3  低电流误差曲线 ============================
def fig03():
    pts = lowI_error_curve.load(LOWI)
    std = np.array([p[0] for p in pts]) * 1e3          # nA -> pA
    cal = np.array([p[1] for p in pts]) * 1e3
    tms = np.array([str(p[2]) for p in pts])
    rel = (cal - std) / std * 100.0

    fig, cap_y = ps.figure(13.0, cap_lines=1)
    ax = fig.add_subplot(1, 1, 1)

    nz = np.abs(rel)
    # 2026-09-21 用户要求: **不连线**, 且**按测量窗口分开**。
    # 固件按电流自动切窗口 (50 s / 10 s / 1 s), 三个窗口是三条不同的代码
    # 路径, 连成一条线会掩盖窗口切换处的接续与否。
    for t, col, mk in (('50000', ps.BLUE, 'o'),
                       ('10000', ps.RED, 's'),
                       ('1000', ps.BLACK, '^')):
        m = tms == t
        if not m.any():
            continue
        ax.plot(std[m], nz[m], mk, color=col, ms=ps.MS + .5, mec=col,
                mew=ps.MEW, ls='none', label='窗口 %d s（%d 点）'
                % (int(t) // 1000, int(m.sum())))
    ax.axhline(5.0, color=ps.RED, lw=ps.LW_MAIN, ls='--',
               label='5 % 误差判据', zorder=1)

    hit = lowI_error_curve.cross(list(std), list(rel), 5.0)
    if hit is not None:
        xc = hit[0]
        ax.plot([xc], [5.0], 'o', color=ps.RED, ms=ps.MS + 3, mec=ps.BLACK,
                mew=0.9, zorder=5)
        ax.plot([xc, xc], [nz.min() * 0.6, 5.0], ls=':', lw=ps.LW_THIN,
                color=ps.BLACK, zorder=1)
        ax.annotate('5 %% 交点\n(%.3g pA, 5 %%)' % xc,
                    xy=(xc, 5.0), xytext=(16, 12), textcoords='offset points',
                    fontsize=ps.FS_LABEL, color=ps.BLACK, ha='left', va='bottom',
                    arrowprops=dict(arrowstyle='->', color=ps.BLACK, lw=0.9))

    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_ylim(nz.min() * 0.55, nz.max() * 2.0)
    ax.set_xlim(std.min() * 0.8, std.max() * 1.25)
    ax.set_xlabel('2636B 回读电流 $I_{std}$   (pA)')
    ax.set_ylabel('测量误差 $|I_{cal} - I_{std}| / I_{std}$   (%)')
    ps.grid(ax, which='both')
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y, '低电流段测量误差曲线')
    _save(fig, 'fig03_lowI_error.png')


# ============================ 图 4  中电流相对精度 ============================
def fig04():
    pts = rel_accuracy.load(DENSE, 5.0, 45.0)
    std = np.array([p[0] for p in pts])
    res = np.array([(p[1] - p[0]) * 1e3 for p in pts])
    rel = res / (std * 1e3) * 100.0
    astd = np.abs(std)
    pos, neg = std > 0, std < 0
    med_abs = float(np.median(np.abs(res)))

    fig, cap_y = ps.figure(18.0, cap_lines=1, title=True)
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2, sharex=ax1)
    ax1.tick_params(labelbottom=False)     # 横轴刻度只在下格标一次
    fig.subplots_adjust(hspace=0.20)

    for tag, m, col, mk in (('正电流', pos, ps.BLUE, 'o'),
                            ('负电流', neg, ps.RED, 's')):
        ax1.plot(astd[m], np.abs(rel[m]), mk, color=col, ms=ps.MS, mec=col,
                 mew=ps.MEW, alpha=.85, label='%s  $|\\Delta I| / I_{std}$' % tag)
    xe = np.array([5.0, 45.0])
    ax1.plot(xe, med_abs / (xe * 1e3) * 100, '-', color=ps.BLACK,
             lw=ps.LW_MAIN, label='恒定绝对误差 中位 %.3f pA ÷ I' % med_abs)
    order = np.argsort(astd)
    K = 8
    rx, ry = [], []
    for i, o in enumerate(order):
        w = order[max(0, i - K):i + K + 1]
        rx.append(astd[o]); ry.append(float(np.median(np.abs(rel[w]))))
    ax1.plot(rx, ry, '-', color=ps.BLACK, lw=ps.LW_THIN, alpha=.75,
             label='实测滚动中位数')
    ax1.set_xscale('log'); ax1.set_yscale('log')
    ax1.set_ylabel('|相对误差|   (%)')
    ps.grid(ax1, which='both')
    ax1.legend(loc='lower right', framealpha=.95)
    ax1.set_title('(a) 相对精度', color=ps.BLACK)

    for tag, m, col, mk in (('正电流', pos, ps.BLUE, 'o'),
                            ('负电流', neg, ps.RED, 's')):
        ax2.plot(astd[m], res[m], mk, color=col, ms=ps.MS, mec=col, mew=ps.MEW,
                 alpha=.85, label=tag)
    ax2.axhline(0, color=ps.BLACK, lw=ps.LW_MAIN, ls='--')
    rms = math.sqrt(float(np.mean(res ** 2)))
    for s in (rms, -rms):
        ax2.axhline(s, color=ps.BLACK, lw=ps.LW_THIN, ls=':',
                    label='±RMS %.3f pA' % rms if s > 0 else None)
    ax2.set_xscale('log')
    ax2.xaxis.set_major_locator(FixedLocator([5, 10, 20, 30, 45]))
    ax2.xaxis.set_major_formatter(ScalarFormatter())
    ax2.xaxis.set_minor_formatter(NullFormatter())
    ax2.set_ylim(-6, 6)
    ax2.set_xlabel('2636B 回读电流 $|I_{std}|$   (nA)')
    ax2.set_ylabel('绝对残差 $I_{cal} - I_{std}$   (pA)')
    ps.grid(ax2)
    ax2.legend(loc='lower right', framealpha=.95, ncol=3)
    ax2.set_title('(b) 绝对残差', color=ps.BLACK)

    ps.caption(fig, cap_y, '中电流段相对精度')
    _save(fig, 'fig04_rel_accuracy.png')


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass
    fig01(); fig02(); fig03(); fig04()
