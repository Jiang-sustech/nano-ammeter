# -*- coding: utf-8 -*-
"""
量程效应判定实验 —— 同一个电流, 交替用 **autorange / 锁 10 nA / 锁 100 nA** 测

    python tools/range_test.py --visa "TCPIP0::192.168.0.2::5025::SOCKET" --port COM8
    python tools/range_test.py --visa ... --points 5,8,10,20 --n 5 --dry

为什么做这个
------------
`sweep_broad` 的 m (相位拍数) 抖动在各点之间差别极大:

    电流      5 nA   6   7   8   9   10 nA   12.5   15   20   30   40
    m 极差     32     6   0   1   2   **42**     3     0    2    1    0
    (宽扫第2轮的 10 nA = 38, 两轮独立复现)

`m` 是参考换向的拍数 —— **量程若在跳, 它必然跟着抖**。10 nA 恰好是
10 nA 档的满量程, 而官方手册说 "The instrument autoranges at **100% of the
range**"。所以怀疑指向 autorange。

**但有个说不通的地方**: 6/7/8/9 nA 和 5/10 在**同一个源档**上, 它们却很稳
(极差 0~6)。所以"处在 10 nA 档"本身解释不了。要么是别的东西, 要么
autorange 的判据不是我以为的那个。**这个实验就是来分开这两者的。**

做法
----
在**同一个电流**上, 三种模式各测 N 次:

    auto     : autorange 自己挑 (现状)
    lock10   : 强制 10 nA 档   (smua.source.rangei = 10e-9, autorange OFF)
    lock100  : 强制 100 nA 档

判据 (**三条都要看**):
    ① 三种模式下的 **m 极差** —— 若 lock 之后 10 nA 的抖动降到 8 nA 的水平, 量程坐实
    ② 三种模式下的 **s (三次读数散布)** —— 10 nA 现在是 8.5~11 pA, 别处 1~3 pA
    ③ 三种模式下的**均值** —— 若换档带来固定偏移, 均值会台阶式变

⚠️ `smu_set(force_range=...)` 已经实现了强制换档, 但**从没有调用者用过它**,
   所以这是第一次实跑 —— 脚本里显式回切 autorange, 别把状态留在锁档上。

⚠️ 测量本身要够多次才看得出: 默认每种模式 5 次, 单点约 25 秒。
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_set, smu_write, board_open,
                       board_measure, board_warmup, ReadbackSampler,
                       READBACK_N, SETTLE_S)

COLS = ['set_nA', 'mode', 'rep', 'rng_A', 'true_nA', 'dev_nA',
        'm', 'n', 'ext1', 'ext2', 'line']

MODES = [('auto', None, 'autorange 自己挑 (现状)'),
         ('lock10', 10e-9, '强制 10 nA 档'),
         ('lock100', 100e-9, '强制 100 nA 档')]


def set_autorange(smu):
    """回切 autorange —— 强制换档之后必须做, 否则状态会留在锁档上。"""
    smu_write(smu, 'smua.source.autorangei = smua.AUTORANGE_ON')
    smu_write(smu, 'smua.measure.autorangei = smua.AUTORANGE_ON')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--points', default='5,8,10,20',
                    help='要判定的电流 (nA), 逗号分隔。默认含两个"可疑点"(5,10)'
                         '和两个"对照点"(8,20)')
    ap.add_argument('--n', type=int, default=5, help='每种模式测几次')
    ap.add_argument('--settle', type=float, default=SETTLE_S)
    ap.add_argument('--out', default='data/range_test.csv')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    pts = [float(x) for x in a.points.split(',')]
    per = a.settle + a.n * 1.2
    print("=" * 80)
    print("量程效应判定实验")
    print("=" * 80)
    print("电流:  %s nA" % ", ".join("%g" % p for p in pts))
    print("模式:  %s" % " / ".join(m[0] for m in MODES))
    print("次数:  每点每种模式 %d 次 -> 共 %d 次测量, 约 %.0f 分钟"
          % (a.n, len(pts) * len(MODES) * a.n,
             len(pts) * len(MODES) * per / 60.0))
    print("判据:  ① m 极差  ② s 读数散布  ③ 均值是否台阶式变")
    print("")
    if a.dry:
        print("--dry: 不连仪器, 退出")
        return

    ser = board_open(a.port)
    smu = smu_open(a.visa)
    rows = []
    try:
        for i_nA in pts:
            print("── %+g nA" % i_nA)
            for mode, force, desc in MODES:
                if force is None:
                    set_autorange(smu)
                else:
                    smu_set(smu, 0.0)            # 先回到 0, 免得带着上一个电平换档
                    time.sleep(0.5)
                rng = smu_set(smu, i_nA * 1e-9, force_range=force)
                smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
                time.sleep(a.settle)
                board_warmup(ser)
                got = []
                for k in range(1, a.n + 1):
                    smp = ReadbackSampler(smu)
                    smp.start()
                    try:
                        d = board_measure(ser)
                    finally:
                        picked = smp.stop(READBACK_N)
                    if d is None or d.get('timeout'):
                        print("     %-8s 第%d次: 无效, 跳过" % (mode, k))
                        continue
                    rb = sum(picked) / len(picked) if picked else float('nan')
                    got.append((int(d['m']), int(d['n']), rb, float(d['i'])))
                    rows.append(dict(set_nA=i_nA, mode=mode, rep=k,
                                     rng_A='%.6g' % rng, true_nA=rb,
                                     dev_nA=float(d['i']), m=d['m'], n=d['n'],
                                     ext1=d['ext1'], ext2=d['ext2'],
                                     line=d['line']))
                smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
                if got:
                    ms = [g[0] for g in got]
                    dev = [g[3] for g in got]
                    sd = (statistics.stdev(dev) * 1e3 if len(dev) > 1 else 0.0)
                    print("     %-8s 档=%-8.3g  m 极差=%3d  读数散布 s=%6.2f pA"
                          "   均值 %.4f nA   (%s)"
                          % (mode, rng * 1e9, max(ms) - min(ms), sd,
                             sum(dev) / len(dev), desc))
            print("")
    finally:
        try:
            set_autorange(smu)                    # 别把状态留在锁档
            smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
            smu_off(smu)
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass

    import csv
    with open(a.out, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print("已写 %s (%d 行)" % (a.out, len(rows)))
    print("")
    print("  判读: 若 lock10/lock100 下 10 nA 的 **m 极差降到 8 nA 的水平** -> 量程坐实;")
    print("        若三种模式没差别 -> **不是量程**, 得回去找别的机制。")


if __name__ == '__main__':
    main()
