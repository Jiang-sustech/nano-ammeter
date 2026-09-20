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

# 板子串口波特率 —— **115200, 提不上去** (2026-09-16 实测裁定)
#   **必须与固件 firmware-v1/Core/Src/uart.c 的 BOARD_BAUD 一致**。
#
#   实测 (整窗 12500 字节回传, 每档至少 16 次):
#       115200  1.085 s  **全清**
#       921600  0.147 s  89% 一次成功, 11% 需重传; 重传后 0 丢失
#       2000000 0.073 s  1/16 成功, 13/16 连 4 次重传全失败 -> 不可用
#   失败率随波特率单调上升 -> 是 CH340G 的带宽问题, 与接地无关
#   (中途怀疑地线松, 接好后 2 Mbps 重测反而更差, 已排除)。
#   完整记录见 uart.c 的 BOARD_BAUD 注释。
#
#   BAUD_FALLBACK 用来兼容烧了别的波特率的板子 —— board_open() 会自动试。
BOARD_BAUD = 115200
BAUD_FALLBACK = 921600

SETTLE_S = 3.0          # 换点后等源稳定
READBACK_N = 10         # 每次测量采几个回读值 (需求①要求 10)
READBACK_DT = 0.1       # 回读轮询间隔

# 单次 S 的等待上限。**必须覆盖固件的最长窗口** —— firmware-v1 的自动路径
# 在 |I| < 1 nA 时会先跑 1 s 判据窗、再跑长窗, 所以一次 S 最坏 = 1 + 长窗。
#   2026-09-17: 长窗由 10 s 改成 50 s (current.h 的 CURRENT_WIN_LONG_TICKS),
#               这个值跟着从 40 提到 60。
# ⚠️ 跟不上窗口的症状是**假故障**: 2026-09-16 撞过一次 —— board_warmup 默认 40 s
#    而窗口 50 s, 于是每次预热都"无响应", 等于没预热, 还白等 40 秒/点。
#    改固件那个常数时, 这里和下面 board_measure/board_warmup 的默认值要一起看。
MEASURE_TIMEOUT_S = 60.0

# 测量精度 (2026-09-15 用户要求"调到最高")
# nplc: 积分多少个工频周期。实测范围 0.01~25 (100 被拒 "Parameter data out of range")。
#   **1 已经能完全抑制工频** —— 整周期积分把 50Hz 及其谐波都积掉了; 再往上
#   主要压白噪声。而本工程是在**装置的 1s 窗口内采多个回读值求平均**, 总积分
#   时间固定 1s -> NPLC=1 采 22 个 与 NPLC=25 采 2 个, 平均值噪声几乎一样。
#   所以不必顶满, 取 10 兼顾"单次读数噪声"与"窗口内还能采到几个点"。
#   实测耗时: NPLC=1 每次 45ms, =10 每次 297ms。
SMU_NPLC = 10
# autozero: 0=OFF 1=ONCE 2=ON。用**数值**设 —— 常量名 smua.AUTOZERO_ON 在
#   3.2.1 固件上报 -104 "Data type error" (实测), 数值 2 才是通的。
SMU_AUTOZERO = 2

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
# 2636B 的低电流档位 (手册, 1年 23±5C):
#
#   档位      **源**精度                 **回读**精度        <- 我们只用回读
#    1 nA   ±(0.15% rdg +  2 pA)     ±(0.15% + 240 fA)
#   10 nA   ±(0.15% rdg +  5 pA)     ±(0.15% +   3 pA)
#  100 nA   ±(0.06% rdg + 50 pA)     ±(0.06% +  40 pA)
#
# **以回读为准** (用户裁定): 回读测的是实际流出的电流, 而且固定项小得多
#   (1 nA 档 240 fA vs 源 2 pA, 小 8 倍)。所以"源精度"那一列只是背景参考,
#   写进不确定度预算的是**回读**那一列。 -> 档位对精度的实际影响远小于
#   按源精度估出来的量级。
#
# 档位本身仍然要**记录下来**: 回读固定项同样随档位变 (240 fA / 3 pA / 40 pA)。
SMU_RANGES = (1e-9, 10e-9, 100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1e0, 3e0)


