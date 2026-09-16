# -*- coding: utf-8 -*-
"""
自动量程切换的影响 —— 用"跨档外推"直接测

    python tools/range_effect.py

问题
----
`sweep_broad` 的 δ(I) 曲线有一条可复现的逐点结构 (两轮 r = 0.989)。
而 6~45 nA 这一段**正好跨 2636B 的 10 nA / 100 nA 两个源档**。
一个很自然的怀疑: 是不是**换档**造出来的?

官方手册(2600BS-901-01)相关事实:
  * 四个功能(源电压/源电流/测电压/测电流)的 autorange **各自独立、默认全开**;
    源与测量功能相同时, **测量档锁定跟随源档**。
  * "The instrument autoranges at 100% of the range" —— 仪器在**满量程的 100%**
    处换档; 源满量程 = 档位的 101%, 测量满量程 = 102%。
  * 精度是**按档位**给的, 换档 = 换一组精度指标。
  * 开 auto delay 时, "a range-dependent delay is applied ... also after
    current range changes during the autoranged measurement"。

⚠️ 我们这边的两个已知缺口 (如实记下):
  1. `cal_sweep.py` / `sweep_broad.py` 都**查了**实际档位 (`smua.source.rangei`)
     但**没有写进 CSV** —— 所以事后无法确证每个点当时用哪个档, 只能按
     `SMU_RANGES` 推。这是这次调查的最大障碍。
  2. `smua.measure.delay` 全程**没设过**, 是出厂默认。手册不同段落对默认值
     说法不一致 (一处说默认关闭, 一处示例注释说默认 DELAY_AUTO), 未定论。

做法
----
不依赖"档位有没有变"的推测, 直接做**跨档外推**:

    只用 **100 nA 档** 的点 (12.5~40 nA) 拟合三常数
    去预测 **10 nA 档** 的点 (6~10 nA)

若两档之间有系统差, 被预测的那组会出现**一致的偏置**; 若没有, 残差应当和
"100 档内部互测"的残差同量级、无偏。
"""
import argparse
import collections
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import fit, consts, pred_from

# 与 cal_sweep.py 的 SMU_RANGES 一致
SMU_RANGES = (1e-9, 10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1e0, 3e0)


def range_of(i_amp, boundary_inclusive=True):
    """能覆盖该电流的最小档位。

    boundary_inclusive 处理"10 nA 到底落在 10 nA 档还是 100 nA 档"这件事 ——
    手册只说"在满量程 100% 处换档", 没明说 100% 那一点算上还是算下。两种都算,
    看结论对边界怎么取敏不敏感。
    """
    a = abs(i_amp)
    for r in SMU_RANGES:
        if a < r or (boundary_inclusive and a == r):
            return r
    return None


def load(path):
    """-> [(set_pA, I_A, [e_A x3]), ...], 含端点码"""
    rows = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                m, n = float(r['m']), float(r['n'])
                c1, c2 = float(r['ext1']), float(r['ext2'])
                I = float(r['true_nA']) * 1e-9
                sp = float(r['set_pA'])
            except (KeyError, ValueError):
                continue
            if m <= 0 or n <= 0 or c1 < 0:
                continue
            rows.append((sp, I, m, n, c1, c2))
    return rows


