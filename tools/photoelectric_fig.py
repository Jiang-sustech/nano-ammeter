# -*- coding: utf-8 -*-
"""
光电效应 I–V 曲线 —— 每个滤光片一张图

    python tools/photoelectric_fig.py

排版沿用《论文制图规范》(tools/paper_style.py)：17 cm 宽、正红/蓝/黑三色、
图名落正下方、只留 图名/轴标签/图例/分格标题。

数据: 用户 2026-09-17 直接给出 (仓库里没有光电效应的数据)。每个波长三块:
  粗扫 → 扫到正电压 → **零点附近细扫** (定遏止电压靠这块)
电流原始单位 nA, 图上画 pA (×1000)。
"""
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_style as ps                    # noqa: E402

ps.apply()          # ⚠️ 漏了这行则 font.sans-serif 退回 DejaVu, 汉字全变方框

H = 6.62607015e-34
C = 2.99792458e8
E = 1.602176634e-19
OUTDIR = 'image_paper'

# (V, I/nA) —— 原样录入, 不做任何"修正"
DATA = {
    546.0: dict(
        num=7, fig='fig07_photoelectric_546nm.png',
        coarse=[(-1.103, -0.029), (-1.000, -0.028), (-0.902, -0.028),
                (-0.800, -0.027), (-0.700, -0.024), (-0.601, -0.011),
                (-0.500, 0.030), (-0.400, 0.107), (-0.300, 0.204),
                (-0.200, 0.313), (-0.100, 0.426)],
        mid=[(-0.040, 0.475), (-0.031, 0.485), (-0.020, 0.495),
             (-0.012, 0.506), (0.010, 0.522)],
        fine=[(-0.590, -0.009), (-0.580, -0.0063145), (-0.570, -0.0030147),
              (-0.560, 0.0007745), (-0.550, 0.0048873)],
        zoom=(-0.60, -0.54),
    ),
    436.0: dict(
        num=9, fig='fig09_photoelectric_436nm.png',
        coarse=[(-1.997, -0.20), (-1.899, -0.20), (-1.800, -0.20),
                (-1.700, -0.20), (-1.601, -0.20), (-1.498, -0.19),
                (-1.401, -0.18), (-1.301, -0.17), (-1.200, -0.15),
                (-1.100, -0.06935), (-0.900, 0.32), (-0.800, 0.61),
                (-0.700, 0.96), (-0.600, 1.32), (-0.500, 1.70),
                (-0.400, 2.10), (-0.300, 2.49), (-0.200, 2.96),
                (-0.101, 3.39), (0.001, 3.77)],
        mid=[],
        # ⚠️ −1.051 破了 0.01 的网格 (其余是 −1.040/−1.060), 疑为 −1.050 之误。
        #    按原值录入, 没改。
        fine=[(-1.000, 0.08752), (-1.010, 0.0675), (-1.020, 0.04742),
              (-1.030, 0.02986), (-1.040, 0.01387), (-1.051, -0.00328),
              (-1.060, -0.01740), (-1.070, -0.03088), (-1.080, -0.04354),
              (-1.090, -0.05664)],
        zoom=(-1.10, -0.98),
    ),
    365.0: dict(
        num=8, fig='fig08_photoelectric_365nm.png',
        coarse=[(-1.949, -0.22), (-1.900, -0.22), (-1.799, -0.21),
                (-1.700, -0.17), (-1.600, -0.10), (-1.500, 0.01997),
                (-1.402, 0.17), (-1.300, 0.39), (-1.200, 0.62),
                (-1.100, 0.90), (-0.999, 1.19), (-0.900, 1.53),
                (-0.801, 1.86), (-0.700, 2.21), (-0.600, 2.61),
                (-0.500, 2.99), (-0.400, 3.39), (-0.300, 3.78),
                (-0.199, 4.23), (-0.100, 4.68), (0.001, 5.12)],
        mid=[],
        fine=[(-1.590, -0.08817), (-1.580, -0.07637), (-1.570, -0.06623),
              (-1.560, -0.05548), (-1.550, -0.04391), (-1.540, -0.03220),
              (-1.530, -0.02000), (-1.520, -0.00735), (-1.511, 0.00536)],
        zoom=(-1.62, -1.45),
    ),
}


