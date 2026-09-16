# -*- coding: utf-8 -*-
"""
真实脉冲的**波形反推**验证 —— 实际设定 vs 从码流反推出来的波形

    python tools/pulse_real.py --visa "TCPIP0::192.168.0.2::5025::SOCKET" --port COM6
    python tools/pulse_real.py --visa ... --peak 8 --ton 100 --toff 100

为什么用 PC 翻转而不是 2636B 的脉冲源
--------------------------------------
2026-09-16 实测: `ConfigPulseIMeasureVSweepLin` (KIPulse 厂脚本) **在这台
3.2.1 固件上跑不起来**, 三个坑逐个查过:

  1. **参数顺序错两位** (已修): 官方签名是 **10 个参数**
         (smu, bias, start, stop, limit, ton, toff, points, buffer, tag)
     我们原来传 8 个, 于是 n_pulse→stop、ton→limit、toff→ton… 整体串位。
     **而且不报错** —— TSP 自己做了类型转换。
  2. **缓冲必须开 appendmode**: 不开时脚本返回
         `false  Buffer must have appendmode activated to measure multiple pulses`
     **错误队列仍是 0** —— 失败走返回值, 不走错误队列。所以
     `smu_write` 的错误检查抓不到, 必须**查返回值**。
  3. 上面两条修完, 脚本返回 `true  Pulse 1 configured.`, **但触发没使能**
     (`trigger.source.action = 0`, `trigger.count = 1`); 手动设成 ENABLE、
     count=10 之后, 缓冲**仍是 0 点**。没再往下查。

所以退回 `--pc-chop` 那条路 (sweep_pulse.py 文件头也写了这个退路):
**PC 直接翻转输出**。它只适合长脉冲 —— LAN 往返 + Python 调度的抖动在 ms
量级, 所以本脚本默认 **200 ms 周期** (抖动 ~5%), 而不是 10 ms。

反推的分辨率与这个周期是匹配的: 时间分辨率 = 斜率跨度 τ (~10 ms),
200 ms 周期给 20 个分辨单元, 够。

约束 (见 docs/脉冲电流实验方案.md §2.2)
--------------------------------------
    I_avg <= min( 45 nA , 50*(1-D) , 860 pC / T )
T = 200 ms 时第三条卡死 -> I_avg <= 4.3 nA。所以默认 峰值 8 nA / 50% -> 4 nA。

⚠️ 抖动: ton/toff 是 PC 用 perf_counter 掐的, 但**翻转指令本身要过 LAN**,
   单程 5~20 ms。所以实际占空比与设定值有 ~5% 的出入 —— 图上会如实画出来。
"""
import argparse
import math
import os
import sys
import threading
import time

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_set, smu_write,
                       board_open, board_measure, board_read_waveform,
                       board_warmup, board_set_window)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

TICK_S = 160e-6          # TIM6 节拍
# 装置**当前**烧进去的常数 (2026-09-16 写入的 162 点拟合值) —— 反推要用它,
# 不能用标称值, 否则反推出来的电流会整体偏 1%。
Q_CODE = 15596e-18       # C/码
I_POS = 49712e-12        # A
I_NEG = -49739e-12


def _slope(y):
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    return (y * x).sum() / (x * x).sum()


def invert(c, n):
    """滑窗反推 I(t)。返回 (t_s, I_A)。

    原理: 积分器的**斜率就是电流**。每个参考极性区间内码流是直线, 而
    "码上升 <-> SEL_POS" 是固件的死逻辑, 所以看斜坡往哪走就知道当时注入的
    是哪一路参考, 不需要额外记录状态。
    """
    ts, Is = [], []
    h = n // 2
    for i in range(h, len(c) - h):
        s = _slope(c[i - h:i + h + 1])
        iref = I_POS if s > 0 else I_NEG
        ts.append((i + 0.5) * TICK_S)
        Is.append(Q_CODE * s / TICK_S - iref)
    return np.array(ts), np.array(Is)


def fold(t_s, i_a, period_s, nb=60):
    """按周期折叠 -> 一个周期内的平均波形 (把 5 个周期叠起来降噪)。"""
    ph = np.mod(t_s, period_s) / period_s
    b = np.clip((ph * nb).astype(int), 0, nb - 1)
    mid = np.full(nb, np.nan)
    se = np.full(nb, np.nan)
    for j in range(nb):
        v = i_a[b == j]
        if len(v) > 1:
            mid[j] = v.mean()
            se[j] = v.std(ddof=1) / math.sqrt(len(v))
    return (np.arange(nb) + 0.5) / nb * period_s * 1e3, mid * 1e9, se * 1e9