def best_range(i_amp):
    """能覆盖该电流的**最小**档位。

    平时用不到 —— 档位交给仪器 autorange 自己挑 (官方默认, 挑的就是这个值)。
    留着是给 force_range 用的: 脉冲的档位必须覆盖**峰值**而不是平均值。
    超出最大档返回 None。
    """
    a = abs(i_amp)
    for r in SMU_RANGES:
        if a <= r:
            return r
    return None


def smu_drain(smu, quiet_ms=300, max_read=64):
    """把仪器**输出队列里残留的应答**读掉并丢弃。

    为什么必须有 —— 2026-09-15 实测踩到:
      上一个进程被强杀 (或上一次会话没读干净), 仪器输出队列里会留一条应答。
      之后每次 query 都**错位一格**: 读"型号"拿到上一条的应答, 读"错误队列"
      拿到"2636B", 于是 int(float(...)) 炸在莫名其妙的地方。
      症状很有迷惑性 —— 看起来像"型号读出来是 0.00000e+00"。

    做法: 用短超时反复读, 读到超时为止。读完恢复原超时。
    """
    old = smu.timeout
    smu.timeout = int(quiet_ms)
    junk = 0
    try:
        for _ in range(max_read):
            try:
                smu.read_raw()
                junk += 1
            except Exception:
                break                      # 超时 = 队列空了
    finally:
        smu.timeout = old
    return junk


def smu_open(res):
    rm = pyvisa.ResourceManager('@py')      # 纯 Python 后端, 不需要 NI-VISA
    smu = rm.open_resource(res)
    smu.read_termination = '\n'
    smu.write_termination = '\n'
    smu.timeout = 5000
    # 先把残留应答读掉, 否则下面每条 query 都错位一格 (见 smu_drain)
    n_junk = smu_drain(smu)
    if n_junk:
        print("    (清掉 %d 条残留应答 —— 上一次连接没读干净)" % n_junk)
    model = smu.query('print(localnode.model)').strip()
    print("    型号:", model)
    if "2636" not in model and "2600" not in model:
        raise RuntimeError("型号读出来是 %r, 不像 2636B —— 应答可能还在错位" % model)
    smu_write(smu, 'smua.reset()')
    # ---- 安全关断状态 (推导与出厂值对比见 docs/2636B源表接入.md 第三节) ----
    # `reset()` 会把 offlimitv 打回**出厂 40 V**, 而出厂 offmode = OUTPUT_NORMAL
    # 意味着"关输出"之后线上仍挂着一台 0 A 电流源, 其限压 40 V —— 对
    # ADA4530-1 这类高阻输入级是危险的。而本工程的每个点之间、每次换档都会
    # 关一次输出, 所以这不是理论风险。
    # 改成 0 A 电流源 + 限压 2 V。**不用 OUTPUT_HIGH_Z**: 手册明确警告它会磨损
    # 输出继电器, 扫描时频繁开关不可取。
    # 注意顺序: reset() 之后才能设, 否则会被 reset 冲掉。
    smu_write(smu, 'smua.source.offmode   = smua.OUTPUT_NORMAL')
    smu_write(smu, 'smua.source.offfunc   = smua.OUTPUT_DCAMPS')
    smu_write(smu, 'smua.source.offlimitv = 2')
    smu_write(smu, 'smua.source.func = smua.OUTPUT_DCAMPS')
    # **用 autorange** —— 官方默认 (四个功能各自独立、默认全开), 它会挑能覆盖
    # 设定值的最小档, 那正是我们想要的。唯一要额外做的是**把实际用的档位查出来
    # 记录** (见 smu_set: 回读固定项 240 fA/3 pA/40 pA 随档位变, 进预算要用)。
    smu_write(smu, 'smua.source.autorangei = smua.AUTORANGE_ON')
    smu_write(smu, 'smua.measure.autorangei = smua.AUTORANGE_ON')
    # 顺从电压。2026-09-15 由 20V 调低到 5V。
    #
    # 判据**不是**"够不够用", 而是"**让输入级的钳位二极管永远不导通**"。
    # 两个手册事实凑出来的:
    #   ADA4530-1: 输入绝对最大值 = (V-) - 0.3V ~ (V+) + 0.3V。超过就钳位二极管
    #       导通, 此时**必须把电流限到 <=10 mA**, 否则永久损坏。
    #   2600B: 顺从限不被超过的**唯一例外**是 ISOURCE 下的 VLIMIT ——
    #       "the VLIMIT will source or sink up to 102 mA for ISOURCE ranges
    #        on or below 100 mA"。即输出一顶到电压合规, 就**不再受 leveli 约束**。
    #
    # 于是: limitv=20V 时, 输入开路 -> 节点被顶到 20V -> 二极管在 5.3V 就导通
    #       -> 102 mA 那条路打开 -> 10 mA 就能烧掉 ADA4530-1。**这不是理论风险。**
    #       limitv <= 轨+0.3V 时, 节点永远到不了导通阈值, 那条路根本不开。
    #
    # 取 5V 的依据 (运放轨据用户报为 ±6V, 待实测):
    #   损坏边界: 绝对最大值 = 轨 ± 0.3V = 6.3V  -> 节点必须 < 6.3V
    #   规格边界: IVR = V- ~ V+ - 1.5V = +4.5V   -> 节点应 < 4.5V (超出不损坏)
    #   5V 卡在两条之间: 离损坏边界 26% 余量, 但故障时会超出 IVR (不可信, 不损坏)
    #   理想值是 4.5V —— 但只有在"正常工作所需电压远低于它"时才敢压下去。
    #
    # ⚠️ 待实测两点:
    #   ① 运放电源轨到底多少伏 (用户说要实测)。若是 ±12V/±15V, 上面两条跟着变。
    #   ② 正常工作时所需电压。用户观察 2 nA 时只有 0.5V, 但那 0.5V 是线性的
    #      (→45nA 需 11V) 还是固定偏置(→永远 ~0.5V), 推算分不出来。
    #      **这一条决定了 5V 能不能压到 4.5V。**
    #      验证: 设 +45 nA 读 smua.measure.v()。接近 5V 就要调回去并查输入级。
    smu_write(smu, 'smua.source.limitv = 5')
    # ---- 测量精度 (见文件头 SMU_NPLC 的推导) ----
    smu_write(smu, 'smua.measure.nplc = %g' % SMU_NPLC)
    smu_write(smu, 'smua.measure.autozero = %g' % SMU_AUTOZERO)
    smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    return smu


