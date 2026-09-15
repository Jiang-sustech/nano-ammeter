# -*- coding: utf-8 -*-
"""
2636B 单独自检 —— **不接纳安表, 不开串口**。

以 tools/cal_sweep.py 为蓝本, 只保留跟源表打交道的部分, 用来把"源表这半边"
单独跑通。装置那半边(串口 / S 指令 / 窗口)这里一概不碰。

    python tools/test_smu.py --visa "TCPIP0::<ip>::5025::SOCKET"
    python tools/test_smu.py --visa ... --no-pulse     # 跳过脉冲段
    python tools/test_smu.py --visa ... --max-na 45    # 收紧电流上限

⚠️ **请把源表输出短接后再跑**
------------------------------
不只是"为了安全"这么简单 —— 这是**唯一能安全测的接法**。刚查实的机制:

    2600B 手册: 顺从限不被超过的**唯一例外**是 ISOURCE 下的 VLIMIT ——
    "the VLIMIT will source or sink up to 102 mA for ISOURCE ranges on or
     below 100 mA"。**输出一顶到电压合规, 就不再受 leveli 约束。**

    输出短接 -> 电压恒为 ~0 -> 永远到不了合规 -> 那条 102 mA 的路根本不开。
    输出开路 -> 电压顶到 limitv -> 若接了被测电路, 钳位二极管导通就危险。

所以本脚本第 0 步就是**验证到底短没短**(读输出电压), 报出来。
短接电阻也顺带量了: R_short = V / I_set。

测什么
------
  0  连接与型号
  1  短接自检 (读 V, 算短接电阻)
  2  DC 阶梯: 设定 vs 回读、实际档位、输出电压   <- 源表自身的增益误差
  3  噪声与漂移: 固定电流下连采, 报 σ 与峰峰值   <- "±1 pA 到底可不可用"
  4  脉冲 TSP 自检: 配置脉冲串并回读参数         <- 这一段**从未在真机上跑过**
  5  安全关断状态回读

**不判通过/不通过** —— 这是一次"看看它到底什么脾气"的自检, 数值报出来自己看。
"""
import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import smu_open, smu_set, smu_off
from sweep_pulse import build_pulse_tsp

# 直流阶梯 (nA)。取的是标定用的那组, 正负兼备
DC_NA = [45.0, 20.0, 10.0, 5.0, 1.0, -1.0, -5.0, -10.0, -20.0, -45.0]
# pA 段单列 —— "±1 pA 这个下限是否真的可用"至今没有结论 (见 docs/2636B源表接入.md §六)
PA_NA = [1.0, 0.1, 0.01]


def q(smu, tsp):
    """查询并转 float, 失败返回 nan (自检脚本不该因为一条查询失败就中断)"""
    try:
        return float(smu.query('print(%s)' % tsp))
    except Exception:
        return float('nan')


def read_v(smu):
    """源表实际输出电压 (V)。

    ⚠️ 切换测量功能 —— 调用期间**不要**同时跑回读采样, 两者会互相干扰
    (2600B 的 measure.func 同一时刻只管一个)。本脚本是串行的, 安全。
    """
    return q(smu, 'smua.measure.v()')


def read_i_n(smu, n, dt):
    """连采 n 个回读电流, 返回 (列表, 秒)"""
    vals = []
    t0 = time.time()
    for _ in range(n):
        vals.append(q(smu, 'smua.measure.i()'))
        time.sleep(dt)
    return vals, time.time() - t0


def stat(v):
    v = [x for x in v if math.isfinite(x)]
    if not v:
        return (float('nan'),) * 4
    mu = sum(v) / len(v)
    if len(v) > 1:
        sd = math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))
    else:
        sd = 0.0
    return mu, sd, min(v), max(v)


