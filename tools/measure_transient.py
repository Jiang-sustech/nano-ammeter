# -*- coding: utf-8 -*-
"""
量 `EN=0` 断开瞬间注入的电荷, 从而定出寄生电容 C_p 与时间常数 tau 的上限。

原理
----
`M0` 的波形 = 「断开瞬间的台阶」+ 「输入电流积出来的直线」。
直线可以从数据里精确拟合, 外推回 t=0 就得到"若没有台阶, 这里应该是多少";
再和**开头几点实测值**比较, 差值就是台阶幅度 A。

    Q = A * q                (总注入电荷)
    C_p = Q / V_n            (V_n = 断开瞬间节点电压 = 参考电压 5V)
    tau = R * C_p

单次测量的噪声有好几百码, 所以单跑一次只能给很松的上限。
**重复 N 次取平均**, 台阶的统计误差降到 sigma/sqrt(N)。

用法
----
    python tools/measure_transient.py COM8            # 默认 20 次
    python tools/measure_transient.py COM8 50
"""
import struct
import sys
import time

import numpy as np
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM8"
N_RUN = int(sys.argv[2]) if len(sys.argv) > 2 else 20

Q_PER_CODE = 1.5546e-14     # C/码
V_NODE     = 5.0            # 断开瞬间节点电压 (V)
R_OHM      = 100.0e6        # 限流电阻
N_PTS      = 6250
CODE_ZERO  = 30782          # 拉回相目标 = 断开前的积分器电平 (码)


def read_block(ser, tag):
    """读波形块: 头行 "<tag> <n>" + n*2 字节小端 u16 + 2 字节校验和"""
    while True:
        line = ser.readline().decode(errors="replace").strip()
        if line.startswith(tag + " "):
            n = int(line.split()[1])
            break
        if not line:
            raise TimeoutError("没等到 %s 头" % tag)
    raw = ser.read(n * 2 + 2)
    vals = struct.unpack("<%dH" % n, raw[:n * 2])
    return np.array(vals, dtype=float), raw[-2:] == struct.pack("<H", sum(vals) & 0xFFFF)


def main():
    ser = serial.Serial(PORT, 115200, timeout=6.0)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    time.sleep(0.4)

    steps, taus, slopes, noises, direct = [], [], [], [], []
    print("跑 %d 次 M0 ..." % N_RUN)
    for k in range(N_RUN):
        ser.reset_input_buffer()
        ser.write(b"M0\n")
        line = ""
        t0 = time.time()
        while time.time() - t0 < 8.0:
            line = ser.readline().decode(errors="replace").strip()
            if line.startswith("MAN DONE"):
                break
        if not line.startswith("MAN DONE"):
            print("  第 %d 次没等到 MAN DONE: %r" % (k + 1, line))
            continue
        # tick_ns 从回显里取, 不靠假设
        tn = float(line.split("tick_ns=")[1].split()[0]) * 1e-9

        ser.write(b"B\n")
        w, ck = read_block(ser, "WAVE")

        t = np.arange(len(w)) * tn
        sl = slice(400, 6000)
        a, b = np.polyfit(t[sl], w[sl], 1)      # 直线: 斜率 a, 截距 b
        resid = w[sl] - (a * t[sl] + b)

        head = w[:6].mean()
        steps.append(head - b)
        # 直接估法: 断开前的电平 = 拉回相的目标 CODE_ZERO, 所以不必外推。
        # 这个数不经过拟合, 不受"斜率-截距补偿"影响。
        direct.append(head - CODE_ZERO)
        taus.append(tn)                          # 仅占位, 下面统一算
        slopes.append(a)
        noises.append(resid.std())
        print("  [%2d/%d] 台阶 = %+7.1f 码   斜率 %+.0f 码/秒   直线残差 %.0f 码"
              % (k + 1, N_RUN, steps[-1], a, resid.std()))

    ser.close()

    if not steps:
        print("一次都没成功"); return

    s = np.array(steps)
    mean, sem = s.mean(), s.std(ddof=1) / np.sqrt(len(s))
    noise = float(np.mean(noises))
    tn = float(np.mean(taus))

    print()
    print("=" * 62)
    print("结果 (%d 次平均)" % len(s))
    print("=" * 62)
    dr = np.array(direct)
    dmean, dsem = dr.mean(), dr.std(ddof=1) / np.sqrt(len(dr))
    print("  ── 两种估法 ──")
    print("  ① 外推法  A = %+.1f ± %.1f 码   <- 斜率和截距互相补偿, 不稳" % (mean, sem))
    print("  ② 直接法  A = %+.1f ± %.1f 码   <- 用已知的断开前电平 %d 码做基准, 不依赖拟合"
          % (dmean, dsem, CODE_ZERO))
    print("     两次估法之差 = %+.0f 码  (若两者一致说明外推可信)" % (dmean - mean))
    print()
    print("  台阶幅度 A = %+.1f ± %.1f 码  (单次噪声 %.0f 码)" % (mean, sem, noise))
    print("  折算注入电荷 Q = %+.2f ± %.2f pC"
          % (mean * Q_PER_CODE * 1e12, sem * Q_PER_CODE * 1e12))
    print()
    if abs(mean) < 2 * sem:
        sig = "与 0 无法区分"
    else:
        sig = "**显著非零**"
    print("  A 是否显著: %s  (|A| = %.1f x 标准误)" % (sig, abs(mean) / sem if sem > 0 else 0))

    print()
    print("  ── 上限 (取 |A| + 2*SEM) ──")
    ub = abs(mean) + 2 * sem
    cp = ub * Q_PER_CODE / V_NODE
    tau = R_OHM * cp
    print("    C_p  < %.2f pF" % (cp * 1e12))
    print("    tau  < %.1f us" % (tau * 1e6))
    print("    注入电荷 < %.1f pC  = %.0f 码" % (ub * Q_PER_CODE * 1e12, ub))
    print()
    print("  ── 对小电流模式 SI_SETTLE_TICKS 的判据 ──")
    print("    现有等待 = 8 x %.1f us = %.0f us" % (tn * 1e6, 8 * tn * 1e6))
    print("    需要 > 5*tau = %.1f us  ->  %s"
          % (5 * tau * 1e6, "够" if 5 * tau < 8 * tn else "**不够, 要加大**"))


if __name__ == "__main__":
    main()
