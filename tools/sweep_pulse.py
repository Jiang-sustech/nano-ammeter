# -*- coding: utf-8 -*-
"""
脉冲输入测试: 验证「脉冲 ≡ 等效直流」

20 组脉冲测试点, 分五组 (A/B/C/D/E), 另配 8 个直流对照点。

    python tools/sweep_pulse.py --list                 # 只打印点表与约束检查
    python tools/sweep_pulse.py --visa ... --check     # 先跑这个: 验证脉冲 TSP
    python tools/sweep_pulse.py --visa ... --port COM8 # 正式采集
    python tools/sweep_pulse.py --visa ... --group A   # 只跑 A 组

判据 (E1/E2/E3 见 docs/脉冲电流实验方案.md §6.2):
    E1  脉冲读数 vs 计算平均 I_avg 的偏差   < 1.5%
    E2  峰值远超直流量程时读数仍正确
    E3  同一 I_avg 下 脉冲读数 vs 直流读数  < 0.5%   <- 核心

⚠️ 关于脉冲怎么产生 —— **2026-09-16 实测定案**
--------------------------------------------
用 2636B 的脉冲源模式 (TSP `ConfigPulseIMeasureVSweepLin` + `InitiatePulseTest`)。
手册出处: 2600BS-901-01 **Rev. C** (2016) 7-38 / 7-112 页 (本地 `D:\\451374.pdf`)。

**三条前置条件, 现状设置三条全违反 —— 而它把失败原因放在返回值里, 用
`smu_write` 发就完全看不到, 表现为"缓冲一直是 0 点"的静默失败。**
2026-09-16 单因素拆分实测 (`tools/pulse_probe.py --recipe --bisect`), 仪器自己
返回的消息逐条点名:

    现状(两路 autorange ON, NPLC 10, autozero 2)
        -> false | Source autorange must be disabled
    只关 autorange                     -> false | Measure autozero must be off
    只改 NPLC                          -> false | Source autorange must be disabled
    只改 autozero                      -> false | Source autorange must be disabled
    关 autorange + 改 autozero         -> false | Source autorange must be disabled
    关 autorange + 改 NPLC             -> false | Measurement aperture greater than pulse width
    改 NPLC + 改 autozero              -> false | Measure autozero must be off
    **三条齐备**                        -> **true | Pulse completed**

**三处都是必需的 (联合条件), 不是"改哪一处都行"。** 校验按固定顺序做, 所以每次
只报第一条违规 —— 上面不同消息就是这么来的。对应的设置见 `pulse_mode()`。

> ⚠️ **`bias` 必须是 0 —— 这是第二个静默陷阱。** 手册 7-40 的 SweepLin 例子
> `ConfigPulseIMeasureVSweepLin(smua, 0, 0.01, 0.05, ...)` 配文 "**return to a
> 0 mA bias level between pulses**"; 7-37 的 IMeasureV 例子同样 `bias=0` 配文
> "return to 0 A ... remain at 0 A for 80 ms"。**bias 是脉冲之间的电平。**
> 本文件 2026-09-16 之前传的是 `bias = start = stop = i_peak` —— 那样波形
> **自始至终是恒定直流, 根本不是脉冲串**, 而仪器不报错、缓冲照样填满。
> `docs/脉冲电流实验方案.md` §4.1 的例子里 `40e-9  -- bias 脉冲之间的电平`
> 把同一个错误抄进了文档, 已一并更正。

> 仍未解释的一条 (不阻塞): 最早探针撞过 `-286 attempt to index field 'start'`。
> `trigger.timer[N].start` 在手册全 919 页 **0 命中** (仪器实测 `type` = nil),
> 但它在这 8 组对照里**再没出现过**。怀疑是"输出未开就调 InitiatePulseTest"
> 那条路径触发的, **未验证**。

如果脉冲模式一时调不通, 用 --pc-chop 退回"PC 翻转输出": 它只适合**长脉冲**
(周期 >= 100 ms), 因为 LAN 往返 + Python 调度的抖动在 ms 量级, 占空比误差
= 抖动/周期。10 ms 周期上抖动就有 10% —— 只能做定性验证, 不能卡 0.5% 判据。
"""
import argparse
import csv
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_write,
                        board_open, board_measure,
                        ReadbackSampler, board_warmup, smu_set,
                        best_range, SMU_NPLC, SMU_AUTOZERO)

# ===========================================================================
# 约束
# ===========================================================================
# 三条, 推导见 docs/脉冲电流实验方案.md §2.2 与本次推导:
#
#   ① 电荷回收:  ton 期间灌入的电荷必须在 toff 期间被参考抽走, 参考只有 50 nA
#                   I_peak*t_on <= I_ref*t_off   ⟹   I_avg <= I_ref*(1-D)
#   ② 量程:       I_avg <= 45 nA
#   ③ 纹波撞轨:   每周期电荷纹波 I_avg*T 必须落在摆幅内 (100pF x 8.6V)
#                   I_avg*T <= 860 pC
#
# **在 T = 10 ms 下 ③ 给出 86 nA, 永远比 ①② 松** —— ③ 只在 T >= 20 ms 时才
# 开始起作用 (D3 就是贴着它的点)。合并后:
#
#       I_avg <= min( 45 nA , 50 nA*(1-D) , 860 pC / T )
#
# 即: 占空比 >10% 时"电荷回收"卡死; <=10% 时"量程"卡死。
REF_NA   = 50.0      # 参考电流幅值 (nA)
RANGE_NA = 45.0      # 直流量程上限 (nA)
SWING_PC = 860.0     # 100 pF x 8.6 V 摆幅电荷 (pC)
LONGW_NA  = 1.0      # |I| < 1 nA 时固件自动换**长窗** (measurement_state.c:130)
LONGWIN_S = 50.0     # 长窗长度 (秒)。**必须与 current.h 的 CURRENT_WIN_LONG_TICKS
                     # 一致** (6250 拍/秒 -> 312500 拍 = 50 s)。
                     # 2026-09-17 由 10 s 改成 50 s。
                     # ⚠️ 脉冲串要**铺满整个积分窗口**, 这个值算小了, 平均就会落到
                     #    脉冲串结束之后 —— 得到一个看着正常、实则错的平均值。
                     #    2026-09-16 已经在"预热吃掉整个串"上踩过一次同类坑。

# 2636B 脉冲串的**每周期固定开销**: 实际周期 = ton + toff + DELTA_MS。
#
# 2026-09-16 实测 (计时 `InitiatePulseTest` 全程, `tools/pulse_probe.py --recipe`):
#     1000 个脉冲 x 设定 10 ms  ->  实测 10.16 s
# 三种配置全给 10.16 s, **与峰值(40/400/2000 nA)和脉宽(5/1/0.2 ms)都无关**:
#     峰值  40 nA, ton 5.0 ms  ->  10.16 s
#     峰值 400 nA, ton 1.0 ms  ->  10.16 s
#     峰值2000 nA, ton 0.2 ms  ->  10.16 s
#
# 所以标称 `I_avg = I_peak x ton/T` **系统性偏高**, 相对误差 = DELTA_MS/T:
#     T =  1 ms -> 16%      T = 10 ms -> 1.6%      T = 50 ms -> 0.32%
# 真值必须用 `I_peak x ton/(T + DELTA_MS)` —— 见 true_avg_of()。
# **这也是"周期下限"的真正来源** —— 方案 §2.3 原先用"脉宽准确度 ±5us / 周期"
# 去推, 量纲就错了 (那是**脉宽**的准确度, δ 是**周期**的开销, 差 32 倍)。
DELTA_MS = 0.16


