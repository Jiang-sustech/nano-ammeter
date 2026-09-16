# -*- coding: utf-8 -*-
"""
残差的**独立性检验** + **离群值检验** —— 两件事, 分开做

    python tools/outlier_test.py
    python tools/outlier_test.py --alpha 0.01
    python tools/outlier_test.py -o data/outlier_test.png

为什么必须分开
--------------
「独立性」和「离群」回答的是**不同的问题**, 混在一起会得出错结论:

    独立性检验   序列是不是随机的?
                 -> 若**不随机**(残差成片同号 / 有趋势), 说明有**系统性结构**,
                    这时「离群点」根本不是离群, 是趋势的一部分。
    离群值检验   哪个点显著偏离其余点?
                 -> 前提是**其余点同分布**。有趋势时这个前提不成立。

所以顺序是: 先验独立性, 再根据结果决定离群检验怎么读。本脚本按这个顺序输出。

用哪一批数据
------------
第二轮 (sweep_broad, **样本外**)。第一轮的残差被拟合压小了 (样本内), 拿来做
离群检验会**系统性漏检** —— 残差本来就偏小。见 data/README.md「误差口径」。

分布用 scipy, 不用手搓
----------------------
初版因为环境里没 scipy, 我手搓了不完全 Beta + t 分位数。用户当即问「能不能用
python 或者已有的 matlab」—— 对。手搓分布等于自己给自己埋 bug, 已全部删掉,
改用 `scipy.stats` (`pip install scipy`, 1.18.1)。`_selftest()` 仍对几个已知
分位数做断言。

**Grubbs 的临界值另用蒙特卡洛复核一遍** —— 两条独立路径算同一个数, 对得上才敢用。
`tools/outlier_test.m` 是同一套检验的 MATLAB 实现, 用 `runstest`/`tinv`/`fcdf`,
可作**第三方交叉验证**。

⚠️ 一个必须说清的口径: 残差里的**系统**部分已被三常数拟合吸收, 所以这里筛出来的
   「离群点」是**相对于其余点**而言, **不是**「装置错了」的证据。
"""
import argparse
import collections
import csv
import math
import os
import random
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import load, fit, pred_from

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ===================== 分布: 用 scipy =====================
# 2026-09-16: 初版因为环境里没有 scipy 而手搓了不完全 Beta + t 分位数。
# 用户问「能不能用 python 或者已有的 matlab」—— 对, 手搓分布是在给自己
# 找风险。现在 `pip install scipy` 已装上 (1.18.1), 手搓版整个删掉。
from scipy import stats as st

t_ppf = st.t.ppf
t_sf = st.t.sf
f_sf = st.f.sf
_norm_cdf = st.norm.cdf


def _selftest():
    """确认 scipy 在位, 并把本脚本用到的几个分位数对**已知值**打出来。"""
    checks = [("t.ppf(0.975, 10)", t_ppf(0.975, 10), 2.2281),
              ("t.ppf(0.995, 29)", t_ppf(0.995, 29), 2.7564),
              ("t.sf(2.0, 29)", t_sf(2.0, 29), 0.0273),
              ("f.sf(3.0, 3, 68)", f_sf(3.0, 3, 68), 0.0362),
              ("norm.cdf(1.96)", _norm_cdf(1.96), 0.9750)]
    for n, got, want in checks:
        print("      %-20s %10.4f  (应为 %.4f)  %s"
              % (n, got, want, "OK" if abs(got - want) <= 2e-3 else "**不符**"))
    bad = [n for n, g, w in checks if abs(g - w) > 2e-3]
    if bad:
        sys.exit("scipy 分位数与已知值不符: %s" % bad)


# ===================== 统计量 =====================
def mean(v):
    return sum(v) / len(v)


def var(v):
    """样本方差 (n-1)"""
    return sum((x - mean(v)) ** 2 for x in v) / (len(v) - 1) if len(v) > 1 else 0.0


