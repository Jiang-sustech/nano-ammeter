# -*- coding: utf-8 -*-
"""
标定扫描 (需求 ①+②): 扫源表设定值 -> 采装置读数与源表回读 -> 出拟合用 CSV

需求原文分解
------------
① 2636B 设定从 +45 nA 递减到 -45 nA, 每步 5 nA (共 19 点)。
   每点: 装置测 **3 次**, 取平均作为测量结果;
        同时**在测量周期中**采源表回读 **10 个/次**(3 次共 30 个), 取平均作为
        实际输出值 I_true。
② 用这三个数(加上每次的端点码与计数)去做最小二乘, 算出 I₋ / I₊ / q(=C/G)。
   拟合用现成的 tools/fit_constants.m。

两个平均值为什么要分开
----------------------
装置测的是**积分窗口内的平均电流**, 而源表面板显示的是**某一瞬间**的值 ——
低电流端这两个数不是一回事。所以 I_true 必须在装置正在测的那段时间里采,
而且要采多个求平均。采完取 10 个(逐次等间隔抽样)而不是全要, 是为了让三点
采样在时间上均匀覆盖整段窗口。

端点码用哪一路
--------------
**ADS8866 外部路 (EXT1/EXT2)** —— 与之前那次标定一致(make_fit_csv.py 的默认),
结果可直接对比。要换内置路就改 --path int。

用法
----
    python tools/cal_sweep.py --visa "TCPIP0::169.254.0.1::5025::SOCKET" --port COM8
    python tools/cal_sweep.py --visa ... --points 45,40,...,-45    # 覆盖点表
    python tools/cal_sweep.py --visa ... --dry                     # 只连不扫, 自检
"""
import argparse
import csv
import re
import sys
import threading
import time

import serial

try:
    import pyvisa
except ImportError:
    print("没装 pyvisa:  pip install pyvisa pyvisa-py")
    sys.exit(1)

# 需求① 的点表: +45 递减到 -45, 步长 5
DEFAULT_POINTS = [45 - 5 * i for i in range(19)]      # 45,40,...,0,...,-45

SETTLE_S = 3.0          # 换点后等源稳定
READBACK_N = 10         # 每次测量采几个回读值 (需求①要求 10)
READBACK_DT = 0.1       # 回读轮询间隔

# 结果行的解析 (与 nanoammeter_capture.py 一致)
RESULT_RE = re.compile(
    r"I=(?P<i>[+-][0-9]+\.[0-9]+) nA"
    r"(?: X=(?P<x>[+-][0-9]+\.[0-9]+) nA)?"
    r" MODE=(?P<mode>[0-9])"
    r"(?: T=(?P<t>[0-9]+)ms)?"
    r"(?P<timeout> TIMEOUT)?"
    # CAL= 必须显式吃掉 —— 见 nanoammeter_capture.py 里同一处的长注释:
    # 固件发 "...T=1000ms CAL=+40.235 RAW m=...", 漏了这一段就会匹配成功但
    # RAW 六字段全 None, 整轮标定静默产出空表。
    r"(?: CAL=(?P<cal>[+-]?[0-9]+\.[0-9]+))?"
    r"(?: RAW m=(?P<m>[0-9]+) n=(?P<n>[0-9]+)"
    r" INT1=(?P<int1>[0-9]+) INT2=(?P<int2>[0-9]+)"
    r" EXT1=(?P<ext1>[0-9]+) EXT2=(?P<ext2>[0-9]+))?")


# ---------------------------------------------------------------- 源表 (pyvisa)
def smu_open(res):
    rm = pyvisa.ResourceManager('@py')      # 纯 Python 后端, 不需要 NI-VISA
    smu = rm.open_resource(res)
    smu.read_termination = '\n'
    smu.write_termination = '\n'
    smu.timeout = 5000
    print("    型号:", smu.query('print(localnode.model)').strip())
    smu.write('smua.reset()')
    smu.write('smua.source.func = smua.OUTPUT_DCAMPS')
    smu.write('smua.source.autorangei = smua.AUTORANGE_ON')
    smu.write('smua.source.limitv = 20')     # 输入是虚地, 20V 余量足够
    smu.write('smua.source.output = smu.OUTPUT_OFF')
    return smu


def smu_off(smu):
    try:
        smu.write('smua.source.output = smu.OUTPUT_OFF')
    except Exception:
        pass


