# -*- coding: utf-8 -*-
"""
从 M0 采集拟合复位相寄生放电的时间常数 tau, 以及注入电荷 Q。

背景
----
ADG1219 的 `EN=0` 是**高阻态而不是 0V**。断开瞬间, 100MΩ 那一端的节点上
存着寄生电容 C_p 的电荷, 它经 100MΩ 泄放到虚地:

    i(t) = (V_n / R) * exp(-t/tau)        tau = R * C_p
    总电荷 Q = C_p * V_n

Q 全部灌进积分电容, 表现为积分器输出上的一个**指数趋近的台阶**。ADC 看到的是
电平移位后的反相值, 所以码域上是:

    code(t) = c0 + A * (1 - exp(-t/tau)) + s * t
              |    |                      |
              |    |                      +-- 被测电流积出来的线性漂移
              |    +-- 台阶幅度 (码);  |A| = Q / q
              +-- 断开瞬间的电平

拟合方法
--------
**固定 tau 时模型对 (c0, A, s) 是线性的** —— 前两项分别是常数项和一个固定的
基函数, 第三项是 t。于是不必上非线性优化: 对 tau 扫一遍, 每个 tau 解一次
线性最小二乘, 取残差最小的。粗扫对数网格 + 两轮细化, 不依赖 scipy。

两个独立的 C_p
--------------
    C_p(台阶) = |A| * q / V_n        <- 用**幅度**
    C_p(tau)  = tau / R              <- 用**时间常数**
两者应当一致。不一致说明模型不对 (比如不是单一指数, 或 R 不是那个 R)。

用法
----
    python tools/fit_tau.py                      # 找当前目录的 raw_*_M0.npz
    python tools/fit_tau.py -f raw_09_13_M0.npz
    python tools/fit_tau.py --plot
"""
import glob
import os
import sys

import numpy as np

# ---- 物理常数 (与固件/拟合保持一致) ----
Q_PER_CODE = 1.5546e-14     # C/码, 由 M1/M2 或闭环拟合给出 (标称 1.5259e-14)
R_OHM      = 100.0e6        # 限流电阻。万用表 99.48MΩ(±1%), 拟合反推 100.51MΩ
V_NODE     = 5.0            # 断开瞬间节点电压 = 参考电压 (V), 取标称


def load(path=None, sub=0):
    if path is None:
        cands = sorted(glob.glob("raw_*_M%d.npz" % sub))
        if not cands:
            raise SystemExit("没找到 raw_*_M%d.npz —— 先用 "
                             "`python tools/nanoammeter_capture.py COM9 --manual=%d` 采一组"
                             % (sub, sub))
        path = cands[-1]
    d = np.load(path, allow_pickle=True)
    tn = int(d["manual_tick_ns"]) if "manual_tick_ns" in d else 0
    return d, path, tn


def fit_exp(t, y):
    """对 tau 一维搜索, 每个 tau 解一次线性最小二乘。返回 (tau, c0, A, s, r, rss)。"""
    def solve(tau):
        e = 1.0 - np.exp(-t / tau)
        M = np.column_stack([np.ones_like(t), e, t])
        x, *_ = np.linalg.lstsq(M, y, rcond=None)
        r = y - M @ x
        return float(np.sum(r * r)), x, r

    # 粗扫: 5us ~ 50ms 对数网格
    best = None
    for tau in np.geomspace(5e-6, 50e-3, 120):
        rss, x, r = solve(tau)
        if best is None or rss < best[0]:
            best = (rss, tau, x, r)

    # 两轮细化
    for span in (0.3, 0.05):
        _, tau0, _, _ = best
        for tau in np.linspace(tau0 * (1 - span), tau0 * (1 + span), 200):
            if tau <= 0:
                continue
            rss, x, r = solve(tau)
            if rss < best[0]:
                best = (rss, tau, x, r)

    rss, tau, x, r = best
    return tau, x[0], x[1], x[2], r, rss


