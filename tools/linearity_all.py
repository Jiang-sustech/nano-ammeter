# -*- coding: utf-8 -*-
"""
把**所有**数据一起拟合, 算全部点的线性度 —— 样本内 vs 样本外并排给

    python tools/linearity_all.py
    python tools/linearity_all.py --fs-na 90 --low

⚠️ **样本内的数不能用。**
   拟合用的就是这些点, 拿它们的残差当"线性度"是循环论证 —— 参数是照着这些点
   调的, 残差必然偏小。用户 2026-09-15 当场指出过这条, 见 data/README.md
   「⚠️ 线性度必须报样本外的」。

   所以本脚本**两个都给, 并排**, 就是要让差距自己显出来。可以引用的是
   **留一交叉验证 (LOO)** 那一行。

线性度定义 (本工程沿用):
    线性度 = max|残差| / FS,   FS = 90 nA (= ±45 nA 满量程)
    残差   = 式(6)算出的电流 − 2636B 回读值

留出的单位是**电流点**不是行 —— 同一个电流的 3 次重复不独立 (同相位、同档位),
按行留一会把信息泄漏进训练集, 结果偏乐观。
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import load, fit, consts, VREF, T_INT   # noqa: F401

# 两批有端点码、能用式(6)的数据。注意表头不同 ——
#   cal_sweep_fit.csv 是 (I_true,c1,c2,m,n), cv_linearity.load() 直接吃;
#   sweep_broad.csv 是采集的原始表 (true_nA/ext1/ext2/m/n), 要走 load_broad()。
CAL_FILE = 'data/cal_sweep_fit.csv'       # 第一轮标定, 19 点 (±5..±45)
BROAD_FILE = 'data/sweep_broad.csv'       # 第二轮宽扫, 34 点 (±6..±40)
LOW_FILE = 'data/cal_lowI_fit.csv'        # <1 nA 那批 (另一套窗口)


def load_broad(path):
    """sweep_broad.csv 没有 (I_true,c1,c2,m,n) 的表头, 在这里转出来。"""
    import csv
    out = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                I = float(r['true_nA']) * 1e-9
                c1, c2 = float(r['ext1']), float(r['ext2'])
                m, n = float(r['m']), float(r['n'])
            except (KeyError, ValueError):
                continue
            if m <= 0 or n <= 0 or c1 < 0:
                continue
            nt = m + n
            out.append((VREF * (c2 - c1) / 65536.0 / (nt * T_INT), m / nt, I))
    return out


def load_low(path='data/cal_lowI_fit.csv'):
    import csv
    out = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            I = float(r['I_true'])
            c1, c2 = float(r['c1']), float(r['c2'])
            m, n = float(r['m']), float(r['n'])
            nt = m + n
            out.append((VREF * (c2 - c1) / 65536.0 / (nt * T_INT), m / nt, I))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--fs-na', type=float, default=90.0)
    ap.add_argument('--low', action='store_true',
                    help='把 <1 nA 那批 (另一套窗口) 也算进来')
    a = ap.parse_args()

    groups = dict(load(CAL_FILE))            # {电流: [(a1,a2,I),...]}
    for a1, a2, I in load_broad(BROAD_FILE):
        groups.setdefault(round(I * 1e9, 4), []).append((a1, a2, I))
    used = [CAL_FILE, BROAD_FILE]
    if a.low:
        for a1, a2, I in load_low(LOW_FILE):
            groups.setdefault(round(I * 1e9, 4), []).append((a1, a2, I))
        used.append(LOW_FILE)

    rows = [r for v in groups.values() for r in v]
    npts, nrow = len(groups), len(rows)
    fs = a.fs_na * 1e-9

    print("=" * 78)
    print("全部点的线性度 —— 样本内 vs 样本外")
    print("=" * 78)
    print("数据: %s" % " + ".join(os.path.basename(p) for p in used))
    print("      **%d 个电流点, %d 行**   (FS = %g nA)" % (npts, nrow, a.fs_na))
    print("")

    # ---------- 样本内: 用全部拟合, 再拿全部算残差 ----------
    x = fit(rows)
    q, ip, inn = consts(x)

    def resid(x, rr):
        return [(x[0] * a1 + x[1] + x[2] * a2 - I) * 1e12 for a1, a2, I in rr]

    def line(tag, e):
        rms = math.sqrt(sum(t * t for t in e) / len(e))
        mx = max(abs(t) for t in e)
        print("  %-22s n=%-4d  RMS %7.3f pA   max|残差| %7.3f pA"
              "   线性度 %.5f %% FS"
              % (tag, len(e), rms, mx, mx * 1e-12 / fs * 100))
        return mx

    print("三常数 (由全部 %d 行拟合): q=%.6e  I+=%+.4f nA  I-=%+.4f nA"
          % (nrow, q, ip * 1e9, inn * 1e9))
    print("")
    print("【样本内】拟合和评估用同一批点  ← **循环论证, 不能引用**")
    ins = line("全部 %d 点" % npts, resid(x, rows))
    print("")

    # ---------- 样本外: 留一, 每次留出一个**电流点**(3 次一起留出) ----------
    print("【样本外】留一交叉验证 (每次留出 1 个电流点)  ← **可以引用**")
    loo = []
    for k in groups:
        tr = [r for kk, v in groups.items() if kk != k for r in v]
        xk = fit(tr)
        loo += resid(xk, groups[k])
    oos = line("全部 %d 点" % npts, loo)
    print("")

    print("  ⚠️ 样本内比样本外小了 %.2f 倍 —— 那个倍数就是「用考题答案判考卷」"
          "的虚高量" % (oos / ins))
    print("")
    print("  可选口径: 若要跟历史数字对齐, 见 data/README.md —— 09-15 那批 19 点:")
    print("     样本内 0.00252 %FS (作废) / 固定奇偶拆分 0.00393 / LOO 0.00424")


if __name__ == '__main__':
    main()