def display(x):
    """按量级自动选单位显示"""
    if not math.isfinite(x):
        return "   ---   "
    a = abs(x)
    if a >= 1e-6:
        return "%+9.4f uA" % (x * 1e6)
    if a >= 1e-9:
        return "%+9.4f nA" % (x * 1e9)
    if a >= 1e-12:
        return "%+9.4f pA" % (x * 1e12)
    return "%+9.4f fA" % (x * 1e15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--n', type=int, default=8, help='每个点连采几个回读值')
    ap.add_argument('--dt', type=float, default=0.25, help='连采间隔 (s)')
    ap.add_argument('--settle', type=float, default=2.0, help='设定后稳定 (s)')
    ap.add_argument('--max-na', type=float, default=100.0,
                    help='电流上限 (nA); 超过直接拒绝运行')
    ap.add_argument('--no-pulse', action='store_true', help='跳过脉冲段')
    ap.add_argument('--no-pa', action='store_true', help='跳过 pA 段')
    ap.add_argument('--dry-run', action='store_true',
                    help='只打印要做什么, 不连仪器')
    a = ap.parse_args()

    all_na = list(DC_NA) + ([] if a.no_pa else list(PA_NA))
    worst = max(abs(x) for x in all_na)
    if worst > a.max_na:
        sys.exit("** 点表最大电流 %.4g nA 超过 --max-na %.4g nA, 拒绝运行 **"
                 % (worst, a.max_na))

    print("=" * 78)
    print("2636B 单独自检  (不接纳安表, 不开串口)")
    print("=" * 78)
    print("电流上限 %.4g nA   连采 %d 次 x %.2f s   稳定 %.1f s"
          % (a.max_na, a.n, a.dt, a.settle))
    print("直流阶梯 (%d 点): %s nA"
          % (len(DC_NA), " ".join("%.4g" % x for x in DC_NA)))
    if not a.no_pa:
        print("pA 段   (%d 点): %s pA"
              % (len(PA_NA), " ".join("%.4g" % x for x in PA_NA)))
    print("** 确认源表输出已经短接 ** —— 短接是唯一能安全测的接法")

    if a.dry_run:
        print("")
        print("[--dry-run] 不连仪器。将要执行的步骤:")
        print("  0  连接 + 读型号/序列号/limitv/offlimitv")
        print("  1  短接自检: 源 %g nA, 读 V, 算短接电阻" % 45)
        print("  2  DC 阶梯: %d 个点, 每点读回读 x%d + 输出电压 + 实际档位"
              % (len(all_na), a.n))
        print("  3  噪声/漂移: 固定 10 nA 连采 10 s")
        if not a.no_pulse:
            print("  4  脉冲 TSP 自检: 下发 ConfigPulseIMeasureVSweepLin 并回读参数")
        print("  5  关断状态回读 (输出/offmode/offfunc/offlimitv)")
        print("")
        per = a.settle + a.n * a.dt + 0.6
        print("运行时长估计: DC 段 %d 点 x %.1f s ≈ %.0f s, 其余步骤约 40 s, 合计约 %.0f s"
              % (len(all_na), per, len(all_na) * per,
                 len(all_na) * per + 40))
        return
    print("")

    smu = smu_open(a.visa)
    try:
        # ---------------- 0 连接 ----------------
        print("── 0 连接 ──")
        # 这三条返回**字符串**, 不能用 q() (它是 float 转换)
        for tsp, label in (('localnode.model', '型号'),
                           ('localnode.description', '描述'),
                           ('localnode.serialno', '序列号')):
            try:
                print("   %-6s: %s" % (label, smu.query('print(%s)' % tsp).strip()))
            except Exception as e:
                print("   %-6s: ** %s" % (label, e))
        print("   limitv   : %.4g V" % q(smu, 'smua.source.limitv'))
        print("   offlimitv: %.4g V" % q(smu, 'smua.source.offlimitv'))
        print("")

        # ---------------- 1 短接自检 ----------------
        # 用最大的一个点量 —— 电流越大, 短接电阻上的压降越好测
        i_chk = 45e-9
        smu.write('smua.source.output = smu.OUTPUT_ON')
        smu_set(smu, i_chk)
        time.sleep(a.settle)
        v_chk = read_v(smu)
        r_chk = v_chk / i_chk if i_chk else float('nan')
        print("── 1 短接自检 (源 %.4g nA) ──" % (i_chk * 1e9))
        print("   输出电压 V = %+.6f V   ->  短接电阻 R = %.4g ohm"
              % (v_chk, r_chk))
        if not math.isfinite(v_chk):
            print("   ** 读不到电压 —— 检查连接 **")
        elif abs(v_chk) < 1.0:
            print("   -> 短接/虚地确认 (V 远低于 limitv) —— **安全, 可以继续**")
        else:
            print("   ** 警告: V = %.3f V, 已接近 limitv —— **输出很可能没有短接**!" % v_chk)
            print("      继续跑会在合规状态下测试, 测到的不是源表本身的特性。")
            print("      若要继续, 请确认接的是短路而不是开路/高阻。")
        smu_off(smu)
        time.sleep(0.3)
        print("")

        # ---------------- 2 DC 阶梯 ----------------
        print("── 2 DC 阶梯: 设定 vs 回读 ──")
        print("%11s %13s %13s %10s %9s %11s"
              % ("设定", "回读均值", "回读 sigma", "偏差", "档位", "输出电压"))
        for na in all_na:
            i_set = na * 1e-9
            smu.write('smua.source.output = smu.OUTPUT_ON')
            rng = smu_set(smu, i_set)
            time.sleep(a.settle)
            vals, _ = read_i_n(smu, a.n, a.dt)
            v = read_v(smu)
            smu_off(smu)
            time.sleep(0.3)
            mu, sd, lo, hi = stat(vals)
            dev = (mu - i_set) / i_set * 100 if i_set else float('nan')
            print("%8.4g nA %13s %13s %9.3f%% %8.4g %11.3e"
                  % (na, display(mu), display(sd), dev, rng, v))
        print("")
        print("   档位是 autorange 实际选中的 (smua.source.rangei)。")
        print("   回读的固定误差项随档位变: 1nA 档 240fA / 10nA 档 3pA / 100nA 档 40pA")
        print("")

        # ---------------- 3 噪声与漂移 ----------------
        print("── 3 噪声与漂移 (固定 10 nA, 连采 10 s) ──")
        smu.write('smua.source.output = smu.OUTPUT_ON')
        smu_set(smu, 10e-9)
        time.sleep(a.settle)
        n_long = max(20, int(10.0 / max(a.dt, 0.05)))
        vals, dur = read_i_n(smu, n_long, a.dt)
        mu, sd, lo, hi = stat(vals)
        print("   %d 次 / %.1f s" % (len(vals), dur))
        print("   均值 %s   sigma %s   峰峰 %s"
              % (display(mu), display(sd), display(hi - lo)))
        if len(vals) > 4:
            h1 = stat(vals[:len(vals) // 2])[0]
            h2 = stat(vals[len(vals) // 2:])[0]
            print("   前半 %s  后半 %s  漂移 %s (相对 %.3f%%)"
                  % (display(h1), display(h2), display(h2 - h1),
                     (h2 - h1) / h1 * 100 if h1 else float('nan')))
        smu_off(smu)
        time.sleep(0.3)
        print("")

        # ---------------- 4 脉冲 TSP 自检 ----------------
        if not a.no_pulse:
            print("── 4 脉冲 TSP 自检 (参数顺序从未在真机上验证过) ──")
            i_pk, ton, toff = 40e-9, 5e-3, 5e-3
            cmd = build_pulse_tsp(i_pk, ton, toff, 1000)
            print("   即将下发:")
            print("     ", cmd)
            try:
                smu.write('smua.source.output = smu.OUTPUT_ON')
                smu_set(smu, i_pk)
                smu.write(cmd)
                print("   配置**成功**, 仪器回读:")
                for tsp in ('smua.trigger.source.pulsewidth',
                            'smua.trigger.source.pulserange',
                            'smua.source.leveli',
                            'smua.source.func',
                            'smua.source.rangei'):
                    try:
                        print("     %-42s -> %s"
                              % (tsp, smu.query('print(%s)' % tsp).strip()))
                    except Exception as e:
                        print("     %-42s -> ** %s" % (tsp, e))
                print("")
                print("   >>> 脉冲模式跑得通。若上面报错, 说明这个 TSP 函数")
                print("   >>> 的参数顺序与本机固件版本不符, 按 2636B 手册改")
                print("   >>> build_pulse_tsp() 里的参数顺序。")
            except Exception as e:
                print("   ** 配置失败: %s" % e)
                print("   -> 手册里确认 ConfigPulseIMeasureVSweepLin 的参数顺序")
            finally:
                try:
                    smu.write('smua.abort()')
                except Exception:
                    pass
                smu_off(smu)
            print("")

        # ---------------- 5 关断状态 ----------------
        print("── 5 关断状态回读 ──")
        smu_off(smu)
        time.sleep(0.5)
        print("   输出    :", q(smu, 'smua.source.output'))
        print("   offmode :", q(smu, 'smua.source.offmode'),
              " (2 = OUTPUT_NORMAL)")
        print("   offfunc :", q(smu, 'smua.source.offfunc'),
              " (0 = OUTPUT_DCAMPS)")
        print("   offlimitv: %.4g V   <- 关输出后线上挂的限压, 越小越安全"
              % q(smu, 'smua.source.offlimitv'))
    finally:
        smu_off(smu)
        try:
            smu.close()
        except Exception:
            pass

    print("")
    print("=" * 78)
    print("自检结束。本脚本**不判通过/不通过** —— 数值在上面, 自己看。")
    print("重点看三处:")
    print("  ① 第 1 步 V 是否远低于 limitv (确认真的短接了)")
    print("  ② 第 2 步 偏差是否随档位跳 (跳 = 档位切换带来的系统差)")
    print("  ③ 第 3 步 sigma 决定 pA 段到底能不能用")


if __name__ == '__main__':
    main()
