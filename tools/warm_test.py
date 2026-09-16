# -*- coding: utf-8 -*-
"""
预热到底要不要 —— 直接测"换电流后的第一次"跟后面几次有没有系统差别

    python tools/warm_test.py --visa "TCPIP0::192.168.0.2::5025::SOCKET" --port COM6
    python tools/warm_test.py --visa ... --points 20,2000 --rounds 3 --n 3

为什么单独有这个脚本
--------------------
低电流段要跑 50 s 窗口, 而**预热也占一个窗口** —— 每个点就是 4 次 50 s 测量。
32 个点里 16 个要 50 s 窗口, 预热若也用 50 s, 整批多花 13 分钟。所以很想让
预热走 1 s 短窗。

**但"预热效果与窗口长度无关"这个假设从没验证过。** 而且有反向的证据:
2026-09-16 的冒烟测试里 +20 nA 那次的预热返回了 TIMEOUT, 而后面 3 次正常 ——
说明"换电流后第一次坏"的现象**确实还在**, 只是样本太小(2 个点里 1 个)。

做法
----
在每个电流上重复 `rounds` 轮, 每轮:
    重新设定电流 -> 整定 -> **不预热** -> 连测 n 次
于是每轮都有一个"第一次"和 n-1 个"后来的"。把所有轮次的这两类分别汇总比较。

判据 (**三条都要看**)
    ① 第一次的残差均值  vs  后来的残差均值   —— 有无系统偏移
    ② 第一次的 TIMEOUT/坏读次数  vs  后来的
    ③ 第一次落在"后来几次散布"之外的比例

三条都没差别 -> 预热可以砍 (或至少可以用短窗); 有差别 -> 保留, 并按差值决定
要不要单独验证"短窗预热够不够"。
"""
import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (board_open, board_measure, board_set_window,
                       ReadbackSampler, READBACK_N,
                       smu_open, smu_set, smu_write, smu_off)

