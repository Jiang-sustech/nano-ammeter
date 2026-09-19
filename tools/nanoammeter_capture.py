# -*- coding: utf-8 -*-
"""
nano_ammeter: full UART data return + plot

Sequence (one shot):
    E                -> observation-path health line
    S                -> single measurement, wait for I= result
    B                -> internal ADC12 (control path) window, 6250 pts
    X                -> ADS8866 16-bit (observation path) same window
    E                -> health line again (bad reads must stay put)
    N                -> 50 s bias/noise run           (--no-noise 时跳过)
    W                -> the 50 per-second points      (--no-noise 时跳过)

Writes raw_<MM_DD>_<value>nA.npz (everything) + matching .png and .log

Usage: python nanoammeter_capture.py [PORT] [--no-noise] [--true=<nA>]
       python nanoammeter_capture.py --replot [FILE.npz]

--true=<nA>: 记下**本次输入电流的已知真值**(单位 nA), 存进 npz 的 true_na。
        做物理常数拟合时, 这些真值就是最小二乘的因变量 —— 不记的话事后得靠
        文件名去对, 很容易错位。注意必须写成等号形式: `--true=-40` (写成
        `--true -40` 的话那个 -40 会被位置解析当成端口名)。

--no-noise: 跳过 N/W 两步。physics_exp_test 已删除底噪指令 (见该工程
README「两个工程的分工」), 对着它跑不加这个开关会在第 7 步干等 90 秒后抛
TimeoutError —— 而 np.savez 在那之后, 结果是一个文件都写不出来。
"""
import glob
import os
import re
import sys
import time

import numpy as np
import serial

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 波特率必须与固件 uart.c 的 BOARD_BAUD 一致, 否则本脚本对板子完全没反应 ——
# 而症状跟"板子没插好"一模一样, 很容易误判。共享库 (cal_sweep.board_open)
# 会在 115200/921600 之间自动试, 但本脚本在**模块级解析 sys.argv**, 不方便
# import, 所以这里写死。
#   2026-09-16 实测: 115200 全清; 921600 有 11% 要重传; 2 Mbps 不可用。
#   裁定保持 115200。详见 uart.c 的 BOARD_BAUD 注释。
BAUD = 115200
CODE_ZERO = 30787          # 1.55 V level-shift zero, internal code domain
RAIL_HI = 65520            # measured positive rail of the internal ADC path


def _opt_float(name):
    """取 `--name=<值>`; 没给返回 None。

    开关一律用**等号形式**。写成 `--true -40` 的话, 那个 -40 会被位置参数
    解析当成端口名 —— 负数开头的值必须黏在等号右边。"""
    for a in sys.argv[1:]:
        if a.startswith(name + "="):
            return float(a[len(name) + 1:])
    return None


# 位置参数只有一个 (端口), 其余是开关 —— 开关不能顶到 PORT 的位置上
DO_NOISE = "--no-noise" not in sys.argv
TRUE_NA = _opt_float("--true")      # 本次输入电流的**已知真值** (nA), 拟合用
MANUAL  = _opt_float("--manual")    # M 指令子模式 (0=断开量tau / 1=+5V / 2=-5V)
_rest = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = _rest[0] if _rest else "COM7"

# 固件说过的每一行 ASCII, 原样留档 (以前只存三个数组, 结果行/健康行全丢了)
TRANSCRIPT = []

