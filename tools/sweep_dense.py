# -*- coding: utf-8 -*-
"""
密集电流网格扫描 —— **每次测量都把整窗原始码值传回并存档**

    python tools/sweep_dense.py --visa "TCPIP0::192.168.0.2::5025::SOCKET" --port COM8
    python tools/sweep_dense.py --visa ... --lo 6 --hi 12 --step 0.25 --n 3   # 只扫关注段
    python tools/sweep_dense.py --visa ... --dry                              # 只估算不测

为什么要这个脚本 (2026-09-16 用户要求)
--------------------------------------
两件事, 缺一不可。

**① 整窗回传。** `cal_sweep.py` / `sweep_broad.py` 只把窗口**首尾两个码**
(`ext1`/`ext2`) 写进 CSV, 中间 6248 点全丢。等要查 δ(I) 那条可复现曲线的
来源时才发现 **8/9 nA 的窗口内数据根本不存在** —— 全仓库只有 09-13 那批手动
采的 `raw_09_13_*.npz` 有整窗缓冲, 而它只覆盖 ±2/5/10/20/30/40。

固件**不用改**: `X` 早就实现了 (main.c:422 → UART_DumpBuffer, 二进制 512 字节
一块 + 头字 + 校验和), 采样本来就写进 `voltage_buf_ext`, 回传只是读出来。

**② 电流网格加密。** δ(I) 的逐点结构在 **~1 nA 尺度上就变号**
(6→7→8→9 nA 是 0.976/0.930/0.793/0.984), 而现有网格是 5 nA (标定) 或
2.5 nA (宽扫) —— **严重欠采样**, 连形状都描不出来。本脚本默认 **0.5 nA**。

代价
----
每个测量点: settle(默认 3s) + 1s 窗口 + 12.5 kB 波形回传 (~0.063s @2Mbps)
≈ 6~8 秒。默认网格 ±5~±45 nA 每 0.5 nA = 162 点 x 3 次 = 486 次
≈ **1 小时**。脚本开跑前会先报估算, 不确认不动手。

⚠️ 只回外部 ADS8866 那一路 (`X`)。内置 ADC (`B`) 实测与它**逐位相同**
   (102 行全等), 回两路只是把机时翻倍。

存档与断点续跑
--------------
每个测量点**立刻**落盘, 不是跑完再写 —— 这是 2026-09-15 的教训:
`cal_sweep.py` 只在最后写 CSV, 中途 Ctrl-C 把整批数据全丢了, 只从日志里
捞回 2 个点 (commit 289c81a)。这里:
    <out>/raw.csv        每行一个测量, 写完立刻 flush
    <out>/w_*.npz        每行对应的整窗波形
重跑时默认**跳过已有 npz 的点**, 所以中断了直接再跑一次就能接着来。

为什么可以砍预热 (`--warmup`)
-----------------------------
预热是 2026-09-13 加的: "一批里**第一次总是坏的**", 五次独立实验都是 1 坏 2~6 好。
当时的判断是空闲期积分器被输入电流推到轨上, 第一次的拉回行程最长、首拍最容易
读到轨码。

**但固件后来把这件事用构造解决了。** 现在 `Current_Start()` 不再直接开窗, 而是
先摆一个 `precond_active = 1` (current.c:373), 由 ISR 逐拍做**拉回相**, 直到
积分器回到两个阈值之间才开窗 (current.c:313-349); 守卫 `PRECOND_TICKS = 2500`
(400 ms), 超时次数记在 `precond_timeout`, 由 `E` 指令的 `PRE=` 字段报出来,
**正常恒为 0**。

也就是说: 窗口"首拍落在轨码"这个前提**已经不可能成立**了 —— 拉回相不到位
就不开窗。那么预热当初要消耗掉的那次坏测量, 已经没有产生条件。

⚠️ 但这是**推论, 不是实测**。所以别直接删:
   `--warmup always`  旧行为 (默认, 保守)
   `--warmup never`   完全不空跑      <- 要验证的就是它
   `--warmup first`   只在整批开头空跑一次
对照办法: 同一段电流分别用 always / never 各扫一遍, 比
   (a) `E` 指令的 `PRE=` 和 `ERR=`
   (b) 每点**第一次**与后几次的残差有没有系统性差别
   (c) 各点 `wave_bad` 计数
三者都没差别, 才能把 never 定为默认。**CSV 里记了 warmup 模式, 两批可分。**
"""
import argparse
import csv
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_set, smu_write, board_open,
                       board_measure, board_read_waveform, board_warmup,
                       board_set_window, ReadbackSampler, READBACK_N, SETTLE_S,
                       BOARD_BAUD, BAUD_FALLBACK)

