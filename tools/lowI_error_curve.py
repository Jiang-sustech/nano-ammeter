# -*- coding: utf-8 -*-
"""
低电流段 (1 pA ~ 4 nA) 的测量误差曲线 —— 对数坐标, 标出 5% 交点

    python tools/lowI_error_curve.py
    python tools/lowI_error_curve.py --dir data/lowI_pos --thr 5

误差口径 (与 data/README.md「误差口径」一致, 别改)
------------------------------------------------
    I_std  2636B **回读值** —— 基准
    I_cal  装置**校准后**读数 = 式(6), 复用 report_table.i_of()
    误差   (I_cal − I_std) / I_std × 100 %

⚠️ 分母是**回读值**不是设定值 —— 设定值不是真值 (源表自己的输出偏差
   要另算), 混用会把源表的账记到装置头上。

⚠️ 纵轴是**对数**, 但误差在 1~2 nA 之间**过零** (先 + 后 −)。
   matplotlib 的对数轴画不了负值, 所以这里画的是 **|误差|** ——
   代价是看不出正负号。过零这个事实在下面的打印里有列。

输入 t_ms 会随电流变 (50 s / 10 s / 1 s), 那是固件的自动窗口切换, 不是异常。
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
from report_table import i_of, CONST_FW, CONST_FIT   # 式(6), 公共实现

# ⚠️ 三常数有两套, 差在取整 (见 report_table.py 的注释)。默认 fit。
#    换 fw 会让曲线整体平移 0.265 pA —— 在 1~10 pA 段上就是
#    检测下限从 6 pA 变到 15 pA。**图的说明里必须写明用的哪套。**
CONSTS = {'fw': CONST_FW, 'fit': CONST_FIT}
from uncal_vs_cal import caption, RED, BLUE, BLACK  # 图名定位 + 同一套配色

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def load(path, const=None):
    """raw.csv -> [(I_std_nA, I_cal_nA, t_ms), ...]  逐点均值"""
    g = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            g.setdefault(float(r['set_nA']), []).append(r)
    out = []
    for k in sorted(g):
        v = g[k]
        std = sum(float(r['true_nA']) for r in v) / len(v)
        cal = sum(i_of(int(r['ext1']), int(r['ext2']), int(r['m']), int(r['n']),
                       *(const or CONST_FIT))
                  for r in v) / len(v) * 1e9
        out.append((std, cal, v[0]['t_ms'], k))
    return out


def cross(x, y, thr):
    """找 y 穿过 thr 的位置 —— 在 log10(x) 上线性插值。

    只有两个夹点, 插值方式会改结果, 所以**必须声明确认用的是哪种**。
    这里用 log10(x) 线性 (横轴本来就是对数), 与图上读出来的一致。
    返回 (交点 x, 低侧点, 高侧点) 或 None。
    """
    for i in range(len(x) - 1):
        a, b = y[i] - thr, y[i + 1] - thr
        if a == 0:
            return x[i], (x[i], y[i]), (x[i], y[i])
        if a * b < 0:
            lx = math.log10(x[i]) + (math.log10(x[i + 1]) - math.log10(x[i])) \
                 * (thr - y[i]) / (y[i + 1] - y[i])
            return 10 ** lx, (x[i], y[i]), (x[i + 1], y[i + 1])
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/lowI_pos')
    ap.add_argument('--thr', type=float, default=5.0, help='误差阈值 (%%), 默认 5')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--const', default='fit', choices=sorted(CONSTS),
                    help='用哪一套三常数: fit = 最小二乘给的小数 (默认); '
                         'fw = 固件里存的整数 pA (会让曲线整体平移 0.265 pA)')
    a = ap.parse_args()
    if a.out is None:
        # 同 lowI_compare: 名字带数据目录, 免得负向那批把正向的图盖掉
        tag = os.path.basename(os.path.normpath(a.dir)) or 'out'
        a.out = 'data/%s_error_curve.png' % tag

    print('常数: %s  I+=%.5f nA  I-=%.5f nA'
          % (a.const, CONSTS[a.const][1]*1e9, CONSTS[a.const][2]*1e9))
    pts = load(os.path.join(a.dir, 'raw.csv'), CONSTS[a.const])
    if not pts:
        sys.exit("没读到 %s/raw.csv" % a.dir)

    std = np.array([p[0] for p in pts]) * 1e3          # nA -> pA (带符号)
    cal = np.array([p[1] for p in pts]) * 1e3
    err = (cal - std) / std * 100.0                    # 带符号
    # 横轴用**幅值**。对数轴画不了负值, 而负向那批 (data/lowI_neg) 的回读值
    # 全是负的 —— 不取幅值就整张图空白。误差本身仍按**带符号的** std 算:
    # 恒定的 +Δ 偏置在正电流上给出 +Δ/I, 在负电流上给出 −Δ/I, 这个变号是
    # 真实信息, 取幅值只作用在横轴, 不碰 err。
    xmag = np.abs(std)
    thr = a.thr

    print("=" * 84)
    print("低电流段测量误差曲线   (%s, %d 个点)" % (os.path.join(a.dir, 'raw.csv'), len(pts)))
    print("=" * 84)
    print("误差 = (装置校准值 − 2636B 回读值) / 回读值 × 100 %%")
    print("")
    print("%12s %12s %12s %9s %8s"%("回读pA", "装置pA", "偏差pA", "误差%", "窗口ms"))
    print("-" * 84)
    for (s0, c0, t, k), e in zip(pts, err):
        print("%12.5g %12.5g %12.4f %+9.2f %8s"
              % (s0 * 1e3, c0 * 1e3, (c0 - s0) * 1e3, e, t))
    print("-" * 84)

    # 过零
    for i in range(len(err) - 1):
        if err[i] * err[i + 1] < 0:
            print("  ⚠️ 误差在 %.3g pA 与 %.3g pA 之间**过零** (%+.2f%% -> %+.2f%%)"
                  % (std[i], std[i + 1], err[i], err[i + 1]))
            break
    print("")

    hit = cross(list(xmag), list(err), thr)
    if hit is None:
        print("  **曲线没有穿过 %.3g%%** —— 全段要么都在上面要么都在下面" % thr)
    else:
        xc, lo, hi = hit
        print("  **%.3g%% 交点**" % thr)
        print("     交点坐标:  (%.4g pA, %.4g %%)" % (xc, thr))
        print("     夹在两点之间:  %.4g pA (%+.2f%%)  ~  %.4g pA (%+.2f%%)"
              % (lo[0], lo[1], hi[0], hi[1]))
        print("     插值方式: 在 log10(电流) 上线性 —— 与图上读法一致")
        print("     -> 回读值 >= %.4g pA 时, |误差| < %.3g%%" % (xc, thr))
    print("")

    # ---------------- 图 ----------------
    fig, ax = plt.subplots(figsize=(10.0, 7.2))
    nz = np.abs(err)
    ax.plot(xmag, nz, 'o-', color=BLUE, ms=5.5, lw=1.3, mec=BLUE, mew=.6,
            label='测量误差  $|I_{cal} - I_{std}| / I_{std}$')
    ax.axhline(thr, color=RED, lw=1.5, ls='--',
               label='%.3g %% 误差判据' % thr, zorder=1)
    ax.text(xmag.min(), thr * 1.15, '  %.3g %%' % thr, color=RED,
            fontsize=10, va='bottom', ha='left')

    if hit is not None:
        xc = hit[0]
        ax.plot([xc], [thr], 'o', color=RED, ms=9, mec=BLACK, mew=1.0, zorder=5)
        # 竖虚线拉到横轴, 让交点横坐标能直接读
        ax.plot([xc, xc], [nz.min() * 0.6, thr], ls=':', lw=1.1, color=BLACK,
                zorder=1)
        # 标注挪到交点右上方的空白处 —— 挤在交点旁边会和曲线、5% 线打架。
        # 夹住交点的两点不标字 (会在这种密集处重叠), 见打印输出。
        ax.annotate('%.3g %% 交点\n(%.4g pA, %.3g %%)' % (thr, xc, thr),
                    xy=(xc, thr), xytext=(18, 16), textcoords='offset points',
                    fontsize=10, color=BLACK, ha='left', va='bottom',
                    arrowprops=dict(arrowstyle='->', color=BLACK, lw=1.0))

    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xlabel('2636B 回读电流 $|I_{std}|$   (pA)      ← 基准', fontsize=11)
    ax.set_ylabel('测量误差 $|I_{cal} - I_{std}| / I_{std}$   (%)', fontsize=11)
    ax.grid(alpha=.25, which='both', color=BLACK, lw=.6)
    ax.tick_params(labelsize=9.5)
    ax.legend(fontsize=10, loc='upper right', framealpha=.95)
    ax.set_ylim(nz.min() * 0.55, nz.max() * 2.0)
    ax.set_xlim(xmag.min() * 0.8, xmag.max() * 1.25)

    caption(ax,
            '低电流段测量误差曲线（对数坐标）\n'
            '%d 个电流点，1 pA ~ 4 nA，每点 3 次取均值　'
            '基准 = 2636B 回读值　2026-09-16\n'
            '三常数: %s'
            % (len(pts), '最小二乘拟合值' if a.const == 'fit'
               else '固件存储值（整数 pA）'),
            dy=54, note_dy=96,
            note='注：纵轴为对数，画的是误差绝对值 —— 误差在 1~2 nA 之间过零'
                 '（+0.17% → −0.05%），负值在对数轴上无法表示。')
    fig.tight_layout()
    fig.savefig(a.out, dpi=150, bbox_inches='tight')
    print("已写", a.out)


if __name__ == '__main__':
    main()