# ---------------------------------------------------------------- 装置 (串口)
def board_open(port=None):
    if port is None:
        import serial.tools.list_ports as lp
        c = [p.device for p in lp.comports() if p.vid == 0x1A86 and p.pid == 0x7523]
        if not c:
            print("** 找不到 CH340 **"); sys.exit(1)
        port = c[0]
    s = serial.Serial(port, 115200, timeout=0.2)
    # 本板自动复位电路拿 DTR/RTS 驱动 NRST/BOOT0 —— 驱动默认置位会把 BOOT0 抬高,
    # 之后一复位就进 bootloader。释放掉。(R23 已改为 0Ω, 自动路径现在真的能用)
    try:
        s.dtr = False
        s.rts = False
    except Exception:
        pass
    time.sleep(0.4)
    s.reset_input_buffer()
    s.write(b'E\n')
    t0 = time.time()
    while time.time() - t0 < 4.0:
        if s.readline().decode(errors='replace').startswith('EXT '):
            print("    装置就绪 (%s)" % port)
            return s
    print("** 装置无响应。检查供电; 给板子断上电一次通常能解决 **")
    sys.exit(1)


class ReadbackSampler(threading.Thread):
    """测量期间在后台轮询源表回读值。

    装置发 S 之后串口会被占住 1~11 秒, 主线程读不了别的东西;
    而源表走的是另一条通道(LAN), 所以可以并行采。
    """
    def __init__(self, smu, dt=READBACK_DT):
        super().__init__(daemon=True)
        self.smu = smu
        self.dt = dt
        self.vals = []
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                self.vals.append(float(self.smu.query('print(smua.measure.i())')))
            except Exception:
                pass
            time.sleep(self.dt)

    def stop(self, want=READBACK_N):
        self._stop = True
        # join 超时必须**大于** pyvisa 自身的 timeout (smu_open 里设的 5000ms),
        # 否则线程可能还卡在一次 query 里就被放走 —— 下一轮又 start 一个新线程,
        # 两个线程同时往同一个 raw socket 发查询 (pyvisa-py 的 socket 不可重入)。
        self.join(timeout=8.0)
        if self.is_alive():
            raise RuntimeError("回读采样线程没能在 8s 内退出 —— pyvisa 卡住了, "
                               "后续会与它并发访问同一资源, 已中止")
        if len(self.vals) <= want:
            return list(self.vals)
        # 等间隔抽 want 个 —— 让采样在时间上均匀覆盖整段窗口
        idx = [round(i * (len(self.vals) - 1) / (want - 1)) for i in range(want)]
        return [self.vals[i] for i in idx]


