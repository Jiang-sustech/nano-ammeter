# -*- coding: utf-8 -*-
"""
未校准输入—输出关系与线性度 (对应第 4 章 4.3.3 / 图 4-2)

用一组已知输入电流的采集 (raw_*.npz, 含 true_na) 算:

    I_board = a · I_true + b

并给出线性度。**线性度与准确度必须分开报告** —— R² 接近 1 只说明"形状是直的",
不代表准确; 存在稳定的 1% 增益误差时 R² 仍会非常接近 1。

线性度的三种常用定义 (本脚本都给, 免得起争议):

    最小二乘线性度  相对**最佳直线**的最大偏离 / FS      <- 最宽松, 最常用
    端基线性度      相对**两端点连线**的最大偏离 / FS    <- 最严格, 也最苛刻
    独立线性度      相对**最小化最大偏离那条线**的偏离   <- 定义上最公平

其中 FS (满量程) 取实测范围的**跨度** |I_max - I_min|。

用法:
    python tools/fit_linearity.py                 # 扫描当前目录的 raw_*.npz
    python tools/fit_linearity.py -d <目录>
    python tools/fit_linearity.py --plot          # 另存图 4-2
"""
import glob
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 图里是中文标签, 默认字体 DejaVu Sans 没有 CJK 字形 (会画成方框)
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False   # 否则负号也是方框


def load(directory="."):
    """收集 {I_true: [board 读数, ...]} —— 内部路与外部路各一套。"""
    rows = []
    for f in sorted(glob.glob(os.path.join(directory, "raw_*.npz"))):
        d = np.load(f, allow_pickle=True)
        if "true_na" not in d:
            continue
        it = float(d["true_na"])
        rows.append((it,
                     float(d["result_na"]),
                     float(d["result_ext_na"]),
                     os.path.basename(f)))
    return rows


def lsq(x, y):
    """最小二乘直线 y = a·x + b, 返回 a, b, 残差, R²。"""
    a, b = np.polyfit(x, y, 1)
    r = y - (a * x + b)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1.0 - np.sum(r ** 2) / ss_tot if ss_tot > 0 else float("nan")
    return a, b, r, r2


def through_endpoints(x, y):
    """端基直线: 过 (x_min, y_min) 与 (x_max, y_max) 两点。"""
    i0, i1 = np.argmin(x), np.argmax(x)
    a = (y[i1] - y[i0]) / (x[i1] - x[i0])
    b = y[i0] - a * x[i0]
    return a, b, y - (a * x + b)


def independent(x, y):
    """独立线性度: 使 max|残差| 最小的那条直线 (Chebyshev 意义下最优)。

    固定斜率 a 时, 残差 r = y - a·x 只差一个平移; 让 r 的 max 与 min 对称
    (b = (max+min)/2) 就是该斜率下的最优截距, 此时最大偏离 = (max-min)/2。
    于是整个问题退化成一维: 只对 a 搜索。这里用逐步收缩的扫描, 不需要 scipy。
    """
    def dev(a):
        r = y - a * x
        return (r.max() - r.min()) / 2.0

    a0 = np.polyfit(x, y, 1)[0]
    best_a, best_v, span = a0, dev(a0), abs(a0) * 0.1
    for _ in range(60):
        for a in np.linspace(best_a - span, best_a + span, 41):
            v = dev(a)
            if v < best_v:
                best_v, best_a = v, a
        span /= 5.0
    r = y - best_a * x
    b = (r.max() + r.min()) / 2.0
    return best_a, b, y - (best_a * x + b)


