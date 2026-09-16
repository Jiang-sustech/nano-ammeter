# -*- coding: utf-8 -*-
"""临时: 残差是"电流的确定函数"还是"每轮随机"?"""
import csv
import math
import sys
import collections

sys.stdout.reconfigure(encoding='utf-8')


def by_point(path, key, devs, tru):
    g = collections.defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                k = float(r[key])
                t = float(r[tru])
                d = None
                for c in devs:
                    if r.get(c):
                        d = float(r[c])
                        break
                if d is None:
                    continue
            except (KeyError, ValueError):
                continue
            if not math.isfinite(t) or abs(t) < 1e-12:
                continue
            g[k].append((d, t))
    return {k: ((sum(d for d, _ in v) / len(v)) - (sum(t for _, t in v) / len(v)))
            / (sum(t for _, t in v) / len(v)) * 100.0 for k, v in g.items()}


def mean(v):
    return sum(v) / len(v)


def sd(v):
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def pearson(x, y):
    mx, my = mean(x), mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den else float('nan')


def report(tag, pairs, show=False):
    """pairs: [(标签, δ1, δ2)]"""
    d1 = [a for _, a, _ in pairs]
    d2 = [b for _, _, b in pairs]
    diff = [a - b for a, b in zip(d1, d2)]
    r = pearson(d1, d2)
    n = len(pairs)
    t = r * math.sqrt((n - 2) / (1 - r * r)) if n > 2 and abs(r) < 1 else 0
    print("=" * 74)
    print(tag)
    print("=" * 74)
    if show:
        print("%10s %12s %12s %12s" % ("电流(nA)", "第一轮 δ/%", "第二轮 δ/%", "差"))
        for lab, a, b in pairs:
            print("%10s %12.4f %12.4f %12.4f" % (lab, a, b, a - b))
        print("")
    print("  各点之间 δ 的散布   第一轮 σ = %.4f %%   第二轮 σ = %.4f %%"
          % (sd(d1), sd(d2)))
    print("  同一电流上两轮的差   σ = %.4f %%   (n=%d)" % (sd(diff), n))
    print("  两轮 δ 的相关系数    r = %.3f   (t = %.2f, n = %d)" % (r, t, n))
    rep = math.sqrt(max(sd(d1) ** 2 - sd(diff) ** 2 / 2, 0))
    unre = sd(diff) / math.sqrt(2)
    print("  可复现部分 = %.4f %%    不可复现部分 = %.4f %%"
          % (rep, unre))
    print("  -> 逐点结构里 **%.1f %%** 是可复现的 (按方差)"
          % (100.0 * rep * rep / (rep * rep + unre * unre)))
    print("  -> 若按逐点表格修正, 残差可降到原来的 %.0f %%"
          % (100.0 * unre / sd(d1)))
    print("")


# ---- 检验 A: 标定轮 vs 宽扫轮, 14 个重合点 ----
r1 = by_point('data/cal_sweep_raw.csv', 'point_nA', ['dev_nA'], 'true_nA')
r2 = by_point('data/sweep_broad.csv', 'set_pA', ['dev_ext_nA', 'dev_nA'], 'true_nA')
common = sorted(int(v) for v in r1 if 10 <= abs(v) <= 40)
pairsA = [("%+d" % c, r1[float(c)], r2[float(c * 1000)]) for c in common]
report("检验 A: 标定轮(08-15 19:41) vs 宽扫轮(20:28), 重合的 14 个点", pairsA,
       show=True)

# ---- 检验 B: 宽扫的两次独立采集, 全部 34 个点(含交错点) ----
b1 = by_point('data/sweep_broad_run1.csv', 'set_pA', ['dev_ext_nA', 'dev_nA'],
              'true_nA')
b2 = by_point('data/sweep_broad.csv', 'set_pA', ['dev_ext_nA', 'dev_nA'],
              'true_nA')
ks = sorted(set(b1) & set(b2))
pairsB = [("%+g" % (k / 1000.0), b1[k], b2[k]) for k in ks]
report("检验 B: sweep_broad 的两次独立采集, 全部 %d 个点(含 10 个交错点)"
       % len(ks), pairsB, show=True)

# ---- 检验 C: δ(I) 是平滑函数还是锯齿? (按**电流**排序, 不是测量顺序) ----
print("=" * 74)
print("检验 C: δ(I) 的平滑性 —— 按电流排好序后的相邻点相关")
print("=" * 74)
for pol, nm in ((1, '正电流'), (-1, '负电流')):
    ks = sorted([k for k in b2 if k * pol > 0], key=abs)
    v = [b2[k] for k in ks]
    m = mean(v)
    x = [t - m for t in v]
    print("  %s (n=%d), 电流序: %s" % (nm, len(v),
          " ".join("%g" % (k / 1000.0) for k in ks)))
    print("      δ 值:       %s" % " ".join("%.3f" % t for t in v))
    for lag in (1, 2, 3):
        rho = sum(x[i] * x[i + lag] for i in range(len(x) - lag)) / sum(t * t for t in x)
        print("      lag-%d  ρ = %+.3f   (白噪声 ~N(0,1/%d), z = %+.2f%s)"
              % (lag, rho, len(x), rho * math.sqrt(len(x)),
                 ",  *平滑*" if rho * math.sqrt(len(x)) > 1.96 else ""))
    print("")