CSV_COLS = ['set_nA', 'rep', 'true_nA', 'dev_nA', 'dev_ext_nA',
            'm', 'n', 'int1', 'int2', 'ext1', 'ext2', 't_ms', 'mode',
            'wave_min', 'wave_max', 'wave_mean', 'wave_bad',
            'rng_A', 'baud', 'warmup', 't_settle_ms', 't_warm_ms',
            't_meas_ms', 't_xfer_ms', 'npz', 'line']


def grid(lo, hi, step, both=True):
    """lo..hi 步长 step, 可选镜像到负向。"""
    n = int(round((hi - lo) / step))
    pts = [round(lo + i * step, 6) for i in range(n + 1)]
    return pts + [-p for p in reversed(pts)] if both else pts


# 预设点表: (点表字符串 pA, 强制窗口 ms, 阈值 pA)
#
# lowI —— 1 pA ~ 4 nA 的**分级**网格。为什么不用均匀步长:
#   用户提过「统一 50 s + 每 20 pA 一点」, 算下来 **29.7 小时**, 而且
#   **低端只拿到 1 个点** (1 pA 起, 下一个就 21 pA 了), 249 个点全挤在
#   1~5 nA 段 —— 疏密放反了。低端恰恰是最难、09-15 实测出过问题的地方。
#   改成分级: 低端每 2 pA 一点 + 50 s 长窗; 20 pA~1 nA 交回固件自动 10 s;
#   1 nA 以上自动 1 s。**约 51 分钟**, 比 29.7 小时快 35 倍, 而且低端点数
#   从 1 个变 8 个。
PRESETS = {
    'lowI': ('1,2,4,6,8,10,15,20,30,50,100,200,300,500,1000,2000,4000',
             50000.0, 20.0),
}