def fit_vs(fine):
    """零点附近细扫 -> (Vs, σ_Vs, 斜率, 截距, 残差σ)。伏安线性段的 I=0 截距。"""
    v = np.array([p[0] for p in fine])
    i = np.array([p[1] for p in fine]) * 1e3            # nA -> pA
    k, b = np.polyfit(v, i, 1)
    r = i - (k * v + b)
    n = len(v)
    s = float(np.sqrt(np.sum(r ** 2) / (n - 2)))
    sx = float(np.sqrt(np.sum((v - v.mean()) ** 2)))
    db = s * float(np.sqrt(1.0 / n + v.mean() ** 2 / sx ** 2))
    dk = s / sx
    vs = -b / k
    dvs = float(np.sqrt((db / k) ** 2 + (b * dk / k ** 2) ** 2))
    return abs(vs), dvs, k, b, s


def plateau(pts):
    """反向电流平台 —— **统一规则**, 不逐波长手挑:

    取最负端开始、|I − I_首| ≤ tol 的最长前缀, tol = max(2 pA, 5%·|I_首|)。
    泄漏电流随 |V| 变, 所以平台是"一段"不是一个数, 这里报它的均值。
    """
    # ⚠️ 一律换算成 **pA** —— 全图其余部分都用 pA。曾经这里留在 nA,
    #    于是 tol 变成 max(2.0 nA, ...) = 2000 pA, 平台吞掉整条曲线;
    #    而且返回的 ir 与 pA 制的拟合线混用, 交点法直接退化成 I=0 法。
    v = np.array([p[0] for p in pts])
    i = np.array([p[1] for p in pts]) * 1e3            # nA -> pA
    o = np.argsort(v); v, i = v[o], i[o]
    tol = max(2.0, 0.05 * abs(i[0]))
    n = 1
    while n < len(i) and abs(i[n] - i[0]) <= tol:
        n += 1
    return float(i[:n].mean()), n


def fit_inter(pts, fine):
    """交点法: 平台水平线与线性段直线的交点。

    ⚠️ 线性段必须取**刚过拐点**那段, 不能取远端最陡那段 —— 光电流曲线是**凸的**
    (546 nm 的局部斜率从拐点 315 pA/V 一路涨到远端 1130 pA/V), 远端那段直线
    不是穿过拐点的那条线, 外推下来交点必然偏小 (实测 h 会从 −6.7% 掉到 −25%)。

    这里线性段取**细扫段** —— 它是实验者自己判断"这段是直的"才加密扫的。
    返回 (Vs, k, b, Ir, n_plateau)。
    """
    ir, np_ = plateau(pts)
    v = np.array([p[0] for p in fine])
    i = np.array([p[1] for p in fine]) * 1e3
    k, b = np.polyfit(v, i, 1)
    return abs((ir - b) / k), k, b, ir, np_


