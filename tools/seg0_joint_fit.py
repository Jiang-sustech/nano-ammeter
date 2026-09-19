# -*- coding: utf-8 -*-
"""实验 (a): 把段0 的点并进拟合, 看常数怎么动、残差怎么变

    python tools/seg0_joint_fit.py

**怎么读这个结果 (不然一定误读)**
--------------------------------
把段0 的点并进一个"模型在低端本来就未必成立"的拟合, 段0 残差**必然**变小 ——
那是过拟合, 不是证据。所以真正有信息的量是两个:

  【判据 A】**段0 的偏置, 相对拟合自身的不确定度有多大?**
      不是拿段0 自己 0.019 pA 的标准误去除 —— 那忽略了**拟合参数的不确定度**。
      正确做法: 对段0 每个工作点 z=(a1,1,m/N), 预测方差 = σ² + zᵀ·Cov·z。
      得到 z 分数。**<2σ = 段0 与拟合外推一致, 不需要新机制;
      >5σ = 模型在低端确实缺东西。**

  【判据 B】**并进去之后, 高电流段的残差会不会变差?**
      如果两组数据一致, 并进去只会让常数在**各自的误差内**微调, 高电流残差不变。
      如果高电流残差明显变差 -> 两组数据在模型层面**互相矛盾**。

**为什么段0 必须关掉闸门①**
   闸门① 是 |回读−设定|/设定 > 1% 就丢。它在高电流段合理(源表能跟到 0.02%),
   但在段0 **无意义**: 2636B 在 1 pA 上本来就只有 ~18% 的分辨能力,
   实测 51 次里被它丢掉 21 次, 而且是**挑着丢的** (留下的是恰好落进 1% 的那些),
   会引入选择偏倚。所以段0 用 tol_pct=1e6, 等于关掉。
"""
import argparse
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dense_report import load_dense, fit_clipped, cluster_cov, ols
from cv_linearity import consts, VREF


def build(rows):
    """[(sp, It, dev, a1, a2)] -> (A, y, groups), 每个电流点一组。"""
    bypt = collections.OrderedDict()
    for sp, It, dev, a1, a2 in rows:
        bypt.setdefault(round(sp, 6), []).append((It, a1, a2))
    A, y, groups = [], [], []
    for k in sorted(bypt, key=lambda k: (k < 0, abs(k))):
        idx = []
        for It, a1, a2 in bypt[k]:
            idx.append(len(A))
            A.append([a1, 1.0, a2])
            y.append(It)
        groups.append(idx)
    return A, y, groups, len(bypt)


def fit(A, y, groups):
    x, AtA_inv, keep, kgroups, dropped = fit_clipped(A, y, groups)
    Ak = [A[i] for i in keep]
    yk = [y[i] for i in keep]
    u = [yk[i] - sum(Ak[i][p] * x[p] for p in range(3)) for i in range(len(Ak))]
    cov = cluster_cov(Ak, u, AtA_inv, kgroups)
    sd = math.sqrt(sum(v * v for v in u) / max(len(u) - 3, 1))
    return dict(x=x, cov=cov, sd=sd, A=Ak, y=yk, u=u, n=len(Ak),
                npt=len(kgroups), dropped=dropped)


