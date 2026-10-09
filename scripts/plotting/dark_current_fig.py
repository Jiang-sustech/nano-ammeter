# -*- coding: utf-8 -*-
"""
光电管暗电流 I–V 散点图 —— 挡住光，看反向漏电本底

    python scripts/plotting/dark_current_fig.py
    python scripts/plotting/dark_current_fig.py --src data/iv_577nm_dark.csv -o out.png
    python scripts/plotting/dark_current_fig.py --with-light      # 叠加有光那条做对照

数据来源: `scripts/smu/sweep_iv.py` 的输出 (2636B, A 路源电压 / B 路当电流表)。
每一轮是**往返扫描**, 所以每个电压有去程、回程两次测量 —— 两点分开画,
它们的**间距就是扫描期间的漂移**, 是判断数据可不可信的直接依据。

⚠️ 这里是**暗电流**, 不是光电流。挡光后测到的电流就是管子的反向漏电 +
电缆/夹具漏电 + 仪器本底。判断光电效应数据能不能用时, 必须先把这一条测出来:
    真实光电流 = 有光 − 挡光
"""
import argparse
import csv
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def load(path):
    """-> (fwd[(V,pA)], rev[(V,pA)])"""
    fwd, rev = [], []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            p = (float(r['v_read_V']), float(r['i_A']) * 1e12)   # A -> pA
            (fwd if r['leg'] == 'fwd' else rev).append(p)
    return fwd, rev


def main():
    ap = argparse.ArgumentParser(description="光电管暗电流 I–V 散点图")
    ap.add_argument('--src', default='data/iv_577nm_dark.csv')
    ap.add_argument('--with-light', default=None,
                    help='再给一条"有光"的数据做对照, 如 data/iv_577nm.csv')
    ap.add_argument('-o', '--out', default='data/dark_current.png')
    a = ap.parse_args()

    if not os.path.exists(a.src):
        sys.exit("没有 %s —— 先用 scripts/smu/sweep_iv.py 挡光测一轮" % a.src)
    fwd, rev = load(a.src)
    if not fwd and not rev:
        sys.exit("%s 里没有数据" % a.src)

    fig, ax = plt.subplots(figsize=(11.0, 7.4))

    if a.with_light and os.path.exists(a.with_light):
        lf, lr = load(a.with_light)
        ax.plot([p[0] for p in lf], [p[1] for p in lf], 'o', ms=5,
                mfc='none', mec='#C00000', mew=1.4, zorder=3,
                label='有光（%s）' % os.path.basename(a.with_light))

    ax.plot([p[0] for p in rev], [p[1] for p in rev], 's', ms=6.5,
            mfc='none', mec='#0040C0', mew=1.6, zorder=4,
            label='暗电流 · 回程（%d 点）' % len(rev))
    ax.plot([p[0] for p in fwd], [p[1] for p in fwd], 'o', ms=6.5,
            mfc='none', mec='#00803C', mew=1.6, zorder=5,
            label='暗电流 · 去程（%d 点）' % len(fwd))

    ax.axhline(0, color='#666666', lw=1.0)
    ax.axvline(0, color='#666666', lw=1.0)

    allp = fwd + rev
    V = [p[0] for p in allp]; I = [p[1] for p in allp]
    ax.set_xlabel('偏置电压 / V', fontsize=13)
    ax.set_ylabel('电流 / pA', fontsize=13)
    ax.set_title('光电管暗电流 I–V（挡光，577 nm 滤光片）\n'
                 '共 %d 个点　电流 %.2f ~ %.2f pA　源: %s'
                 % (len(allp), min(I), max(I), os.path.basename(a.src)),
                 fontsize=13.5, pad=14)
    ax.grid(alpha=.28, lw=.7)
    ax.tick_params(labelsize=10.5)
    ax.legend(fontsize=10.5, loc='lower left', framealpha=.95)

    # 漂移 = 同一电压去/回两次之差。这是"数据可不可信"最直接的证据
    by = {round(p[0], 6): p[1] for p in fwd}
    d = [p[1] - by[round(p[0], 6)] for p in rev if round(p[0], 6) in by]
    if d:
        mx = max(d, key=abs)
        ax.text(0.985, 0.03,
                '往返差（回程−去程）: 均值 %+.3f pA　最大 %+.3f pA' % (sum(d) / len(d), mx),
                transform=ax.transAxes, ha='right', va='bottom',
                fontsize=10, color='#333333')

    fig.savefig(a.out, dpi=150, bbox_inches='tight')
    print('已写 %s  (%d 点: 去程 %d / 回程 %d)' % (a.out, len(allp), len(fwd), len(rev)))
    if d:
        print('  往返差: 均值 %+.3f pA, 最大 %+.3f pA' % (sum(d) / len(d), max(d, key=abs)))


if __name__ == '__main__':
    main()