def draw(lam, d):
    pts = d['coarse'] + d['mid'] + d['fine']
    V = np.array([p[0] for p in pts])
    I = np.array([p[1] for p in pts]) * 1e3
    vs, dvs, k, b, s = fit_vs(d['fine'])
    nu = C / (lam * 1e-9)
    hv = H * nu / E

    print('─' * 70)
    print('%.1f nm   %d 点' % (lam, len(pts)))
    print('  遏止电压    Vs = %.4f ± %.4f V   (斜率 %.1f pA/V, 残差 σ %.2f pA)'
          % (vs, dvs, k, s))
    print('  光子能量    hν = %.4f eV' % hv)
    print('  逸出功      W  = %.4f eV   (= hν − eVs, 用了已知 h, 方向是反的)'
          % (hv - vs))

    fig, cap_y = ps.figure(15.0, cap_lines=1, title=True)
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)
    fig.subplots_adjust(hspace=0.30)

    # (a) 全曲线 —— 点 + 平滑引导线
    # 用 **PCHIP 单调三次插值**而不是普通 CubicSpline: 高遏止电压那段几乎不平,
    # 普通样条会在那里过冲、抖出实际不存在的假起伏。
    # ⚠️ 这是**引导线不是拟合** —— 只把点连起来, 背后没有物理模型,
    #    中间没有数据的地方不代表测到了。图例里必须这么标。
    o = np.argsort(V)
    xs, ys = V[o], I[o]
    if np.any(np.diff(xs) == 0):              # 同电压多点先合并, 否则插值报错
        ux, uy = [], []
        for x in np.unique(xs):
            ux.append(x); uy.append(float(np.mean(ys[xs == x])))
        xs, ys = np.array(ux), np.array(uy)
    xd = np.linspace(xs.min(), xs.max(), 900)
    ax1.plot(xd, PchipInterpolator(xs, ys)(xd), '-', color=ps.BLUE,
             lw=ps.LW_THIN, label='平滑引导线（PCHIP 插值）')
    ax1.plot(V, I, 'o', color=ps.BLUE, ms=ps.MS + .5, mec=ps.BLUE, mew=ps.MEW,
             label='实测（%d 点）' % len(V))
    ax1.axhline(0, color=ps.BLACK, lw=ps.LW_THIN, ls='--')
    ax1.axvline(-vs, color=ps.RED, lw=ps.LW_MAIN, ls='--',
                label='遏止电压 %.3f V' % vs)
    ax1.set_ylabel('光电流   (pA)')
    ax1.set_xlabel('电压   (V)')
    ps.grid(ax1)
    ax1.legend(loc='lower right', framealpha=.95)
    ax1.set_title('(a) 全曲线　%.0f nm' % lam, color=ps.BLACK)

    # (b) 零点附近放大
    fv = np.array([p[0] for p in d['fine']])
    fi = np.array([p[1] for p in d['fine']]) * 1e3
    ax2.plot(fv, fi, 'o', color=ps.BLUE, ms=ps.MS + 1, mec=ps.BLUE, mew=ps.MEW,
             label='零点附近细扫（%d 点）' % len(fv))
    xe = np.array(d['zoom'])
    ax2.plot(xe, k * xe + b, '-', color=ps.BLACK, lw=ps.LW_MAIN,
             label='线性拟合  截距 %.3f V' % -vs)
    ax2.axhline(0, color=ps.BLACK, lw=ps.LW_THIN, ls='--')
    # ⚠️ 标签别写成 '$I$ = 0 交点': mathtext 段之后的汉字会掉到 DejaVu 里,
    #    渲染成方框 (matplotlib 的已知行为)。汉字放前面就没事。
    ax2.plot([-vs], [0], 'o', color=ps.RED, ms=ps.MS + 4, mec=ps.BLACK,
             mew=0.9, zorder=5, label='交点（$I=0$）')
    ax2.set_xlabel('电压   (V)')
    ax2.set_ylabel('光电流   (pA)')
    ax2.set_xlim(*d['zoom'])
    ps.grid(ax2)
    ax2.legend(loc='lower right', framealpha=.95)
    ax2.set_title('(b) 零点附近放大', color=ps.BLACK)

    ps.caption(fig, cap_y, '光电效应 I–V 曲线（%.0f nm）' % lam)
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, d['fig'])
    fig.savefig(p)
    plt.close(fig)
    print('  已写', p)
    return vs, dvs, nu, hv