def qq(ff, name):
    """报三个物理常数及其聚类稳健标准误。

    ⚠️ 三个物理量不是回归系数的同一个: cv_linearity.consts() 的映射是
        q   = x0 * VREF/65536
        I-  = -x1
        I+  = I- - x2          <- **两个系数之差**, 误差要把协方差算进去,
                                  不能只看 sqrt(Cov[2][2])
    """
    c = ff['cov']
    q, ip, im = consts(ff['x'])
    s_q = math.sqrt(max(c[0][0], 0)) * VREF / 65536.0
    s_im = math.sqrt(max(c[1][1], 0))
    s_ip = math.sqrt(max(c[2][2] + c[1][1] - 2.0 * c[1][2], 0))
    print("   %-20s q=%.8e (±%.1e)" % (name, q, s_q))
    print("   %-20s I+=%+.5f (±%.5f) nA   I-=%+.5f (±%.5f) nA"
          % ("", ip * 1e9, s_ip * 1e9, im * 1e9, s_im * 1e9))
    return s_q, s_im, s_ip


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--hi', default='data/dense')
    ap.add_argument('--lo', default='data/lowI_pos')
    a = ap.parse_args()

    hi = load_dense(a.hi, 1.0, [])
    # ⚠️ 两道处理缺一不可:
    #   ① 闸门①必须关 —— 它在段0 无意义 (2636B 在 1 pA 上只有 ~18% 分辨能力,
    #      51 次里会挑着丢掉 21 次, 引入选择偏倚)。见文件头。
    #   ② **必须按 |I| < 1 nA 过滤** —— `data/lowI_pos` 那份 CSV 里还含
    #      ±1/2/4 nA 三个**段1**点(走 1 s 窗), 不过滤的话它们会混进"段0"
    #      的均值里, 把偏置从 +0.5 pA 拉到 +0.11 pA。
    #      (2026-09-19 修: 初版漏了这一步, 跑出来的数直接和分析脚本对不上。)
    #      ⚠️ `load_dense` 返回的 r[0] 是 **nA**(列名 set_nA), 而 r[1] 是**安培**。
    #         这里按 nA 判: 段0 = |设定| < 1 nA。
    lo = [r for r in load_dense(a.lo, 1e6, []) if abs(r[0]) < 1.0]
    if not hi or not lo:
        sys.exit("数据没读到")

    print("=" * 96)
    print("实验 (a): 段0 并进拟合")
    print("=" * 96)
    print("高电流段 %s: %d 次测量" % (a.hi, len(hi)))
    print("段0      %s: %d 次测量  (**闸门①已关**) " % (a.lo, len(lo)))
    print("")

    # ---- 外推有多远: a1 的取值范围 ----
    a1_hi = [r[3] for r in hi]
    a1_lo = [r[3] for r in lo]
    print("## 外推距离 (a1 = VREF*dC/65536/(N*T), 即「每单位 q 的电荷项」)")
    print("   高电流段 a1 in [%.4g, %.4g]" % (min(a1_hi), max(a1_hi)))
    print("   段0      a1 in [%.4g, %.4g]" % (min(a1_lo), max(a1_lo)))
    if max(a1_lo) < min(a1_hi):
        print("   -> 段0 的 a1 **整段落在高电流段之下**, 是不重叠的外推区")
    print("")

    Ahi, yhi, ghi, npt_hi = build(hi)
    Alo, ylo, glo, npt_lo = build(lo)

    fa = fit(Ahi, yhi, ghi)
    print("## 拟合 A —— 只用高电流段 (%d 点 / %d 次)" % (fa['npt'], fa['n']))
    sea = qq(fa, "高电流段单独拟合")
    print("")

    # ---- 判据 A: 段0 残差 vs 拟合外推的**预测**不确定度 ----
    print("## 判据 A: 段0 的偏置, 相对拟合自身不确定度有多大")
    print("   预测方差 = σ_noise² + zᵀ·Cov·z   (z = [a1, 1, m/N])")
    print("   ⚠️ 只用段0 自身的散布 (0.019 pA) 会**严重高估**显著性 ——")
    print("      那个数没算进「拟合常数本身不确定」这一项")
    print("")
    res, var = [], []
    for sp, It, dev, a1, a2 in lo:
        z = [a1, 1.0, a2]
        pred = sum(z[p] * fa['x'][p] for p in range(3))
        v = fa['sd'] ** 2 + sum(z[p] * fa['cov'][p][q] * z[q]
                                for p in range(3) for q in range(3))
        res.append((It - pred) * 1e12)
        var.append(v * 1e24)
    mr = sum(res) / len(res)
    # 拆成两项, 这样能看出到底是哪一项在主导 —— 这是本次实验的关键信息。
    #   只算段0 自身的散布  -> 高估显著性 (它假装常数是精确已知的)
    #   只算拟合参数不确定  -> 这才是"外推"真正带来的代价
    v_scat = fa['sd'] ** 2 * 1e24                      # pA²
    v_par = sum(sum(z[p] * fa['cov'][p][q] * z[q]
                    for p in range(3) for q in range(3))
                for z in ([r[3], 1.0, r[4]] for r in lo)) / len(lo) * 1e24
    n = len(res)
    s_scat = math.sqrt(v_scat / n)
    s_par = math.sqrt(v_par / n)
    sv = math.sqrt(v_scat / n + v_par / n)
    print("   段0 平均残差 (在拟合 A 下) = %+.4f pA" % mr)
    print("     只算段0 自身散布  = ±%.4f pA   <- 错的口径, 会高估显著性" % s_scat)
    print("     只算拟合参数不确定 = ±%.4f pA   <- **外推真正的代价**" % s_par)
    print("     合成              = ±%.4f pA" % sv)
    print("   ==> **%+.4f pA = %.1f σ**" % (mr, abs(mr) / sv))
    print("       (两项谁主导: %s)" % ("参数不确定" if s_par > s_scat else "段0自身散布"))
    print("")

    # ---- 判据 B: 并起来拟合 ----
    Aj, yj, gj = [], [], []
    off = 0
    for src_A, src_y, src_g in ((Ahi, yhi, ghi), (Alo, ylo, glo)):
        for g in src_g:
            gj.append([i + off for i in g])
        Aj += list(src_A)
        yj += list(src_y)
        off += len(src_A)
    fb = fit(Aj, yj, gj)
    print("## 拟合 B —— 高电流 + 段0 全部并起来 (%d 点 / %d 次)" % (fb['npt'], fb['n']))
    seb = qq(fb, "并起来拟合")
    print("")

    print("## 判据 B: 常数移动了多少 (以拟合 A 自己的标准误为单位)")
    qa, ipa, ima = consts(fa['x'])
    qb, ipb, imb = consts(fb['x'])
    for nm, va, vb, s in (("q  ", qa, qb, sea[0] * 1.5708 / 65536.0),
                          ("I+ ", ipa * 1e9, ipb * 1e9, sea[2] * 1e9),
                          ("I- ", ima * 1e9, imb * 1e9, sea[1] * 1e9)):
        print("   %s  %+12.7g -> %+12.7g   动 %+10.4g  = %.1f σ"
              % (nm, va, vb, vb - va, abs(vb - va) / s if s else float('inf')))
    print("")
    print("## 判据 B 之二: 高电流段残差有没有变差")
    print("   拟合 A (只用高电流) RMS = %.4f pA"
          % math.sqrt(sum(v * v for v in fa['u']) / len(fa['u']) * 1e24))
    ub = [fb['y'][i] - sum(fb['A'][i][p] * fb['x'][p] for p in range(3))
          for i in range(fb['n'])]
    rms_all = math.sqrt(sum(v * v for v in ub) / len(ub) * 1e24)
    print("   拟合 B (并起来) 全部 RMS = %.4f pA   <- 含段0, 会变小是必然的"
          % rms_all)
    print("   ⚠️ 关键不是这个数, 而是**常数移动是否超过拟合自身的误差** (上表)")


if __name__ == '__main__':
    main()