def _pt(gid, peak_na, ton_ms, period_ms, note, xfail=False):
    duty = (ton_ms / 1000.0) / (period_ms / 1000.0)
    return dict(gid=gid, peak=float(peak_na), ton=float(ton_ms),
                period=float(period_ms), duty=duty,
                avg=float(peak_na) * duty, note=note, xfail=xfail)


# ===========================================================================
# 20 组测试点
# ===========================================================================
# 组 A  占空比扫描 (峰值固定 40 nA, T=10 ms)      -> 证明 I_avg = I_peak x D
# 组 B  峰值扫描 (占空比 10%, t_on=1 ms)          -> 幅度线性 + 超量程主线
# 组 C  峰值扫描 (占空比 20%, t_on=2 ms)          -> 第二条占空比上的线性, 卡约束角点
# 组 D  周期扫描 (峰值 40 nA, 25%, I_avg 恒 10)   -> 周期无关性 + 折叠周期数
# 组 E  极限状态 (2 组)                            -> E1 大峰值 / E2 越界
POINTS = [
    _pt('A1',  40, 5.000, 10, "占空比扫描 基准 (50%)"),
    _pt('A2',  40, 4.000, 10, "40%"),
    _pt('A3',  40, 3.000, 10, "30%"),
    _pt('A4',  40, 2.500, 10, "25%"),
    _pt('A5',  40, 2.000, 10, "20%"),
    _pt('A6',  40, 1.500, 10, "15%"),
    _pt('A7',  40, 1.000, 10, "10%"),
    _pt('A8',  40, 0.750, 10, "7.5%"),
    _pt('A9',  40, 0.500, 10, "5%"),
    _pt('A10', 40, 0.375, 10, "3.75%"),
    _pt('A11', 40, 0.250, 10, "2.5%; I_avg=1.0 nA 落在窗口切换边界上"),
    # 边界对照: 同样的 ton, 但把峰值降到 20 nA 让 I_avg=0.5 nA **落在 10 s 窗口
    # 内部**(而不是卡在 1 nA 阈值上) —— 用来判 A11 那个失真是"边界效应"还是
    # "小电流都不行"。
    _pt('A12', 20, 0.250, 10, "边界对照: I_avg=0.5 nA 在 10 s 窗口内部"),

    _pt('B1',   1, 1.00, 10, "微弱脉冲, 落 10s 窗口"),
    _pt('B2',   5, 1.00, 10, "微弱脉冲, 落 10s 窗口"),
    _pt('B3',  10, 1.00, 10, "跨窗口边界"),
    _pt('B4', 100, 1.00, 10, "峰值已 2.2 倍量程"),
    _pt('B5', 200, 1.00, 10, "峰值 4.4 倍量程"),
    _pt('B6', 400, 1.00, 10, "峰值 8.9 倍量程; 回收余量仅 11%"),

    _pt('C1',   5, 2.00, 10, ""),
    _pt('C2',  20, 2.00, 10, ""),
    _pt('C3', 100, 2.00, 10, "峰值 2.2 倍量程"),
    _pt('C4', 200, 2.00, 10, "约束角点: 回收余量 0%"),

    _pt('D1',  40, 2.50, 10, "100 周期/窗口"),
    _pt('D2',  40, 5.00, 20, "50 周期/窗口"),
    _pt('D3',  40, 10.0, 40, "25 周期/窗口; 纹波 400 pC 逼近撞轨"),

    _pt('E1', 1000, 0.40, 10, "极限-大峰值: 峰值 22 倍量程, 应当通过"),
    _pt('E2',  120, 5.00, 10, "极限-越界: 平均 60 nA 超量程, 应当失败", xfail=True),

    # ---- 组 P  周期下限扫描 (峰值 40 nA / 占空比 25% / I_avg 恒 10 nA) ----
    # 逼近 §2.3 的"周期 >= 10 ms", 专往它下面走。加密到 11 点, 每点 n=5。
    # 真值口径见 true_avg_of(): 误差主要是 delta/T (delta 实测 0.16 ms)。
    _pt('P1',  40, 12.500, 50, "基准"),
    _pt('P2',  40,  7.500, 30, ""),
    _pt('P3',  40,  5.000, 20, ""),
    _pt('P4',  40,  3.750, 15, ""),
    _pt('P5',  40,  2.500, 10, "= 方案 §2.3 说的下限"),
    _pt('P6',  40,  1.750,  7, "低于下限"),
    _pt('P7',  40,  1.250,  5, ""),
    _pt('P8',  40,  0.875, 3.5, ""),
    _pt('P9',  40,  0.500,  2, ""),
    _pt('P10', 40,  0.375, 1.5, ""),
    _pt('P11', 40,  0.250,  1, "低于下限 10 倍"),

    # ---- 组 K  瞬时电流上限扫描 (I_avg 恒 40 nA, 靠拉长周期抬峰值) ----
    # ton 有下限(实测 100~200 us 之间, 短了仪器返回 "Pulse too short"), 所以
    # 抬峰值只能靠拉长 T:  peak = I_avg x T / ton。约束 I_avg x T <= 860 pC
    # -> T <= 21.5 ms -> peak <= 860 pC/0.2 ms = 4300 nA。
    _pt('K1',   400, 1.000, 10.0, "峰值 8.9 倍量程"),
    _pt('K2',  1000, 0.400, 10.0, "峰值 22 倍量程"),
    _pt('K3',  2000, 0.200, 10.0, "峰值 44 倍量程; ton 到最小可用值"),
    _pt('K4',  2400, 0.200, 12.0, "峰值 53 倍量程"),
    _pt('K5',  2800, 0.200, 14.0, "峰值 62 倍量程"),
    _pt('K6',  3200, 0.200, 16.0, "峰值 71 倍量程"),
    _pt('K7',  3600, 0.200, 18.0, "峰值 80 倍量程"),
    _pt('K8',  4000, 0.200, 20.0, "峰值 89 倍量程"),
    _pt('K9',  4200, 0.200, 21.5, "峰值 **93 倍**量程; 纹波 840 pC 贴着上限"),
]

# 直流对照点 (E3 需要)。20 组脉冲的 I_avg 全部落在这个集合里。
DC_POINTS = [0.1, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0,
             10.0, 12.0, 16.0, 20.0, 40.0]   # 覆盖 A/B/C/D/P/K 全部 I_avg

# 若干点标为"核心" —— 定结论用, 其余是趋势点。--n-core/--n 可分别设重复次数。
CORE = set()      # 加密后不再区分核心/趋势, 全部同重复次数


def limit_of(p):
    """该点的 I_avg 上限 (nA) 与三个分项"""
    l_bal = REF_NA * (1.0 - p['duty'])          # 电荷回收 (nA)
    l_rng = RANGE_NA                            # 量程 (nA)
    # 纹波撞轨: I_avg*T <= 860 pC  ->  I_avg <= 860 pC / T。
    # T 的单位就是 ms, 而 1 pC/ms = 1e-12 C/1e-3 s = 1e-9 A = 1 nA, 所以直接相除即得 nA
    l_swg = SWING_PC / p['period']              # (nA)
    return min(l_bal, l_rng, l_swg), dict(bal=l_bal, rng=l_rng, swg=l_swg)


