# -*- coding: utf-8 -*-
"""
脉冲波形反演 —— 第一性原理仿真 (离线, 不接硬件)

    python tools/sim_pulse_recover.py                       # 默认: 20nA/50%/20ms
    python tools/sim_pulse_recover.py --peak 40 --duty 0.25 --period 10
    python tools/sim_pulse_recover.py --tau 4 -o my.png

为什么有这个脚本
----------------
2026-09-14 的一次讨论中, 我先断言"脉冲周期比锯齿相位还短, 所以从码流反推不出
波形", 随后被证伪。这个脚本就是那个反证的可复现版本, 也是 `tools/wave_recover.py`
(将来写) 的算法原型与回归基准。

**核心事实**: 装置报出的**结果**是每个窗口一个平均值 (1 s 或 10 s), 那确实推不出
波形。但固件 `B`/`X` 回传的 6250 点原始码缓冲里有 —— **积分器的斜率就是电流**。
每个参考极性区间内, 码流是一条直线; 脉冲的通断会让斜率**折弯**。那些折弯就是
要找回的波形, 而且它每 160 µs 都在被采。

    I_net = q · Δc/Δt          (斜率)
    I_in  = I_net − I_ref      I_ref = +I_POS 若码上升, −I_NEG 若码下降

"码上升 <-> SEL_POS" 是固件的死逻辑 (current.c:413-416), 所以**不需要额外记录
参考状态** —— 看斜坡往哪走就知道当时注入的是哪一路参考。

分辨率
------
被测量是**读数的导数**, 所以一个采样点不含电流信息, 斜率要靠一段跨度 τ 换:

    σ_I · τ ≈ 0.8 pC      (电荷噪声, 实测; 与 τ 无关, 跨 24 倍范围)

时间分辨率恒等于 τ, 幅度分辨率随之改善。两者可以自由互换, 乘积锁死。
τ 超过 ~10 ms 后信号自身漂移接管 (实测地板 ≈ 0.05 nA), 再加 τ 收益很小。

噪声模型的来源
--------------
`SIG = 70` 码不是假设: 实测三个直流采集 (2/20/40 nA) 的**二阶差分**噪声都是
62–71 码, 且**几乎不随斜坡速率变**(斜坡变 4.5 倍, 噪声只动 14%) —— 所以它是
加在积分器节点上的电压噪声 ≈ 10 mV, 不是时序抖动、也不是 ADC 的噪声。

→ 这个仿真只验证**反演算法**, 不验证标定/增益。真实数据里 `q` 用标定值。
"""
import argparse
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# ---- 硬件常数 (标称值; 真实分析用标定过的 q) ----
Q_CODE = 1.5259e-14          # C/码  = C_INT x K,  K = 3.3/65536/0.33
TICK_S = 160e-6              # TIM6 节拍
CODE_LO, CODE_HI = 2602, 58963   # +/-4.3 V 对应码 (滞回阈值)
I_POS, I_NEG = 50.232e-9, -50.255e-9
SIG_CODE = 70.0              # 节点噪声 (码), 实测


def simulate(peak_a, duty, period_s, npt, seed=0, noise=SIG_CODE):
    """跑一遍电荷平衡, 返回 (码流, 每拍的参考极性, 每拍的真值电流)。"""
    rng = np.random.default_rng(seed)
    c = (CODE_LO + CODE_HI) / 2.0
    sel = +1                                  # +1 = SEL_POS (码上升)
    out = np.empty(npt)
    ref = np.empty(npt, dtype=np.int8)
    itrue = np.empty(npt)
    for k in range(npt):
        on = ((k * TICK_S) % period_s) < duty * period_s
        i_in = peak_a if on else 0.0
        itrue[k] = i_in
        out[k] = c + rng.normal(0, noise)     # 存下来的值带噪
        ref[k] = sel
        c += (i_in + (I_POS if sel > 0 else I_NEG)) * TICK_S / Q_CODE
        if out[k] >= CODE_HI:                 # 判决用带噪值 (与固件同构)
            sel = -1
        elif out[k] <= CODE_LO:
            sel = +1
    return out, ref, itrue


def _slope(y):
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    return (y * x).sum() / (x * x).sum()


def invert(c, npts, n):
    """滑窗反推 I(t)。返回 (t_s, I_A)。"""
    ts, Is = [], []
    h = n // 2
    for i in range(h, npts - h):
        s = _slope(c[i - h:i + h + 1])
        iref = I_POS if s > 0 else I_NEG        # 斜坡方向 <-> 参考极性
        ts.append((i + 0.5) * TICK_S)
        Is.append(Q_CODE * s / TICK_S - iref)
    return np.array(ts), np.array(Is)


def switches(ref, npts):
    """参考切换的下标 (反演时必须避开, 否则会减错参考)"""
    return np.where(np.diff(ref) != 0)[0] + 1


def pulse_edges(itrue):
    return np.where(np.diff((itrue > 0).astype(int)) != 0)[0] + 1