def smu_set(smu, i_amp, force_range=None):
    """设定电流, 并**返回仪器实际用的档位**。

    档位策略 —— 交回给仪器的 autorange:
      官方手册: 四个功能(源电压/源电流/测电压/测电流)的自动量程各自独立、
      **默认全开**; 测同一种功能时测量档自动锁定与源档一致。autorange 会挑
      **能覆盖设定值的最小档**, 那正是我们想要的(固定误差项最小)。
      所以不必硬设 —— 硬设反而可能挡住仪器自适应。

    **但档位要记录下来**: **回读**的固定误差项也随档位变
        1 nA 档 240 fA | 10 nA 档 3 pA | 100 nA 档 40 pA
      (注: **源**那一列的固定项大得多, 是 2 pA / 5 pA / 50 pA, 但**我们以回读
       为准** —— 回读测的是实际流出的电流, 且固定项小 1.7~8 倍。进不确定度
       预算的是回读那一列。)
      写进预算时必须知道当时是哪个档, 所以这里**查** smua.source.rangei 并返回。

    force_range: 少数场合要强制(比如脉冲的档位必须覆盖**峰值**而不是平均值),
      传了就关掉 autorange 并显式设档。**当前没有调用者传它** —— autorange
      对脉冲同样会按设定值(即峰值)挑档, 够用; 留着是备强制。
    """
    r = force_range if force_range is not None else best_range(i_amp)
    if r is None:
        raise ValueError("设定值 %.4g A 超出 2636B 最大档 3 A" % i_amp)
    smu_write(smu, 'smua.source.leveli = %.9e' % i_amp)
    if force_range is not None:
        smu_write(smu, 'smua.source.autorangei = smua.AUTORANGE_OFF')
        smu_write(smu, 'smua.source.rangei = %.9e' % force_range)
    # 查实际档位 (autorange 选出来的, 或上面强制设的)
    try:
        actual = float(smu.query('print(smua.source.rangei)'))
    except Exception:
        actual = r
    return actual


