# -*- coding: utf-8 -*-
"""
脉冲输入测试 (需求④): 验证「脉冲 ≡ 等效直流」—— 只做关键三点

三个点 (方案见 docs/脉冲电流实验方案.md, 这里只取最能说明问题的三条):
    P1  峰值  40 nA, 占空比 25%  ->  平均  10 nA     对照:  10 nA 直流
    P2  峰值 200 nA, 占空比 10%  ->  平均  20 nA     对照:  20 nA 直流
        ^^ 峰值 200 nA **超出装置 ±45 nA 的直流量程 4.4 倍**, 仍应测出 20 nA
    P3  峰值   1 nA, 占空比 10%  ->  平均 0.1 nA     对照: 0.1 nA 直流

判据: 同一条平均电流下, 脉冲读数与直流读数之差 < 0.5% -> 「脉冲调制不影响准确度」。

⚠️ 关于脉冲怎么产生
------------------
需要 2636B 的**脉冲源模式** (TSP: ConfigPulseIMeasureVSweepLin 一类)。
**我没有实机验证过那几个 TSP 函数的参数顺序** —— 网表/手册对得上, 但没跑过。
所以本脚本把脉冲配置写成 **可替换的 TSP 字符串** (--pulse-cmd), 并提供了:

    --check     只配置脉冲并把仪器的回读打出来, 不采集
                **先跑这个**, 用示波器或万用表确认波形对了, 再上采集。

如果脉冲模式一时调不通, 用 --pc-chop 退回"PC 翻转输出"的方案: 它只适合
**长脉冲** (ton >= 100ms), 因为 LAN 往返 + Python 调度的抖动在 ms 量级,
占空比误差 = 抖动/周期, 1 ms 的抖动落在 100 ms 周期上是 1% —— 够做定性验证,
不够卡 0.5% 的判据。脚本会在结果里如实标出这个局限。

用法
----
    python tools/sweep_pulse.py --visa "TCPIP0::<ip>::5025::SOCKET" --check
    python tools/sweep_pulse.py --visa ... --port COM8
    python tools/sweep_pulse.py --visa ... --pc-chop --ton 0.1 --toff 0.9
"""
import argparse
import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, board_open, board_measure,
                        ReadbackSampler, board_warmup)

# 三个关键点: (峰值 nA, 占空比, 说明)
KEY_POINTS = [
    (40.0, 0.25, "峰值 40nA / 25% -> 平均 10nA"),
    (200.0, 0.10, "峰值 200nA / 10% -> 平均 20nA  (峰值超直流量程 4.4 倍)"),
    (1.0, 0.10, "峰值 1nA / 10% -> 平均 0.1nA"),
]

PERIOD_S = 0.010        # 脉冲周期 10 ms (方案里定的下限: 再短占空比就测不准了)


def build_pulse_tsp(i_peak, ton, toff, n_pulse):
    """生成配置脉冲串的 TSP。**参数顺序待与 2636B 参考手册核对** —— 用 --check 验证。

    思路: 用 ConfigPulseIMeasureVSweepLin 把起止电流设成同一个值, 得到等幅脉冲串。
    """
    return (
        "ConfigPulseIMeasureVSweepLin("
        "smua, %.9e, %.9e, %d, %.6f, %.6f, smua.nvbuffer1, 1)"
        % (i_peak, i_peak, n_pulse, ton, toff)
    )


def pulse_start(smu, i_peak, duty, n_pulse):
    ton = PERIOD_S * duty
    toff = PERIOD_S - ton
    smu.write('smua.source.leveli = %.9e' % i_peak)
    smu.write(build_pulse_tsp(i_peak, ton, toff, n_pulse))
    smu.write('InitiatePulseTest(1)')
    return ton, toff