def true_avg_of(p):
    """**真实**平均电流 (nA) —— 用实测周期 `T + DELTA_MS`, 不是标称 T。

    直流点 (period == 0) 没有周期开销, 原样返回 p['avg']。
    判据 E1/E3 应当拿这个当真值; CSV 里两列都存 (`avg_nA` 标称 / `true_avg_nA`),
    因为**标称值才是设定量**, 出问题时得看得出是哪一头的问题。
    """
    if not p['period']:
        return p['avg']
    return p['peak'] * p['ton'] / (p['period'] + DELTA_MS)


def window_of(p):
    return "%g s" % LONGWIN_S if abs(p['avg']) < LONGW_NA else "1 s"


def print_table(points, dc):
    print("=" * 96)
    print("脉冲测试点表  (%d 组)" % len(points))
    print("=" * 96)
    print("%4s %8s %8s %8s %7s %9s %9s %8s %6s  %s"
          % ("#", "峰值nA", "t_on", "周期", "占空比", "I_avg", "上限", "余量",
             "窗口", "说明"))
    print("-" * 96)
    bad = []
    for p in points:
        lim, parts = limit_of(p)
        marg = (lim - p['avg']) / lim * 100 if lim > 0 else -999
        flag = "  ** 越界 **" if marg < 0 else ("  (紧)" if marg < 12 else "")
        if p.get('xfail'):
            flag += "   [预期失败, 不判通过]" 
        why = min(parts, key=lambda k: parts[k])
        if marg < 0:
            bad.append(p['gid'])
        print("%4s %8.4g %7.2fms %7.0fms %6.1f%% %8.4f  %8.4f  %7.1f%% %6s  %s%s"
              % (p['gid'], p['peak'], p['ton'], p['period'], p['duty'] * 100,
                 p['avg'], lim, marg, window_of(p),
                 p['note'] or ("卡:" + why), flag))
    print("-" * 96)
    print("直流对照点: " + "  ".join("%.4g" % v for v in dc) + "  nA")
    print("  每个直流点配到的脉冲组:")
    for v in dc:
        g = [p['gid'] for p in points if abs(p['avg'] - v) < 1e-9]
        print("    %6.4g nA <- %s" % (v, " ".join(g) if g else "** 无 **"))
    if bad:
        print("")
        print("  ** 越界点 (预期失败, 用来标定失效模式): %s **" % " ".join(bad))
        print("     若这些点也通过了, 说明约束推导有误, 要回头查。")
    print("")
    print("  注: B1/B2 落 10 s 窗口, 固件抽点后 1.6 ms/点, 而 t_on=1 ms < 1.6 ms")
    print("      -> 平台在存储波形里不存在, **平均照样准, 但不能做波形反演**。")
    nc = sum(1 for p in points if p['gid'] in CORE)
    if nc:
        print("  核心点 %d 个 (%s), 其余 %d 个为趋势点。"
              % (nc, " ".join(sorted(CORE)), len(points) - nc))
    else:
        print("  %d 个点, 不分核心/趋势 —— 重复次数由 --n / --n-core 给定。"
              % len(points))
    xf = [p['gid'] for p in points if p.get('xfail')]
    if xf:
        print("  预期失败点 (不判通过): %s" % " ".join(xf))


def build_pulse_tsp(i_peak, ton, toff, n_pulse, limitv=5.0):
    """生成配置脉冲串的 TSP。

    ⚠️ **2026-09-16 修正: 原来这里参数顺序错两位, 什么都没设上。**

    官方签名 (2600B 参考手册, KIPulse 厂脚本) —— **10 个参数**:
        ConfigPulseIMeasureVSweepLin(smu, bias, start, stop, limit,
                                     ton, toff, points, buffer, tag)
    其中 `toff` 是脉冲之间的间隔时间; **周期 = ton + toff**, 没有单独的
    pulsePeriod 参数; 测量在 **ton 结束**时做。

    原版传的是 8 个:
        (smua, i_peak, i_peak, n_pulse, ton, toff, buffer, 1)
    于是 n_pulse 落到 stop、ton 落到 limit、toff 落到 ton、buffer 落到 toff、
    1 落到 points —— **整体串位两位**。要命的是**不报错** (TSP 自己做了类型
    转换), 所以脉冲静默地没配出来。`--check` 正是为抓这个写的, 它抓到了:
    `smua.trigger.source.pulsewidth` 回读是 nil。

    **bias 必须传 0** —— 它是**脉冲之间的电平**, 不是峰值。手册 7-40 的 SweepLin
    例子 `ConfigPulseIMeasureVSweepLin(smua, 0, 0.01, 0.05, ...)` 配文
    "return to a **0 mA bias level** between pulses"; 7-37 的 IMeasureV 例子同样
    `bias=0`。2026-09-16 之前这里传的是 `bias = i_peak` —— 那样 start=stop=bias
    **波形恒定不变, 输出的是直流而不是脉冲串**, 而仪器**不报错、缓冲照样填满**。
    这个错误会让每个"脉冲点"静默退化成"峰值直流点", E1/E3 全部失真。

    本函数: bias=0, start=stop=i_peak -> 0 与 i_peak 之间跳变的等幅脉冲串。
    """
    return (
        "ConfigPulseIMeasureVSweepLin("
        "smua, %.9e, %.9e, %.9e, %.6f, %.6f, %.6f, %d, smua.nvbuffer1, 1)"
        % (0.0, i_peak, i_peak, limitv, ton, toff, n_pulse)
    )


def pulse_configure(smu, i_peak, ton, toff, n_pulse):
    """配置脉冲串 —— **并且检查厂脚本的返回值**。返回 (ok, msg)。

    ⚠️ 两个坑, 都是 2026-09-16 实机撞到的:

    **① 缓冲必须开 appendmode。** 不开时厂脚本返回
       `false  Buffer must have appendmode activated to measure multiple pulses`

    **② 失败走返回值, 不走错误队列。** 上面那条失败时 `errorqueue.count` 仍是
       **0** —— 所以 `smu_write()` 的错误检查**抓不到它**, 脚本会一路跑完,
       读数全是"输出根本没在脉冲"时的本底, 看起来还像正常数据。
       **必须用 `smu.query('print(...)')` 把 f,msg 读回来判成败。**

    (另一个坑是参数顺序错两位, 已在 build_pulse_tsp 里修掉。)
    """
    # **不设 nvbuffer1.capacity。** 实测它的默认值就是 149789
    # (`print(smua.nvbuffer1.capacity)` 回读 1.49789e+05), 而本工程最长的一串
    # 也就 ~6200 个脉冲 —— 本来就绰绰有余。
    # 2026-09-16 我加过一行 `capacity = N`, 结果是**纯风险零收益**: 它写在
    # appendmode 打开**之后**, 仪器拒改并抛 -286(前面板 "cannot modify this"),
    # 而那句写被 `except: pass` 吞了 —— 队列被 smu_write 读空、日志干净、
    # 程序照跑, **只有前面板的 ERR 灯锁存着**, 排查了很久。
    # 手册 7-113 的配方里也没有这一步。
    try:
        smu_write(smu, 'smua.nvbuffer1.clear()')
    except Exception as e:
        # **不许静默吞掉。** smu_write 失败时会**先把错误从队列读走再抛**,
        # 所以吞掉异常 = 队列被清空(日志干净、运行不中断)但**前面板的 ERR
        # 指示灯已锁存** —— 2026-09-16 用户就是被这个误导的。
        print("      ** nvbuffer1.clear() 失败: %s"
              % str(e).splitlines()[0][:90])
    smu_write(smu, 'smua.nvbuffer1.appendmode = 1')
    r = smu.query('print(%s)' % build_pulse_tsp(i_peak, ton, toff, n_pulse))
    s = (r or '').strip()
    ok = s.lower().startswith('true')
    msg = s.split('\t', 1)[1].strip() if '\t' in s else s
    return ok, msg