def smu_check_errors(smu, ctx=""):
    """把错误队列**读空并抛异常**。

    为什么必须有这个 —— 2026-09-15 实测踩到的坑:
      TSP 的 `smua.OUTPUT_ON` (这里 `smu` 是 nil, 正确的是 `smua.`) 会抛
      "attempt to index global `smu` (a nil value)" (-286), 但 **pyvisa 的 write
      不检查错误队列** —— 异常进了仪器的队列, 脚本这边毫无感觉, 一路跑完。
      后果: 35 条错误静静躺着, 而所有读数都是"输出根本没开"时的本底,
      看起来像正常数据。**这类静默失效比报错危险得多。**

    任何**写操作**之后都该过一遍。读操作(query)出错一般会当场抛, 风险小些。
    """
    try:
        n = int(float(smu.query('print(errorqueue.count)')))
    except Exception as e:
        raise RuntimeError("查错误队列就失败了 (%s): %s" % (ctx, e))
    if n == 0:
        return
    msgs = []
    for _ in range(n):
        try:
            msgs.append(smu.query('print(errorqueue.next())').strip())
        except Exception:
            break
    raise RuntimeError("源表报错 %d 条%s:\n    %s"
                       % (len(msgs), (" (%s)" % ctx) if ctx else "",
                          "\n    ".join(msgs)))


def smu_write(smu, tsp, ctx=""):
    """写 TSP + 立刻查错误队列。**所有写操作都用它, 不要直接 smu.write。**

    代价是每次写多一次查询往返 —— 本工程的写操作都是每点一次, 不在热路径上,
    这点开销换"错误不再静默"完全值得。
    """
    smu.write(tsp)          # 这里必须用裸 write, 不能再套 smu_write (无限递归)
    smu_check_errors(smu, ctx or tsp.strip()[:60])


def smu_off(smu):
    try:
        smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    except Exception:
        pass


# ---------------------------------------------------------------- 装置 (串口)
def board_open(port=None, baud=BOARD_BAUD):
    """打开板子串口, 返回 Serial。实际生效的波特率挂在 `s.baud_actual` 上。

    **两个波特率都试**: 固件 (uart.c 的 BOARD_BAUD) 与上位机必须一致,
    而不一致时的表现是"完全没反应", 跟"板子没插好"长得一模一样。
    这里按 [baud, BAUD_FALLBACK] 顺序各试一次, 省掉一次误判。
    """
    if port is None:
        import serial.tools.list_ports as lp
        c = [p.device for p in lp.comports() if p.vid == 0x1A86 and p.pid == 0x7523]
        if not c:
            print("** 找不到 CH340 **"); sys.exit(1)
        port = c[0]

    tried = []
    for b in ([baud] if baud == BAUD_FALLBACK else [baud, BAUD_FALLBACK]):
        s = serial.Serial(port, b, timeout=0.2)
        # 本板自动复位电路拿 DTR/RTS 驱动 NRST/BOOT0 —— 驱动默认置位会把 BOOT0
        # 抬高, 之后一复位就进 bootloader。释放掉。(R23 已改为 0Ω, 自动路径现在能用)
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
                s.baud_actual = b
                tried.append(b)
                print("    装置就绪 (%s @ %d bps%s)"
                      % (port, b, "" if b == baud else " —— **回退值, 固件波特率没改成?**"))
                return s
        s.close()
        tried.append(b)
    print("** 装置无响应 (试过 %s)。检查供电; 给板子断上电一次通常能解决 **"
          % "/".join(str(t) for t in tried))
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


def board_measure(ser, timeout=MEASURE_TIMEOUT_S):
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


def _read_exact(ser, n, timeout):
    buf = bytearray()
    t0 = time.time()
    while len(buf) < n:
        c = ser.read(n - len(buf))
        if c:
            buf += c
            t0 = time.time()
        elif time.time() - t0 > timeout:
            raise TimeoutError("要 %d 字节, 只收到 %d" % (n, len(buf)))
    return bytes(buf)