def board_measure(ser, timeout=40.0):
    """发 S 等结果行。返回解析后的 dict (没等到返回 None)。"""
    ser.reset_input_buffer()
    ser.write(b'S\n')
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = ser.readline().decode(errors='replace').strip()
        if line.startswith('I='):
            m = RESULT_RE.search(line)
            if m:
                d = m.groupdict()
                d['line'] = line
                return d
    return None


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', help='2636B 资源串, 如 TCPIP0::169.254.0.1::5025::SOCKET')
    ap.add_argument('--port', help='装置串口 (默认自动找 CH340)')
    ap.add_argument('--points', default=None,
                    help='逗号分隔的设定值, 单位 nA。默认 +45 递减到 -45 步长 5')
    ap.add_argument('--n', type=int, default=3, help='每点测量次数 (需求①=3)')
    ap.add_argument('--path', choices=['ext', 'int'], default='ext',
                    help='用哪一路的端点码: ext=ADS8866(默认, 与上次标定一致), int=内置')
    ap.add_argument('--settle', type=float, default=SETTLE_S)
    ap.add_argument('--dry', action='store_true', help='只连不自检, 不扫描')
    ap.add_argument('-o', '--out', default='data/cal_sweep.csv')
    a = ap.parse_args()

    pts = ([float(x) for x in a.points.split(',')] if a.points else DEFAULT_POINTS)

    print("=" * 70)
    print("标定扫描   %d 点 x %d 次" % (len(pts), a.n))
    print("  端点码: %s" % ("ADS8866 外部 EXT1/EXT2" if a.path == 'ext' else "内置 ADC INT1/INT2"))
    print("=" * 70)

    print("[1] 连装置 ...")
    ser = board_open(a.port)

    if not a.visa:
        print("没给 --visa, 只做装置自检就退出。")
        ser.close()
        return

    print("[2] 连 2636B ...")
    smu = smu_open(a.visa)
    if a.dry:
        print("    --dry: 自检通过, 退出")
        smu_off(smu); ser.close(); return

    rows = []
    try:
        for i_nA in pts:
            print()
            print("── 设定 %+.4f nA " % i_nA + "─" * 46)
            smu.write('smua.source.leveli = %.9e' % (i_nA * 1e-9))
            smu.write('smua.source.output = smu.OUTPUT_ON')
            time.sleep(a.settle)

            dev_vals, rb_all = [], []
            for k in range(a.n):
                smp = ReadbackSampler(smu)
                smp.start()
                try:
                    d = board_measure(ser)
                finally:
                    picked = smp.stop(READBACK_N)   # 无论如何都要把线程停干净

                if d is None:
                    # **这次装置没应答, 那 10 个回读不能计入 I_true** ——
                    # 需求要的是"装置正在测的那段时间里的回读", 出错的这次根本
                    # 没在测。源稳时无所谓, 源在漂时会静默稀释标准值。
                    print("    第%d次: ** 装置无响应/超时 ** (本次回读不计入)" % (k + 1))
                    continue
                rb_all += picked
                iread = float(d['i'])
                dev_vals.append(iread)
                print("    第%d次  装置 %+9.4f nA (MODE=%s T=%sms)   回读采了 %d 个   %s"
                      % (k + 1, iread, d['mode'], d['t'], len(picked),
                         "有RAW" if d['m'] else "**无RAW**"))
                rows.append(dict(point_nA=i_nA, rep=k + 1, mode=int(d['mode']),
                                 t_ms=int(d['t']) if d['t'] else 0,
                                 dev_nA=iread,
                                 dev_ext_nA=float(d['x']) if d['x'] else float('nan'),
                                 m=d['m'], n=d['n'],
                                 int1=d['int1'], int2=d['int2'],
                                 ext1=d['ext1'], ext2=d['ext2'],
                                 line=d['line']))

            if not dev_vals:
                print("    本点没有有效测量, 跳过"); continue

            # 需求① 的两个平均: 装置读数取 3 次均值; 源表取 30 个回读的均值
            dev_avg = sum(dev_vals) / len(dev_vals)
            rb_avg = (sum(rb_all) / len(rb_all)) if rb_all else float('nan')
            print("    ==> 装置平均 %+9.4f nA (%d 次)  源表平均 %+9.5f nA (%d 个回读)  误差 %+.3f%%"
                  % (dev_avg, len(dev_vals), rb_avg * 1e9, len(rb_all),
                     (dev_avg - rb_avg * 1e9) / (rb_avg * 1e9) * 100 if rb_all else float('nan')))
            # 同一点的每一行都记同一个 I_true (需求①: 30 个回读求一个平均)
            for r in rows:
                if r['point_nA'] == i_nA:
                    r['true_nA'] = rb_avg * 1e9
                    r['dev_avg_nA'] = dev_avg
    finally:
        print()
        print("[3] 关源表输出")
        smu_off(smu)
        ser.close()

    # ---- 写两样东西: 原始记录 + fit_constants.m 要的 CSV ----
    if not rows:
        # 全军覆没时 rows 是空的, 再往下走会在 rows[0] 上 IndexError ——
        # 那样看到的是一个莫名其妙的 traceback, 而不是"没有数据"这个事实
        print("\n** 一次有效测量都没有, 不写文件 **")
        return

    import os
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    raw_out = a.out.replace('.csv', '_raw.csv')
    with open(raw_out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    c1k, c2k = ('ext1', 'ext2') if a.path == 'ext' else ('int1', 'int2')
    fit_out = a.out.replace('.csv', '_fit.csv')
    nfit = 0
    with open(fit_out, 'w', newline='', encoding='utf-8') as f:
        f.write('I_true,c1,c2,m,n\n')
        for r in rows:
            if r.get('true_nA') is None:
                continue
            if r[c1k] is None or r[c2k] is None:
                print("  !! 有一行缺 RAW 段(端点码), 跳过:", r['line'][:60])
                continue
            f.write('%.10e,%s,%s,%s,%s\n'
                    % (r['true_nA'] * 1e-9, r[c1k], r[c2k], r['m'], r['n']))
            nfit += 1

    print()
    print("原始记录:", raw_out, "(%d 行)" % len(rows))
    print("拟合输入:", fit_out, "(%d 行, 端点码 = %s)" % (nfit, c1k.upper()))
    print()
    print("下一步 —— 三常数最小二乘 (I₋ / I₊ / q):")
    print("  bash tools/matlab_utf8.sh \"cd('tools'); fit_constants('../%s')\"" % fit_out)
    print("结尾会输出一行可直接粘给固件的 Q 指令。")


if __name__ == '__main__':
    main()