def safe_name(v):
    return ("%+010.4f" % v).replace('+', 'p').replace('-', 'n').replace('.', '_')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--lo', type=float, default=5.0, help='最小 |I| (nA)')
    ap.add_argument('--hi', type=float, default=45.0, help='最大 |I| (nA)')
    ap.add_argument('--step', type=float, default=0.5, help='电流步长 (nA)')
    ap.add_argument('--pos-only', action='store_true', help='只扫正电流')
    ap.add_argument('--preset', default=None, choices=sorted(PRESETS),
                    help='预设点表, 省得手打。lowI = 1 pA~4 nA 的分级网格'
                         ' (低端每 2 pA + 50 s 长窗, 高端交回自动)。'
                         ' 显式给的 --points/--win-ms/--win-below-pa 会覆盖预设')
    ap.add_argument('--points', default=None,
                    help='显式点表 (**单位 pA**, 逗号分隔), 覆盖 --lo/--hi/--step。'
                         '分级网格必须用它 —— 均匀步长在低电流端会把点数压成 1 个: '
                         '「1 pA 起、每 20 pA 一点」在 1~20 pA 段只拿到起点那一个点, '
                         '而 1~5 nA 段拿到 249 个。')
    ap.add_argument('--win-ms', type=float, default=0.0,
                    help='强制窗口长度 (ms), 配 --win-below-pa 用。'
                         '0 = 不强制, 交回固件自动 (固件: >=1 nA -> 1 s, <1 nA -> 10 s)')
    ap.add_argument('--win-below-pa', type=float, default=0.0,
                    help='|设定| <= 这个值 (pA) 的点用 --win-ms 的窗口')
    ap.add_argument('--warm-ms', type=float, default=0.0,
                    help='预热用这个窗口 (ms); 0 = 与测量窗口相同。'
                         '低电流段测量窗口 50 s, 预热也占 50 s 是纯浪费 —— '
                         '但「预热效果与窗口长度无关」这个假设**还没验证过**')
    ap.add_argument('--n', type=int, default=3, help='每个点测几次')
    ap.add_argument('--settle', type=float, default=SETTLE_S)
    ap.add_argument('--baud', type=int, default=BOARD_BAUD,
                    help='板子串口波特率, 必须与固件 uart.c 的 BOARD_BAUD 一致'
                         ' (默认 %d; 不一致会自动回退到 %d)' % (BOARD_BAUD, BAUD_FALLBACK))
    ap.add_argument('--warmup', choices=['always', 'never', 'first'],
                    default='always',
                    help='预热策略: always=每个电流点前空跑一次(旧行为); '
                         'never=完全不空跑; first=只在整批最开头空跑一次。'
                         ' 见文件末尾「为什么可以砍预热」')
    ap.add_argument('--out', default='data/dense', help='输出目录')
    ap.add_argument('--dry', action='store_true', help='只打印点表和估算, 不连仪器')
    ap.add_argument('--redo', action='store_true',
                    help='重测已有的点 (默认跳过已有 npz 的)')
    a = ap.parse_args()

    if a.preset:
        _p, _w, _b = PRESETS[a.preset]
        if a.points is None:
            a.points = _p
        if a.win_ms == 0.0:
            a.win_ms = _w
        if a.win_below_pa == 0.0:
            a.win_below_pa = _b
        print("** 预设 %s: 点表 %s pA, 强制窗口 %g ms (|I| <= %g pA) **"
              % (a.preset, _p, _w, _b))

    if a.points:
        base = sorted({float(x) / 1000.0 for x in a.points.split(',')})
        pts = base + ([-p for p in reversed(base)] if not a.pos_only else [])
    else:
        pts = grid(a.lo, a.hi, a.step, both=not a.pos_only)
    total = len(pts) * a.n

    def win_of(i_nA):
        """该点要强制多长的窗口 (ms); 0 = 交回固件自动。"""
        if a.win_ms > 0 and abs(i_nA) * 1000.0 <= a.win_below_pa:
            return a.win_ms
        return 0.0

    def wsec(i_nA):
        """该点实际窗口长 (s) —— 强制的按强制算, 其余的按固件自动规则推。"""
        w = win_of(i_nA)
        if w:
            return w / 1000.0
        return 1.0 if abs(i_nA) >= 1.0 else 10.0

    # ⚠️ settle 与预热是**每个电流点一次**, 不是每次测量一次。
    #    第一版把 settle 按"每次测量"算, 估出 51 分钟 —— 实际约一半。
    # ⚠️ 窗口现在是**逐点不同**的 (低电流 50 s), 所以必须逐点累加估算。
    xfer = 6250 * 2 * 10.0 / a.baud          # 整窗回传 (10 bit/字节)
    t_warm_ms = a.warm_ms if a.warm_ms > 0 else None
    est = 0.0
    for p in pts:
        w = wsec(p)
        est += a.settle + a.n * (w + xfer + 0.1)
        if a.warmup == 'always':
            est += (t_warm_ms / 1000.0 if t_warm_ms else w) + 0.1
    if a.warmup == 'first':
        est += (t_warm_ms / 1000.0 if t_warm_ms else wsec(pts[0])) + 0.1
    n_warm = {'always': len(pts), 'first': 1, 'never': 0}[a.warmup]
    t_meas = sum(wsec(p) for p in pts) / len(pts) + xfer + 0.1   # 平均每次

    print("=" * 78)
    print("密集电流网格扫描 —— 每次测量整窗回传")
    print("=" * 78)
    print("网格:   %+g ~ %+g nA, %d 个电流点%s%s"
          % (min(pts), max(pts), len(pts),
             " (只正)" if a.pos_only else " (正负都有)",
             "" if a.points else ", 步长 %g" % a.step))
    if a.points:
        print("        点表: %s" % " ".join("%g" % p for p in
                                            sorted({abs(p) for p in pts})))
    print("每点:   %d 次   ->  共 **%d 次测量**" % (a.n, total))
    print("串口:   %d bps   (整窗 %d 字节回传 %.3f s)"
          % (a.baud, 6250 * 2, xfer))
    if a.win_ms > 0:
        n50 = sum(1 for p in pts if win_of(p) > 0)
        print("窗口:   |I| <= %g pA 的 %d 点强制 %g ms; 其余交回固件自动"
              % (a.win_below_pa, n50, a.win_ms))
        print("        (固件自动: >=1 nA -> 1 s, <1 nA -> 10 s)")
    print("预热:   %s  (共空跑 %d 次%s)"
          % (a.warmup, n_warm,
             ", 窗口 %g ms" % a.warm_ms if a.warm_ms > 0 else ""))
    print("估算:   平均每次测量 %.2f s  ->  约 **%.0f 分钟**"
          % (t_meas, est / 60.0))
    print("  拆开: 整定 %.1f s/点 | 测量 x%d/点 | 预热 x%d/点"
          % (a.settle, a.n, 1 if a.warmup == 'always' else 0))
    print("存档:   %s/raw.csv  (%d 列, 每行写完立刻落盘)" % (a.out, len(CSV_COLS)))
    print("        %s/w_*.npz  (整窗 %d 点)" % (a.out, 6250))
    print("")

    if a.dry:
        print("--dry: 点表如下, 不连仪器")
        print("  " + "  ".join("%+g" % p for p in pts))
        return
    if est > 3600:
        print("⚠️  预计超过 1 小时。确认无误再跑; 中途 Ctrl-C 不会丢数据,")
        print("    重跑本命令会自动跳过已完成的部分。")
        print("")

    os.makedirs(a.out, exist_ok=True)
    csv_path = os.path.join(a.out, 'raw.csv')
    new = not os.path.exists(csv_path)
    f = open(csv_path, 'a', encoding='utf-8', newline='')
    w = csv.DictWriter(f, fieldnames=CSV_COLS)
    if new:
        w.writeheader()
        f.flush()

    ser = board_open(a.port, a.baud)
    smu = smu_open(a.visa)
    done = 0
    warmed = False
    try:
        for i_nA in pts:
            todo = []
            for k in range(1, a.n + 1):
                npz = os.path.join(a.out, 'w_%s_r%d.npz' % (safe_name(i_nA), k))
                if (not a.redo) and os.path.exists(npz):
                    continue
                todo.append((k, npz))
            if not todo:
                print("── %+8.4f nA  已完成, 跳过" % i_nA)
                continue

            print("── %+8.4f nA  (%d/%d)" % (i_nA, done + 1, total))
            wms = win_of(i_nA)
            wtot = wsec(i_nA)
            m_timeout = wtot + 30.0          # 超时必须跟着窗口走, 否则长窗必超时
            if wms:
                echo = board_set_window(ser, wms)
                print("   窗口: %s" % (echo or "** 无回显 **"))
            else:
                board_set_window(ser, 0)     # 复位成自动, 免得沿用上一个点的强制值
            t0 = time.time()
            rng = smu_set(smu, i_nA * 1e-9)
            print("   档位 %.4g A" % rng)
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            time.sleep(a.settle)
            t_settle_ms = int((time.time() - t0) * 1000)

            # 预热: 一次不取波形的空测, 跑完丢掉 (见文件末尾「为什么可以砍预热」)
            t_warm_ms = 0
            if a.warmup == 'always' or (a.warmup == 'first' and not warmed):
                # 预热可以用**另一个 (更短的) 窗口** —— 它只要消耗掉"换电流后
                # 第一次坏"的现象。低电流段测量窗口 50 s, 预热也 50 s 是纯浪费。
                if a.warm_ms > 0:
                    board_set_window(ser, a.warm_ms)
                tw = time.time()
                print("   ", board_warmup(ser))
                t_warm_ms = int((time.time() - tw) * 1000)
                warmed = True
                if a.warm_ms > 0:            # 预热用了短窗 -> 恢复测量窗口
                    board_set_window(ser, wms)

            for k, npz in todo:
                tm = time.time()
                smp = ReadbackSampler(smu)
                smp.start()
                try:
                    d = board_measure(ser, m_timeout)
                finally:
                    picked = smp.stop(READBACK_N)
                t_meas_ms = int((time.time() - tm) * 1000)
                if d is None:
                    print("   第%d次: ** 无响应 ** 跳过" % k)
                    continue
                if d.get('timeout'):
                    print("   第%d次: ** TIMEOUT -> 丢弃补测? (本次不存) **" % k)
                    continue
                # 整窗回传 —— 本脚本存在的理由
                tx = time.time()
                try:
                    wv = board_read_waveform(ser, 'X')
                except (TimeoutError, ValueError) as e:
                    print("   第%d次: ** 波形回传失败: %s ** 端点码仍存, 波形缺"
                          % (k, e))
                    wv = None
                t_xfer_ms = int((time.time() - tx) * 1000)
                # ⚠️ true_nA 是**源表回读的平均**(picked), 不是装置读数 ——
                # 这两者一度被我写成同一个值, 那样校准就退化成自己跟自己比。
                # ⚠️ **单位: 存 nA, 不存 A**。ReadbackSampler 给的是安培
                # (smua.measure.i()), 而本工程所有 CSV 的 true_nA 都是纳安
                # (cal_sweep_raw.csv 的 44.99782727、sweep_broad.csv 的
                # 5.99875875 都是 nA)。列名叫 nA 就得存 nA, 否则差 1e9 ——
                # 这个坑本项目已经踩过多次。
                rb = ((sum(picked) / len(picked)) * 1e9) if picked else float('nan')
                # ⚠️ 存 **uint16**, 不是 board_read_waveform 返回的 int64。
                # 码值本来就在 0~65535, 降位无损; 而 int64 落盘 17651 字节、
                # uint16 只要 12733 —— 白省 28%。zlib 压不掉 int64 那 6 个全零
                # 高位字节, 所以这个差别是实打实的。
                np.savez_compressed(
                    npz, wave_ext=(wv.astype(np.uint16) if wv is not None
                                   else np.zeros(0, dtype=np.uint16)),
                    set_nA=i_nA, rep=k, true_nA=rb, rng_A=rng,
                    m=int(d['m']), n=int(d['n']),
                    int1=int(d['int1']), int2=int(d['int2']),
                    ext1=int(d['ext1']), ext2=int(d['ext2']),
                    line=d['line'])

                row = dict(set_nA=i_nA, rep=k,
                           true_nA=rb, dev_nA=float(d['i']),
                           dev_ext_nA=float(d['x']) if d['x'] else float('nan'),
                           m=d['m'], n=d['n'],
                           int1=d['int1'], int2=d['int2'],
                           ext1=d['ext1'], ext2=d['ext2'],
                           t_ms=d['t'], mode=d['mode'],
                           wave_min=int(wv.min()) if wv is not None and len(wv) else '',
                           wave_max=int(wv.max()) if wv is not None and len(wv) else '',
                           wave_mean=round(float(wv.mean()), 1) if wv is not None and len(wv) else '',
                           wave_bad=int((wv >= 65535).sum() + (wv <= 0).sum())
                           if wv is not None and len(wv) else '',
                           rng_A='%.6g' % rng, baud=getattr(ser, 'baud_actual', a.baud),
                           warmup=a.warmup,
                           t_settle_ms=t_settle_ms, t_warm_ms=t_warm_ms,
                           t_meas_ms=t_meas_ms, t_xfer_ms=t_xfer_ms,
                           npz=os.path.basename(npz), line=d['line'])
                w.writerow(row)
                f.flush()                     # ← 每行立刻落盘
                done += 1
                print("   第%d次  装置 %+9.4f nA  波形 %s  %s   测量 %dms 回传 %dms"
                      % (k, float(d['i']),
                         ("%d 点 [%d,%d]" % (len(wv), wv.min(), wv.max()))
                         if wv is not None and len(wv) else "**缺**",
                         "回读采了 %d 个" % len(picked),
                         t_meas_ms, t_xfer_ms))
            smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    except KeyboardInterrupt:
        print("\n** 中断 —— 已完成的测量都已落盘, 重跑本命令会自动接着来 **")
    finally:
        f.close()
        try:
            smu_off(smu)
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
    print("\n完成 %d 次测量。数据在 %s/" % (done, a.out))


if __name__ == '__main__':
    main()
