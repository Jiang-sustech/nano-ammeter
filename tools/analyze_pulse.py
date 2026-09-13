# -*- coding: utf-8 -*-
"""
脉冲电流实验的分析 (方案见 docs/脉冲电流实验方案.md)

读两组采集:
    脉冲组  raw_*.npz, true_na = **平均电流** I_peak*duty (不是峰值!)
    直流组  raw_*.npz, true_na = 直流值

输出:
    pulse_analysis.png   脉冲点与直流点画在同一张图上 + 残差
    三条判据的数值

**最容易犯的错**: 把 2636B 脉冲模式下的回读值 (那是 ton 末尾的**峰值**)
当成 --true= 填进去。峰值要乘占空比才是平均值。

用法:
    python tools/analyze_pulse.py --pulse "data/pulse/*.npz" --dc "data/dc/*.npz"
    python tools/analyze_pulse.py --pulse "raw_*_P*.npz"        # 只有脉冲组也行
"""
import glob
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def opt(name, default=None):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def load(pattern):
    """返回 {I_true(nA): [board 读数 nA, ...]}"""
    pts = {}
    for f in sorted(glob.glob(pattern)):
        d = np.load(f, allow_pickle=True)
        if "true_na" not in d:
            continue
        t = float(d["true_na"])
        if not np.isfinite(t):
            continue
        r = d["result_na"]
        if not np.isfinite(r):
            r = d["result_ext_na"]
        if not np.isfinite(r):
            continue
        pts.setdefault(t, []).append(float(r))
    return pts


def mean_pts(pts):
    xs = np.array(sorted(pts))
    ys = np.array([np.mean(pts[x]) for x in xs])
    es = np.array([np.std(pts[x]) / np.sqrt(len(pts[x])) if len(pts[x]) > 1 else 0.0
                   for x in xs])
    return xs, ys, es


def main():
    p_pat = opt("--pulse", "data/pulse/*.npz")
    d_pat = opt("--dc")

    pulse = load(p_pat)
    if not pulse:
        print("没读到脉冲组 (%s) —— 检查 --pulse 的路径, 以及 npz 里有没有 true_na"
              % p_pat)
        return

    px, py, pe = mean_pts(pulse)
    print("=" * 66)
    print("脉冲组: %d 个电流点, %d 次采集" % (len(px), sum(len(v) for v in pulse.values())))
    print("=" * 66)

    dc = load(d_pat) if d_pat else {}
    if dc:
        dx, dy, de = mean_pts(dc)
        print("直流组: %d 个点, %d 次采集" % (len(dx), sum(len(v) for v in dc.values())))
    print()

    # ---- 判据 1: 与计算平均值的偏差 ----
    rel = (py - px) / px * 100.0
    print("── 判据 1: 脉冲读数 vs 计算平均值 ──")
    for x, y, e, r in zip(px, py, pe, rel):
        print("   I_avg = %+8.4f nA   读数 %+8.4f nA   偏差 %+6.3f %%"
              % (x, y, r))
    print("   最大 |偏差| = %.3f %%   (判据 < 1.5%%)  -> %s"
          % (np.abs(rel).max(), "通过" if np.abs(rel).max() < 1.5 else "**未通过**"))

    # ---- 判据 2: 与等效直流之差 (核心) ----
    if dc:
        common = sorted(set(np.round(px, 6)) & set(np.round(dx, 6)))
        print()
        print("── 判据 2: 同一平均值下, 脉冲 vs 直流 (核心判据) ──")
        if not common:
            print("   脉冲组与直流组没有相同的 I_avg, 无法比较")
            print("   -> 两组请至少安排一个相同的平均电流 (方案里是 P1/P4 对 D1)")
        worst = 0.0
        for v in common:
            a = np.mean(pulse[v]) if v in pulse else None
            b = np.mean(dc[v]) if v in dc else None
            if a is None or b is None:
                continue
            dd = (a - b) / b * 100.0
            worst = max(worst, abs(dd))
            print("   I_avg = %+8.4f nA   脉冲 %+8.4f   直流 %+8.4f   差 %+6.3f %%"
                  % (v, a, b, dd))
        if worst:
            print("   最大 |差| = %.3f %%   (判据 < 0.5%%)  -> %s"
                  % (worst, "通过" if worst < 0.5 else "**未通过**"))

    # ---- 判据 3: 线性度 ----
    if len(px) >= 3:
        a, b = np.polyfit(px, py, 1)
        r = py - (a * px + b)
        fs = px.max() - px.min()
        print()
        print("── 判据 3: 脉冲组的线性度 ──")
        print("   斜率 %.5f   截距 %+.4f nA   R² = %.8f"
              % (a, b, 1 - np.sum(r ** 2) / np.sum((py - py.mean()) ** 2)))
        print("   max|残差|/FS = %.4f %%   (FS = %.4f nA)"
              % (np.abs(r).max() / fs * 100, fs))

    # ---- 画图 ----
    fig, ax = plt.subplots(1, 2, figsize=(13.5, 5.5))

    ax[0].errorbar(px, py, yerr=pe, fmt="o", ms=6, capsize=3,
                   color="C0", label="脉冲信号")
    if dc:
        ax[0].errorbar(dx, dy, yerr=de, fmt="s", ms=6, mfc="none", capsize=3,
                       color="C3", label="等效直流")
    lo = min(px.min(), dx.min() if dc else px.min()) * 1.15
    hi = max(px.max(), dx.max() if dc else px.max()) * 1.15
    ax[0].plot([lo, hi], [lo, hi], "k--", lw=1, label="理想 1:1")
    ax[0].set_xlabel("平均电流 = 峰值 × 占空比 (nA)")
    ax[0].set_ylabel("装置读数 (nA)")
    ax[0].set_title("脉冲 vs 等效直流")
    ax[0].grid(alpha=.3); ax[0].legend(fontsize=9)

    ax[1].plot(px, rel, "o-", ms=5)
    ax[1].axhline(0, color="k", ls="--", lw=1)
    ax[1].axhspan(-1.5, 1.5, color="green", alpha=.12, label="判据 ±1.5%")
    ax[1].set_xlabel("平均电流 (nA)"); ax[1].set_ylabel("偏差 (%)")
    ax[1].set_title("脉冲读数相对计算平均值的偏差")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=9)

    fig.tight_layout()
    fig.savefig("pulse_analysis.png", dpi=150)
    print("\n已存 pulse_analysis.png")


if __name__ == "__main__":
    main()