def pc_chop_start(smu, i_peak, duty, ton, toff, duration, stop_flag):
    """退化方案: PC 直接翻转输出。只在长脉冲下可用 (见文件头)。"""
    import threading

    def loop():
        while not stop_flag['stop']:
            smu.write('smua.source.output = smu.OUTPUT_ON')
            time.sleep(ton)
            smu.write('smua.source.output = smu.OUTPUT_OFF')
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--n', type=int, default=3, help='每点测量次数')
    ap.add_argument('--check', action='store_true',
                    help='只配置脉冲并打印仪器回读, 不采集 (先跑这个!)')
    ap.add_argument('--pc-chop', action='store_true',
                    help='退回 PC 翻转输出的方案 (只适合长脉冲)')
    ap.add_argument('--ton', type=float, default=0.1, help='--pc-chop 的导通时间 (s)')
    ap.add_argument('--toff', type=float, default=0.9, help='--pc-chop 的关断时间 (s)')
    ap.add_argument('-o', '--out', default='data/sweep_pulse.csv')
    a = ap.parse_args()

    print("=" * 70)
    print("脉冲输入测试" + ("  [--check 只自检]" if a.check else ""))
    print("=" * 70)

    smu = smu_open(a.visa)

    # ---- --check: 把脉冲配置下去, 让仪器自己回读 ----
    if a.check:
        i_peak = KEY_POINTS[0][0] * 1e-9
        duty = KEY_POINTS[0][1]
        ton = PERIOD_S * duty
        toff = PERIOD_S - ton
        cmd = build_pulse_tsp(i_peak, ton, toff, 1000)
        print("即将下发:")
        print("   ", cmd)
        try:
            smu.write('smua.source.leveli = %.9e' % i_peak)
            smu.write('smua.source.output = smu.OUTPUT_ON')
            smu.write(cmd)
            print("配置成功。仪器回读:")
            for q in ('print(smua.trigger.source.pulsewidth)',
                      'print(smua.source.leveli)',
                      'print(smua.source.func)'):
                try:
                    print("   %-42s -> %s" % (q, smu.query(q).strip()))
                except Exception as e:
                    print("   %-42s -> ** %s" % (q, e))
            print()
            print(">>> 现在用示波器/万用表确认波形。对了再跑正式采集。")
            print(">>> 若上面报错, 说明这个 TSP 函数的参数与本机固件版本不符,")
            print(">>> 请按 2636B 参考手册改 build_pulse_tsp() 里的参数顺序。")
        except Exception as e:
            print("** 配置失败: %s" % e)
            print("   -> 手册里确认 ConfigPulseIMeasureVSweepLin 的参数顺序")
        finally:
            smu_off(smu)
        # 注意 return 不能写在 finally 里 —— 那会吞掉 try 里抛出的异常
        return

    ser = board_open(a.port)
    rows = []
    stop_flag = {'stop': False}
    chop_thread = None
    try:
        for i_peak, duty, label in KEY_POINTS:
            i_avg = i_peak * duty
            print()
            print("── %s" % label + " " + "─" * 40)
            for kind in ('pulse', 'dc'):
                if kind == 'pulse':
                    if a.pc_chop:
                        stop_flag['stop'] = False
                        chop_thread = pc_chop_start(smu, i_peak * 1e-9, duty,
                                                    a.ton, a.toff, 0, stop_flag)
                        print("    (PC 翻转, ton=%.0fms toff=%.0fms —— 占空比精度受限)"
                              % (a.ton * 1e3, a.toff * 1e3))
                        time.sleep(1.0)
                    else:
                        smu.write('smua.source.output = smu.OUTPUT_ON')
                        pulse_start(smu, i_peak * 1e-9, duty, 100000)
                        time.sleep(0.3)
                else:
                    if chop_thread:
                        stop_flag['stop'] = True; time.sleep(0.3); chop_thread = None
                    # 先把脉冲串停掉, 再设直流 —— 否则脉冲可能还在跑, 直流设不进去
                    try:
                        smu.write('smua.abort()')
                    except Exception:
                        pass
                    time.sleep(0.2)
                    smu.write('smua.source.output = smu.OUTPUT_ON')
                    smu.write('smua.source.leveli = %.9e' % (i_avg * 1e-9))
                    time.sleep(2.0)

                # 预热: 空跑一次丢弃 —— 每批第一次的坏率特别高
                # (见 cal_sweep.py 里 board_warmup 的注释)
                board_warmup(ser)

                dev, rb = [], []
                tries = 0
                while (len(dev) < a.n) and (tries < a.n * 3):
                    tries += 1
                    # 脉冲段不采回读 (见 measure_once 的注释); 直流段照采
                    d, r_avg = measure_once(ser, smu, sample_readback=(kind == 'dc'))
                    if d is None:
                        continue
                    if d.get('timeout'):    # 坏点补测 (原来会静默混进均值)
                        print("      (丢弃一次 TIMEOUT)"); continue
                    dev.append(float(d['i']))
                    rb.append(r_avg)
                    rows.append(dict(peak_nA=i_peak, duty=duty, avg_nA=i_avg,
                                     kind=kind, rep=len(dev), mode=int(d['mode']),
                                     t_ms=int(d['t']) if d['t'] else 0,
                                     dev_nA=float(d['i']), rb_nA=r_avg * 1e9))
                if not dev:
                    print("      %-5s ** 无数据 **" % kind); continue
                print("      %-5s 装置 %+10.5f nA   源表 %+10.5f nA   (%d 次, MODE=%d)"
                      % (kind, sum(dev) / len(dev), sum(rb) / len(rb) * 1e9,
                         len(dev), rows[-1]['mode']))

                if kind == 'pulse' and chop_thread:
                    stop_flag['stop'] = True; time.sleep(0.3); chop_thread = None
            smu_off(smu)
    finally:
        stop_flag['stop'] = True
        smu_off(smu)
        ser.close()

    if not rows:
        print("没有任何数据"); return

    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("\n已写", a.out, "(%d 行)" % len(rows))

    # ---- E3 判据: 同一平均电流下 脉冲 vs 直流 ----
    print()
    print("=" * 70)
    print("E3 判据: 同一平均电流下, 脉冲读数 vs 直流读数")
    print("=" * 70)
    print("%10s %10s %14s %14s %10s" % ("峰值(nA)", "占空比", "脉冲读数(nA)", "直流读数(nA)", "差(%)"))
    worst = 0.0
    bad = False
    for i_peak, duty, _ in KEY_POINTS:
        p = [r['dev_nA'] for r in rows if r['peak_nA'] == i_peak and r['kind'] == 'pulse']
        d = [r['dev_nA'] for r in rows if r['peak_nA'] == i_peak and r['kind'] == 'dc']
        if not p or not d:
            print("%10.4g %10.2f %14s %14s %10s" % (i_peak, duty, "-", "-", "-")); continue
        pa, da = sum(p) / len(p), sum(d) / len(d)
        dd = (pa - da) / da * 100 if da else float('nan')
        # 差值是 nan 时**不能**走 max(worst, ...) —— Python 的 max(0.0, nan)
        # 返回 0.0 (nan > 0.0 为 False), worst 原封不动, 于是表里是 nan 而
        # 结论行照样打印"通过"。必须显式标记失败。
        if math.isfinite(dd):
            worst = max(worst, abs(dd))
        else:
            bad = True
        print("%10.4g %10.2f %14.5f %14.5f %+10.3f%s"
              % (i_peak, duty, pa, da, dd, "" if math.isfinite(dd) else "  **差值无效**"))
    print()
    if bad:
        print("** 有点的差值算不出来 (直流读数分母为 0 或缺数据) —— 判据不成立, 不能算通过 **")
    else:
        print("最大 |差| = %.3f %%   (判据 < 0.5%%)  -> %s"
              % (worst, "通过" if worst < 0.5 else "**未通过 / 见下方局限**"))
    if a.pc_chop:
        print()
        print("注意: 本次用 --pc-chop, 占空比精度受 PC 调度抖动限制 (ton=%.0fms 上约 %.1f%%),"
              % (a.ton * 1e3, 0.003 / a.ton * 100))
        print("      这个误差会 1:1 进 E3, 所以**不能用它判 0.5%** —— 只作定性验证。")


if __name__ == '__main__':
    main()