def runs_test(signs):
    """Wald-Wolfowitz 游程检验 (两侧, 带连续性修正)。

    H0: 正负号在序列里随机排列(相邻观测独立)。
    游程**过少** = 成片同号 = 正相关; **过多** = 交替 = 负相关。
    """
    s = [x for x in signs if x != 0]
    n1 = sum(1 for x in s if x > 0)
    n2 = len(s) - n1
    n = len(s)
    if n1 == 0 or n2 == 0 or n < 3:
        return None
    r = 1 + sum(1 for i in range(1, n) if (s[i] > 0) != (s[i - 1] > 0))
    mu = 2.0 * n1 * n2 / n + 1.0
    v = 2.0 * n1 * n2 * (2.0 * n1 * n2 - n) / (n * n * (n - 1.0))
    if v <= 0:
        return None
    sd = math.sqrt(v)
    cc = 0.5 if r > mu else -0.5            # 朝 mu 方向挪半格
    z = (r - cc - mu) / sd
    p = 2.0 * min(_norm_cdf(z), 1.0 - _norm_cdf(z))
    return dict(n=n, n1=n1, n2=n2, r=r, mu=mu, sd=sd, z=z, p=min(p, 1.0))


def lag_autocorr(v, lag=1):
    """滞后 lag 的自相关系数 (去均值后)"""
    m = mean(v)
    x = [t - m for t in v]
    num = sum(x[i] * x[i + lag] for i in range(len(x) - lag))
    den = sum(t * t for t in x)
    return num / den if den else 0.0


def grubbs(v, alpha):
    """Grubbs 单离群点检验 (两侧)。返回 (G, G_crit, 最极端点下标)"""
    n = len(v)
    m, s = mean(v), math.sqrt(var(v))
    if s == 0:
        return None
    i = max(range(n), key=lambda k: abs(v[k] - m))
    G = abs(v[i] - m) / s
    t = t_ppf(1.0 - alpha / (2.0 * n), n - 2)
    Gc = (n - 1.0) / math.sqrt(n) * math.sqrt(t * t / (n - 2.0 + t * t))
    return G, Gc, i


def grubbs_p_mc(v, nsim=40000, seed=12345):
    """蒙特卡洛估 P(G >= G_obs), 正态零假设下 —— 复核解析临界值。"""
    rnd = random.Random(seed)
    n = len(v)
    m, s = mean(v), math.sqrt(var(v))
    Gobs = max(abs(t - m) for t in v) / s
    hit = 0
    for _ in range(nsim):
        x = [rnd.gauss(0.0, 1.0) for _ in range(n)]
        xm, xs = mean(x), math.sqrt(var(x))
        if xs == 0:
            continue
        if max(abs(t - xm) for t in x) / xs >= Gobs:
            hit += 1
    return (hit + 1.0) / (nsim + 1.0)


def esd(v, alpha, kmax=5):
    """Rosner 迭代 ESD。返回 (每步 [(下标, R, λ)], 判定为离群的个数)。

    Rosner 的 λ 用**当前剩余点数** n_i:  λ_i = n_i·t / sqrt((n_i-1+t²)(n_i+1))
    其中 t = t_{p, n_i-2}, p = 1 - α/(2·(n_i+1))。
    """
    idx = list(range(len(v)))
    vals = list(v)
    out = []
    for _ in range(kmax):
        n = len(vals)
        if n < 3:
            break
        m, s = mean(vals), math.sqrt(var(vals))
        if s == 0:
            break
        k = max(range(n), key=lambda j: abs(vals[j] - m))
        R = abs(vals[k] - m) / s
        t = t_ppf(1.0 - alpha / (2.0 * (n + 1.0)), n - 2)
        lam = n * t / math.sqrt((n - 1.0 + t * t) * (n + 1.0))
        out.append((idx[k], R, lam))
        idx.pop(k)
        vals.pop(k)
    keep = 0                       # 从最后一步往回找第一个 R >= λ
    for j in range(len(out) - 1, -1, -1):
        if out[j][1] >= out[j][2]:
            keep = j + 1
            break
    return out, keep


def anova(groups):
    """单因素方差分析 -> (F, df1, df2, p)"""
    gs = [g for g in groups if len(g) >= 2]
    k = len(gs)
    N = sum(len(g) for g in gs)
    if k < 2 or N <= k:
        return None
    gm = mean([x for g in gs for x in g])
    ssb = sum(len(g) * (mean(g) - gm) ** 2 for g in gs)
    ssw = sum(sum((x - mean(g)) ** 2 for x in g) for g in gs)
    df1, df2 = k - 1, N - k
    if ssw == 0:
        return None
    F = (ssb / df1) / (ssw / df2)
    return F, df1, df2, f_sf(F, df1, df2)