# NPLC 上限。实测: NPLC=10 (即孔径 200 ms) 撞 "Measurement aperture greater than
# pulse width" —— 孔径 = NPLC/工频 = NPLC/50 s, 必须 < ton。本点表最小 ton 是
# 0.25 ms (A5), 故 NPLC <= 0.0125 才通。取 0.01 并留一半裕量, 见 pulse_mode()。
PULSE_NPLC_MAX = 0.01
# autozero: 0=OFF 1=ONCE 2=ON。脉冲模式必须 OFF 或 ONCE (手册 7-113 前置条件),
# 实测 autozero=2 直接返回 "Measure autozero must be off"。
# **用数值 1 而不是 smua.AUTOZERO_ONCE** —— 同 cal_sweep.SMU_AUTOZERO 那条注释:
# 该固件对 autozero 的**常量名**报过 -104 "Data type error"。本次实测
# `smua.AUTOZERO_ONCE` 是通的, 但既然数值一定通, 不冒这个险。
AUTOZERO_ONCE = 1


def pulse_mode(smu, i_peak, ton):
    """把源表切到脉冲模式 —— 手册 7-113 三条前置条件逐条落实。返回 (档位, nplc)。

    | 前置条件 (手册 7-113)        | 违反时仪器返回的消息                          |
    |-----------------------------|---------------------------------------------|
    | Source autorange off        | `Source autorange must be disabled`          |
    | Measure autorange off       | (同上, 两者一起报)                            |
    | Measure NPLC < ton          | `Measurement aperture greater than pulse width` |
    | Measure autozero OFF/ONCE   | `Measure autozero must be off`               |

    **档位必须显式设、而且要覆盖峰值** —— 关掉 autorange 后它不会再自动挑,
    设小了会限制输出。1 uA 峰值 (E1) -> 10 uA 档, 用 best_range()。
    """
    rng = best_range(i_peak)
    if rng is None:
        raise ValueError("峰值 %.4g A 超出最大档" % i_peak)
    nplc = max(0.0005, min(PULSE_NPLC_MAX, ton * 50 * 0.5))
    prng = smu_set(smu, 0.0, force_range=rng)   # 关源**电流**autorange + 显式设档
    # ⚠️ `autorangei = OFF` 只管**电流**那一路。**电压**自动量程 (autorangev) 是
    # 独立的, 手册配方里那句 `smua.source.rangev = 5` 在关它 —— 设档位本身就
    # 隐含关掉该功能的自动量程。2026-09-16 抄漏过这一句, 仪器照样返回
    # "Source autorange must be disabled" (而 rangei 读回来是对的, 极易看漏)。
    smu_write(smu, 'smua.source.rangev = 5')
    smu_write(smu, 'smua.measure.autorangei = smua.AUTORANGE_OFF')
    smu_write(smu, 'smua.measure.rangev = 5')
    smu_write(smu, 'smua.measure.rangei = %.9e' % rng)
    smu_write(smu, 'smua.measure.nplc = %g' % nplc)
    smu_write(smu, 'smua.measure.autozero = %d' % AUTOZERO_ONCE)
    print("      脉冲模式: 档位 %.4g A (覆盖峰值 %.4g nA), nplc %g (孔径 %.3g ms < ton %.3g ms)"
          % (prng, i_peak * 1e9, nplc, nplc / 50 * 1e3, ton * 1e3))
    return prng, nplc


def dc_mode(smu):
    """把源表切回**直流测量**的设置 —— 即 smu_open() 原来那套。

    ⚠️ **必须调, 否则静默污染后面每一个直流对照点。** pulse_mode() 关掉了两路
    autorange 并把 NPLC 压到 0.01; 不还原的话直流点会: ① 在小档位上测 (回读
    固定误差项偏大) ② NPLC 0.01 -> 噪声比 10 差 1000 倍。而 E3 判据是拿直流
    点当基准的 —— 基准被污染, 整个核心结论就不可信, **且不会有任何报错**。
    """
    smu_write(smu, 'smua.source.autorangei = smua.AUTORANGE_ON')
    smu_write(smu, 'smua.measure.autorangei = smua.AUTORANGE_ON')
    smu_write(smu, 'smua.measure.nplc = %g' % SMU_NPLC)
    smu_write(smu, 'smua.measure.autozero = %g' % SMU_AUTOZERO)


def pulse_initiate(smu, n_pulse=None, period_s=None):
    """启动脉冲串, **并读回 InitiatePulseTest 自己的返回值**。返回 (ok, msg)。

    ⚠️ **失败原因只在这个返回值里。** 手册 7-113 的写法是
        `f2, msg2 = InitiatePulseTest(1)`
    而原来这里用 `smu_write(smu, 'InitiatePulseTest(1)')` —— 返回值被丢掉,
    错误队列**始终是 0 条**, 于是三条前置条件违规全部表现为"缓冲 0 点"的
    静默失败, 看起来跟"配置没生效"一模一样。**smu_write 的错误检查抓不到它。**

    (注意 `ConfigPulseIMeasureVSweepLin` 的返回值同理 —— 它只做**可行性校验**,
    返回 true 不代表触发模型配好了。手册 7-39: "This function does not cause the
    specified smu to output a pulse train." 所以两个返回值都要查。)

    ⚠️ **VISA 超时必须按串长放宽。** 本函数阻塞整串时长, 而 `smu_open` 设的
    超时只有 5000 ms (`cal_sweep.smu_open`: `smu.timeout = 5000`)。**串长一过
    5 s 这个查询就必然超时** —— 而超时后仪器还在跑, 后续所有查询被堵住,
    查个型号都要等 240 s。2026-09-16 连撞两次 (先是 100000 个脉冲的 1000 s,
    再是自检的 5.6 s)。这里按串长 + 30 s 余量临时放宽, 查完恢复。
    """
    old = smu.timeout
    if n_pulse and period_s:
        smu.timeout = int((n_pulse * period_s + 30.0) * 1000)
    _t0 = time.perf_counter()
    try:
        r = smu.query('local f, m = InitiatePulseTest(1)  '
                      'print(tostring(f) .. "|" .. tostring(m))').strip()
    finally:
        smu.timeout = old
    elapsed = time.perf_counter() - _t0
    f, _, m = r.partition('|')
    return f.strip().lower().startswith('true'), m.strip(), elapsed


