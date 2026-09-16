# -*- coding: utf-8 -*-
"""
密集网格的交付报告 (1-5 项)

    python tools/dense_report.py
    python tools/dense_report.py --dir data/dense -o data/dense_report.png

产出
----
1. **三常数** (要烧进固件的那组)
2. **准确度** (未校准, 相对回读值的误差 —— 主要看是不是纯增益)
3. **线性度** (校准后残差: max|·|/FS 与 RMS 两个口径)
4. **常数的标准误** —— OLS 与**聚类稳健**并排。要不要聚类, 由数据自己说:
   先算残差在**电流方向**上的自相关; ρ≈0 就说明 0.5 nA 的密网格没有带来
   额外相关, OLS 的标准误本来就对; ρ>0 才需要修正。
5. **δ(I) 的形状** —— 这轮真正的目的: 那条"逐点结构"长什么样、平不平滑

口径 (2026-09-16 用户/老师裁定)
--------------------------------
**不再分标定组/验证组** —— 全部点都用来拟合, 报**样本内**的线性度。
所以脚本不再给 LOO。但要记住两条:
  * 加数据不会让残差变小 —— 三常数只有 3 个自由参数, 落在
    span{Δc, 1, w} 之外的误差一个都吃不到;
  * max|残差| 由**单个最差的点**决定, 所以 RMS 一起报 (两者能差 2~3 倍)。
"""
import argparse
import collections
import csv
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import VREF, T_INT, consts

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ---------------- 3x3 线性代数 (只解 3 阶, 不引 numpy 之外的东西) ----------
def mat3_inv(M):
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]
    det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
    if det == 0:
        raise ZeroDivisionError("3x3 奇异")
    return [[(e*i - f*h)/det, (c*h - b*i)/det, (b*f - c*e)/det],
            [(f*g - d*i)/det, (a*i - c*g)/det, (c*d - a*f)/det],
            [(d*h - e*g)/det, (b*g - a*h)/det, (a*e - b*d)/det]]


