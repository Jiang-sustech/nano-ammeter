# -*- coding: utf-8 -*-
"""
nano_ammeter: full UART data return + plot

Sequence (one shot, ~65 s):
    E                -> observation-path health line
    S                -> single measurement, wait for I= result
    B                -> internal ADC12 (control path) window, 6250 pts
    X                -> ADS8866 16-bit (observation path) same window
    E                -> health line again (bad reads must stay put)
    N                -> 50 s bias/noise run
    W                -> the 50 per-second points

Writes raw_<MM_DD>_<value>nA.npz (everything) + matching .png and .log

Usage: python nanoammeter_capture.py [PORT]
       python nanoammeter_capture.py --replot [FILE.npz]
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

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM7"
BAUD = 115200
CODE_ZERO = 30787          # 1.55 V level-shift zero, internal code domain
RAIL_HI = 65520            # measured positive rail of the internal ADC path

# 固件说过的每一行 ASCII, 原样留档 (以前只存三个数组, 结果行/健康行全丢了)
TRANSCRIPT = []
RESULT_RE = re.compile(r"I=([+-][0-9]+\.[0-9]+) nA MODE=(\d)( TIMEOUT)?")


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
    """raw_<MM_DD>_<value>nA.npz —— 模式二超时时数值无意义, 用 TIMEOUT 顶替"""
    day = time.strftime("%m_%d")
    m = RESULT_RE.search(result_line or "")
    if m is None:
        tag = "NO_RESULT"
    elif m.group(3):
        tag = "TIMEOUT"
    else:
        tag = "%snA" % m.group(1)           # 例: +25.274nA
    path = "raw_%s_%s.npz" % (day, tag)
    n = 2
    while os.path.exists(path):             # 同名不覆盖, 加序号
        path = "raw_%s_%s_%d.npz" % (day, tag, n)
        n += 1
    return path


def main():
    ser = serial.Serial(PORT, BAUD, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    out = {}
    result_line = ""

    print("[1] boot banner")
    try:
        wait_line(ser, "NANO-AMMETER", 3.0)
    except TimeoutError:
        print("     (already running, no banner)")

    print("[2] E: observation-path health")
    ser.write(b"E\n")
    wait_line(ser, "EXT ", 5.0)

    print("[3] S: single measurement")
    ser.write(b"S\n")
    result_line = wait_line(ser, "I=", 30.0)

    print("[4] B: internal ADC waveform")
    ser.write(b"B\n")
    out["wave_int"] = read_block(ser, "WAVE")

    print("[5] X: ADS8866 waveform")
    ser.write(b"X\n")
    out["wave_ext"] = read_block(ser, "WAVEX")

    print("[6] E: health after 2x6250 samples of SPI traffic")
    ser.write(b"E\n")
    wait_line(ser, "EXT ", 5.0)

    print("[7] N: 50 s bias run")
    ser.write(b"N\n")
    wait_line(ser, "IBIAS=", 90.0)

    print("[8] W: per-second series")
    ser.write(b"W\n")
    out["noise"] = read_block(ser, "NOISE", timeout=30.0)

    ser.close()

    wi, we, ns = out["wave_int"], out["wave_ext"], out["noise"]
    path = capture_name(result_line)

    m = RESULT_RE.search(result_line or "")
    np.savez(
        path,
        wave_int=wi, wave_ext=we, noise=ns,
        lines=np.array(TRANSCRIPT),            # 固件说过的每一行, 原样留档
        result_line=result_line,
        result_na=(float(m.group(1)) if m else np.nan),
        result_mode=(int(m.group(2)) if m else 0),
        result_timeout=(bool(m.group(3)) if m else False),
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        port=PORT,
    )
    with open(path[:-4] + ".log", "w", encoding="utf-8") as f:   # 人读的纯文本副本
        f.write("# %s  port=%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), PORT))
        f.write("\n".join(TRANSCRIPT) + "\n")

    print("\n>>> wrote %s  (+ .log)" % path)
    plot_only(wi, we, ns, path)


def plot_only(wi, we, ns, path="nanoammeter_data.npz"):
    print("\n=== data summary ===")
    stats("WAVE", wi)
    stats("WAVEX", we)
    # 拉回相验收判据: 窗口首样本应落在上阈值(58962)而不是电平移位饱和码(65520),
    # 且开头不应再有落在饱和区的连续前缀
    lead = 0
    while lead < len(wi) and wi[lead] >= 65000:
        lead += 1
    print("     >>> WAVE[0]=%d  (期望 ~58962 / 旧行为 65520)"
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
    stats("NOISE", ns)

    edges = rising_edges(wi, CODE_ZERO)
    if len(edges) > 1:
        per = np.diff(edges)
        print("     WAVE rising edges=%d  avg period=%.1f ticks (%.2f ms)"
              % (len(edges), per.mean(), per.mean() * 0.160))

    # ---------------- plot ----------------
    fig, ax = plt.subplots(3, 1, figsize=(12, 11))

    ax[0].plot(np.arange(len(wi)), wi, lw=0.6, color="#1f77b4",
               label="internal ADC12 (control path)")
    if len(we) == len(wi):
        ax[0].plot(np.arange(len(we)), we, lw=0.6, color="#ff7f0e", alpha=0.75,
                   label="ADS8866 16-bit (observation path)")
    ax[0].axhline(CODE_ZERO, color="gray", ls=":", lw=0.8)
    ax[0].set_title("Mode-1 charge-balancing window: two acquisition paths, same node")
    ax[0].set_xlabel("tick index (160 us/tick)")
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

    xs = np.arange(len(ns))
    ax[2].plot(xs, ns, "o-", ms=4, lw=1.0, color="#d62728")
    ax[2].axhline(RAIL_HI, color="black", ls="--", lw=0.9,
                  label="positive rail (%d)" % RAIL_HI)
    ax[2].set_title("Bias run: integrator code, 1 point/s (ADG off, pure integration)")
    ax[2].set_xlabel("time (s)")
    ax[2].set_ylabel("raw code")
    ax[2].legend(loc="best", fontsize=8)
    ax[2].grid(alpha=0.3)
    if len(ns) > 0 and (ns.min() >= 0xFF00 or ns.max() <= 0x00FF):
        ax[2].text(0.5, 0.78, "SATURATED - slope is meaningless",
                   transform=ax[2].transAxes, ha="center", fontsize=13,
                   color="red", weight="bold")

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
        plot_only(z["wave_int"], z["wave_ext"], z["noise"], src)
    else:
        main()