def board_read_waveform(ser, cmd="X", head_timeout=30.0, body_timeout=3.0,
                        retries=4, verbose=True):
    """发一条波形指令并收**整窗**数据, 返回 numpy int64 数组。

    cmd: 'X' = 外部 ADS8866 (表征以它为准), 'B' = 内置 ADC。
    响应头字分别是 'WAVEX' / 'WAVE'。

    协议: `'<头字> <count>\\r\\n'` + count*2 字节小端 u16 + u16 校验和。

    ⚠️ **校验和必须校验**: 串口丢字节是静默的, 不校验会拿到一条看着正常、
    实则错位的波形 —— 那种数据比没有更糟。不符直接抛。

    ⚠️ **必须重传, 而且重传是免费的**: 2026-09-16 实测 —— 固件波特率提到
       2 Mbps 后, **4 次回传里坏了 2 次**, 每次少 1~5 字节, 且丢掉的字节
       **再也没来**。固件用的是**阻塞式** HAL_UART_Transmit, 所以 MCU 侧不可能
       丢 —— 丢在 **CH340G -> USB -> 主机** 这一段 (板载是 CH340G 克隆片,
       2 Mbps 是它的规格上限)。
       波形还在 MCU 的 `voltage_buf_ext` 里没动, **重发一次 X 就重来一遍**,
       不产生任何代价 (HTML 上位机里也是这么写的)。

    body_timeout = 3.0 s 是量出来的, 不是拍的: 12500 字节在 921600 下 **136 ms**,
    在 115200 下 1.09 s。3 s 对 921600 有 22 倍余量、对 115200 有 2.8 倍余量;
    而字节一旦丢了**再等也不会来**, 所以超时越短越好。
    (2026-09-16 实测: 921600 下 **11% 的回传需要一次重传**, 89% 一次成功。
     用 10 s 超时时每次重传白等 10.9 s —— 全程 486 次里约 10 分钟纯等待;
     改成 3 s 后省约 6 分钟。**没有把 body_timeout 设成 60s 那种** ——
     最初那版就是 60s, 每次失败白等一分钟。)

    与 `tools/nanoammeter_capture.py` 的 `read_block` 是同一个协议; 那份实现
    在**模块级解析 sys.argv** (58~62 行), import 会读走调用者的参数, 不能复用,
    所以在共享库里重写一份。
    """
    import numpy as np            # 局部 import: 本模块其他函数不需要 numpy

    head = "WAVEX" if cmd == "X" else "WAVE"
    last = None
    for attempt in range(1, retries + 1):
        try:
            ser.reset_input_buffer()
            ser.write((cmd + "\n").encode())

            t0 = time.time()
            n = None
            while time.time() - t0 < head_timeout:
                line = ser.readline().decode(errors="replace").strip()
                if line.startswith(head + " "):
                    n = int(line.split()[1])
                    break
            if n is None:
                raise TimeoutError("%s 在 %.0fs 内没有头字" % (cmd, head_timeout))

            data = _read_exact(ser, n * 2 + 2, body_timeout)
            vals = np.frombuffer(data[:n * 2], dtype="<u2").astype(np.int64)
            rx = data[n * 2] | (data[n * 2 + 1] << 8)
            calc = int(vals.sum() & 0xFFFF)
            if rx != calc:
                raise ValueError("%s 校验和不符 (算出 0x%04X, 收到 0x%04X)"
                                 % (head, calc, rx))
            if verbose and attempt > 1:
                print("       波形第 %d 次重传才成功" % attempt)
            return vals
        except (TimeoutError, ValueError) as e:
            last = e
            if attempt < retries:
                time.sleep(0.3)
    raise ValueError("%s 连续 %d 次回传都失败, 最后一次: %s"
                     % (head, retries, last))


def board_set_window(ser, ms):
    """下发 T<ms> 强制窗口长度; ms=0 回到自动。返回装置的回显行。

    **为什么需要** (2026-09-15 用户要求): 1~20 pA 那一段 10s 窗口信噪比不够,
    要 50s。而固件的自动切换是按幅值判的 —— 20 pA 与 50 pA 都 <1nA, 区分不开,
    所以必须由上位机按**设定值**指定。

    ⚠️ 用完之后记得 board_set_window(ser, 0) 复位, 否则后面的点也一直用长窗口。
    """
    ser.reset_input_buffer()
    ser.write(('T%d\n' % int(ms)).encode())
    t0 = time.time()
    while time.time() - t0 < 3.0:
        ln = ser.readline().decode(errors='replace').strip()
        if ln.startswith('WIN'):
            return ln
    return None


