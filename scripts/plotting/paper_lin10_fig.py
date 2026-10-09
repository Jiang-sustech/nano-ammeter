# -*- coding: utf-8 -*-
"""
±10 pA 微电流段的残差图 —— 图 6

    python scripts/plotting/paper_lin10_fig.py

排版按《论文制图规范》(scripts/lib/paper_style.py)：17 cm 宽、正红/蓝/黑三色、
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
# 脚本按用途分家后, 让本目录 + scripts 根 + 各子目录(lib/smu/plotting/analysis/misc)都在 import 路径上
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ('lib', 'smu', 'plotting', 'analysis', 'misc'):
    sys.path.insert(0, os.path.join(_ROOT, _sub))
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
    values(const)
    relerror(const)


def values(const, src=SRC):
    """±10 pA 段: 装置读数 vs 回读值 —— **只有散点与理想直线**。

    ⚠️ 这张图**不承担量化结论**, 只作直觉引入。±10 pA 量程下 0.4 pA 的偏置
    仅占纵轴 4%, 任何绝对参考线 (含 ±0.4 pA 的平行线) 都会紧贴 y=x、看不出来;
    而 ±5 % 楔形带是**比例**判据, 与本装置**恒定绝对偏置**的误差结构不匹配,
    读者看着点"漏出"带外也看不出为什么。量化请见图 06(绝对残差) / 06c(相对偏差)。
    """
    pts = load(src, const)
    std = np.array([p[0] for p in pts])             # pA, 带符号
    dev = np.array([p[1] for p in pts])

    fig, cap_y = ps.figure(12.0, cap_lines=2)
    ax = fig.add_subplot(1, 1, 1)

    lim = np.array([std.min(), std.max()]) * 1.15
    xe = np.array([lim[0], lim[1]])
    ax.plot(xe, xe, '-', color=ps.BLACK, lw=ps.LW_MAIN,
            label='理想  $I_{dev} = I_{std}$')
    ax.plot(std, dev, 'o', color=ps.BLUE, ms=ps.MS + .5, mec=ps.BLUE,
            mew=ps.MEW, ls='none', label='实测（%d 点）' % len(pts))

    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel('2636B 回读电流 $I_{std}$   (pA)')
    ax.set_ylabel('装置读数 $I_{dev}$   (pA)')
    ps.grid(ax)
    # 右下角 —— 与全图册统一。数据在对角线上, 右下角本来就是空的。
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y,
               '±10 pA 段：装置读数与回读值\n'
               '三常数: %s'
               % ('最小二乘拟合值' if const is CONST_FIT
                  else '固件存储值（整数 pA）'))
    os.makedirs('image_paper', exist_ok=True)
    p = 'image_paper/fig06b_lin10_values.png'
    fig.savefig(p)
    print('已写', p)


def relerror(const, src=SRC):
    """±10 pA 段的**相对**偏差 + 5 % 水平判据线 —— 这张才承担检测下限的结论。

    ⚠️ 5 % 线画成**水平线**, 不画成 y = 1.05x / 0.95x 的楔形: 判据本身是
    "相对偏差 ≤ 5 %", 水平线才是它的直接表示, 交点位置可直接读。楔形带会
    让读者以为装置有比例误差, 而它实际是恒定绝对偏置。
    """
    pts = load(src, const)
    std = np.array([p[0] for p in pts])
    dev = np.array([p[1] for p in pts])
    # ⚠️ 剔掉两个 0 pA 点: 它们的回读只有 ~0.1 pA, 分母趋零, 相对偏差冲到 400 %
    #    以上 —— 但**相对判据在零电流处本就没有定义** (0.4 pA 偏置除以 0.1 pA),
    #    那不是装置"差", 是判据不适用。留着会把线性纵轴拉到 450 %, 其余点全压平。
    #    阈值取 0.5 pA 而不是 1 pA: 设定 +1 pA 那点的回读只有 0.847 pA,
    #    卡 1 pA 会把它一起排掉, 正负就不对称了。两个零点 |回读| ≈ 0.10。
    keep = np.abs(std) >= 0.5
    std, dev = std[keep], dev[keep]
    rel = np.abs((dev - std) / std * 100.0)
    mag = np.abs(std)
    mu = float(np.mean(dev - std))
    print('  图 06c: 用了 %d 点 (剔除 |回读| < 0.5 pA 的 %d 个零点)'
          % (len(std), int((~keep).sum())))

    fig, cap_y = ps.figure(12.0, cap_lines=2)
    ax = fig.add_subplot(1, 1, 1)

    ax.plot(mag, rel, 'o', color=ps.BLUE, ms=ps.MS + .5, mec=ps.BLUE,
            mew=ps.MEW, ls='none', label='相对偏差（%d 点）' % len(std))
    ax.axhline(5.0, color=ps.RED, lw=ps.LW_MAIN, ls='--', label='5 % 判据')

    ax.set_xscale('log')
    ax.set_xlim(mag.min() * 0.9, mag.max() * 1.15)
    # 纵轴下探到 0 以下: 右下角要放图例, 而 5~10 pA 那几点正好落在 4~6 %,
    # 不腾地方图例会盖住**最要紧的那几个点**(它们就在 5 % 判据线附近)。
    # 代价: 轴上一段负的 % —— 只是范围取值, 不代表数据。
    ax.set_ylim(-max(rel.max() * 0.22, 4.5), max(rel.max() * 1.08, 6.0))
    ax.set_xlabel('2636B 回读电流 $|I_{std}|$   (pA)')
    ax.set_ylabel('相对偏差 $|I_{dev} - I_{std}| / |I_{std}|$   (%)')
    ps.grid(ax)
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y,
               '±10 pA 段的相对偏差（仅 |$I_{std}$| ≥ 0.5 pA）\n'
               '绝对偏置均值 %+.4f pA　三常数: %s'
               % (mu, '最小二乘拟合值' if const is CONST_FIT
                  else '固件存储值（整数 pA）'))
    os.makedirs('image_paper', exist_ok=True)
    p = 'image_paper/fig06c_lin10_relerror.png'
    fig.savefig(p)
    print('已写', p)
    return mu


if __name__ == '__main__':
    main()