def pulse_start(smu, i_peak, ton, toff, n_pulse):
    """配置并启动脉冲串。**调用前必须先 pulse_mode() 且输出已 ON。**

    ⚠️ **本函数会阻塞 n_pulse x (ton+toff) 那么久** —— 实测
    `InitiatePulseTest` 返回时整串已经跑完 (返回 "Pulse completed" 的同时
    nvbuffer1.n 已满)。2026-09-16 就是这个坑: 传了 100000 个脉冲 ->
    仪器被占住 1000 s, 期间**所有**查询都超时, 看着像死机。
    所以调用者要么把 n_pulse 定得刚好, 要么把测量放到别的线程去 (见 run_point)。
    """
    ok, msg = pulse_configure(smu, i_peak, ton, toff, n_pulse)
    if not ok:
        raise RuntimeError("脉冲配置失败: %s" % msg)
    print("      脉冲配置: %s" % msg)
    ok2, msg2, _el = pulse_initiate(smu, n_pulse, ton + toff)
    if not ok2:
        raise RuntimeError(
            "InitiatePulseTest 失败: %s\n"
            "       -> 按手册 7-113 的前置条件逐条查 (见 pulse_mode 的表格)" % msg2)
    print("      脉冲启动: %s" % msg2)
    return _el      # 实际串长 (s) —— 用来反解每点的 delta


def pc_chop_start(smu, ton, toff, stop_flag):
    """退化方案: PC 直接翻转输出。只在长脉冲下可用 (见文件头)。"""
    import threading

    def loop():
        while not stop_flag['stop']:
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            time.sleep(ton)
            smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
            time.sleep(toff)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def measure_once(ser, smu, sample_readback=True):
    """测一次装置读数。

    sample_readback 只在**直流**段为 True。脉冲段必须关掉, 两个原因:
      ① 2600B 在脉冲模式下**只在 ton 末尾测量** —— 它的回读是**峰值**, 不是平均,
         拿来当"实际输出"是错的 (真值应当由 I_peak x 占空比 算);
      ② 我们自己额外发 measure.i() 可能干扰脉冲时序。
    """
    smp = None
    if sample_readback:
        smp = ReadbackSampler(smu)
        smp.start()
    d = board_measure(ser)
    rb = smp.stop(10) if smp else []
    return d, (sum(rb) / len(rb) if rb else float('nan'))


def pulse_count(p, n):
    """这一点的脉冲串要发几个脉冲。

    **必须覆盖整段测量。** `InitiatePulseTest` 是阻塞的 (见 pulse_start), 串在
    后台线程跑, 主线程同时测装置 —— 串一停, 剩下的测量就全测到 0。

    两段时长都要算进去:

      ① **首次测量最坏是 10 s 窗口。** 装置按**上一次**读数的量级选窗口; 脉冲
         刚开始时它上一次看到的是 0 A, 所以还停在 10 s 档 —— 哪怕这一点的
         I_avg 有 20 nA, **第一次测量照样要 10 s**。
      ② 之后 n 次按该点的窗口 (|I_avg| < 1 nA 是 10 s, 否则 1 s,
         measurement_state.c:115)。

    预热**不在**这里算 —— 它已经挪到脉冲开始**之前**(见 run_point)。
    2026-09-16 教训: 预热留在串里面时, 它那一整个窗口把 5.6 s 的串整个吃光,
    装置读到 -0.0055 nA (就是空), 而**脚本不报错**。
    """
    period_s = p['period'] / 1000.0
    win_s = LONGWIN_S if abs(p['avg']) < LONGW_NA else 1.0
    need_s = 10.0 + n * win_s + 2.0       # ① + ② + 余量
    return int(math.ceil(need_s / period_s)) + 10


def _measure_loop(ser, smu, p, kind, n, rows, out, warmup=True):
    """(可选预热) + 采 n 次 (含坏点补测)。结果写进 out['dev'] / out['rb']。

    脉冲段的预热必须由**调用者**在脉冲开始前做掉, 所以传 warmup=False ——
    见 pulse_count 的注释: 预热会占掉一整个窗口, 放串里会把串吃光。

    脉冲段 `sample_readback=False`, 所以本函数**不碰源表**, 只有串口读写 ——
    pyvisa 资源不是线程安全的, 这条约束必须守住 (脉冲段本函数跑在主线程,
    源表由跑脉冲串的子线程独占)。
    """
    dev, rb = [], []
    out['dev'], out['rb'] = dev, rb
    if warmup:
        # 预热: 空跑一次丢弃 —— 每批第一次的坏率特别高 (见 cal_sweep 的 board_warmup)
        board_warmup(ser)
    tries = 0
    while (len(dev) < n) and (tries < n * 3):
        tries += 1
        d, r_avg = measure_once(ser, smu, sample_readback=(kind == 'dc'))
        if d is None:
            continue
        if d.get('timeout'):    # 坏点补测
            print("      (丢弃一次 TIMEOUT)")
            continue
        # 结果行有**三个通道**, 全部存下来, 报数用 CAL=(固件注释: 物理表征以它为准):
        #   I=   内置 ADC
        #   X=   ADS8866 外部
        #   CAL= X= 再经**分段校准** a·x+b (本机实测恒等: `CAL 3 1000000,0 ...`)
        # 注意**三常数**(q, I+, I-)在两种通道的换算式里都有 —— 实测已装 162 点拟合值
        # (`Q` 命令回读 `CAL Q 15596,49712,-49739`)。所以 I= 与 CAL= 的差别是
        # **ADC 通道**, 不是"标定与否"。
        def _f(k):
            v = d.get(k)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float('nan')
        dev.append(_f('cal') if _f('cal') == _f('cal') else _f('i'))
        rb.append(r_avg)
        rows.append(dict(gid=p['gid'], kind=kind, peak_nA=p['peak'],
                         ton_ms=p['ton'], period_ms=p['period'],
                         duty=p['duty'], avg_nA=p['avg'],
                         true_avg_nA=true_avg_of(p), rep=len(dev),
                         mode=int(d['mode']), t_ms=int(d['t']) if d['t'] else 0,
                         dev_nA=dev[-1], i_nA=_f('i'), x_nA=_f('x'), cal_nA=_f('cal'),
                         train_s=float('nan'), rb_nA=r_avg * 1e9))