def draw_inter(lam, d):
    """交点法版本 —— 与原图**并存**, 不覆盖。文件名加 _inter。"""
    pts = d['coarse'] + d['mid'] + d['fine']
    V = np.array([p[0] for p in pts])
    I = np.array([p[1] for p in pts]) * 1e3
    vs, k, b, ir, np_ = fit_inter(pts, d['fine'])
    nu = C / (lam * 1e-9)
    hv = H * nu / E
    print('  [交点法] 平台 %.1f pA (%d 点)   线性段斜率 %.0f pA/V'
          % (ir, np_, k))
    print('            Vs = %.4f V   (I=0 法给 %.4f V, 差 %+.4f)'
          % (vs, fit_vs(d['fine'])[0], vs - fit_vs(d['fine'])[0]))

    fig, cap_y = ps.figure(15.0, cap_lines=1, title=True)
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)
    fig.subplots_adjust(hspace=0.30)

    o = np.argsort(V); xs, ys = V[o], I[o]
    if np.any(np.diff(xs) == 0):
        ux, uy = [], []
        for x in np.unique(xs):
            ux.append(x); uy.append(float(np.mean(ys[xs == x])))
        xs, ys = np.array(ux), np.array(uy)
    xd = np.linspace(xs.min(), xs.max(), 900)
    ax1.plot(xd, PchipInterpolator(xs, ys)(xd), '-', color=ps.BLUE,
             lw=ps.LW_THIN, label='平滑引导线（PCHIP 插值）')
    ax1.plot(V, I, 'o', color=ps.BLUE, ms=ps.MS + .5, mec=ps.BLUE, mew=ps.MEW,
             label='实测（%d 点）' % len(V))
    ax1.axhline(ir, color=ps.RED, lw=ps.LW_MAIN, ls='--',
                label='反向电流平台 %.1f pA' % ir)
    ax1.axhline(0, color=ps.BLACK, lw=ps.LW_THIN, ls='--')
    ax1.axvline(-vs, color=ps.RED, lw=ps.LW_MAIN, ls='--',
                label='交点 %.3f V' % vs)
    ax1.set_ylabel('光电流   (pA)')
    ax1.set_xlabel('电压   (V)')
    ps.grid(ax1)
    ax1.legend(loc='lower right', framealpha=.95)
    ax1.set_title('(a) 全曲线　%.0f nm' % lam, color=ps.BLACK)

    # (b) 构造放大: 平台线 + 线性段延长 + 交点
    fv = np.array([p[0] for p in d['fine']])
    fi = np.array([p[1] for p in d['fine']]) * 1e3
    lo = np.sort(V)[0]
    lo = V[np.argsort(V)][np_ - 1] - 0.05          # 平台末点再往左一点
    hi = fv.max() + 0.04
    ax2.plot(fv, fi, 'o', color=ps.BLUE, ms=ps.MS + 1, mec=ps.BLUE, mew=ps.MEW,
             label='零点附近细扫（%d 点）' % len(fv))
    axe = np.array([lo, hi])
    ax2.plot(axe, k * axe + b, '-', color=ps.BLACK, lw=ps.LW_MAIN,
             label='线性段拟合（延长）')
    ax2.axhline(ir, color=ps.RED, lw=ps.LW_MAIN, ls='--',
                label='平台 %.1f pA' % ir)
    ax2.axhline(0, color=ps.BLACK, lw=ps.LW_THIN, ls='--')
    ax2.plot([-vs], [ir], 'o', color=ps.RED, ms=ps.MS + 4, mec=ps.BLACK,
             mew=0.9, zorder=5, label='交点（$I = I_r$）')
    ax2.set_xlabel('电压   (V)')
    ax2.set_ylabel('光电流   (pA)')
    ax2.set_xlim(lo, hi)
    ps.grid(ax2)
    ax2.legend(loc='lower right', framealpha=.95)
    ax2.set_title('(b) 交点法构造放大', color=ps.BLACK)

    ps.caption(fig, cap_y, '光电效应 I–V 曲线（%.0f nm，交点法）' % lam)
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, d['fig'].replace('.png', '_inter.png'))
    fig.savefig(p)
    plt.close(fig)
    print('  已写', p)
    return vs, nu, hv, ir, k


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    res = {}
    for lam in sorted(DATA, reverse=True):
        res[lam] = draw(lam, DATA[lam])

    print('\n' + '=' * 70)
    print('交点法（与原图并存，不覆盖）')
    print('=' * 70)
    inter = {}
    for lam in sorted(DATA, reverse=True):
        inter[lam] = draw_inter(lam, DATA[lam])

    if len(res) < 2:
        print('\n只有 1 个波长, 算不出 h。')
        return

    print('=' * 70)
    print('普朗克常数 (%d 个波长)' % len(res))
    print('=' * 70)
    lams = sorted(res)
    for lam in lams:
        vs, dvs, nu, hv = res[lam]
        print('  %.0f nm   ν=%.5e Hz   Vs=%.4f±%.4f V   W=%.4f eV'
              % (lam, nu, vs, dvs, hv - vs))
    nu = np.array([res[l][2] for l in lams])
    vs = np.array([res[l][0] for l in lams])
    dvs = np.array([res[l][1] for l in lams])
    w = 1.0 / dvs ** 2
    a, b = np.polyfit(nu, vs, 1, w=w)          # 加权: 各点 σ_Vs 不同
    yhat = a * nu + b
    chi2 = float(np.sum(w * (vs - yhat) ** 2))
    dof = len(nu) - 2
    S = float(np.sum(w))
    Sxx = float(np.sum(w * nu ** 2) - np.sum(w * nu) ** 2 / S)
    if dof > 0:
        sa = float(np.sqrt(chi2 / dof / Sxx))
    else:
        sa = float('nan')
    h = a * E
    print('')
    print('  加权直线拟合 Vs = a·ν + b     (%d 点, %d 自由度)' % (len(nu), dof))
    print('    a = %.4e ± %.4e V·s      b = %+.4f V' % (a, sa, b))
    if dof > 0:
        print('    χ²/dof = %.3f' % (chi2 / dof))
    print('')
    print('  ** h = a·e = %.4e ± %.4e J·s **' % (h, sa * E))
    print('     真值 6.62607015e-34  ->  相对偏差 %+.2f %% ± %.2f %%'
          % ((h - H) / H * 100, sa * E / H * 100))
    if dof == 0:
        print('  ⚠️ **2 点 = 0 自由度**, 斜率标准误算不出来, 上面这个 h 没有误差。')

    wl = [(res[l][3] - res[l][0]) for l in lams]
    print('')
    print('  逸出功一致性:  %s   极差 %.4f eV'
          % ('  '.join('W(%.0f)=%.4f' % (l, res[l][3] - res[l][0]) for l in lams),
             max(wl) - min(wl)))
    print('  ⚠️ 接触电势差只平移截距、**不改斜率** -> W 应与波长无关;')
    print('     这里不一致就说明存在**与波长有关**的系统误差, CPD 解释不了。')
    if len(lams) >= 3:
        seg = [(vs[i + 1] - vs[i]) / (nu[i + 1] - nu[i]) for i in range(len(nu) - 1)]
        print('')
        print('  直线性检验 (相邻两点斜率):')
        for i, s in enumerate(seg):
            print('    %.0f→%.0f nm   %.5e V·s' % (lams[i], lams[i + 1], s))
        print('    两段只差 %.2f %%  -> **几乎完全共线**'
              % (abs(seg[0] / seg[-1] - 1) * 100))
        print('    共线好而斜率偏 -> 偏差是**尺度/系统**问题, 不是散点。')
        print('    **再多测波长也修不了**, 要改的是定 Vs 的方法或电压标定。')

    einstein_plot(lams, nu, vs, dvs, a, b, h, sa * E)

    # ---- 交点法的爱因斯坦图 (并存) ----
    print('\n' + '=' * 70)
    print('交点法的 h')
    print('=' * 70)
    vi = np.array([inter[l][0] for l in lams])
    for l in lams:
        print('  %.0f nm   Vs=%.4f V (I=0: %.4f)   W=%.4f eV'
              % (l, inter[l][0], res[l][0], inter[l][2] - inter[l][0]))
    # 交点法没有逐点的 σ_Vs (平台/线性段都是选的), 用等权拟合, 误差按两段斜率差估
    ai, bi = np.polyfit(nu, vi, 1)
    hi_ = ai * E
    segi = [(vi[i + 1] - vi[i]) / (nu[i + 1] - nu[i]) for i in range(len(nu) - 1)]
    dai = float(np.std(segi) / np.sqrt(len(segi))) if len(segi) > 1 else float('nan')
    print('')
    print('  等权拟合 a = %.4e V·s   ->   ** h = %.4e J·s **' % (ai, hi_))
    print('     相对偏差 %+.2f %%' % ((hi_ - H) / H * 100))
    print('  ⚠️ 相邻段斜率散布 ±%.2e (%.1f%%) -> σ_h ≈ %.2e, 比 I=0 法大得多;'
          % (dai, dai / ai * 100, dai * E))
    print('     因为交点法没有逐点的误差估计, 误差只能从这种散布里估。')
    print('  逸出功: %s   极差 %.4f eV'
          % ('  '.join('W(%.0f)=%.4f' % (l, inter[l][2] - inter[l][0]) for l in lams),
             max(inter[l][2] - inter[l][0] for l in lams)
             - min(inter[l][2] - inter[l][0] for l in lams)))
    einstein_plot(lams, nu, vi, np.full(len(lams), 0.03), ai, bi, hi_, float('nan'),
                  fname='fig11_einstein_plot_inter.png', suffix='，交点法')