# 两代结果行的并集。nano_ammeter:  "I=+25.274 nA MODE=1"
#                      physics_exp_test: "I=<内置> X=<外部> MODE=1 T=1000ms [CAL=..]
#                                         [RAW m=.. n=.. INT1=.. INT2=.. EXT1=.. EXT2=..]"
# X= / T= / RAW 都是可选 —— 用 `nA MODE=` 直接匹配会在第二行格式上落空 (中间隔着
# X=), 于是 capture_name 退化成 NO_RESULT、result_na 变 NaN, 所以必须留出那一段。
#
# RAW 段是模式一的原始量 (POS/NEG 周期数 + 窗口首尾两路原始码), 只有
# physics_exp_test 的 MODE=1 会出。存进 npz 是为了让 (m+n) 与 (m+n-1) 两种
# 分母之争、以及分段系数 a/b 的拟合能直接从**同一次测量**的原始量出发。
RESULT_RE = re.compile(
    r"I=(?P<i>[+-][0-9]+\.[0-9]+) nA"
    r"(?: X=(?P<x>[+-][0-9]+\.[0-9]+) nA)?"
    r" MODE=(?P<mode>[0-9])"
    r"(?: T=(?P<t>[0-9]+)ms)?"
    r"(?P<timeout> TIMEOUT)?"
    # CAL= 必须显式吃掉: 固件发的是 "...T=1000ms CAL=+40.235 RAW m=..." ——
    # 而 TIMEOUT/RAW 那两组都是可选的, 中间插一个 CAL= 会让正则在它前面就收尾,
    # 结果是"匹配成功但六个 RAW 字段全是 None"。CAL= 一旦写过 Q/K 就恒存在
    # (Z 清除也会把 a=1,b=0 存回 Flash 并保留 magic), 所以这不是边界而是常态。
    r"(?: CAL=(?P<cal>[+-]?[0-9]+\.[0-9]+))?"
    r"(?: RAW m=(?P<m>[0-9]+) n=(?P<n>[0-9]+)"
    r" INT1=(?P<int1>[0-9]+) INT2=(?P<int2>[0-9]+)"
    r" EXT1=(?P<ext1>[0-9]+) EXT2=(?P<ext2>[0-9]+))?")


def read_exact(ser, n, timeout):
    buf = bytearray()
    t0 = time.time()
    while len(buf) < n:
        c = ser.read(n - len(buf))
        if c:
            buf += c
            t0 = time.time()
        elif time.time() - t0 > timeout:
            raise TimeoutError("expected %d bytes, got %d" % (n, len(buf)))
    return bytes(buf)


def read_line(ser, timeout, echo=True):
    """One CRLF-terminated ASCII line, or '' if nothing arrived in time."""
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < timeout:
        b = ser.read(1)
        if not b:
            continue
        if b == b"\n":
            s = buf.decode("ascii", "replace").strip()
            if s:
                TRANSCRIPT.append(s)
                if echo:
                    print("  <- %s" % s)
            return s
        if b != b"\r":
            buf += b
    return ""


def wait_line(ser, prefix, timeout, echo=True):
    t0 = time.time()
    while time.time() - t0 < timeout:
        left = timeout - (time.time() - t0)
        s = read_line(ser, min(left, 20.0), echo)
        if s.startswith(prefix):
            return s
    raise TimeoutError("no line starting with %r within %.0fs" % (prefix, timeout))


def read_block(ser, tag, timeout=180.0):
    """'<tag> <count>\\r\\n' + count*2 bytes LE u16 + u16 checksum."""
    t0 = time.time()
    n = None
    while time.time() - t0 < timeout:
        left = timeout - (time.time() - t0)
        s = read_line(ser, min(left, 20.0))
        if s.startswith(tag + " "):
            n = int(s.split()[1])
            break
    if n is None:
        raise TimeoutError("no %s header within %.0fs" % (tag, timeout))

    data = read_exact(ser, n * 2 + 2, 60.0)
    vals = np.frombuffer(data[: n * 2], dtype="<u2").astype(np.int64)
    rx_chk = data[n * 2] | (data[n * 2 + 1] << 8)
    calc_chk = int(vals.sum() & 0xFFFF)
    ok = "OK" if rx_chk == calc_chk else "MISMATCH"
    print("     %s: %d pts, checksum %s (0x%04X vs 0x%04X)"
          % (tag, n, ok, calc_chk, rx_chk))
    if rx_chk != calc_chk:
        raise ValueError("%s checksum mismatch -> link dropped bytes" % tag)
    return vals


def stats(name, v):
    print("     %-6s min=%-6d max=%-6d mean=%8.1f  span=%d"
          % (name, v.min(), v.max(), v.mean(), v.max() - v.min()))


def rising_edges(v, thresh, hyst=2000):
    """Count low->high crossings of thresh with hysteresis."""
    state = 0
    idx = []
    for i, x in enumerate(v):
        if state == 0 and x > thresh + hyst:
            state = 1
            idx.append(i)
        elif state == 1 and x < thresh - hyst:
            state = 0
    return np.array(idx)


def capture_name(result_line):
    """raw_<MM_DD>_<value>nA.npz —— 超时时数值无意义, 用 TIMEOUT 顶替"""
    day = time.strftime("%m_%d")
    m = RESULT_RE.search(result_line or "")
    if m is None:
        tag = "NO_RESULT"
    elif m.group("timeout"):
        tag = "TIMEOUT"
    else:
        tag = "%snA" % m.group("i")         # 例: +25.274nA
    path = "raw_%s_%s.npz" % (day, tag)
    n = 2
    while os.path.exists(path):             # 同名不覆盖, 加序号
        path = "raw_%s_%s_%d.npz" % (day, tag, n)
        n += 1
    return path


