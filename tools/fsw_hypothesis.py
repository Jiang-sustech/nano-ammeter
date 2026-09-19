# -*- coding: utf-8 -*-
"""检验「段0 偏置 = 锯齿轮翻转电荷注入」这个假设 —— 用高电流段残差的形状去**预测**段0

    python tools/fsw_hypothesis.py

推理链 (每一步都可查)
--------------------
【1】式(6) 的拟合矩阵是 [a1, 1.0, m/N] —— **带自由截距**。也就是说:
     任何「在所有电流上恒定的加性电流」(漏电流 / 回读偏置 / I+ 与 I- 共同平移)
     **会被拟合整个吸收掉**, 不可能在段0 留下偏置。
     => 段0 出现 +0.53 pA, 只能说明那个东西**在 5~45 nA 上不是常数**。

【2】随电流变、且在小电流端最强的机制, 首选**每次翻转的电荷注入**。
     积分器锯齿周期 = C*dV*(1/(I+ + I) + 1/(I+ - I)) = C*dV*2*I+/(I+^2 - I^2)
     => 翻转频率 f_sw ∝ (I+^2 - I^2)
     => 注入的等效电流 I_err = Q_sw * f_sw ∝ (I+^2 - I^2)
     注意这个**形状不含任何待定常数** —— 只用到 I+ (已由拟合给出)。

【3】所以可以**预测**: 高电流段残差 d(I) 若来自注入, 必然 ∝ ĝ(I) = I+^2/(I+^2-I^2)。
     把它外推到 I=0 (ĝ(0)=1), 应该正好等于段0 实测的那个 +0.53 pA。
     **这是一个真正的样本外预测** —— 段0 数据已经在了, 现在就能验。

若预测不中, 这个假设就被**证伪**, 而不是"再编一个解释"。
"""
import argparse
import collections
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dense_report import load_dense, fit_clipped, consts
from report_table import i_of


def pearson(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((a - my) ** 2 for a in y))
    if sx == 0 or sy == 0:
        return float('nan')
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (sx * sy)


def ols2(x, y):
    """y = a*x + b -> (a, b, a 的标准误, 残差 sd)"""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    a = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / sxx
    b = my - a * mx
    u = [y[i] - (a * x[i] + b) for i in range(n)]
    s = math.sqrt(sum(v * v for v in u) / max(n - 2, 1))
    return a, b, s / math.sqrt(sxx), s