def stats(v):
    m = sum(v) / len(v)
    return (m, math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
            if len(v) > 1 else 0.0)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--val', default='data/sweep_broad.csv')
    ap.add_argument('--draw', default='data/sweep_broad_run1.csv')
    a = ap.parse_args()

    print("=" * 78)
    print("自动量程切换的影响 —— 跨档外推检验")
    print("=" * 78)
    print("")

    rows = load(a.val)
    for inc in (True, False):
        g = collections.defaultdict(list)
        for sp, I, m, n, c1, c2 in rows:
            g[range_of(sp * 1e-12, inc)].append((sp, I, m, n, c1, c2))
        print("  边界取法 (%s):  %s"
              % ("10 nA 落在 10 nA 档" if inc else "10 nA 落在 100 nA 档",
                 "   ".join("%g A: %d 行" % (k, len(v))
                           for k, v in sorted(g.items()))))
    print("")

    inc = True       # 首选口径
    g = collections.defaultdict(list)
    for r in rows:
        g[range_of(r[0] * 1e-12, inc)].append(r)

    lo, hi = 10e-9, 100e-9
    if lo not in g or hi not in g:
        sys.exit("数据里没有同时出现这两个档, 改了 SMU_RANGES?")

    # ---- 用 100 nA 档拟合三常数 ----
    a1a2y = []
    for sp, I, m, n, c1, c2 in g[hi]:
        nt = m + n
        a1a2y.append((3.3 * (c2 - c1) / 65536.0 / (nt * 160e-6), m / nt, I))
    x = fit(a1a2y)
    q, i_pos, i_neg = consts(x)
    print("--- 只用 **100 nA 档** 的点 (%.4g~%.4g nA, %d 点) 拟合三常数 ---"
          % (min(abs(sp) for sp, _, _, _, _, _ in g[hi]) / 1000.0,
             max(abs(sp) for sp, _, _, _, _, _ in g[hi]) / 1000.0,
             len(set(sp for sp, _, _, _, _, _ in g[hi]))))
    print("      q = %.6e C/码   I+ = %+.4f nA   I- = %+.4f nA"
          % (q, i_pos * 1e9, i_neg * 1e9))
    print("")

    # ---- 用这组常数分别预测两个档 ----
    print("--- 拿它预测 (残差 = 装置 − 回读, 按**档位**分组) ---")
    print("%-14s %6s %14s %14s %10s" % ("档位", "点数", "残差均值(pA)", "残差σ(pA)", "相对均值"))
    out = {}
    for rng in sorted(g):
        by = collections.defaultdict(list)
        for sp, I, m, n, c1, c2 in g[rng]:
            by[sp].append(pred_from(c1, c2, m, n, x) - I)
        E = [sum(v) / len(v) * 1e12 for v in by.values()]
        Imean = sum(abs(k) for k in by) / len(by)        # pA, 每点一次不是每行
        mu, sd = stats(E)
        rel = mu / Imean * 100
        out[rng] = E
        print("%-14s %6d %14.3f %14.3f %9.4f%%"
              % ("%g A" % rng, len(E), mu, sd, rel))
    print("")

    # ---- 关键: 两档的均值差 ----
    mu_lo, sd_lo = stats(out[lo])
    mu_hi, sd_hi = stats(out[hi])
    n_lo, n_hi = len(out[lo]), len(out[hi])
    se = math.sqrt(sd_lo ** 2 / n_lo + sd_hi ** 2 / n_hi)
    t = (mu_lo - mu_hi) / se if se else 0.0
    print("--- 两档之间的**系统性偏置** ---")
    print("      10 nA 档残差均值  %+.3f pA  (n=%d)" % (mu_lo, n_lo))
    print("     100 nA 档残差均值  %+.3f pA  (n=%d)" % (mu_hi, n_hi))
    print("      差 = %+.3f ± %.3f pA   (Welch t = %+.2f)"
          % (mu_lo - mu_hi, se, t))
    print("      相对: 在 %.1f nA 上相当于 %+.4f %%"
          % (8.0, (mu_lo - mu_hi) / 8.0 * 100e-3))
    print("      -> %s"
          % ("**跨档有系统性偏置**" if abs(t) > 2.0
             else "没有证据说有系统偏置 (|t| < 2)"))
    print("")
    print("      注意: 这里同时混着**电流区间**的差别 —— 10 nA 档只有 6~10 nA,")
    print("      100 nA 档只有 12.5~40 nA, 两者**不重叠**。所以就算测出偏置,")
    print("      也分不清是「档位」造成的还是「电流区间」造成的。要分开, 必须让")
    print("      **同一个电流在两个档上各测一次** (见下)。")

    # ---- 手工可做的决定性实验 ----
    print("")
    print("=" * 78)
    print("决定性实验 (需要仪器, 本脚本做不了)")
    print("=" * 78)
    print("  上面的混淆靠数据分不开, 但要分开很简单 ——")
    print("  在**同一个电流**上, 强制换档各测一次:")
    print("")
    print("      smua.source.autorangei = smua.AUTORANGE_OFF")
    print("      smua.source.rangei = 10e-9      # 或 100e-9")
    print("")
    print("  在 8 nA 上分别用 10 nA 档和 100 nA 档各测 3 次。")
    print("  两者之差就是**纯档位效应**, 与电流区间无关。")
    print("  `smu_set(smu, i, force_range=...)` 已经实现了这个动作, 但**目前")
    print("  没有任何调用者传它** (见 cal_sweep.py 的 docstring), 所以要跑这个")
    print("  实验得先写一个新入口 —— 不是现成能用的。")
    print("")
    print("  顺带该补的两件事 (本次调查发现的口子):")
    print("    1. 把 smu_set() 查回来的档位**写进 CSV** —— 现在只 print/打日志,")
    print("       事后没法确证每个点用了哪档。")
    print("    2. 显式设 smua.measure.delay —— 现在是出厂默认, 而手册说")
    print("       auto delay 会在量程切换后加延迟。不设等于把这件事交给默认值。")


if __name__ == '__main__':
    main()