def raw_fields(m):
    """模式一的原始量 -> npz 字段, 缺失一律记 -1。

    m/n       = POS / NEG 注入的周期数 (注意式(6)实际用的是 m 与 n-1)
    int1/int2 = 内置 ADC 那路的窗口首尾原始码
    ext1/ext2 = ADS8866 那路的窗口首尾原始码 (表征以它为准)

    老固件没有这一段; 实验固件也只有 MODE=1 (电荷平衡窗口) 会出 —— 小电流
    模式不算 m/n, 用的是起点/终点两段平均, 原始量是另一套。"""
    out = {}
    for k in ("m", "n", "int1", "int2", "ext1", "ext2"):
        v = m.group(k) if m is not None else None
        out["raw_" + k] = int(v) if v is not None else -1
    return out


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    # ---- 释放 DTR/RTS (重要) ----
    # 本板的自动复位电路 (2026-09-13 网表) 拿这两条线驱动 NRST / BOOT0:
    #   RTS# 经 Q2 把 BOOT0 抬到 3V3; DTR#/RTS# 交叉翻转产生 NRST 脉冲。
    # 而串口驱动**默认会置位这两条线** —— 那意味着打开串口就把 BOOT0 拉高,
    # 此后任何复位都会让芯片进 ROM bootloader 而不是跑 app (实测踩过)。
    # 这里主动释放, 让 BOOT0 回到 R16 的下拉。
    # 注意: pyserial 的 True/False 与 CH340 引脚有效电平的对应随驱动而异,
    # 所以**这一步是尽力而为**; 真正的保险是下面 [2] 的 E 指令应答检查。
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    time.sleep(0.3)
    ser.reset_input_buffer()

    out = {}
    result_line = ""
    MANUAL_LINE = ""

    print("[1] boot banner")
    try:
        wait_line(ser, "NANO-AMMETER", 3.0)
    except TimeoutError:
        print("     (already running, no banner)")

    print("[2] E: observation-path health")
    ser.write(b"E\n")
    wait_line(ser, "EXT ", 5.0)

    if MANUAL is not None:
        # 手动模式 (M 指令): 阻塞连采 6250 点, 不走 TIM6 网格。
        # M0 = 断开参考量 tau; M1/M2 = 固定极性给 q 做开环标定。
        sub = int(MANUAL)
        print("[3] M%d: 手动连采 (阻塞约 130ms)" % sub)
        ser.write(("M%d\n" % sub).encode())
        # ⚠️ 等待上限必须覆盖固件最长窗口。physics_exp_test 的自动路径在
        #    |I| < 1 nA 时先跑 1 s 判据窗再跑长窗, 一次最坏 = 1 + 长窗秒。
        #    长窗 2026-09-17 由 10 s 改成 50 s (current.h), 所以这里 30 -> 60。
        MANUAL_LINE = wait_line(ser, "MAN DONE", 60.0)
        print("    <- %s" % MANUAL_LINE)
        result_line = MANUAL_LINE
    else:
        print("[3] S: single measurement")
        ser.write(b"S\n")
        result_line = wait_line(ser, "I=", 60.0)

    print("[4] B: internal ADC waveform")
    ser.write(b"B\n")
    out["wave_int"] = read_block(ser, "WAVE")

    print("[5] X: ADS8866 waveform")
    ser.write(b"X\n")
    out["wave_ext"] = read_block(ser, "WAVEX")

    print("[6] E: health after 2x6250 samples of SPI traffic")
    ser.write(b"E\n")
    wait_line(ser, "EXT ", 5.0)

    if DO_NOISE:
        print("[7] N: 50 s bias run")
        ser.write(b"N\n")
        wait_line(ser, "IBIAS=", 90.0)

        print("[8] W: per-second series")
        ser.write(b"W\n")
        out["noise"] = read_block(ser, "NOISE", timeout=30.0)
    else:
        print("[7/8] 跳过底噪 (--no-noise)")
        out["noise"] = np.zeros(0, dtype=np.int64)

    ser.close()

    wi, we, ns = out["wave_int"], out["wave_ext"], out["noise"]
    if MANUAL is not None:
        # 手动模式没有 "I=" 结果行, capture_name() 会退化成 NO_RESULT —— 显式命名
        path = "raw_%s_M%d.npz" % (time.strftime("%m_%d"), int(MANUAL))
        _t = re.search(r"tick_ns=(\d+)", MANUAL_LINE or "")
        manual_tick_ns = int(_t.group(1)) if _t else 0
    else:
        path = capture_name(result_line)
        manual_tick_ns = 0

    m = RESULT_RE.search(result_line or "")
    np.savez(
        path,
        wave_int=wi, wave_ext=we, noise=ns,
        manual_sub=(int(MANUAL) if MANUAL is not None else -1),
        manual_tick_ns=manual_tick_ns,
        lines=np.array(TRANSCRIPT),            # 固件说过的每一行, 原样留档
        result_line=result_line,
        result_na=(float(m.group("i")) if m else np.nan),
        result_ext_na=(float(m.group("x")) if (m and m.group("x")) else np.nan),
        result_mode=(int(m.group("mode")) if m else 0),
        result_time_ms=(int(m.group("t")) if (m and m.group("t")) else 0),
        result_timeout=(bool(m.group("timeout")) if m else False),
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        port=PORT,
        true_na=(TRUE_NA if TRUE_NA is not None else np.nan),
        **raw_fields(m),
    )

    if m is not None and m.group("m") is not None:
        print("\n[raw] m=%s n=%s  INT %s->%s  EXT %s->%s"
              % (m.group("m"), m.group("n"), m.group("int1"), m.group("int2"),
                 m.group("ext1"), m.group("ext2")))
        print("      (式(6) 的分母就是 m+n: 窗口两端都是 ISR 采样, 计数与间隔一一对应)")
    with open(path[:-4] + ".log", "w", encoding="utf-8") as f:   # 人读的纯文本副本
        f.write("# %s  port=%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), PORT))
        f.write("\n".join(TRANSCRIPT) + "\n")

    print("\n>>> wrote %s  (+ .log)" % path)
    plot_only(wi, we, ns, path, dt_us_of(result_line, len(wi)))


def dt_us_of(result_line, npts):
    """每点代表多少微秒 —— 由结果行的 T= 除以点数反推。

    不能硬编码 160: MODE=1 (1s 窗口) 是 160us/点, 而 MODE=2 (10s 长窗口) 的
    缓冲是**抽点**存的 —— 6250 点覆盖 62500 拍, 每点 1600us。硬编码会让锯齿
    周期和整条横轴差 10 倍, 而那个数是当测量结果打印出来的。
    结果行的 T= 报的是窗口实际拍数的时长, 所以 T/npts 就是每点时长。
    """
    m = RESULT_RE.search(result_line or "")
    if (not m) or (not m.group("t")) or (npts <= 0):
        return 160.0                    # 老格式没有 T=, 只能按名义值
    return float(m.group("t")) * 1000.0 / npts


def plot_only(wi, we, ns, path="nanoammeter_data.npz", dt_us=160.0):
    """dt_us = 每**点**代表多少微秒。

    **不能硬编码 160**: 10 s 长窗口 (MODE=2) 下缓冲是抽点存的 —— 6250 点覆盖
    62500 拍, 每点是 1600us 而不是 160us。硬编码会让锯齿周期和整条横轴差 10 倍,
    而且那个数是当测量结果打印出来的。由调用方按 result_time_ms/点数 反推。"""
    print("\n=== data summary ===")
    stats("WAVE", wi)
    stats("WAVEX", we)
    # 拉回相验收判据: 窗口首样本应落在上阈值(58962)而不是电平移位饱和码(65520),
    # 且开头不应再有落在饱和区的连续前缀
    lead = 0
    while lead < len(wi) and wi[lead] >= 65000:
        lead += 1
    print("     >>> WAVE[0]=%d  (期望 ~30782 = code_zero; 旧行为 65520 压轨)"
          "  leading saturated prefix=%d (期望 0)" % (wi[0], lead))
    n = min(len(wi), len(we))
    a, e = wi[:n], we[:n]
    # ADS8866 坏读 (0x0000 转换未完成 / 0xFFFF DOUT 常高) 是链路故障样本,
    # 不是测量值。留着算差分/相关只会把统计量整个拖坏 (一个 0x0000 就是
    # -65520 的假差异) -> 单独计数后剔除, 只看有效样本间的真实一致性
    bad = (e == 0x0000) | (e == 0xFFFF)
    print("     WAVEX bad reads: %d of %d (%.2f%%)"
          % (bad.sum(), n, 100.0 * bad.sum() / n))
    av, ev = a[~bad], e[~bad]
    d = ev - av
    print("     ext-int (valid %d pts): mean=%+.2f  rms=%.2f  max|d|=%d"
          % (len(d), d.mean(), np.sqrt((d.astype(float) ** 2).mean()),
             np.abs(d).max()))
    if len(d) > 1:
        r = np.corrcoef(av, ev)[0, 1]
        k, b = np.polyfit(av, ev, 1)
        print("     correlation r=%.6f   fit ext=%.5f*int %+.2f" % (r, k, b))
    has_noise = ns is not None and len(ns) > 0
    if has_noise:
        stats("NOISE", ns)
    else:
        print("     NOISE  (--no-noise, 本次未采)")

    edges = rising_edges(wi, CODE_ZERO)
    if len(edges) > 1:
        per = np.diff(edges)
        print("     WAVE rising edges=%d  avg period=%.1f pts (%.2f ms)"
              % (len(edges), per.mean(), per.mean() * dt_us / 1000.0))

    # ---------------- plot ----------------
    fig, ax = plt.subplots(3 if has_noise else 2, 1,
                           figsize=(12, 11 if has_noise else 7.5))

    ax[0].plot(np.arange(len(wi)), wi, lw=0.6, color="#1f77b4",
               label="internal ADC12 (control path)")
    if len(we) == len(wi):
        ax[0].plot(np.arange(len(we)), we, lw=0.6, color="#ff7f0e", alpha=0.75,
                   label="ADS8866 16-bit (observation path)")
    ax[0].axhline(CODE_ZERO, color="gray", ls=":", lw=0.8)
    ax[0].set_title("Mode-1 charge-balancing window: two acquisition paths, same node")
    ax[0].set_xlabel("sample index (%.2f us/sample%s)"
                     % (dt_us, ", MODE=2 长窗口抽点" if dt_us > 200 else ""))
    ax[0].set_ylabel("raw code (16-bit domain)")
    ax[0].legend(loc="upper right", fontsize=8)
    ax[0].grid(alpha=0.3)

    ax[1].plot(np.where(~bad)[0], d, lw=0.6, color="#2ca02c")
    ax[1].axhline(0, color="gray", ls=":", lw=0.8)
    ax[1].set_title("ADS8866 - internal ADC, point by point "
                    "(%d bad reads dropped)" % int(bad.sum()))
    ax[1].set_xlabel("tick index")
    ax[1].set_ylabel("code difference")
    ax[1].grid(alpha=0.3)

    if has_noise:
        xs = np.arange(len(ns))
        ax[2].plot(xs, ns, "o-", ms=4, lw=1.0, color="#d62728")
        ax[2].axhline(RAIL_HI, color="black", ls="--", lw=0.9,
                      label="positive rail (%d)" % RAIL_HI)
        ax[2].set_title("Bias run: integrator code, 1 point/s (ADG off, pure integration)")
        ax[2].set_xlabel("time (s)")
        ax[2].set_ylabel("raw code")
        ax[2].legend(loc="best", fontsize=8)
        ax[2].grid(alpha=0.3)
        if ns.min() >= 0xFF00 or ns.max() <= 0x00FF:
            ax[2].text(0.5, 0.78, "SATURATED - slope is meaningless",
                       transform=ax[2].transAxes, ha="center", fontsize=13,
                       color="red", weight="bold")

    # 文件名带着被测电流值, 直接当标题 —— 三张图并排看时不用猜哪张是哪张
    fig.suptitle(os.path.basename(path)[:-4], fontsize=12, weight="bold")
    fig.tight_layout()
    fig.savefig(path[:-4] + ".png", dpi=130)
    print("\nwrote %s + %s" % (path[:-4] + ".png", path))


if __name__ == "__main__":
    if "--replot" in sys.argv:
        # 可给文件名; 不给就取目录里最新的一份 raw_*.npz
        rest = [a for a in sys.argv[1:] if not a.startswith("--")]
        if rest:
            src = rest[0]
        else:
            cand = sorted(glob.glob("raw_*.npz"), key=os.path.getmtime)
            if not cand:
                sys.exit("no raw_*.npz here, and no file given")
            src = cand[-1]
        print("replot: %s" % src)
        z = np.load(src)
        _rl = str(z["result_line"]) if "result_line" in z else ""
        plot_only(z["wave_int"], z["wave_ext"], z["noise"], src,
                  dt_us_of(_rl, len(z["wave_int"])))
    else:
        main()