def main():
    path = None
    for a in sys.argv[1:]:
        if a.startswith("-f="):
            path = a[3:]
    if "-f" in sys.argv:
        path = sys.argv[sys.argv.index("-f") + 1]

    d, path, tick_ns = load(path)
    wi = d["wave_int"].astype(float)
    we = d["wave_ext"].astype(float)
    n = len(wi)

    if tick_ns <= 0:
        print("!! npz 里没有 tick_ns, 按 19.2us 估算 (老数据)")
        tick_ns = 19200

    t = np.arange(n) * (tick_ns * 1e-9)
    print("=" * 66)
    print("M0 衰减拟合   %s" % os.path.basename(path))
    print("=" * 66)
    print("  点数 N = %d, 实测采样周期 = %d ns, 覆盖 %.1f ms"
          % (n, tick_ns, t[-1] * 1e3))
    print("  首点 = %d 码, 末点 = %d 码, 首尾差 = %+d 码"
          % (wi[0], wi[-1], wi[-1] - wi[0]))

    noise = float(np.std(np.diff(wi))) / np.sqrt(2.0)
    print("  相邻差分估的噪声底 ≈ %.1f 码 (单点)" % noise)

    for name, y in (("内置 ADC", wi), ("ADS8866 ", we)):
        tau, c0, A, s, r, rss = fit_exp(t, y)
        rmse = np.sqrt(rss / len(t))
        cp_step = abs(A) * Q_PER_CODE / V_NODE
        cp_tau = tau / R_OHM
        i_drift = s * Q_PER_CODE          # 码/秒 * q = A

        print()
        print("  ── %s ──" % name)
        print("    tau   = %-10s   C_p(由 tau) = %-9s"
              % (_fmt_t(tau), _fmt_c(cp_tau)))
        print("    台阶 A = %+.1f 码       C_p(由幅度) = %-9s"
              % (A, _fmt_c(cp_step)))
        print("    起点 c0 = %.0f 码      线性漂移 s = %+.1f 码/秒 -> %+.3f pA"
              % (c0, s, i_drift * 1e12))
        print("    残差 RMSE = %.1f 码 (噪声底 %.1f)" % (rmse, noise))

        if cp_step > 0:
            ratio = cp_tau / cp_step
            print("    两个 C_p 之比 = %.2f  %s"
                  % (ratio, "(一致)" if 0.5 < ratio < 2.0 else
                     "(**) 不一致 -> 不是单一指数, 或 R/V_n 取值不对"))
        print("    死区: %.1f 码的电荷 = %.2f pC = %.1f 倍噪声底"
              % (A, abs(A) * Q_PER_CODE * 1e12, abs(A) / max(noise, 1e-9)))

    # ---- 判据: 现有 SI_SETTLE_TICKS 够不够 ----
    tau, _, _, _, _, _ = fit_exp(t, wi)
    print()
    print("  ── 对小电流模式 (SI_SETTLE_TICKS) 的判据 ──")
    print("    实测 tau ≈ %s" % _fmt_t(tau))
    print("    但 SI_SETTLE_TICKS=8 是**阻塞 ReadBoth 的次数**, 不是 160us 拍:")
    print("    实际等待 ≈ 8 x 19.2us = 154us")
    need = 5 * tau
    print("    需要 > 5*tau = %s;  现有 154us -> %s"
          % (_fmt_t(need), "够" if need < 154e-6 else "**不够**"))

    if "--plot" in sys.argv:
        plot(t, wi, we)


def _fmt_t(x):
    if x < 1e-3:
        return "%.1f us" % (x * 1e6)
    if x < 1.0:
        return "%.2f ms" % (x * 1e3)
    return "%.3f s" % x


def _fmt_c(x):
    if x < 1e-12:
        return "%.2f pF" % (x * 1e12)
    if x < 1e-9:
        return "%.1f pF" % (x * 1e12)
    return "%.0f pF" % (x * 1e12)


def plot(t, wi, we):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    tau, c0, A, s, r, _ = fit_exp(t, wi)
    tf = np.linspace(t[0], min(t[-1], 20 * tau), 2000)
    mf = c0 + A * (1 - np.exp(-tf / tau)) + s * tf

    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].plot(t * 1e3, wi, ".", ms=2, label="内置 ADC")
    ax[0].plot(t * 1e3, we, ".", ms=2, alpha=.5, label="ADS8866")
    ax[0].plot(tf * 1e3, mf, "r-", lw=1.5,
               label="拟合 tau=%.1fus  A=%.0f" % (tau * 1e6, A))
    ax[0].set_xlabel("断开后时间 (ms)"); ax[0].set_ylabel("原始码")
    ax[0].set_title("M0: 参考断开后的暂态"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)

    ax[1].plot(t * 1e3, r, ".", ms=2)
    ax[1].axhline(0, color="k", ls="--", lw=1)
    ax[1].set_xlabel("断开后时间 (ms)"); ax[1].set_ylabel("残差 (码)")
    ax[1].set_title("拟合残差"); ax[1].grid(alpha=.3)

    fig.tight_layout()
    fig.savefig("tau_fit.png", dpi=150)
    print("\n已存 tau_fit.png")


if __name__ == "__main__":
    main()
