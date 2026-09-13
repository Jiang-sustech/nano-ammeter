# -*- coding: utf-8 -*-
"""
直流准确度自动扫描：2636B(源) + 装置(被测) 同时驱动, 回读值与装置读数**配对**

为什么需要它
------------
手动记回读值有两个问题:
  1. 面板上显示的是**某一瞬间**的值, 而装置测的是**整个积分窗口内的平均** ——
     这两个数严格说不是一回事, 低电流端尤其明显
  2. 没有时间戳, 事后没法把"装置何时测的"和"源当时输出多少"配对

本脚本在装置测量窗口的**前后各连续采一段回读值**, 分别求平均:
  - 两段一致 -> 源稳定, 用它们的均值作标准值
  - 两段不一致 -> **明确报出漂移**, 这次数据不可用

连接
----
  2636B : 网线 (pyvisa, 走 pyvisa-py 后端, 不需要装 NI-VISA)
          资源串形如 TCPIP0::192.168.1.100::5025::SOCKET
  装置  : CH340 串口, 见 --port

用法
----
  python tools/sweep_dc.py --visa "TCPIP0::192.168.1.100::5025::SOCKET" --port COM7 \
         --points 0.2,0.3,0.5,0.7,0.9 --n 3 -o data/sweep_smallI.csv

  python tools/sweep_dc.py --list                 # 先看看能发现哪些仪器
"""
import argparse
import csv
import sys
import time

import serial

try:
    import pyvisa
except ImportError:
    print("没装 pyvisa:  pip install pyvisa pyvisa-py")
    sys.exit(1)


# ===========================================================================
def list_resources():
    rm = pyvisa.ResourceManager('@py')      # 强制用 pyvisa-py, 不依赖 NI-VISA
    rs = rm.list_resources()
    print("pyvisa 发现的资源:")
    for r in rs:
        print("   ", r)
    if not rs:
        print("   (空) —— 检查 2636B 的 LAN 设置, 以及它和电脑是否在同一网段")
    return rs


def open_smu(res):
    rm = pyvisa.ResourceManager('@py')
    smu = rm.open_resource(res)
    # TSP over raw socket: 行结束符是 \n
    smu.read_termination = '\n'
    smu.write_termination = '\n'
    smu.timeout = 5000
    print("  已连:", smu.query('print(localnode.model)').strip())
    return smu


def smu_setup(smu):
    """直流电流源模式。**只在开始时调用一次。**"""
    smu.write('smua.reset()')
    smu.write('smua.source.func = smua.OUTPUT_DCAMPS')
    smu.write('smua.source.autorangei = smua.AUTORANGE_ON')
    smu.write('smua.source.limitv = 20')          # 顺从电压, 我们输入是虚地, 20V 足够
    smu.write('smua.source.output = smua.OUTPUT_OFF')


def smu_set(smu, i_amp):
    smu.write('smua.source.leveli = %.9e' % i_amp)


def smu_on(smu):
    smu.write('smua.source.output = smua.OUTPUT_ON')


def smu_off(smu):
    smu.write('smua.source.output = smu.OUTPUT_OFF')


def smu_read(smu):
    return float(smu.query('print(smua.measure.i())'))


def smu_average(smu, seconds, interval=0.1):
    """在 `seconds` 秒内尽量多地采回读值, 返回 (均值, 个数, 标准差)。"""
    vals = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            vals.append(smu_read(smu))
        except Exception as e:
            print("     (读源表失败: %s)" % e)
        time.sleep(interval)
    if not vals:
        return float('nan'), 0, float('nan')
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return m, len(vals), var ** 0.5


# ===========================================================================
def open_board(port, baud=115200):
    s = serial.Serial(port, baud, timeout=1.0)
    # 本板自动复位电路拿 DTR/RTS 驱动 NRST/BOOT0, 驱动默认置位 => BOOT0 被抬高
    try:
        s.dtr = False
        s.rts = False
    except Exception:
        pass
    time.sleep(0.4)
    s.reset_input_buffer()
    return s


def board_check(ser):
    ser.reset_input_buffer()
    ser.write(b'E\n')
    t0 = time.time()
    while time.time() - t0 < 4.0:
        line = ser.readline().decode(errors='replace').strip()
        if line.startswith('EXT '):
            return line
    return None


def board_measure(ser, timeout=40.0):
    """发 S, 等结果行。返回 (I 内置, X 外部, MODE, T_ms)。"""
    ser.reset_input_buffer()
    ser.write(b'S\n')
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = ser.readline().decode(errors='replace').strip()
        if line.startswith('I='):
            p = line.split()
            i_int = float(p[0][2:])
            i_ext = float(p[1][2:]) if len(p) > 1 and p[1].startswith('X=') else float('nan')
            mode = int(line.split('MODE=')[1].split()[0]) if 'MODE=' in line else 0
            t_ms = int(line.split('T=')[1].split('ms')[0]) if 'T=' in line else 0
            return i_int, i_ext, mode, t_ms, line
    return float('nan'), float('nan'), 0, 0, "(超时)"


# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true', help='列出 pyvisa 能发现的仪器')
    ap.add_argument('--visa', help='2636B 的资源串')
    ap.add_argument('--port', default='COM7', help='装置的串口')
    ap.add_argument('--points', default='0.2,0.3,0.5,0.7,0.9',
                    help='电流点, 单位 nA, 逗号分隔, 可带负号')
    ap.add_argument('--n', type=int, default=3, help='每点重复次数')
    ap.add_argument('--settle', type=float, default=3.0, help='换点后稳定等待 (s)')
    ap.add_argument('--bracket', type=float, default=2.0,
                    help='测量窗口前后各采多久的回读值 (s)')
    ap.add_argument('--drift-limit', type=float, default=0.5,
                    help='前后两段回读的相对差超过这个 %% 就判本次数据不可用')
    ap.add_argument('-o', '--out', default='sweep_dc.csv')
    args = ap.parse_args()

    if args.list:
        list_resources()
        return
    if not args.visa:
        print("要 --visa <资源串>; 不确定就先跑 --list")
        sys.exit(1)

    pts = [float(x) for x in args.points.split(',')]
    print("=" * 68)
    print("直流准确度扫描")
    print("=" * 68)

    print("[1] 连装置 (%s) ..." % args.port)
    ser = open_board(args.port)
    h = board_check(ser)
    if not h:
        print("    ** 装置无响应。检查串口/供电, 或给板子断上电一次 **")
        sys.exit(1)
    print("   ", h)

    print("[2] 连 2636B ...")
    smu = open_smu(args.visa)
    smu_setup(smu)
    print("   已设为直流电流源, 输出 OFF")

    rows = []
    try:
        for i_nA in pts:
            i_set = i_nA * 1e-9
            print()
            print("── 设定 %+.4f nA ──────────────────────────────" % i_nA)
            smu_set(smu, i_set)
            smu_on(smu)
            time.sleep(args.settle)

            for k in range(args.n):
                # 窗口前
                pre, npre, sdpre = smu_average(smu, args.bracket)
                # 装置测量 (阻塞)
                i_int, i_ext, mode, t_ms, line = board_measure(ser)
                # 窗口后
                post, npost, sdpost = smu_average(smu, args.bracket)

                std = (pre + post) / 2.0
                drift = abs(post - pre) / abs(std) * 100 if std else float('nan')
                usable = drift <= args.drift_limit
                err = (i_int - std) / std * 100 if std else float('nan')

                print("  第%d次  源前 %.5f nA(±%.5f, n=%d)  源后 %.5f nA  "
                      "漂 %.3f%% %s" %
                      (k + 1, pre * 1e9, sdpre * 1e9, npre, post * 1e9, drift,
                       "OK" if usable else "**不可用**"))
                print("          装置 I=%+.4f nA (MODE=%d T=%dms)   "
                      "标准值 %+.5f  误差 %+.3f%%" % (i_int, mode, t_ms, std * 1e9, err))

                rows.append(dict(i_set_nA=i_nA, rep=k + 1, mode=mode, t_ms=t_ms,
                                 pre_nA=pre * 1e9, post_nA=post * 1e9,
                                 std_nA=std * 1e9, drift_pct=drift,
                                 usable=int(usable), board_nA=i_int,
                                 board_ext_nA=i_ext, err_pct=err))
    finally:
        print()
        print("[3] 关输出")
        smu_off(smu)
        ser.close()

    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("   已写", args.out, "(%d 行)" % len(rows))

    # ---- 汇总 ----
    print()
    print("=" * 68)
    print("汇总 (只用 usable 的行)")
    print("=" * 68)
    print("%10s %12s %12s %10s %8s" % ("设定(nA)", "标准值(nA)", "装置(nA)", "误差%", "可用"))
    for p in pts:
        ok = [r for r in rows if r['i_set_nA'] == p and r['usable']]
        tot = [r for r in rows if r['i_set_nA'] == p]
        if not ok:
            print("%10.4f %12s %12s %10s %4d/%d" % (p, "-", "-", "-", 0, len(tot)))
            continue
        std = sum(r['std_nA'] for r in ok) / len(ok)
        brd = sum(r['board_nA'] for r in ok) / len(ok)
        err = (brd - std) / std * 100 if std else float('nan')
        print("%10.4f %12.5f %12.5f %+9.3f%% %4d/%d"
              % (p, std, brd, err, len(ok), len(tot)))


if __name__ == '__main__':
    main()