def board_warmup(ser, timeout=MEASURE_TIMEOUT_S):
    """预热: 空测一次并丢弃。

    **为什么需要** (2026-09-13 实测): 一批测量里**第一次总是坏的** ——
    五次独立实验都是"1 坏 2~6 好"。原因是空闲期积分器被输入电流推到轨上,
    第一次的拉回相行程最长, 首拍最容易读到坏值(INT1 落在轨码)。
    后面几次都从上一窗末端起步(在阈值之间), 行程短, 就不会。
    所以每个测点前先空跑一次把它消耗掉。

    返回值只用于打印, 不参与任何计算。
    """
    d = board_measure(ser, timeout)
    if d is None:
        return "预热: 无响应"
    return "预热: %s%s" % (d['i'], " (TIMEOUT)" if d.get('timeout') else "")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', help='2636B 资源串, 如 TCPIP0::169.254.0.1::5025::SOCKET')
    ap.add_argument('--port', help='装置串口 (默认自动找 CH340)')
    ap.add_argument('--win-ms', type=float, default=0.0,
                    help='对 |设定| <= --win-below-pa 的点, 下发 T<ms> 强制窗口长度; '
                         '0 = 全部用固件自动 (1s / <1nA 换 10s)')
    ap.add_argument('--win-below-pa', type=float, default=20.0,
                    help='--win-ms 适用的电流上限 (pA)。默认 20 -> 1~20 pA 用长窗口')
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
            rng = smu_set(smu, i_nA * 1e-9)
            print("   档位 %.4g A" % rng)

            # ---- 窗口长度: 低电流点按需强制 ----
            # |设定| <= win_below_pa 的点用 --win-ms 指定的窗口, 其余交回固件自动。
            # 超时必须跟着窗口走 —— 50s 窗口配 40s 超时会必然超时。
            if a.win_ms > 0 and abs(i_nA) * 1000.0 <= a.win_below_pa:
                win_ms = a.win_ms
            else:
                win_ms = 0.0
            echo = board_set_window(ser, win_ms)
            m_timeout = (win_ms / 1000.0) + 30.0 if win_ms else 40.0
            print("   窗口: %s" % (echo or ("自动" if not win_ms else "** 无回显 **")))
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            time.sleep(a.settle)

            # ---- 预热: 空跑一次并丢弃 ----
            # 2026-09-13 五次独立实验都是"一批里**第一次坏**、后面全好":
            # 空闲期积分器被输入电流推到轨上, 第一次的拉回相行程最长, 首拍最
            # 容易读到坏值(INT1 落在轨码)。后面几次都从上一窗末端起步(在阈值
            # 之间), 行程短就不会。空跑一次把它消耗掉。
            print("   ", board_warmup(ser, m_timeout))

            dev_vals, rb_all = [], []
            tries = 0
            # 上限 a.n*3 次: 丢弃坏点后要补测, 但不能无限循环
            while (len(dev_vals) < a.n) and (tries < a.n * 3):
                tries += 1
                smp = ReadbackSampler(smu)
                smp.start()
                try:
                    d = board_measure(ser, m_timeout)
                finally:
                    picked = smp.stop(READBACK_N)   # 无论如何都要把线程停干净

                if d is None:
                    # **这次装置没应答, 那 10 个回读不能计入 I_true** ——
                    # 需求要的是"装置正在测的那段时间里的回读", 出错的这次根本
                    # 没在测。源稳时无所谓, 源在漂时会静默稀释标准值。
                    print("    第%d次: ** 装置无响应/超时 ** (本次回读不计入)" % tries)
                    continue

                # ---- 丢弃被标 TIMEOUT 的测量 ----
                # 2026-09-13 新接上的标记: 拉回相守卫超时, 或开窗首拍就落在轨码上
                # —— 两种都意味着这一窗不可信。以前这类坏点会**静默混进**数据
                # (实测首拍坏读率 1/6, 是平均坏读率的 200 倍), 现在它会自己报
                # 出来, 这里就把它丢掉补测。
                if d.get('timeout'):
                    print("    第%d次: ** TIMEOUT (拉回超时/首拍在轨) -> 丢弃补测 **" % tries)
                    continue

                rb_all += picked
                iread = float(d['i'])
                dev_vals.append(iread)
                print("    第%d次  装置 %+9.4f nA (MODE=%s T=%sms)   回读采了 %d 个   %s"
                      % (tries, iread, d['mode'], d['t'], len(picked),
                         "有RAW" if d['m'] else "**无RAW**"))
                rows.append(dict(point_nA=i_nA, rep=len(dev_vals), mode=int(d['mode']),
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