def clean_mask(t_s, n, bnd):
    """窗 [i-h, i+h] 内不含任何跳变 (参考切换或脉冲沿) 的样本掩码"""
    m = np.ones(len(t_s), bool)
    h = n // 2
    for j, tk in enumerate((t_s / TICK_S).astype(int)):
        lo, hi = tk - h, tk + h
        k = np.searchsorted(bnd, lo, 'right') - 1
        m[j] = (lo >= bnd[k]) and (hi < bnd[k + 1])
    return m


def fold(t_s, i_a, period_s, nb=40):
    ph = np.mod(t_s, period_s) / period_s
    b = np.clip((ph * nb).astype(int), 0, nb - 1)
    mid = np.full(nb, np.nan)
    se = np.full(nb, np.nan)
    for j in range(nb):
        v = i_a[b == j]
        if len(v) > 1:
            mid[j] = v.mean()
            se[j] = v.std(ddof=1) / math.sqrt(len(v))
    return (np.arange(nb) + 0.5) / nb * period_s, mid, se


def main():
    ap = argparse.ArgumentParser(description="脉冲波形反演仿真")
    ap.add_argument('--peak', type=float, default=20.0, help='峰值 (nA)')
    ap.add_argument('--duty', type=float, default=0.5, help='占空比')
    ap.add_argument('--period', type=float, default=20.0, help='周期 (ms)')
    ap.add_argument('--win', type=float, default=1000.0, help='窗口长度 (ms)')
    ap.add_argument('--noise', type=float, default=SIG_CODE, help='节点噪声 (码)')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tau', type=float, default=None,
                    help='画图用的 τ (ms); 默认取最短平台的 1/3')
    ap.add_argument('-o', '--out', default='data/sim_pulse_recover.png')
    a = ap.parse_args()

    peak, duty, period = a.peak * 1e-9, a.duty, a.period * 1e-3
    npt = int(round(a.win * 1e-3 / TICK_S))

    c, ref, itrue = simulate(peak, duty, period, npt, a.seed, a.noise)
    sw, eg = switches(ref, npt), pulse_edges(itrue)
    bnd = np.sort(np.r_[sw, eg, 0, npt])

    # 固件式(6)的窗口平均: I = q*dC/(N*T) - (m*I+ + n*I-)/N
    m, n = int((ref > 0).sum()), int((ref < 0).sum())
    win_avg = Q_CODE * (c[-1] - c[0]) / (npt * TICK_S) - \
        (m * I_POS + n * I_NEG) / (m + n)

    print("=" * 78)
    print("脉冲波形反演仿真")
    print("=" * 78)
    print("配置   峰值 %.4g nA / 占空比 %.1f%% / 周期 %.4g ms / 窗口 %.4g ms"
          % (a.peak, duty * 100, a.period, a.win))
    print("       噪声 %.1f 码 (节点电压噪声 %.2f mV), seed=%d"
          % (a.noise, a.noise * 1.525879e-4 * 1e3, a.seed))
    print("真值   平均电流 = %+.5f nA" % (itrue.mean() * 1e9))
    print("式(6)  窗口平均 = %+.5f nA   (m=%d n=%d)" % (win_avg * 1e9, m, n))
    print("       -> 装置如实报出平均值, 但给不出「峰值」和「施加时间」")
    seg = np.diff(np.r_[0, sw, npt])
    print("")
    print("参考切换 %d 次 (最短相位 %d 拍 = %.2f ms); 脉冲沿 %d 次"
          % (len(sw), int(seg.min()), seg.min() * TICK_S * 1e3, len(eg)))
    print("锯齿半相位 %.2f ms  vs  脉冲周期 %.4g ms"
          % (np.diff(sw).mean() * TICK_S * 1e3, a.period))

    # ---- 干净窗统计: 单点噪声随 τ 的变化 ----
    print("")
    print("--- 只取严格干净的窗口 (窗内既不跨参考切换, 也不跨脉冲沿) ---")
    print("%5s %9s %20s %20s %10s"
          % ("N", "τ (ms)", "通平台 (真值 %.3g)" % a.peak,
             "断平台 (真值 0)", "可用占比"))
    stats = []
    for n in (4, 6, 8, 12, 16, 32):
        t, I = invert(c, npt, n)
        mk = clean_mask(t, n, bnd)
        on = (itrue[bnd[np.searchsorted(bnd, (t / TICK_S).astype(int),
                                        'right') - 1]] > 0)
        g = I[mk & on] * 1e9
        z = I[mk & ~on] * 1e9
        stats.append((n, n * TICK_S * 1e3, g.std(), mk.mean()))
        print("%5d %9.2f %13.3f±%.3f %13.3f±%.3f %9.1f%%"
              % (n, n * TICK_S * 1e3, g.mean(), g.std(),
                 z.mean(), z.std(), 100 * mk.mean()))
    if len(stats) > 2:
        N = np.array([s[0] for s in stats])
        S = np.array([s[2] for s in stats])
        p, la = np.polyfit(np.log(N), np.log(S), 1)
        # 换成 τ 的幂律: N = τ/T, 所以 σ ∝ τ^-p 用的是同一个指数
        A = np.exp(la) * (TICK_S * 1e3) ** p       # τ 以 ms 计时 σ 的系数
        print("  幂律: σ_I = %.4f nA / N^%.2f  =  %.4f nA / (τ/ms)^%.2f"
              "   (白噪声理论 τ^-1.5)" % (np.exp(la), -p, A, -p))

    # ---- 折叠 ----
    n_use = (a.tau and max(4, int(round(a.tau * 1e-3 / TICK_S)))) or 6
    t, I = invert(c, npt, n_use)
    mk = clean_mask(t, n_use, bnd)
    tf, mf, ef = fold(t[mk], I[mk], period)
    nb = len(mf)
    # 平台内部 (避开前后各 15% 的过渡箱); 占空比 50% 时通断各占一半
    half = int(round(duty * nb))
    ins_on = np.zeros(nb, bool)
    ins_on[max(1, int(half * .15)):max(2, int(half * .85))] = True
    ins_off = np.zeros(nb, bool)
    ins_off[half + max(1, int((nb - half) * .15)):nb - max(1, int((nb - half) * .15))] = True

    hi = mf[ins_on].mean() * 1e9
    lo = mf[ins_off].mean() * 1e9
    print("")
    print("--- 折叠平均 (τ = %.2f ms, %d 箱, 每箱 ~%d 个点) ---"
          % (n_use * TICK_S * 1e3, nb, len(t[mk]) // nb))
    print("  通平台 = %8.4f nA   (真值 %8.4f)   偏差 %+.3f%%"
          % (hi, a.peak, (hi - a.peak) / a.peak * 100))
    print("  断平台 = %8.4f nA   (真值    0.000)" % lo)
    print("  幅度   = %8.4f nA   (真值 %8.4f)   偏差 %+.3f%%"
          % (hi - lo, a.peak, ((hi - lo) - a.peak) / a.peak * 100))
    print("  每箱标准误 (平台内中位) = %.4f nA"
          % (np.nanmedian(np.r_[ef[ins_on], ef[ins_off]]) * 1e9))
    print("")
    print("  上升时间只能给上界 ≈ τ = %.2f ms —— 估的是 τ 内的平均," % (n_use * TICK_S * 1e3))
    print("  边沿被卷积成宽 τ 的斜坡。真边沿更快, 这装置分辨不了。")

    # ---- 图 ----
    nshow = min(npt, int(round(80e-3 / TICK_S)))
    fig, ax = plt.subplots(3, 1, figsize=(13, 10.5))
    ax[0].plot(np.arange(nshow), c[:nshow], lw=.9, color="C0")
    for e in eg[eg < nshow]:
        ax[0].axvline(e, color="C3", ls=":", lw=1)
    for s_ in sw[sw < nshow]:
        ax[0].axvline(s_, color="gray", ls="--", lw=1)
    ax[0].plot([], [], color="C3", ls=":", label="脉冲沿 (要找回的东西)")
    ax[0].plot([], [], color="gray", ls="--", label="参考切换 (干扰)")
    ax[0].set_title("仿真码流 (前 %d 拍 = %.0f ms) —— 每个通断都让斜率折弯"
                    % (nshow, nshow * TICK_S * 1e3))
    ax[0].set_ylabel("码"); ax[0].grid(alpha=.3)
    ax[0].legend(fontsize=8, loc="upper right")

    t2, I2 = invert(c, npt, 6)
    tmax = min(npt * TICK_S * 1e3, 8 * a.period)
    ax[1].plot(np.arange(npt) * TICK_S * 1e3, itrue * 1e9, 'k--', lw=1.2,
               label="真值")
    ax[1].plot(t2 * 1e3, I2 * 1e9, lw=.8, color="C0", label="反演 (τ=0.96 ms)")
    ax[1].set_xlim(0, tmax)
    ax[1].set_ylim(-0.35 * a.peak, 1.75 * a.peak)
    ax[1].set_ylabel("nA"); ax[1].grid(alpha=.3); ax[1].legend(fontsize=9)
    ax[1].set_title("反演 I(t) vs 真值 (尖刺全部落在参考切换处, 可识别剔除)")

    ax[2].errorbar(tf * 1e3, mf * 1e9, yerr=ef * 1e9, fmt='o-', ms=4,
                   capsize=2, color="C3")
    ax[2].plot([0, a.period * duty, a.period * duty, a.period, a.period],
               [0, 0, a.peak, a.peak, 0], 'k--', lw=1.5, label="真值")
    ax[2].set_xlabel("折叠相位 (ms)"); ax[2].set_ylabel("nA")
    ax[2].grid(alpha=.3); ax[2].legend(fontsize=9)
    ax[2].set_title("折叠平均后: 峰值、占空比、边沿位置全部恢复")
    fig.tight_layout()
    d = os.path.dirname(a.out)
    if d:
        os.makedirs(d, exist_ok=True)
    fig.savefig(a.out, dpi=130)
    print("")
    print("已存", a.out)


if __name__ == '__main__':
    main()
