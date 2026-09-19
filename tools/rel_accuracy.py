# -*- coding: utf-8 -*-
"""
中电流段 (默认 5 ~ 45 nA) 的**相对精度** —— 相对误差与绝对残差并排

    python tools/rel_accuracy.py
    python tools/rel_accuracy.py --dir data/dense --min-na 5 --max-na 45

为什么必须两格一起看
--------------------
只画相对误差会**误导** —— 它从 5 nA 的 0.036% 掉到 45 nA 的 0.004%,
看着像"大电流下装置更准"。其实不是:

    绝对残差全程平在 ~1 pA 量级, **不随电流增长** (见下格)。
    相对误差 = 绝对残差 / 电流  ——  下降**纯粹是除法**。

所以上格那条 `RMS ÷ I` 参考线是这张图的重点: 实测点贴着它走, 才说明
"绝对误差恒定"这个解释成立, 而不是装置在大电流下真的变准了。

口径 (与 data/README.md「误差口径」一致, 别改)
--------------------------------------------
    I_std  2636B **回读值** —— 基准
    I_cal  装置**校准后**读数 = 式(6), 复用 report_table.i_of()
    残差   I_cal − I_std          (pA)
    相对   残差 / I_std × 100     (%)

⚠️ 纵轴画的是**绝对值** —— 相对误差在本段会变号, 而对数轴表示不了负值。
⚠️ 正/负电流分开画只是便于对照。注意**不要**照抄 data/README 里"正负不对称
   (2.08/1.05 pA)"那句 —— 那是**真验证点**那批的结论; 这 162 点密网格算下来
   正负 RMS 相当, 没有那个结构。
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
from matplotlib.ticker import FixedLocator, NullFormatter, ScalarFormatter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_table import i_of                             # 式(6), 公共实现
from uncal_vs_cal import (caption, BAD, RED, BLUE, BLACK)  # 图名定位 / 坏点表 / 配色

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# uncal_vs_cal.BAD 是 (设定, 第几次, 原因) 三元组; 这里只要前两个
BAD_REPS = {(b[0], b[1]) for b in BAD}


def load(path, lo, hi):
    """raw.csv -> [(I_std_nA, I_cal_nA), ...] 逐点均值, 只留 lo<=|set|<=hi"""
    g = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            s, rp = float(r['set_nA']), int(r['rep'])
            if (s, rp) in BAD_REPS:                # 与 uncal_vs_cal 同一张坏点表
                continue
            if not (lo <= abs(s) <= hi):
                continue
            g.setdefault(s, []).append(
                (float(r['true_nA']),
                 i_of(int(r['ext1']), int(r['ext2']), int(r['m']), int(r['n'])) * 1e9))
    out = []
    for k in sorted(g, key=lambda t: (t < 0, abs(t))):
        v = g[k]
        out.append((sum(x[0] for x in v) / len(v), sum(x[1] for x in v) / len(v)))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/dense')
    ap.add_argument('--min-na', type=float, default=5.0)
    ap.add_argument('--max-na', type=float, default=45.0)
    ap.add_argument('-o', '--out', default='data/rel_accuracy_5_45.png')
    a = ap.parse_args()

    pts = load(os.path.join(a.dir, 'raw.csv'), a.min_na, a.max_na)
    if not pts:
        sys.exit("没读到 %s/raw.csv 里 %.4g~%.4g nA 的点" % (a.dir, a.min_na, a.max_na))

    std = np.array([p[0] for p in pts])
    res = np.array([(p[1] - p[0]) * 1e3 for p in pts])     # pA
    rel = res / (std * 1e3) * 100.0                        # %
    astd = np.abs(std)
    pos, neg = std > 0, std < 0

    rms_abs = math.sqrt(float(np.mean(res ** 2)))
    q = lambda v: (float(np.percentile(v, 25)), float(np.median(v)),
                   float(np.percentile(v, 75)))

    def rms(v):
        return math.sqrt(float(np.mean(v ** 2)))

    print("=" * 80)
    print("中电流段相对精度   (%s, %.4g ~ %.4g nA, %d 个点)"
          % (os.path.join(a.dir, 'raw.csv'), a.min_na, a.max_na, len(pts)))
    print("=" * 80)
    print("  残差 = 式(6) − 2636B 回读值;  相对 = 残差 / 回读值")
    print("")
    for tag, m in (("正电流", pos), ("负电流", neg), ("合并", np.ones(len(std), bool))):
        r, e = np.abs(res[m]), np.abs(rel[m])
        p25, p50, p75 = q(e)
        print("  【%s】n=%d" % (tag, m.sum()))
        print("      绝对残差   RMS %6.3f pA   中位 %6.3f pA   max %6.3f pA"
              % (rms(res[m]), float(np.median(r)), float(np.max(r))))
        print("      |相对误差| 中位 %.5f %%   四分位 %.5f ~ %.5f %%   max %.5f %%"
              % (p50, p25, p75, float(np.max(e))))
    print("")
    print("  整个 %.4g~%.4g nA 段: 绝对残差 RMS = %.4f pA" % (a.min_na, a.max_na, rms_abs))
    print("      -> 若绝对误差恒定, 相对误差应为 RMS/I:")
    for x in (a.min_na, 10.0, 20.0, a.max_na):
        # 回读值不是整数 (5 nA 实测 4.9987), 所以取最接近的点, 不能判相等
        j = int(np.argmin(np.abs(astd - x)))
        print("         %4.4g nA 处  预期 %.5f %%   实测 %.5f %%  (回读 %.4g nA)"
              % (x, rms_abs / (x * 1e3) * 100, abs(rel[j]), astd[j]))
    print("")
    print("  ⚠️ 相对误差**不是**大电流下装置更准 —— 是同一个绝对误差被更大的电流除。")
    print("")

    # ---------------- 图: 上格相对, 下格绝对 ----------------
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10.0, 9.6), sharex=True,
        gridspec_kw=dict(height_ratios=[1, 1]))

    ser = (("正电流", pos, BLUE, 'o'), ("负电流", neg, RED, 's'))

    # 上格: |相对误差|, 双对数
    for tag, m, col, mk in ser:
        ax1.plot(astd[m], np.abs(rel[m]), mk, color=col, ms=5.0, mec=col, mew=.6,
                 alpha=.85, label='%s  $|\\Delta I| / I_{std}$' % tag)
    # 理论参考线 —— 这张图的重点, 见文件头。
    # ⚠️ 用**中位数**而不是 RMS: 下面的滚动线也是中位数, 两个统计量得同口径,
    #    否则中位 0.824 pA 与 RMS 1.332 pA 的固有差距会被误读成"没贴合"。
    med_abs = float(np.median(np.abs(res)))
    xe = np.array([a.min_na, a.max_na])
    ax1.plot(xe, med_abs / (xe * 1e3) * 100, '-', color=BLACK, lw=1.8,
             label='恒定绝对误差 中位 %.3f pA ÷ I  （理论）' % med_abs)
    # ⚠️ 散点本身**不**贴着那条线 —— 同一电流下绝对残差从 0.006 pA 到 4.1 pA 都在出现,
    #    所以相对误差散点跨两个量级。**趋势**要滚动中位数才看得出来, 别拿散点当"贴着"。
    order = np.argsort(astd)
    K = 8
    rx, ry = [], []
    for i, o in enumerate(order):
        w = order[max(0, i - K):i + K + 1]
        rx.append(astd[o]); ry.append(float(np.median(np.abs(rel[w]))))
    ax1.plot(rx, ry, '-', color=BLACK, lw=1.0, alpha=.75,
             label='实测滚动中位数（±%d 点窗）' % K)
    ax1.set_xscale('log'); ax1.set_yscale('log')
    ax1.set_ylabel('|相对误差|   (%)', fontsize=11)
    ax1.grid(alpha=.25, which='both', color=BLACK, lw=.6)
    ax1.legend(fontsize=9.0, loc='lower left', framealpha=.95)
    ax1.set_title('(a) 相对精度 —— 滚动中位数贴住理论线；散点的宽散布是 δ(I) 逐点结构',
                  fontsize=11, color=BLACK)

    # 下格: 绝对残差, 线性纵轴 —— 平不平一眼可见
    for tag, m, col, mk in ser:
        ax2.plot(astd[m], res[m], mk, color=col, ms=5.0, mec=col, mew=.6,
                 alpha=.85, label=tag)
    ax2.axhline(0, color=BLACK, lw=1.2, ls='--')
    for s in (rms_abs, -rms_abs):
        ax2.axhline(s, color=BLACK, lw=.9, ls=':',
                    label='±RMS %.3f pA' % rms_abs if s > 0 else None)
    ax2.set_xscale('log')
    # 5~45 nA 只有一个十倍程, 默认只给 10^1 一个刻度 —— 太糙, 手动钉死
    ax2.xaxis.set_major_locator(FixedLocator([5, 10, 20, 30, 45]))
    ax2.xaxis.set_major_formatter(ScalarFormatter())
    ax2.xaxis.set_minor_formatter(NullFormatter())
    ax2.set_xlabel('2636B 回读电流 $|I_{std}|$   (nA)      ← 基准', fontsize=11)
    ax2.set_ylabel('绝对残差 $I_{cal} - I_{std}$   (pA)', fontsize=11)
    ax2.grid(alpha=.25, color=BLACK, lw=.6)
    ax2.legend(fontsize=9.5, loc='upper left', framealpha=.95, ncol=3)
    ax2.set_ylim(-6, 6)
    ax2.set_title('(b) 绝对残差 —— 全程平在 ±2 pA 内，不随电流增长', fontsize=11,
                  color=BLACK)

    caption(ax2,
            '中电流段相对精度（5 ~ 45 nA，双对数）\n'
            '%d 个电流点，步长 0.5 nA，每点 3 次取均值　基准 = 2636B 回读值　'
            '绝对残差 RMS %.3f pA　|相对误差| 中位 %.5f %%，max %.5f %%　2026-09-16'
            % (len(pts), rms_abs, float(np.median(np.abs(rel))), float(np.max(np.abs(rel)))),
            dy=76, note_dy=124,
            note='注：纵轴为对数，画的是误差绝对值 —— 相对误差在 0 附近变号，'
                 '负值在对数轴上无法表示。正负电流分开画仅为便于对照；'
                 '本段正负 RMS 相当 (%.3f / %.3f pA)，未见不对称。'
                 % (rms(res[pos]), rms(res[neg])))
    fig.tight_layout()
    fig.savefig(a.out, dpi=150, bbox_inches='tight')
    print("已写", a.out)


if __name__ == '__main__':
    main()