def report(name, x, y, out):
    a, b, r, r2 = lsq(x, y)
    fs = x.max() - x.min()
    ae, be, re_ = through_endpoints(x, y)
    ai, bi, ri = independent(x, y)

    ae_l = np.max(np.abs(re_))
    ai_l = np.max(np.abs(ri))
    ls_l = np.max(np.abs(r))

    out.append("")
    out.append("  ── %s ──" % name)
    out.append("    最小二乘拟合   I_board = %.6f · I_true %+.4f pA" % (a, b * 1e3))
    out.append("    斜率 a = %.6f     截距 b = %+.3f pA     R² = %.8f" % (a, b * 1e3, r2))
    out.append("    残差 RMS = %.3f pA    最大 |残差| = %.3f pA" %
               (np.sqrt(np.mean(r ** 2)) * 1e3, ls_l * 1e3))
    out.append("")
    out.append("    线性度 (FS = |I_max - I_min| = %.4f nA):" % fs)
    out.append("      最小二乘线性度  max|残差| / FS        = %.4f %%   (%.3f pA)" % (ls_l / fs * 100, ls_l * 1e3))
    out.append("      端基线性度      相对两端点连线的偏离   = %.4f %%   (%.3f pA)" % (ae_l / fs * 100, ae_l * 1e3))
    out.append("      独立线性度      最优直线的最大偏离     = %.4f %%   (%.3f pA)" % (ai_l / fs * 100, ai_l * 1e3))
    out.append("")
    out.append("    两个**不同**的量, 必须分开报 (相对读数 |I_true|):")
    nonlin = np.abs(r) / np.abs(x)          # 相对**拟合直线**的偏离 -> 非线性
    acc = np.abs(y - x) / np.abs(x)         # 相对**参考值**的偏离   -> 未校准准确度
    out.append("      非线性   |y - (a·x+b)| / |x| :  最大 = %.3f %%   中位 = %.3f %%"
               % (nonlin.max() * 100, np.median(nonlin) * 100))
    out.append("      准确度   |y - x|       / |x| :  最大 = %.3f %%   中位 = %.3f %%"
               % (acc.max() * 100, np.median(acc) * 100))
    out.append("      -> 两者相差 %.0f 倍。R² 再好也**不代表准确**。" % (acc.max() / nonlin.max()))
    return a, b, r, r2, fs


def main():
    directory = "."
    if "-d" in sys.argv:
        directory = sys.argv[sys.argv.index("-d") + 1]

    rows = load(directory)
    if not rows:
        print("没找到带 true_na 的 raw_*.npz")
        return

    it = np.array([r[0] for r in rows])
    yin = np.array([r[1] for r in rows])
    yex = np.array([r[2] for r in rows])

    out = []
    out.append("")
    out.append("=" * 68)
    out.append("未校准输入—输出关系   N = %d 次采集, %d 个电流点" % (len(rows), len(set(it))))
    out.append("=" * 68)

    report("内部 ADC  (结果行 I=)", it, yin, out)
    report("ADS8866  (结果行 X=)", it, yex, out)

    # 每个电流点的均值 —— 逐点看比逐次看更清楚
    out.append("")
    out.append("  ── 逐点 (每点取均值) ──")
    out.append("    %10s  %12s  %12s  %10s  %10s" %
               ("I_true(nA)", "I_board(nA)", "X_board(nA)", "δ_in(%)", "δ_ex(%)"))
    for v in sorted(set(it)):
        m = it == v
        mi, mx = yin[m].mean(), yex[m].mean()
        out.append("    %+10.4f  %+12.4f  %+12.4f  %+10.3f  %+10.3f" %
                   (v, mi, mx, (mi - v) / v * 100, (mx - v) / v * 100))

    print("\n".join(out))

    if "--plot" in sys.argv:
        plot(it, yin, yex)


def plot(it, yin, yex):
    a, b, _, _ = lsq(it, yin)
    lim = np.array([it.min(), it.max()]) * 1.15
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))

    ax[0].plot(it, yin, "o", ms=5, label="内部 ADC (I=)")
    ax[0].plot(it, yex, "s", ms=5, mfc="none", label="ADS8866 (X=)")
    ln = np.array([lim[0], lim[1]])
    ax[0].plot(ln, ln, "k--", lw=1, label="理想 1:1")
    ax[0].plot(ln, a * ln + b, "r-", lw=1, label="最小二乘 a=%.5f" % a)
    ax[0].set_xlim(lim); ax[0].set_ylim(lim)
    ax[0].set_xlabel("2636B 回读 I_true (nA)"); ax[0].set_ylabel("装置读数 (nA)")
    ax[0].set_title("未校准输入—输出关系"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)

    ax[1].plot(it, (yin - (a * it + b)) * 1e3, "o", ms=5, label="内部 ADC")
    ax[1].plot(it, (yex - (a * it + b)) * 1e3, "s", ms=5, mfc="none", label="ADS8866")
    ax[1].axhline(0, color="k", ls="--", lw=1)
    ax[1].set_xlabel("I_true (nA)"); ax[1].set_ylabel("相对最小二乘直线的残差 (pA)")
    ax[1].set_title("残差"); ax[1].grid(alpha=.3); ax[1].legend(fontsize=8)

    fig.tight_layout()
    fig.savefig("linearity.png", dpi=150)
    print("\n已存 linearity.png  (图 4-2)")


if __name__ == "__main__":
    main()