def run_point(ser, smu, p, kind, n, stop_flag, rows, pc_chop=False):
    """跑一组 (pulse 或 dc)。返回装置读数列表 (nA)。"""
    out = {}
    dev, rb = [], []
    if kind == 'pulse':
        ton, toff = p['ton'] / 1000.0, (p['period'] - p['ton']) / 1000.0
        if pc_chop:
            stop_flag['stop'] = False
            pc_chop_start(smu, ton, toff, stop_flag)
            print("      (PC 翻转 ton=%.0fms toff=%.0fms —— 占空比精度受限)"
                  % (ton * 1e3, toff * 1e3))
            time.sleep(1.0)
            _measure_loop(ser, smu, p, kind, n, rows, out)
        else:
            # 顺序: **先切脉冲模式(前置条件), 再开输出**。手册 7-113 里
            # `smua.source.output = smua.OUTPUT_ON` 也排在 Config 之前。
            pulse_mode(smu, p['peak'] * 1e-9, ton)
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            np_s = pulse_count(p, n)
            print("      脉冲串 %d 个 x %.0f ms = %.1f s  (要覆盖 %d 次测量)"
                  % (np_s, p['period'], np_s * p['period'] / 1000.0, n))
            # **脉冲串在子线程跑, 测量在主线程做。** 反过来(测量放子线程)不行:
            # 线程启动会白白耗掉串的开头。
            hold = {}

            def _train():
                try:
                    hold['train_s'] = pulse_start(smu, p['peak'] * 1e-9, ton, toff,
                                                  np_s)
                except Exception as e:      # 异常要带回主线程, 不能吞
                    hold['err'] = e

            th = threading.Thread(target=_train, daemon=True)
            th.start()
            time.sleep(0.5)                 # 让串先跑起来, 别测到串之前的 0
            # ⚠️ **预热必须在串**里**做** —— 预热的意义是丢弃"激励刚变化"之后的
            # 第一次读数, 而串一开始正是那个变化点。放到串之前等于没预热。
            # 2026-09-16 实测 (A 组): 第一次 14.13 / 7.19 / 2.93 / 1.50, 后两次
            # 19.94+19.90 / 10.00+10.03 / 4.10+4.07 / 2.12+2.12 —— **只有第一次坏**,
            # 它把均值拖到 -10%, 看起来像"占空比越小偏得越多"的物理效应, 其实是
            # 被测的第一次读数。pulse_count 里那个前导 10 s 就是留给它吃的。
            board_warmup(ser)
            _measure_loop(ser, smu, p, kind, n, rows, out, warmup=False)
            th.join(timeout=np_s * (ton + toff) + 60.0)
            if th.is_alive():
                print("      ** 脉冲串线程没退出 —— 装置可能还在测 **")
            if 'err' in hold:
                raise hold['err']
            # 回填实际串长, 并反解**这一点的 delta** —— delta 不该是个假设的常数,
            # 每点都记下来才能进不确定度预算 (真值 = I_peak x ton/(T + delta))。
            if 'train_s' in hold:
                dlt = hold['train_s'] / np_s * 1000.0 - p['period']
                hold['delta_ms'] = dlt
                for r in rows:
                    if r['gid'] == p['gid'] and r['kind'] == 'pulse':
                        r['train_s'] = hold['train_s']
                print("      实测串长 %.2f s / %d 个 -> 每周期 %.3f ms, "
                      "delta = %+.3f ms" % (hold['train_s'], np_s,
                                            hold['train_s'] / np_s * 1000.0, dlt))
            time.sleep(0.3)
    else:
        # ⚠️ 必须先把仪器还原成直流设置。上一轮脉冲调过 autorange/nplc/autozero,
        # 不还原的话这个直流对照点会带着"小档位 + NPLC 0.01"去测, **且不报错**。
        # 而 E3 判据(核心)是拿直流点当基准的 —— 基准被污染, 结论就不可信。
        dc_mode(smu)
        smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
        rng = smu_set(smu, p['avg'] * 1e-9)
        print("      直流段 %.4g nA -> 档位 %.4g A" % (p['avg'], rng))
        time.sleep(2.0)
        _measure_loop(ser, smu, p, kind, n, rows, out)

    dev, rb = out.get('dev', []), out.get('rb', [])

    if pc_chop:
        stop_flag['stop'] = True
        time.sleep(0.3)
    # 脉冲串停掉, 再设直流 —— 否则脉冲还在跑, 直流设不进去
    try:
        smu_write(smu, 'smua.abort()')
    except Exception:
        pass
    time.sleep(0.2)

    if not dev:
        print("      %-5s ** 无数据 **" % kind)
        return []
    print("      %-5s 装置 %+10.5f nA   源表 %+10.5f nA   (%d 次, MODE=%d)"
          % (kind, sum(dev) / len(dev), sum(rb) / len(rb) * 1e9,
             len(dev), rows[-1]['mode']))
    return dev


def wave_check(ser, smu, a):
    """波形自检 —— **用装置自己当探测器**。

    为什么必须单独验一次, 而不是只看 --check 通过:
      `--check` 只能证明"脉冲串跑起来了 (缓冲有 N 点)"。**一个恒定不变的直流
      输出同样能让缓冲填满 N 点** —— 所以"跑得动"完全不能证明"波形对"。

    2026-09-16 的 `bias` 就是这类错误: `bias = start = stop = 峰值` 时波形
      自始至终不变, 仪器不报错、缓冲照样填满, 但**输出的是直流不是脉冲串**。

    判别原理: 装置是**电荷积分器**, 读数 = 窗口内的平均电流。
        直流 40 nA            -> 读数 ~ 40 nA
        脉冲 40 nA / 50% 占空比 -> 读数应 ~ 20 nA
        若脉冲读数 ~ 40 nA     -> **bias 还是峰值, 根本没在脉冲**
    这是最省事也最直接的判据 —— 不需要示波器, 而且用的就是装置本身的原理。
    """
    period_ms, ton_ms = 10.0, 5.0
    n = a.n
    print("=" * 96)
    print("波形自检 —— 用装置当探测器 (电荷积分器, 读数 = 窗内平均电流)")
    print("=" * 96)
    print("峰值 %.4g nA, 周期 %g ms, 占空比 50%%" % (a.peak, period_ms))
    print("预期: 直流段 %.4g nA;  脉冲段 **一半** = %.4g nA"
          % (a.peak, a.peak * 0.5))
    print("若脉冲段读到接近 %.4g nA -> bias 还是峰值, 输出的是直流, 要回头改。"
          % a.peak)
    print("")

    rows = []
    sf = {'stop': False}
    print("--- 1/2 直流基准 ---")
    p_dc = dict(gid='WAVE-DC', peak=a.peak, ton=0.0, period=0.0, duty=1.0,
                avg=a.peak, note='波形自检 直流基准')
    dc_dev = run_point(ser, smu, p_dc, 'dc', n, sf, rows)
    print("--- 2/2 脉冲 ---")
    p_pl = dict(gid='WAVE-PL', peak=a.peak, ton=ton_ms, period=period_ms, duty=0.5,
                avg=a.peak * 0.5, note='波形自检 50% 脉冲')
    pl_dev = run_point(ser, smu, p_pl, 'pulse', n, sf, rows)

    print("")
    print("=" * 96)
    if not dc_dev or not pl_dev:
        print("** 数据不足 —— 装置或源表没通, 判不了。**")
        print("   (板子无响应时先给它断上电一次)")
        print("=" * 96)
        return False
    mdc = sum(dc_dev) / len(dc_dev)
    mpl = sum(pl_dev) / len(pl_dev)
    ratio = (mpl / mdc) if mdc else float('nan')
    print("直流 %.4f nA   脉冲 %.4f nA   比值 **%.3f**   (预期 0.500)"
          % (mdc, mpl, ratio))
    if abs(ratio - 0.5) < 0.05:
        print("-> **通过**: 脉冲段读数是一半, 波形确实在 0 与峰值之间跳变。")
        ok = True
    elif abs(ratio - 1.0) < 0.05:
        print("-> **未通过**: 脉冲段读数与直流相同 —— 波形恒定, 没有在脉冲。")
        print("   查 build_pulse_tsp 的 bias: 必须是 0, 不能是峰值。")
        ok = False
    else:
        print("-> **不确定**: 比值既不是 0.5 也不是 1.0。可能是升压/钳位/装置")
        print("   未稳定, 或脉冲串与窗口没同步。先查这两条再判。")
        ok = False
    print("=" * 96)
    return ok