CSV_COLS = ['point_pA', 'round', 'idx', 'is_first', 'set_pA', 'true_nA',
            'dev_nA', 'delta_pA', 'timeout', 't_ms', 'line']


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--points', default='20,2000',
                    help='要测的电流 (pA), 逗号分隔。默认 20 pA (50 s 窗) 与 '
                         '2000 pA (1 s 窗) —— 一个长窗一个短窗, 好比较')
    ap.add_argument('--rounds', type=int, default=3, help='每个电流测几轮')
    ap.add_argument('--n', type=int, default=3, help='每轮连测几次')
    ap.add_argument('--win-ms', type=float, default=0.0,
                    help='强制窗口 (ms); 0 = 交回固件自动')
    ap.add_argument('--win-below-pa', type=float, default=0.0)
    ap.add_argument('--settle', type=float, default=3.0)
    ap.add_argument('--out', default='data/warm_test.csv')
    a = ap.parse_args()

    pts = [float(x) for x in a.points.split(',')]
    tot = sum(a.rounds * a.n * (a.win_ms / 1000.0 if a.win_ms else
                                (1.0 if p >= 1000 else 10.0)) for p in pts)
    print("=" * 76)
    print("预热必要性测试 (每轮: 重新设电流 -> 整定 -> **不预热** -> 连测)")
    print("=" * 76)
    print("电流: %s pA   每点 %d 轮 x %d 次" % (a.points, a.rounds, a.n))
    print("估算: 约 %.0f 分钟 (不含整定)" % (tot / 60.0))
    print("")

    ser = board_open(a.port)
    smu = smu_open(a.visa)
    rows = []
    try:
        for p_pa in pts:
            i_nA = p_pa / 1000.0
            wms = (a.win_ms if (a.win_ms > 0
                                and abs(p_pa) <= a.win_below_pa) else 0.0)
            wtot = (wms / 1000.0) if wms else (1.0 if p_pa >= 1000 else 10.0)
            mto = wtot + 30.0
            print("── %g pA   窗口 %s" % (p_pa, "%g ms (强制)" % wms if wms else "自动"))
            board_set_window(ser, wms)
            for r in range(1, a.rounds + 1):
                # **每轮都重新设一次电流** —— 这就是"换电流"这个条件的复现
                smu_set(smu, 0.0)
                time.sleep(0.3)
                smu_set(smu, i_nA * 1e-9)
                smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
                time.sleep(a.settle)
                line = []
                for k in range(1, a.n + 1):
                    smp = ReadbackSampler(smu)
                    smp.start()
                    try:
                        d = board_measure(ser, mto)
                    finally:
                        picked = smp.stop(READBACK_N)
                    if d is None:
                        line.append("无响应")
                        continue
                    rb = sum(picked) / len(picked) * 1e9 if picked else float('nan')
                    dev = float(d['i'])
                    rows.append(dict(point_pA=p_pa, round=r, idx=k,
                                     is_first=1 if k == 1 else 0, set_pA=p_pa,
                                     true_nA=rb, dev_nA=dev,
                                     delta_pA=(dev - rb) * 1e3,
                                     timeout=1 if d.get('timeout') else 0,
                                     t_ms=d.get('t', ''), line=d['line']))
                    line.append("%+9.4f%s" % (dev, "T" if d.get('timeout') else ""))
                print("   轮%d: %s" % (r, "  ".join(line)))
                smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    except KeyboardInterrupt:
        print("\n** 中断 —— 已完成的都记在内存里, 下面照常出结果 **")
    finally:
        try:
            board_set_window(ser, 0)
            smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
            smu_off(smu)
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass

    # ---------------- 判读 ----------------
    print("")
    print("=" * 76)
    print("判读")
    print("=" * 76)
    ok = [r for r in rows if not r['timeout']
          and math.isfinite(r['delta_pA'])]
    if len(ok) < 6:
        print("  有效数据太少 (%d 条), 不下结论" % len(ok))
    else:
        f = [r['delta_pA'] for r in ok if r['is_first']]
        o = [r['delta_pA'] for r in ok if not r['is_first']]

        def ms(v):
            m = sum(v) / len(v)
            s = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0
            return m, s
        mf, sf = ms(f)
        mo, so = ms(o)
        se = math.sqrt(sf * sf / max(len(f), 1) + so * so / max(len(o), 1))
        t = (mf - mo) / se if se else 0
        print("  ① **残差** (装置 − 回读, pA):")
        print("       第一次 (n=%2d)  均值 %+8.3f   散布 %6.3f" % (len(f), mf, sf))
        print("       后来   (n=%2d)  均值 %+8.3f   散布 %6.3f" % (len(o), mo, so))
        print("       差 = %+.3f ± %.3f pA   (Welch t = %+.2f)  -> %s"
              % (mf - mo, se, t, "**有系统差别**" if abs(t) > 2 else "无系统差别"))
        nf = sum(1 for r in rows if r['is_first'] and r['timeout'])
        no = sum(1 for r in rows if not r['is_first'] and r['timeout'])
        print("  ② **超时/坏读**: 第一次 %d/%d, 后来 %d/%d"
              % (nf, sum(1 for r in rows if r['is_first']), no,
                 sum(1 for r in rows if not r['is_first'])))
        out = sum(1 for x in f if x > max(o) or x < min(o))
        print("  ③ **第一次落在'后来几次散布'之外**: %d/%d" % (out, len(f)))
        print("")
        if abs(t) <= 2 and nf == 0:
            print("  结论: **预热可以砍** —— 三条判据都没有系统差别。")
            print("        下一批低电流扫描可以加 --warmup never (或 --warm-ms 1000)。")
        else:
            print("  结论: **预热要保留** —— 至少有一条判据显示出差别。")
            print("        下一步该测的是: 预热用 1 s 短窗是否和 50 s 长窗等效")
            print("        (把 --win-ms 设成测量窗口, 再单独变预热的窗口)。")

    if rows:
        import csv
        with open(a.out, 'w', encoding='utf-8', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLS)
            w.writeheader()
            w.writerows(rows)
        print("")
        print("已写", a.out, "(%d 行)" % len(rows))


if __name__ == '__main__':
    main()