def load_dense(d, tol_pct=1.0, skipped=None):
    """<d>/raw.csv -> [(set_nA, I_true_A, dev_nA, a1, a2), ...]

    **从 CSV 读, 不从 npz 读** —— npz 只存了波形和端点码, 没有 dev_nA;
    CSV 有全部列, 而且是"每行立刻落盘"的那份权威记录。
    """
    out = []
    if skipped is None:
        skipped = []
    p = os.path.join(d, 'raw.csv')
    with open(p, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            try:
                m, n = float(r['m']), float(r['n'])
                c1, c2 = float(r['ext1']), float(r['ext2'])
                It = float(r['true_nA']) * 1e-9
                dev = float(r['dev_nA'])
                sp = float(r['set_nA'])
            except (KeyError, ValueError):
                continue
            if m <= 0 or n <= 0 or c1 < 0:
                continue
            # ⚠️ 数据质量闸门 —— 2026-09-16 实测: 429 次里有 1 次
            # (14.5 nA r1) 源表还没到设定值(回读 9.49 而设定 14.5, 偏 34.5%),
            # 装置也只读到 7.43。**这一个点就制造了 2126 pA 的假 max**,
            # 把整份报告带偏 (RMS 从 ~1.5 pA 变成 108 pA)。
            # 正常偏差中位 0.022% / 99 分位 0.045%, 闸门设 1% 有 20 倍余量。
            if abs(sp) > 1e-9 and abs(It * 1e9 - sp) / abs(sp) * 100 > tol_pct:
                skipped.append((sp, It * 1e9, dev))
                continue
            nt = m + n
            a1 = VREF * (c2 - c1) / 65536.0 / (nt * T_INT)
            out.append((sp, It, dev, a1, m / nt))
    return out


def ols(A, y):
    k = 3
    AtA = [[sum(A[i][p] * A[i][q] for i in range(len(A))) for q in range(k)]
           for p in range(k)]
    Aty = [sum(A[i][p] * y[i] for i in range(len(A))) for p in range(k)]
    AtA_inv = mat3_inv(AtA)
    return [sum(AtA_inv[p][q] * Aty[q] for q in range(k)) for p in range(k)], AtA_inv


def cluster_cov(A, u, AtA_inv, groups):
    """Liang-Zeger 聚类稳健协方差。groups: [[行下标], ...]"""
    k = 3
    meat = [[0.0] * k for _ in range(k)]
    for g in groups:
        s = [sum(A[i][p] * u[i] for i in g) for p in range(k)]
        for p in range(k):
            for q in range(k):
                meat[p][q] += s[p] * s[q]
    return [[sum(AtA_inv[p][a] * meat[a][b] * AtA_inv[b][q]
                 for a in range(k) for b in range(k)) for q in range(k)]
            for p in range(k)]


def fit_clipped(A, y, groups, k=5.0, iters=4):
    """OLS + **σ 裁剪** —— 迭代剔除 |残差| > kσ 的测量。

    为什么必须有: 2026-09-16 的密集扫描里 432 次有 **2 次坏点**, 而且是
    **两种不同成因**, 固定闸门抓不全:
        14.5 nA r1  源侧 —— 回读 9.49 而设定 14.5 (偏 34.5%), 源还没整定完
        15.5 nA r3  装置侧 —— 回读正常, 但装置读 +16.302 (偏 +5.2%, 正常是 +1.04%)
    两个都不滤的话, max|残差| 从 ~4 pA 变成 621 pA, **整份报告被 2 个点带偏**。
    σ 裁剪对两类都有效, 且不需要预先知道是哪一类。

    返回 (x, AtA_inv, keep_rows, keep_groups, n_dropped)
    """
    keep = list(range(len(A)))
    dropped = []
    for _ in range(iters):
        x, inv = ols([A[i] for i in keep], [y[i] for i in keep])
        u = [y[i] - sum(A[i][p] * x[p] for p in range(3)) for i in range(len(A))]
        s = math.sqrt(sum(u[i] * u[i] for i in keep) / max(len(keep) - 3, 1))
        bad = [i for i in keep if abs(u[i]) > k * s]
        if not bad:
            break
        dropped += bad
        keep = [i for i in keep if i not in set(bad)]
    x, inv = ols([A[i] for i in keep], [y[i] for i in keep])
    # ⚠️ 返回的 groups 用的是**过滤后的新位置**, 不是原始行号 ——
    #    调用方之后用 A/y/E 都是按新位置索引的, 混用会 IndexError。
    gmap = {i: gi for gi, g in enumerate(groups) for i in g}
    pos = {i: p for p, i in enumerate(keep)}
    newg = collections.OrderedDict()
    for i in keep:
        newg.setdefault(gmap[i], []).append(pos[i])
    return x, inv, keep, list(newg.values()), dropped


def autocorr(v, lag):
    m = sum(v) / len(v)
    x = [t - m for t in v]
    num = sum(x[i] * x[i + lag] for i in range(len(x) - lag))
    den = sum(t * t for t in x)
    return num / den if den else 0.0


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/dense')
    ap.add_argument('--fs-na', type=float, default=90.0)
    ap.add_argument('--cluster-na', type=float, default=2.0,
                    help='聚类宽度 (nA): 电流相差在这个以内的点归为一簇')
    ap.add_argument('--tol-pct', type=float, default=1.0,
                    help='数据质量闸门: |回读−设定|/设定 超过这个 %% 的测量丢弃')
    ap.add_argument('-o', '--out', default='data/dense_residual.png',
                    help='图1: δ(I) 残差 vs 电流')
    ap.add_argument('--out-ac', default='data/dense_autocorr.png',
                    help='图2: 残差在电流方向上的自相关')
    a = ap.parse_args()

    skipped = []
    raw = load_dense(a.dir, a.tol_pct, skipped)
    if not raw:
        sys.exit("没找到 %s/*.npz" % a.dir)

    # 按电流点聚合 (3 次)
    g = collections.OrderedDict()
    for sp, It, dev, a1, a2 in raw:
        g.setdefault(round(sp, 4), []).append((It, dev, a1, a2))
    keys = sorted(g, key=lambda k: (k < 0, abs(k)))     # 先正后负, 各按大小

    A, y, groups = [], [], []
    for k in keys:
        v = g[k]
        idx = []
        for It, dev, a1, a2 in v:
            idx.append(len(A))
            A.append([a1, 1.0, a2])
            y.append(It)
        groups.append(idx)                  # 先按"点"聚一次
    n, npt = len(A), len(keys)
    fs = a.fs_na * 1e-9

    n_raw = n
    x, AtA_inv, keep, groups, dropped = fit_clipped(A, y, groups)
    A = [A[i] for i in keep]
    y = [y[i] for i in keep]
    n = len(A)
    q, i_pos, i_neg = consts(x)
    u = [y[i] - sum(A[i][p] * x[p] for p in range(3)) for i in range(n)]

    print("=" * 80)
    print("密集网格交付报告   (%s, %d 个电流点, %d 次测量)" % (a.dir, npt, n))
    if skipped:
        print("  ⚠️ 闸门①源侧 (|回读−设定|>%.1f%%) 丢弃 %d 次:" % (a.tol_pct, len(skipped)))
        for sp, t, d in skipped:
            print("      设定 %+g nA, 回读 %+.4f nA (偏 %.1f%%), 装置 %+.4f nA"
                  % (sp, t, abs(t - sp) / abs(sp) * 100, d))
    if dropped:
        print("  ⚠️ 闸门②σ裁剪 (>5σ) 丢弃 %d 次:" % len(dropped))
        for i in dropped:
            print("      设定 %+g nA, 回读 %+.4f nA, 装置 %+.4f nA"
                  % (raw[i][0], raw[i][1] * 1e9, raw[i][2]))
    print("")
    print("  净用 %d / %d 次测量" % (n, n_raw))
    print("=" * 80)
    print("")

    # ---------------- 1. 三常数 ----------------
    print("【1】三常数 (由**全部 %d 点**拟合 —— 按新口径, 不再分标定/验证组)" % npt)
    print("      q  = %.6e C/码" % q)
    print("      I+ = %+.4f nA" % (i_pos * 1e9))
    print("      I- = %+.4f nA" % (i_neg * 1e9))
    print("")

    # ---------------- 2. 准确度 (未校准) ----------------
    rel = []
    for k in keys:
        for It, dev, _, _ in g[k]:
            if abs(It) > 1e-12:
                rel.append((dev * 1e-9 - It) / It * 100)
    mu = sum(rel) / len(rel)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rel) / (len(rel) - 1))
    print("【2】准确度 (未校准) —— 装置原始读数 vs 2636B 回读值")
    print("      相对误差 %+.4f %%  (σ = %.4f %%, n = %d)" % (mu, sd, len(rel)))
    print("      -> %s"
          % ("**是纯增益误差**" if abs(mu) > 5 * sd / math.sqrt(len(rel))
             else "不是单一增益"))
    print("      注: 这个数**含**增益项, 所以叫「准确度」不叫「线性度」;")
    print("          去掉增益/偏置之后剩下的才是【3】那个数。")
    print("")

    # ---------------- 3. 线性度 (校准后残差) ----------------
    E = [t * 1e12 for t in u]
    # ⚠️ 口径统一: **逐点均值**为准 (每个电流点先对 3 次取平均, 再算残差),
    #    这是本工程一直用的口径 (49 点那批报的 RMS 1.563 / max 4.155 就是它)。
    #    逐次的数一起给, 但不当头条 —— 两者能差好几倍, 混用会得出不同结论。
    per_pt = [sum(E[i] for i in grp) / len(grp) for grp in groups]
    def _rms(v):
        return math.sqrt(sum(t * t for t in v) / len(v))
    rms = _rms(per_pt); mx = max(abs(t) for t in per_pt)
    rms_a = _rms(E);    mxa = max(abs(t) for t in E)
    print("【3】线性度 (校准后残差 = 式(6) − 回读值)")
    print("      **逐点均值 (%d 点)**   RMS %7.3f pA   max %7.3f pA   -> **线性度 %.5f %%FS**"
          % (len(per_pt), rms, mx, mx * 1e-12 / fs * 100))
    print("      逐次     (%d 次)   RMS %7.3f pA   max %7.3f pA"
          % (len(E), rms_a, mxa))
    print("      逐次/逐点 = %.2f 倍 (均值压掉了重复性噪声, 所以逐次更大是正常的)"
          % (rms_a / rms))
    print("      max/RMS   = %.2f 倍 —— max 由**单个最差点**决定, 所以要一起报" % (mx / rms))
    print("")

    # ---------------- 4. 标准误 (先判要不要聚类) ----------------
    per_pt = [sum(E[i] for i in grp) / len(grp) for grp in groups]
    print("【4】常数的标准误")
    print("      先判要不要聚类 —— 残差在**电流方向**上的自相关:")
    rho = {}
    for lag in (1, 2, 3):
        rho[lag] = autocorr(per_pt, lag)
        z = rho[lag] * math.sqrt(len(per_pt))
        print("         lag-%d  ρ = %+.3f   (白噪声下 z = %+.2f%s)"
              % (lag, rho[lag], z, ",  *显著*" if abs(z) > 1.96 else ""))
    need = abs(rho[1]) * math.sqrt(len(per_pt)) > 1.96

    sig2 = sum(t * t for t in u) / (n - 3)
    cl = collections.defaultdict(list)
    ci = 0
    for p_i, k in enumerate(keys):
        if not cl or abs(k - ci) > a.cluster_na:
            ci = k
        cl[ci].extend(groups[p_i])
    V_ols = [[sig2 * AtA_inv[p][q] for q in range(3)] for p in range(3)]
    Vc = cluster_cov(A, u, AtA_inv, list(cl.values()))

    def se_of(V):
        """把 3 个回归系数的协方差**变换**成物理常数的标准误。

        ⚠️ 必须变换, 不能直接拿 V[p][p] 当答案 —— 2026-09-16 第一版就错了:
            q  = x0 x VREF/65536      -> SE(q) = sqrt(V00) x VREF/65536
            I- = -x1                  -> SE(I-) = sqrt(V11)
            I+ = -x1 - x2             -> SE(I+) = sqrt(V11 + V22 + 2*V12)
          原版把 q 的系数标准误直接当 q 的 (差 65536/3.3 ≈ 2e4 倍),
          而且把 I+ 和 I- 的标签写反了。
        """
        k = VREF / 65536.0
        return (math.sqrt(max(V[0][0], 0)) * k,
                math.sqrt(max(V[1][1] + V[2][2] + 2 * V[1][2], 0)),
                math.sqrt(max(V[1][1], 0)))

    oq, oip, oin = se_of(V_ols)
    cq, cip, cin = se_of(Vc)
    print("")
    print("      %-12s %16s %16s %16s" % ("参数", "值", "OLS 标准误", "聚类标准误"))
    print("      %-12s %16.6e %16.3e %16.3e" % ("q", q, oq, cq))
    print("      %-12s %16.4f %16.4f %16.4f" % ("I+ /nA", i_pos * 1e9, oip * 1e9, cip * 1e9))
    print("      %-12s %16.4f %16.4f %16.4f" % ("I- /nA", i_neg * 1e9, oin * 1e9, cin * 1e9))
    print("      相对标准误:  q %.4f %%   I+ %.4f %%   I- %.4f %%"
          % (oq / abs(q) * 100, oip / abs(i_pos) * 100, oin / abs(i_neg) * 100))
    print("      聚类宽度 %.1f nA, 共 %d 簇" % (a.cluster_na, len(cl)))
    print("      -> %s"
          % ("**自相关显著, 该用聚类标准误**" if need
             else "自相关不显著 -> 密网格没带来额外相关, **OLS 标准误本来就对**"))
    print("")

    # ---------------- 5. δ(I) 的形状 ----------------
    print("【5】δ(I) 的形状 —— 校准后残差 vs 电流")
    neg = [k for k in keys if k < 0]
    pos = [k for k in keys if k > 0]
    for tag, ks in (("正电流", pos), ("负电流", neg)):
        v = [per_pt[keys.index(k)] for k in ks]
        if len(v) < 5:
            continue
        print("      %s: %d 点  σ=%.3f pA  峰峰=%.3f pA  极值 %.2f pA @ %.1f nA"
              % (tag, len(v), math.sqrt(sum(t*t for t in v)/len(v)),
                 max(v) - min(v), max(v, key=abs),
                 ks[v.index(max(v, key=abs))]))
    print("      最大残差的 5 个点:")
    for k, e in sorted(zip(keys, per_pt), key=lambda t: -abs(t[1]))[:5]:
        print("         %+8.2f nA   %+7.3f pA" % (k, e))
    print("")

    # ---------------- 图 (两张分开) ----------------
    # 图1: δ(I) 的形状
    fig, ax = plt.subplots(figsize=(13, 5.6))
    for tag, ks, col in (("正", pos, 'C3'), ("负", neg, 'C0')):
        v = [per_pt[keys.index(k)] for k in ks]
        if ks:
            ax.scatter(ks, v, s=30, c=col, edgecolors='k', linewidths=.3,
                       label='%s电流' % tag, zorder=3)
    ax.axhline(0, color='k', ls='--', lw=1)
    for sg in (rms, -rms):
        ax.axhline(sg, color='r', ls=':', lw=1.3,
                   label='±RMS (%.3f pA)' % rms if sg > 0 else None)
    for k, e in sorted(zip(keys, per_pt), key=lambda t: -abs(t[1]))[:5]:
        ax.annotate('%.1f' % k, (k, e), textcoords='offset points',
                    xytext=(0, -12), ha='center', fontsize=7.5, color='0.3')
    ax.set_xlabel('设定电流 (nA)')
    ax.set_ylabel('校准后残差 (pA)   = 式(6) − 2636B 回读值')
    ax.set_title('δ(I) 的形状   %d 个电流点 (每点 3 次取均值)   '
                 'RMS %.3f pA   max %.3f pA   ->  线性度 %.5f %%FS'
                 % (len(per_pt), rms, mx, mx * 1e-12 / fs * 100), fontsize=11)
    ax.grid(alpha=.3); ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(a.out, dpi=150)
    print("已写", a.out)

    # 图2: 自相关
    fig2, ax2 = plt.subplots(figsize=(9, 5.6))
    lags = range(1, 11)
    vals = [autocorr(per_pt, l) for l in lags]
    band = 1.96 / math.sqrt(len(per_pt))
    ax2.bar(list(lags), vals, width=0.6, color='C2', edgecolor='k', linewidth=.5)
    ax2.axhspan(-band, band, color='r', alpha=.12,
                label='95%% 白噪声带 (±%.3f)' % band)
    ax2.axhline(0, color='k', lw=.9)
    ax2.set_xlabel('滞后 (相邻电流点)')
    ax2.set_ylabel('残差自相关 ρ')
    ax2.set_title('残差在「电流方向」上的自相关   n = %d 个点\n'
                  'lag-1 ρ = %+.3f (z = %+.2f)  ->  %s'
                  % (len(per_pt), vals[0], vals[0] * math.sqrt(len(per_pt)),
                     "**有相关**" if abs(vals[0]) * math.sqrt(len(per_pt)) > 1.96
                     else "无相关 -> 曲线是锯齿状,\n"
                          "不能靠插值修正, 也不用做聚类标准误修正"),
                  fontsize=11)
    ax2.grid(alpha=.3, axis='y'); ax2.legend(fontsize=9)
    fig2.tight_layout()
    fig2.savefig(a.out_ac, dpi=150)
    print("已写", a.out_ac)


if __name__ == '__main__':
    main()