class Chopper(threading.Thread):
    """PC 翻转输出: ON ton 秒, OFF toff 秒, 循环。记录每次翻转的**实测**时刻。"""
    def __init__(self, smu, ton, toff):
        super().__init__(daemon=True)
        self.smu, self.ton, self.toff = smu, ton, toff
        self.stop = False
        self.edges = []          # (t, state)  相对 t0
        self.t0 = None

    def run(self):
        self.t0 = time.perf_counter()
        st = True
        smu_write(self.smu, 'smua.source.output = smua.OUTPUT_ON')
        self.edges.append((0.0, 1))
        while not self.stop:
            time.sleep(self.ton if st else self.toff)
            if self.stop:
                break
            st = not st
            smu_write(self.smu, 'smua.source.output = %s'
                      % ('smua.OUTPUT_ON' if st else 'smua.OUTPUT_OFF'))
            self.edges.append((time.perf_counter() - self.t0, 1 if st else 0))


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--peak', type=float, default=8.0, help='脉冲峰值 (nA)')
    ap.add_argument('--ton', type=float, default=100.0, help='导通时长 (ms)')
    ap.add_argument('--toff', type=float, default=100.0, help='断开时长 (ms)')
    ap.add_argument('--tau-ms', type=float, default=10.0, help='反推的斜率跨度 (ms)')
    ap.add_argument('-o', '--out', default='data/pulse_real.png')
    ap.add_argument('--npz', default='data/pulse_real.npz')
    a = ap.parse_args()

    T = a.ton + a.toff
    iavg = a.peak * a.ton / T
    lim = min(45.0, 50.0 * (1 - a.ton / T), 860.0 / T)
    print("=" * 76)
    print("真实脉冲波形反推")
    print("=" * 76)
    print("设定: 峰值 %g nA, ton %g ms, toff %g ms, 周期 %g ms" % (a.peak, a.ton, a.toff, T))
    print("      I_avg = %.2f nA   约束上限 %.2f nA  -> %s"
          % (iavg, lim, "OK" if iavg <= lim else "** 超限! **"))
    print("      窗口 1 s 里有 %.1f 个周期" % (1.0 / (T / 1000.0)))
    print("")

    ser = board_open(a.port)
    smu = smu_open(a.visa)
    smu_set(smu, a.peak * 1e-9)
    board_set_window(ser, 1000)          # 强制 1 s 窗口, 别让它自动切 10 s
    board_warmup(ser)

    ch = Chopper(smu, a.ton / 1000.0, a.toff / 1000.0)
    ch.start()
    time.sleep(0.6)                      # 让它先转几拍
    d = board_measure(ser, 30.0)
    wv = board_read_waveform(ser, 'X')
    ch.stop = True
    ch.join(timeout=3.0)
    smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    smu_off(smu)
    ser.close()

    if d is None:
        sys.exit("装置无响应")
    print("装置读数: %s" % d['line'][:80])
    print("波形: %d 点" % len(wv))
    print("")

    # ---- 反推 ----
    h = max(2, int(a.tau_ms / 1000.0 / TICK_S / 2))
    t_s, i_a = invert(wv.astype(float), h)
    # 剔除参考切换附近的点 (那里斜率不连续, 会减错参考)
    step = np.diff(wv.astype(float))
    sw = np.where(np.abs(step) > 32768)[0]        # 码绕回
    jump = np.where(np.abs(np.diff(np.sign(step))) > 0)[0] + 1
    bnd = np.unique(np.concatenate([[0], sw, jump, [len(wv) - 1]]))
    keep = np.ones(len(t_s), bool)
    for j, tk in enumerate((t_s / TICK_S).astype(int)):
        lo, hi = tk - h, tk + h
        k = np.searchsorted(bnd, lo, 'right') - 1
        keep[j] = (lo >= bnd[k]) and (hi < bnd[k + 1])
    # ---- ⚠️ 折叠必须用**实测**周期, 不是设定周期 ----
    # 2026-09-16 实测: PC 翻转的实际周期与设定值差很远 —— 设定 200 ms,
    # 实测 500 ms。因为每次翻转是 "write + 查错误队列" 两个 LAN 往返, 单次
    # 开销约 150 ms, 完全盖过了 ton/toff。用设定周期折叠会把相位搅乱。
    # 好在我们记录了每次翻转的**实际时刻**, 所以拿实测值当基准, 图上反而
    # 更诚实 —— 画出来的"实际设定"就是真实发生过的波形。
    edges = np.array([e[0] for e in ch.edges])
    states = np.array([e[1] for e in ch.edges])
    dt = np.diff(edges)
    on_d = dt[states[:-1] == 1]
    off_d = dt[states[:-1] == 0]
    ton_m = on_d.mean() if len(on_d) else a.ton / 1000.0
    toff_m = off_d.mean() if len(off_d) else a.toff / 1000.0
    per_m = ton_m + toff_m
    print("翻转时长实测 (%d 次翻转):" % len(edges))
    print("  导通 %.1f ± %.1f ms   (设定 %g)" % (ton_m * 1e3, on_d.std() * 1e3, a.ton))
    print("  断开 %.1f ± %.1f ms   (设定 %g)" % (toff_m * 1e3, off_d.std() * 1e3, a.toff))
    print("  周期 %.1f ms (设定 %g), 占空比 %.1f%% (设定 %.1f%%)"
          % (per_m * 1e3, T, ton_m / per_m * 100, a.ton / T * 100))
    print("  ⚠️ 与设定差这么多是因为每次翻转要两个 LAN 往返 (~150 ms), 盖过了 ton/toff")
    print("")

    # ---- 折叠 + **相位对齐** ----
    # ⚠️ 折叠的相位原点是**窗口起点**, 而实测方波的相位原点是 chopper 的 t0,
    #    两者毫无关系 -> 直接把两条曲线叠在一起会差大半个周期 (第一次就是这样)。
    #    这里按**互相相关**找最佳圆周位移, 只挪相位、**不改幅度** ——
    #    所以峰值/低电平的数值对比仍然是诚实的。
    tf, im, ise = fold(t_s[keep], i_a[keep], per_m)
    nb = len(tf)
    ref = np.array([a.peak if (t < ton_m * 1e3) else 0.0 for t in tf])
    best, bestr = 0, -2.0
    for sh in range(nb):
        c = np.corrcoef(np.roll(im, sh), ref)[0, 1]
        if c > bestr:
            bestr, best = c, sh
    im, ise = np.roll(im, best), np.roll(ise, best)
    print("相位对齐: 圆周位移 %d/%d 格 (相关系数 %.3f) —— 只挪相位, 不改幅度"
          % (best, nb, bestr))
    print("")

    rec_peak = np.nanmax(im)
    print("反推结果 (按**实测**周期折叠):")
    print("  峰值 %6.2f nA   设定 %g nA   偏差 %+.2f%%"
          % (rec_peak, a.peak, (rec_peak - a.peak) / a.peak * 100))
    lo_q = np.nanpercentile(im, 10)
    print("  低电平 %6.2f nA  设定 0 nA   (差 %+.2f nA)" % (lo_q, lo_q))
    print("")

    # ---- 图 ----
    fig, ax = plt.subplots(2, 1, figsize=(12, 8))
    ax[0].plot(t_s * 1e3, i_a * 1e9, lw=.6, color='0.6', label='逐点反推 (含切换附近的坏点)')
    ax[0].scatter(t_s[keep] * 1e3, i_a[keep] * 1e9, s=3, color='C0',
                  label='保留的点 (已剔除参考切换处)')
    ax[0].axhline(a.peak, color='r', ls='--', lw=1.2, label='设定峰值 %g nA' % a.peak)
    ax[0].axhline(0, color='k', ls='-', lw=.8)
    ax[0].set_xlabel('窗口内时间 (ms)')
    ax[0].set_ylabel('反推输入电流 (nA)')
    ax[0].set_title('反推的 I(t) 整窗 (1 s) —— 灰色是未剔除的原始反推')
    ax[0].grid(alpha=.3); ax[0].legend(fontsize=8)

    ax[1].errorbar(tf, im, yerr=ise, fmt='o-', ms=4, lw=1.2, color='C3',
                   ecolor='0.7', capsize=2, label='反推 (按周期折叠, 均值±SE)')
    ax[1].plot(tf, [a.peak if (t < ton_m * 1e3) else 0.0 for t in tf], 'k--', lw=1.4,
               label='实测方波 (%g nA, 导通 %.0f / 周期 %.0f ms)'
                     % (a.peak, ton_m * 1e3, per_m * 1e3))
    ax[1].axhline(0, color='k', lw=.8)
    ax[1].set_xlabel('周期内相位 (ms)')
    ax[1].set_ylabel('输入电流 (nA)')
    ax[1].set_title('折叠后的一个周期 —— 反推 vs 实际设定   峰值偏差 %+.2f%%'
                    % ((rec_peak - a.peak) / a.peak * 100))
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=9)

    fig.suptitle('脉冲电流的波形反推验证   (PC 翻转, 实测周期 %.0f ms, 占空比 %.0f%%)'
                 % (per_m * 1e3, ton_m / per_m * 100), fontsize=12, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(a.out, dpi=150)
    np.savez(a.npz, wave_ext=wv.astype(np.uint16), dev=float(d['i']),
             set_peak=a.peak, ton=a.ton, toff=a.toff,
             t_s=t_s, i_a=i_a, keep=keep)
    print("已写", a.out, "和", a.npz)


if __name__ == '__main__':
    main()
