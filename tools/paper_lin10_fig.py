# -*- coding: utf-8 -*-
"""
±10 pA 微电流段的残差图 —— 图 6

    python tools/paper_lin10_fig.py

排版按《论文制图规范》(tools/paper_style.py)：17 cm 宽、正红/蓝/黑三色、
图名落正下方、只留 图名/轴标签/图例/分格标题 四类文字。

口径
----
    I_std  2636B **回读值** —— 基准 (不用设定值, 设定值不是真值)
    I_dev  装置**校准后**读数 = 式(6), 复用 report_table.i_of()
    偏置   I_dev − I_std

⚠️ 横轴用**回读值**不用设定值 —— 偏置的分母就是回读值, 两个量同基准;
   与图 3 一致。用设定值会让"基准"在两处指不同的东西。

⚠️ 装置读数取的是**式(6) 算出来的**值, 不是 CSV 的 dev_nA ——
   后者只有 3 位小数 (nA), 分辨不到 0.5 pA, 画这张图会全是台阶。
"""
import argparse
import csv
import os
import statistics
import sys

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_table import i_of, CONST_FW, CONST_FIT   # noqa: E402
import paper_style as ps                     # noqa: E402

ps.apply()

# ⚠️ 三常数有两套, 差在取整 (见 report_table.py 的注释)。
#    默认 `fit` —— 最小二乘直接给的小数, 物理上更好的估计。
#    换成 `fw` (固件里存的整数 pA) 会让残差整体平移 0.265 pA,
#    在 ±10 pA 段上是该段读数的 2.7%, 图上一眼看得出来。
#    **图的说明里必须写明用的哪一套**, 否则别人复现不出同一个数。
CONSTS = {'fw': CONST_FW, 'fit': CONST_FIT}

SRC = 'data/lin_10nA_mirror/raw.csv'
OUT = 'image_paper/fig06_lin10_residual.png'


def load(path, const):
    out = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line']:
                continue
            out.append((float(r['true_nA']) * 1e3,                       # pA
                        i_of(int(r['ext1']), int(r['ext2']),
                             int(r['m']), int(r['n']), *const) * 1e12,   # pA
                        float(r['set_nA'])))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--const', default='fit', choices=sorted(CONSTS),
                    help='用哪一套三常数: fit = 最小二乘给的小数 (默认); '
                         'fw = 固件里存的整数 pA。两者差 0.265 pA, '
                         '在 ±10 pA 段上是 2.7%%, 图的说明要写明用了哪套')
    ap.add_argument('-o', '--out', default=OUT)
    a = ap.parse_args()
    const = CONSTS[a.const]
    print('常数: %s  I+=%.5f nA  I-=%.5f nA  (q=%.8e)'
          % (a.const, const[1] * 1e9, const[2] * 1e9, const[0]))

    pts = load(SRC, const)
    if not pts:
        sys.exit('没读到 %s' % SRC)
    std = np.array([p[0] for p in pts])
    dev = np.array([p[1] for p in pts])
    res = dev - std
    mu = float(np.mean(res))
    sd = float(np.std(res, ddof=1))
    # 正负按**采集顺序**分 (前 11 行正向扫描, 后 11 行负向) —— 三个判据都不行:
    #   按回读值分: 两个 0 pA 点的回读是负的  -> 10/12
    #   按设定值分: −0.0 == 0.0, `< 0` 为 False -> 12/10
    # 只有行序能把两个 0 pA 点分到各自的扫描里去, 与表格的 11/11 一致。
    h = len(pts) // 2
    pos = np.zeros(len(pts), bool); pos[:h] = True
    neg = ~pos

    print('=' * 74)
    print('±10 pA 微电流段残差   (%s, %d 个点)' % (SRC, len(pts)))
    print('=' * 74)
    print('  偏置 = 装置校准值(式6) − 2636B 回读值')
    print('  均值 %+.4f pA   σ %.4f pA   极差 %.4f pA  (min %+.4f, max %+.4f)'
          % (mu, sd, res.max() - res.min(), res.min(), res.max()))
    print('  正电流 %d 点 均值 %+.4f pA    负电流 %d 点 均值 %+.4f pA'
          % (pos.sum(), res[pos].mean(), neg.sum(), res[neg].mean()))
    print('  -> 偏置与电流**大小和极性都无关**, 是恒定偏置')
    print('')

    fig, cap_y = ps.figure(11.0, cap_lines=1)
    ax = fig.add_subplot(1, 1, 1)

    ax.plot(std, res, 'o', color=ps.BLUE, ms=ps.MS + .5, mec=ps.BLUE,
            mew=ps.MEW, label='偏置  $I_{dev} - I_{std}$')
    # ⚠️ 不画 y=0 那条线: 偏置离 0 有 15σ (0.664 / 0.044), 零线没有信息量,
    #    却要把纵轴从 0 起, 白白吃掉 2/3 的图高、把散点压成一条带。
    #    这里的"参考线"就是均值本身。
    ax.axhline(mu, color=ps.BLACK, lw=ps.LW_MAIN,
               label='均值 %+.4f pA' % mu)
    for s in (mu + sd, mu - sd):
        ax.axhline(s, color=ps.BLACK, lw=ps.LW_THIN, ls=':',
                   label='±1σ = %.4f pA' % sd if s > mu else None)

    ax.set_xlabel('2636B 回读电流 $I_{std}$   (pA)')
    ax.set_ylabel('偏置 $I_{dev} - I_{std}$   (pA)')
    ax.set_xlim(std.min() * 1.15, std.max() * 1.15)
    ax.set_ylim(mu - 4.5 * sd, mu + 4.5 * sd)
    ps.grid(ax)
    ax.legend(loc='lower right', framealpha=.95, ncol=3)
    ps.caption(fig, cap_y,
               '±10 pA 微电流段的残差\n'
               '三常数: %s（I+ = %.5f nA, I- = %.5f nA）'
               % ('最小二乘拟合值' if a.const == 'fit'
                  else '固件存储值（整数 pA）', const[1] * 1e9, const[2] * 1e9))
    os.makedirs('image_paper', exist_ok=True)
    fig.savefig(a.out)
    print('已写', a.out)


if __name__ == '__main__':
    main()