def make_shape(i_pos):
    """ĝ(I) = I+²/(I+²−I²), 归一化到 ĝ(0)=1。只由 I+ 定形状, 无自由参数。"""
    def g(i_amp):
        r = abs(i_amp) / abs(i_pos)
        return float('inf') if r >= 1.0 else 1.0 / (1.0 - r * r)
    return g


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='data/dense')
    ap.add_argument('--seg0', default='data/lowI_pos')
    a = ap.parse_args()

    raw = load_dense(a.dir, 1.0, [])
    bypt = collections.OrderedDict()
    for sp, It, dev, a1, a2 in raw:
        bypt.setdefault(round(sp, 4), []).append((It, dev, a1, a2))
    keys = sorted(bypt, key=lambda k: (k < 0, abs(k)))

    A, y, groups = [], [], []
    for k in keys:
        idx = []
        for It, dev, a1, a2 in bypt[k]:
            idx.append(len(A))
            A.append([a1, 1.0, a2])
            y.append(It)
        groups.append(idx)
    x, AtA_inv, keep, groups, dropped = fit_clipped(A, y, groups)
    q, i_pos, i_neg = consts(x)
    shape = make_shape(i_pos)

    print("=" * 92)
    print("假设检验: 段0 偏置 = 锯齿轮翻转电荷注入")
    print("=" * 92)
    print("高电流段拟合 (%s): q=%.6e  I+=%+.4f nA  I-=%+.4f nA"
          % (a.dir, q, i_pos * 1e9, i_neg * 1e9))
    print("  ⚠️ 模型带**自由截距** -> 恒定加性项会被吸收 (文件头【1】)")
    print("")

    Ak = [A[i] for i in keep]
    yk = [y[i] for i in keep]
    uu = [yk[i] - sum(Ak[i][p] * x[p] for p in range(3)) for i in range(len(Ak))]
    gmap = {i: gi for gi, gg in enumerate(groups) for i in gg}
    pt_d, pt_i = collections.OrderedDict(), collections.OrderedDict()
    for i in range(len(Ak)):
        gi = gmap[i]
        pt_d.setdefault(gi, []).append(uu[i])
        pt_i.setdefault(gi, []).append(yk[i])

    items = []
    for gi in pt_d:
        dd = sum(pt_d[gi]) / len(pt_d[gi]) * 1e12
        ii = sum(pt_i[gi]) / len(pt_i[gi])
        items.append((ii, dd, len(pt_d[gi])))
    items.sort(key=lambda t: abs(t[0]))

    print("## 形状检验: 残差 d(I)  vs  ĝ(I) = I+²/(I+²−I²)")
    print("   ĝ 只由 I+ = %.4f nA 定形状, **没有自由参数**" % (i_pos * 1e9))
    print("")
    print("%11s %10s %11s %7s" % ("回读nA", "ĝ(I)", "d(I) pA", "次数"))
    print("-" * 44)
    xs, ys = [], []
    for i_amp, dd, cnt in items:
        gg = shape(i_amp)
        if not math.isfinite(gg):
            continue
        xs.append(gg)
        ys.append(dd)
        print("%11.4f %10.5f %+11.4f %7d" % (i_amp * 1e9, gg, dd, cnt))
    print("-" * 44)

    aa, bb, ae, sd = ols2(xs, ys)
    # 外推值 ĝ(0)=1 的不确定度。注意**不能**只报斜率的标准误 ——
    # ĝ(0)=1 离数据重心 x̄ 有 (1-x̄) 的距离, 截距的误差要一起算进去。
    npts = len(xs)
    xbar = sum(xs) / npts
    var_pred = (sd ** 2 / npts) + ((1.0 - xbar) ** 2) * (ae ** 2)
    sp = math.sqrt(var_pred)
    print("   Pearson r = %+.4f   (n=%d 个电流点)" % (pearson(xs, ys), npts))
    print("   拟合  d = %+.4f * ĝ %+.4f    斜率 ±%.4f, 残差 sd %.4f pA"
          % (aa, bb, ae, sd))
    print("   ĝ 的数据重心 x̄ = %.4f  ->  外推到 ĝ=1 要走 %.3f 的距离"
          % (xbar, abs(1.0 - xbar)))
    print("   **外推到 I=0 预测段0 偏置 = %+.4f ± %.4f pA**"
          % (aa + bb, sp))
    print("")

    print("## 与段0 实测对比")
    s0 = {}
    with open(os.path.join(a.seg0, 'raw.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if 'TIMEOUT' in r['line'] or abs(float(r['set_nA'])) >= 1.0:
                continue
            s0.setdefault(float(r['set_nA']), []).append(r)
    off = []
    for k in s0:
        v = s0[k]
        dev = sum(i_of(int(r['ext1']), int(r['ext2']), int(r['m']),
                       int(r['n'])) for r in v) / len(v) * 1e12
        std = sum(float(r['true_nA']) for r in v) / len(v) * 1e3
        off.append(dev - std)
    mo = sum(off) / len(off)
    gap = (aa + bb) - mo
    # 实测那一侧也有误差: 段0 的组间散布 / sqrt(点数)
    so = math.sqrt(sum((v - mo) ** 2 for v in off) / (len(off) - 1))
    sig = math.sqrt(sp ** 2 + (so / math.sqrt(len(off))) ** 2)
    print("   段0 实测偏置 = %+.4f ± %.4f pA   (%d 点, %s)"
          % (mo, so / math.sqrt(len(off)), len(off), a.seg0))
    print("   注入假设外推 = %+.4f ± %.4f pA" % (aa + bb, sp))
    print("   差           = %+.4f pA  = **%.1f σ**" % (gap, abs(gap) / sig))
    print("")
    print("   判据: <1σ -> 假设成立;  >3σ -> 该形状解释不了;  1~3σ -> 不成立但不算铁证")


if __name__ == '__main__':
    main()