def einstein_plot(lams, nu, vs, dvs, a, b, h, dh,
                  fname='fig10_einstein_plot.png', suffix=''):
    """Vs–ν 图 (爱因斯坦直线) —— 斜率就是 h/e。"""
    # cap_lines=2: 图名是两行 (第二行报 h), 按一行留边距会压住横轴标签
    fig, cap_y = ps.figure(13.0, cap_lines=2)
    ax = fig.add_subplot(1, 1, 1)
    x = nu / 1e14
    ax.errorbar(x, vs, yerr=dvs, fmt='o', color=ps.BLUE, ms=ps.MS + 2,
                mec=ps.BLUE, mew=ps.MEW, ecolor=ps.BLUE, elinewidth=1.0,
                capsize=3, label='实测 $V_s$（±1σ）')
    xe = np.array([x.min(), x.max()]) * np.array([0.85, 1.15])
    ax.plot(xe, a * xe * 1e14 + b, '-', color=ps.BLACK, lw=ps.LW_MAIN,
            label='加权拟合  斜率 %.4e V·s' % a)
    ax.set_xlabel('频率  $\\nu$   ($10^{14}$ Hz)')
    ax.set_ylabel('遏止电压  $V_s$   (V)')
    ps.grid(ax)
    ax.legend(loc='lower right', framealpha=.95)
    ps.caption(fig, cap_y,
               '遏止电压与频率的关系（爱因斯坦直线）%s\n'
               '$h$ = %.4e ± %.4e J·s，相对偏差 %+.2f %%'
               % (suffix, h, dh, (h - H) / H * 100))
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, fname)
    fig.savefig(p)
    plt.close(fig)
    print('\n  已写', p)


if __name__ == '__main__':
    main()