def main():
    # Windows 控制台默认 GBK, 本文件输出全是中文 —— 不强制 utf-8 会乱码
    # (pulse_real.py 同样处理)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', default=None)
    ap.add_argument('--port', default=None)
    ap.add_argument('--n', type=int, default=3, help='趋势点的测量次数')
    ap.add_argument('--n-core', type=int, default=3, help='核心点的测量次数')
    ap.add_argument('--group', default=None,
                    help='只跑某些组, 逗号分隔 (如 A,C 或 E1,E2)')
    ap.add_argument('--list', action='store_true', help='只打印点表, 不连硬件')
    ap.add_argument('--check', action='store_true',
                    help='脉冲串自检: 切模式->开输出->配置->启动->看缓冲 (先跑这个!)')
    ap.add_argument('--wavecheck', action='store_true',
                    help='波形自检: 用装置当探测器, 直流基准 vs 50%% 脉冲, 判 bias 对不对')
    ap.add_argument('--peak', type=float, default=40.0,
                    help='--wavecheck 用的峰值 (nA), 默认 40 (= A1 的峰值)')
    ap.add_argument('--pc-chop', action='store_true',
                    help='退回 PC 翻转输出的方案 (只适合周期 >= 100 ms)')
    ap.add_argument('--no-dc', action='store_true', help='不跑直流对照点')
    ap.add_argument('-o', '--out', default='data/sweep_pulse.csv')
    a = ap.parse_args()

    pts = POINTS
    if a.group:
        want = [s.strip().upper() for s in a.group.split(',') if s.strip()]
        pts = [p for p in POINTS
               if p['gid'].upper() in want or p['gid'][0].upper() in want]
        if not pts:
            sys.exit("--group %s 没匹配到任何点" % a.group)

    print("=" * 96)
    print("脉冲输入测试" + ("  [--list]" if a.list else "")
          + ("  [--check 只自检]" if a.check else "")
          + ("  [--pc-chop]" if a.pc_chop else ""))
    print("=" * 96)
    print_table(pts, DC_POINTS if not a.no_dc else [])

    if a.list:
        return

    if a.pc_chop and min(p['period'] for p in pts) < 100:
        print("** 警告: --pc-chop 用于周期 < 100 ms 的点时, 占空比误差达 "
              "抖动/周期 >= 1%%, 不能用来判 E3 的 0.5%% **")
    if not a.visa:
        sys.exit("需要 --visa (或加 --list 只看点表)")

    smu = smu_open(a.visa)

    # ---- --check: 把脉冲配置下去, 让仪器自己回读 ----
    if a.check:
        # 自检: 走**完整**序列 (切模式 -> 开输出 -> 配置 -> 启动 -> 看缓冲)。
        # 原来的版本只"配置"不"启动", 而失败恰恰在启动那一步 —— 它抓不到。
        p = pts[0]
        ton, toff = p['ton'] / 1000.0, (p['period'] - p['ton']) / 1000.0
        npulse = 20                      # 自检用短的, 别等 10 s
        print("用第 1 个点 %s (峰值 %g nA, ton %g ms, toff %g ms, %d 个脉冲):"
              % (p['gid'], p['peak'], p['ton'], p['period'] - p['ton'], npulse))
        print("   ", build_pulse_tsp(p['peak'] * 1e-9, ton, toff, npulse))
        try:
            pulse_mode(smu, p['peak'] * 1e-9, ton)
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            ok, msg = pulse_configure(smu, p['peak'] * 1e-9, ton, toff, npulse)
            print("    Config 返回:   %s  %s" % ("true" if ok else "**false**", msg))
            if not ok:
                print("")
                print(">>> **配置失败** —— 按上面的 msg 处理, 别往下跑。")
                print(">>> (Config 只做**可行性校验**, 手册 7-39 —— 它成功不代表能跑)")
                return
            ok2, msg2, _el = pulse_initiate(smu, npulse, ton + toff)
            print("    Initiate 返回: %s  %s" % ("true" if ok2 else "**false**", msg2))
            time.sleep(0.5)
            n = int(float(smu.query('print(smua.nvbuffer1.n)')))
            print("    缓冲点数: %d / %d" % (n, npulse))
            print("    仪器回读:")
            for q in ('print(smua.source.rangei)',
                      'print(smua.measure.rangei)',
                      'print(smua.measure.nplc)',
                      'print(smua.measure.autozero)',
                      'print(smua.source.limitv)',
                      'print(smua.nvbuffer1.appendmode)'):
                try:
                    print("       %-42s -> %s" % (q, smu.query(q).strip()))
                except Exception as e:
                    print("       %-42s -> ** %s" % (q, str(e)[:40]))
            print("")
            if ok2 and n >= npulse:
                print(">>> **通过** —— 脉冲串真的跑起来了 (%d/%d 点)。" % (n, npulse))
                print(">>> 但自检只证明「跑得动」, 不证明「波形对」 ——")
                print(">>> 波形靠 bias=0 + start=stop=峰值, 上采集前最好用示波器看一眼。")
            else:
                print(">>> **未通过** —— Initiate 返回 false 或缓冲没长。")
                print(">>> 按返回的 msg 对照 pulse_mode() 里那张前置条件表。")
        except Exception as e:
            print("** 自检失败: %s" % e)
        finally:
            try:
                smu_write(smu, 'smua.abort()')
            except Exception:
                pass
            smu_off(smu)
        # 注意 return 不能写在 finally 里 —— 那会吞掉 try 里抛出的异常
        return

    if a.wavecheck:
        if not a.port:
            sys.exit("--wavecheck 需要 --port (要读装置读数)")
        ser = board_open(a.port)
        try:
            wave_check(ser, smu, a)
        finally:
            smu_off(smu)
            ser.close()
        return

    ser = board_open(a.port)
    rows = []
    stop_flag = {'stop': False}

    # ---- 按 I_avg 分组交织: 同一平均电流下的脉冲点跑完, 紧跟该点的直流对照 ----
    # 不按"每个脉冲点各跑一次直流"是为了省时间; 不按"全部脉冲跑完再跑全部直流"
    # 是为了**抑制源表漂移** —— 相隔三小时的两次读数比较是不可信的。
    # ⚠️ **分组键必须和过滤条件同源。** 原来看法是:
    #     avgs = sorted({p['avg'] for p in pts})               <- 集合, 按精确浮点去重
    #     grp  = [p for p in pts if abs(p['avg']-avg) < 1e-9]  <- 过滤, 带容差
    # K 组就栽在这: `avg = peak x ton/T` 对 K4..K8 算出 40.00000000000001,
    # 与 K1..K3 的 40.0 **不相等** -> avgs 里出现两个键; 而 1e-9 的容差让
    # **两个键都匹配全部 8 个点** -> K1..K8 各跑两遍 (CSV 里 10 行, 2026-09-16 实测)。
    # 改成先取整成同一个键, 组内也用这个键比较。
    def _akey(v):
        return round(v, 9)
    avgs = sorted({_akey(p['avg']) for p in pts})
    try:
        for avg in avgs:
            grp = [p for p in pts if _akey(p['avg']) == avg]
            print("")
            print("╔══ I_avg = %.4g nA ══════════════════════════════════════" % avg)
            for p in grp:
                n = a.n_core if p['gid'] in CORE else a.n
                print("║ [%s] 峰值 %.4g nA / t_on %.2f ms / T %.0f ms / D %.1f%%  (n=%d)"
                      % (p['gid'], p['peak'], p['ton'], p['period'],
                         p['duty'] * 100, n))
                try:
                    run_point(ser, smu, p, 'pulse', n, stop_flag, rows, a.pc_chop)
                except Exception as e:
                    # 预期失败的点 (E2 / K7) 不该把整轮拖停 —— 它们**就是要失败**,
                    # 用来标定失效模式。第一版没有这个保护, K4 一个
                    # "Pulse too short" 就把整轮干掉了, 前面采的数据连 CSV 都没落盘。
                    if not p.get('xfail'):
                        raise
                    print("      ** 预期失败 (xfail), 记下并继续: %s **"
                          % str(e).splitlines()[0][:90])
                    rows.append(dict(gid=p['gid'], kind='pulse', peak_nA=p['peak'],
                                     ton_ms=p['ton'], period_ms=p['period'],
                                     duty=p['duty'], avg_nA=p['avg'],
                                     true_avg_nA=true_avg_of(p), rep=0,
                                     mode=-1, t_ms=0, dev_nA=float('nan'),
                                     i_nA=float('nan'), x_nA=float('nan'),
                                     cal_nA=float('nan'), train_s=float('nan'),
                                     rb_nA=float('nan')))
            if not a.no_dc:
                dc = dict(DC_POINTS=0, gid='D:%g' % avg, peak=avg, ton=0.0,
                          period=0.0, duty=1.0, avg=avg,
                          note='直流对照')
                n = a.n_core if avg in (0.1, 1.0, 40.0) else a.n
                print("║ [直流对照 %.4g nA]  (n=%d)" % (avg, n))
                run_point(ser, smu, dc, 'dc', n, stop_flag, rows)
            print("╚" + "═" * 60)
            smu_off(smu)
    finally:
        stop_flag['stop'] = True
        smu_off(smu)
        ser.close()

    if not rows:
        print("没有任何数据")
        return

    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("\n已写", a.out, "(%d 行)" % len(rows))

    # ---- 判据 E1 / E3 ----
    print("")
    print("=" * 96)
    print("E1: 脉冲读数 vs 计算平均值 I_avg")
    print("    真值 = I_peak x ton/(T + %.2f ms) —— 实测周期比标称长 DELTA_MS," % DELTA_MS)
    print("    标称值系统性偏高 delta/T; 判据**以真值为准**, 标称列留作对照。")
    print("=" * 96)
    print("%5s %9s %10s %10s %12s %9s %9s"
          % ("#", "峰值nA", "I_avg标称", "I_avg真值", "装置读数", "偏差标称", "偏差真值"))
    worst1, bad1, seen1 = 0.0, False, 0
    for p in pts:
        v = [r['dev_nA'] for r in rows
             if r['gid'] == p['gid'] and r['kind'] == 'pulse'
             and math.isfinite(r['dev_nA'])]   # xfail 占位行是 nan, 要跳过
        if not v:
            continue
        seen1 += 1
        mu = sum(v) / len(v)
        tv = true_avg_of(p)
        dd = (mu - p['avg']) / p['avg'] * 100 if p['avg'] else float('nan')
        dt = (mu - tv) / tv * 100 if tv else float('nan')
        if math.isfinite(dt):
            worst1 = max(worst1, abs(dt))
        else:
            bad1 = True
        print("%5s %9.4g %10.4f %10.4f %+12.5f %+8.3f%% %+8.3f%%%s"
              % (p['gid'], p['peak'], p['avg'], tv, mu, dd, dt,
                 "" if math.isfinite(dt) else "  **无效**"))
    print("-" * 96)
    # 没有数据时 worst 停在 0.0 会**假报通过** —— 必须显式拦住
    if seen1 == 0:
        print("** 没有任何脉冲数据 —— 判据不成立, 不能算通过 **")
    else:
        print("最大 |偏差(真值)| = %.3f %%   (判据 < 1.5%%, %d/%d 点有数据)  -> %s"
              % (worst1, seen1, len(pts), "**差值无效**" if bad1 else
                 ("通过" if worst1 < 1.5 else "**未通过**")))

    print("")
    print("=" * 96)
    print("E3 (核心): 同一 I_avg 下, 脉冲 vs 直流")
    print("    直流点没有周期开销, 脉冲点有 -> 两边**要对齐到真值**才公平:")
    print("    等效直流量 = 直流读数 x (I_avg真值/标称)。判据以**修正后**为准。")
    print("=" * 96)
    print("%6s %9s %10s %9s %11s %11s %9s %9s"
          % ("组", "I_avg标称", "I_avg真值", "脉冲", "直流(等效)", "实际直流", "差标称", "差修正"))
    worst3, bad3, seen3 = 0.0, False, 0
    for avg in avgs:
        dv = [r['dev_nA'] for r in rows
              if r['kind'] == 'dc' and abs(r['avg_nA'] - avg) < 1e-9
              and math.isfinite(r['dev_nA'])]
        if not dv:
            continue
        dmu = sum(dv) / len(dv)
        for p in [q for q in pts if abs(q['avg'] - avg) < 1e-9]:
            pv = [r['dev_nA'] for r in rows
                  if r['gid'] == p['gid'] and r['kind'] == 'pulse'
                  and math.isfinite(r['dev_nA'])]
            if not pv:
                continue
            pmu = sum(pv) / len(pv)
            tv = true_avg_of(p)
            # 该脉冲"等效"的直流读数 —— 直流点没有周期开销, 只是电平不同
            deq = dmu * (tv / avg) if avg else float('nan')
            dd = (pmu - dmu) / dmu * 100 if dmu else float('nan')
            dt = (pmu - deq) / deq * 100 if deq else float('nan')
            if math.isfinite(dt):
                worst3 = max(worst3, abs(dt))
                seen3 += 1
            else:
                bad3 = True
            tag = "  <- 峰值 %.0f nA" % p['peak'] if p['peak'] > 45 else ""
            print("%6s %9.4f %10.4f %+9.4f %+11.4f %+11.4f %+8.3f%% %+8.3f%%%s%s"
                  % (p['gid'], avg, tv, pmu, deq, dmu, dd, dt,
                     "" if math.isfinite(dt) else "  **无效**", tag))
    print("-" * 96)
    if seen3 == 0:
        print("** 没有可比较的 脉冲/直流 配对 —— 判据不成立, 不能算通过 **")
        if a.no_dc:
            print("   (本次带了 --no-dc, 所以没有直流对照)")
    else:
        print("最大 |差(修正后)| = %.3f %%   (判据 < 0.5%%, %d 对)  -> %s"
              % (worst3, seen3, "**差值无效**" if bad3 else
                 ("通过" if worst3 < 0.5 else "**未通过**")))
    print("")
    print("注: 标了 '<- 峰值 ... nA' 的行是**峰值超出 ±45 nA 直流量程**的点。")
    print("    这些行若同样落在 0.5% 内, 就是\"脉冲调制不影响准确度\"的最强证据。")
    if a.pc_chop:
        print("")
        print("** 本次用了 --pc-chop: 占空比精度受 PC 调度抖动限制 (约 1-3 ms),")
        print("   这个误差会 1:1 进 E1/E3, 所以不能用来判 0.5% —— 只作定性验证。**")


if __name__ == '__main__':
    main()