# ===================== 数据 =====================
def load_round2(calpath, valpath):
    """第二轮 (样本外) 残差, 按**实际测量顺序**返回 [(set_nA, I_A, [e_A,...]), ...]"""
    cp = load(calpath)
    ck = sorted(cp)
    x = fit([r for k in ck for r in cp[k]])
    order, g = [], collections.OrderedDict()
    with open(valpath, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                m, n = int(r['m']), int(r['n'])
                c1, c2 = int(r['ext1']), int(r['ext2'])
                I = float(r['true_nA']) * 1e-9
                sp = float(r['set_pA'])
            except (KeyError, ValueError):
                continue
            if m <= 0 or n <= 0 or c1 < 0 or c2 < 0:
                continue
            if sp not in g:
                order.append(sp)
                g[sp] = []
            g[sp].append((I, pred_from(c1, c2, m, n, x) - I))
    return [(sp, mean([I for I, _ in g[sp]]), [e for _, e in g[sp]])
            for sp in order]


def polarity_demeaned(E, I_nA):
    """去掉极性偏置 —— 否则 +1.3 pA 的偏置会让整片正电流都被判「离群」。

    返回 (去偏置后的值, 对应的原始下标), 正电流在前、负电流在后。
    """
    pos = [i for i in range(len(E)) if I_nA[i] > 0]
    neg = [i for i in range(len(E)) if I_nA[i] < 0]
    v = []
    for ii in (pos, neg):
        if not ii:
            continue
        mu = mean([E[i] for i in ii])
        v += [E[i] - mu for i in ii]
    return v, pos + neg


# ===================== 主流程 =====================
def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--cal', default='data/cal_sweep_fit.csv')
    ap.add_argument('--val', default='data/sweep_broad.csv')
    ap.add_argument('--alpha', type=float, default=0.05)
    ap.add_argument('-o', '--out', default='data/outlier_test.png')
    a = ap.parse_args()

    for p in (a.cal, a.val):
        if not os.path.exists(p):
            sys.exit("找不到 %s" % p)

    print("=" * 78)
    print("残差的独立性检验 + 离群值检验")
    print("=" * 78)
    print("分布函数自检 (scipy %s):" % __import__("scipy").__version__)
    _selftest()
    print("")

    pts = load_round2(a.cal, a.val)
    I_nA = [I * 1e9 for _, I, _ in pts]
    E = [mean(es) * 1e12 for _, _, es in pts]              # 逐点均值残差 (pA)
    reps = [[e * 1e12 for e in es] for _, _, es in pts]    # 逐次
    n = len(pts)

    # 导出逐次残差给 MATLAB 版 (tools/outlier_test.m) 做**独立交叉验证**。
    # 让 MATLAB 读同一份数字, 交叉验证的才是**统计实现**, 不是残差算法 ——
    # 残差算法本身早就验过了 (matlab fit_constants.m 与 python lstsq 常数一致)。
    exp_path = os.path.join(os.path.dirname(a.val), 'outlier_residuals.csv')
    with open(exp_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['set_nA', 'rep', 'I_nA', 'resid_pA'])
        for sp, I, es in pts:
            for k, e in enumerate(es):
                w.writerow(['%.6g' % (sp / 1000.0), k + 1,
                            '%.10g' % (I * 1e9), '%.10g' % (e * 1e12)])
    print("已导出", exp_path, "(给 tools/outlier_test.m 交叉验证用)")
    print("")

    print("数据: %s 的**样本外**残差 —— %d 个电流点 × 3 次"
          % (os.path.basename(a.val), n))
    print("      残差 = Ī_校准后 − 2636B 回读值;  α = %g" % a.alpha)
    print("")

    # ---------- 1. 独立性 ----------
    print("【1】独立性检验 —— 残差是随机散布, 还是有结构?")
    print("")
    print("  序列 = **实际测量顺序** (不是按电流排)。独立性问的是")
    print("  「前后两次测量之间有没有关系」, 所以必须用真实时序。")
    print("")
    for pol, nm in ((1, '正电流'), (-1, '负电流')):
        ii = [i for i in range(n) if I_nA[i] * pol > 0]
        raw = [E[i] for i in ii]
        rt_raw = runs_test(raw)
        mu = mean(raw)
        rt_dm = runs_test([e - mu for e in raw])
        if not (rt_raw and rt_dm):
            continue
        print("  %s (n=%d), 极性偏置 %.3f pA:" % (nm, len(ii), mu))
        print("      未去偏置  r = %d, 期望 %.1f ± %.1f, p = %.4f"
              % (rt_raw['r'], rt_raw['mu'], rt_raw['sd'], rt_raw['p']))
        print("      已去偏置  r = %d, 期望 %.1f ± %.1f, z = %+.3f, p = %.4f"
              "  -> %s"
              % (rt_dm['r'], rt_dm['mu'], rt_dm['sd'], rt_dm['z'], rt_dm['p'],
                 "**不独立**" if rt_dm['p'] < a.alpha else "不能拒绝独立"))
    print("")
    print("  ⚠️ 未去偏置那行 p 小, 反映的是**极性偏置**(常数偏移把符号全推到")
    print("     一边), **不是**序列相关。判独立性必须看已去偏置那行。")
    print("")

    print("  自相关 (按测量顺序, 已去偏置):")
    v_all, imap = polarity_demeaned(E, I_nA)
    for lag in (1, 2, 3):
        rho = lag_autocorr(v_all, lag)
        z = rho * math.sqrt(len(v_all))
        print("      lag-%d   ρ = %+.3f   (白噪声下 ~N(0, 1/%d), z = %+.2f%s)"
              % (lag, rho, len(v_all), z,
                 ",  *显著*" if abs(z) > 1.96 else ""))
    print("")

    # ---------- 2. 过度离散 ----------
    print("【2】过度离散检验 —— 逐点之间的差异, 比重复性大吗?")
    print("")
    an = anova(reps)
    Nrep = sum(len(g) for g in reps)
    sw = math.sqrt(sum(sum((x - mean(g)) ** 2 for x in g) for g in reps)
                   / (Nrep - len(reps)))
    sb = math.sqrt(var([mean(g) for g in reps]))
    if an:
        F, d1, d2, p = an
        print("      组内 (重复性)  σ = %.3f pA,  df = %d" % (sw, d2))
        print("      组间 (逐点)    σ = %.3f pA,  df = %d" % (sb, d1))
        print("      单因素 ANOVA:  F(%d,%d) = %.2f,  p = %.2e  -> %s"
              % (d1, d2, F, p,
                 "**逐点差异真实存在**" if p < a.alpha else "逐点差异不显著"))
        print("      方差比 (σ_组间/σ_组内)² = %.2f" % ((sb / sw) ** 2))
    print("")
    print("      判读: 这是「残差里多少是噪声、多少是结构」的分界。")
    print("            若组间显著大于组内 -> 残差**不是**纯噪声, 逐点有真结构。")
    pos_v = [E[i] for i in range(n) if I_nA[i] > 0]
    neg_v = [E[i] for i in range(n) if I_nA[i] < 0]
    L, pL = st.levene(pos_v, neg_v, center='median')
    print("")
    print("      极性方差齐性 (Levene, 中位数中心):  W = %.3f, p = %.3f" % (L, pL))
    print("      正电流 σ = %.3f pA   vs   负电流 σ = %.3f pA"
          % (math.sqrt(var(pos_v)), math.sqrt(var(neg_v))))
    print("      -> %s"
          % ("两极性方差相等" if pL > a.alpha else
             "**两极性方差不相等**。后果: 把两极**合并**做 Grubbs 时, 大的那"
             "一边\n         会把合并 σ 抬高, **低估功效** -> 更容易漏掉真离群点。"
             "所以下面\n         分极性各做一次, 三个结果都要看。"))
    print("")

    # ---------- 3. 离群值 ----------
    print("【3】离群值检验 —— 哪个点显著偏离?")
    print("")
    print("  3.0 前提检验: Grubbs/ESD 都假设残差**近似正态**, 先验这一条")
    for tag, v in (("全部(去极性偏置)", v_all),
                   ("正电流", [E[i] - mean([E[k] for k in range(n)
                                          if I_nA[k] > 0])
                             for i in range(n) if I_nA[i] > 0]),
                   ("负电流", [E[i] - mean([E[k] for k in range(n)
                                          if I_nA[k] < 0])
                             for i in range(n) if I_nA[i] < 0])):
        W, pw = st.shapiro(v)
        sk, pk = st.skewtest(v) if len(v) >= 8 else (0, 1)
        print("      %-18s Shapiro-Wilk W = %.3f, p = %.3f -> %s"
              % (tag, W, pw, "不能拒绝正态" if pw > a.alpha else "**偏离正态**"))
        if len(v) >= 8:
            print("      %-18s 偏度检验 p = %.3f -> %s"
                  % ("", pk, "对称" if pk > a.alpha else "**有偏**"))
    print("      ⚠️ 偏离正态时 Grubbs 的临界值只是近似 —— 所以下面每条结论")
    print("         都配了蒙特卡洛复核, 且只当**筛查**用。")
    print("")
    print("  3a. Grubbs 单离群点检验 (两侧, α = %g)" % a.alpha)
    print("      %-24s %-9s %-9s %-8s %s"
          % ("集合", "G", "G_crit", "判定", "最极端点"))
    subsets = [("全部(去极性偏置)", None),
               ("正电流", [i for i in range(n) if I_nA[i] > 0]),
               ("负电流", [i for i in range(n) if I_nA[i] < 0])]
    for tag, sel in subsets:
        if sel is None:
            v, idxmap = polarity_demeaned(E, I_nA)
        else:
            mu = mean([E[i] for i in sel])
            v = [E[i] - mu for i in sel]
            idxmap = sel
        r = grubbs(v, a.alpha)
        if not r:
            continue
        G, Gc, k = r
        j = idxmap[k]
        pmc = grubbs_p_mc(v, nsim=40000)
        consistent = (pmc < a.alpha) == (G > Gc)
        print("      %-24s %-9.3f %-9.3f %-8s %+.3f nA (残差 %+.2f pA)"
              % (tag, G, Gc, "**离群**" if G > Gc else "不显著",
                 I_nA[j], E[j]))
        print("      %-24s 蒙特卡洛复核 P(G>=G_obs) = %.4f  ->  %s"
              % ("", pmc, "与解析临界值一致" if consistent
                 else "**与解析临界值不一致, 别用这个结论!**"))
    print("")

    print("  3b. 迭代 ESD (Rosner, 最多剔 5 个, 从末步往回定个数)")
    out, keep = esd(v_all, a.alpha, kmax=5)
    for step, (k, R, lam) in enumerate(out):
        j = imap[k]
        print("      第 %d 步剔除 %+8.3f nA   R = %.3f,  λ = %.3f   %s"
              % (step + 1, I_nA[j], R, lam,
                 "R>=λ 判离群" if R >= lam else "R<λ 保留"))
    print("      -> 结论: 前 **%d** 个判为离群点" % keep)
    if keep:
        print("         具体: %s"
              % "   ".join("%+.3f nA (残差 %+.2f pA)"
                           % (I_nA[imap[k]], E[imap[k]]) for k, _, _ in out[:keep]))
    else:
        print("         一个都没有 —— 没有点在 α = %g 下显著偏离。" % a.alpha)
    print("")

    # ---------- 4. 判读 ----------
    rho1 = lag_autocorr(v_all, 1)
    z1 = rho1 * math.sqrt(len(v_all))
    indep = abs(z1) <= 1.96
    overdisp = an and an[3] < a.alpha

    print("=" * 78)
    print("【4】判读")
    print("=" * 78)
    print("  独立性     lag-1 ρ = %+.3f (z = %+.2f) -> %s"
          % (rho1, z1,
             "序列上**看不到**相关, 残差可当独立同分布处理" if indep
             else "序列上**有**相关, 残差不是独立同分布"))
    if an:
        print("  过度离散   组间 σ %.3f vs 组内 σ %.3f, ANOVA p = %.2e -> %s"
              % (sb, sw, an[3],
                 "逐点差异**真实存在**(残差不是纯噪声)" if overdisp
                 else "逐点差异不显著"))
    print("")
    print("  **三个检验合起来才是完整的判断** (只引其中一个都会读错):")
    print("")
    print("    ① 独立性  %s" % ("通过 — 无序列相关" if indep else "**不通过** — 有序列相关"))
    print("    ② 过度离散 %s"
          % ("**显著** — 逐点差异是真结构" if overdisp else "不显著 — 只有噪声"))
    print("    ③ 离群点  %s" % ("筛出 %d 个" % keep if keep else "**一个都没筛出**"))
    print("")
    if indep and overdisp and keep == 0:
        print("    ③ 的「一个都没筛出」**不是**「装置很准」—— 恰恰相反, 它是")
        print("    ② 的**推论**。Grubbs 抓不到离群点, 是因为**离散是普遍的、")
        print("    不是局部的**: 如果只有某几个点是坏点, 它们相对其余点会很突出;")
        print("    而现在的实际情况是**每个点都带着 ~%.2f pA 的逐点结构**," % sb)
        print("    没有任何一个点突出到能单独拎出来。")
        print("")
        print("    最直观的证据是**换一把尺子, 结论就翻过来**:")
        j = max(range(n), key=lambda k: abs(E[k]))
        z_in = abs(E[j]) / sw
        z_bt = abs(E[j]) / sb
        print("      最极端的 %+.3f nA (残差 %+.2f pA):" % (I_nA[j], E[j]))
        print("        用**组内** σ (%.3f pA) 当尺子 -> |z| = %.1f  **极端离群**"
              % (sw, z_in))
        print("        用**组间** σ (%.3f pA) 当尺子 -> |z| = %.1f  不显著"
              % (sb, z_bt))
        print("")
        print("    **该问的问题因此不是「哪几个点是坏点」, 而是「为什么每个点都")
        print("    带着 ~%.2f pA 的固定结构」。** 那是未建模的系统项(逐点非线性)," % sb)
        print("    修它才涨精度; 剔点不会。")
    elif indep and not overdisp:
        print("    残差既独立、逐点也无显著差异 -> 只有噪声, 没有真离群点。")
        print("    这时 Grubbs/ESD 若筛出点来, 都是**多重比较的假阳性**, 直接忽略。")
    else:
        print("    残差在序列上有相关 -> 存在系统漂移/趋势, 离群检验的「同分布」")
        print("    前提不成立, Grubbs 的结果只能当筛查, 不能当定论。")
    print("")
    print("  ⚠️ 最后一条, 别忘了: 残差里**系统**的部分已被三常数拟合吸收。")
    print("     所以这里筛出的「离群」是**相对于其余点**而言, **不是**「装置错了」")
    print("     的证据。要分清, 只能换**独立参考**(标准电阻 + 标准电压)复测。")
    print("")

    # ---------- 图 ----------
    fig, axs = plt.subplots(1, 2, figsize=(15, 5.4))
    zsc = [e / sw if sw else 0.0 for e in E]
    colors = ['C3' if I > 0 else 'C0' for I in I_nA]

    for axx, ys, ylab, ti in (
            (axs[0], zsc, '标准化残差 z = 残差 / σ_组内 (%.3f pA)' % sw,
             '标准化残差 vs 电流'),
            (axs[1], E, '残差 (pA)', '残差 vs 电流')):
        axx.scatter(I_nA, ys, c=colors, s=55, edgecolors='k', linewidths=.5,
                    zorder=3)
        axx.axhline(0, color='k', ls='--', lw=1)
        axx.set_xlabel('回读电流 (nA)')
        axx.set_ylabel(ylab)
        axx.set_title('%s      (红 = 正电流, 蓝 = 负电流)' % ti)
        axx.grid(alpha=.3)
    for sgn in (+1, -1):
        axs[0].axhline(sgn * 3, color='r', ls=':', lw=1.2,
                       label='±3σ' if sgn > 0 else None)
    axs[0].legend(fontsize=9)
    for i in range(n):
        if abs(zsc[i]) > 2.5:
            axs[0].annotate('%.3f' % I_nA[i], (I_nA[i], zsc[i]),
                            textcoords='offset points', xytext=(0, 7),
                            ha='center', fontsize=7)

    fig.suptitle('离群值筛查 (第二轮样本外残差, n=%d, 每点 3 次, α = %g)      '
                 '组内 σ = %.3f pA,  组间 σ = %.3f pA'
                 % (n, a.alpha, sw, sb), fontsize=11.5, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(a.out, dpi=150)
    print("已写", a.out)


if __name__ == '__main__':
    main()
