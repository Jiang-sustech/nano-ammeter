# -*- coding: utf-8 -*-
"""
真实脉冲的**波形反推**验证 —— 实际波形 vs 从码流反推出的波形

    python tools/pulse_real.py --visa "TCPIP0::192.168.0.2::5025::SOCKET" --port COM6
    python tools/pulse_real.py --visa ... --wave duty25
    python tools/pulse_real.py --visa ... --wave triangle --period 400
    python tools/pulse_real.py --visa ... --segs "0.5:100,1:100,0:100"
    python tools/pulse_real.py --visa ... --dry            # 只看会下发什么

两代波形发生器
--------------
**`--gen pc`（默认, 已实机验证)** —— PC 翻转输出。
  问题是**定时被 LAN 往返盖死**: 2026-09-16 实测设定 200 ms 周期实测 **547 ms**
  (每次翻转是 write + 查错误队列两个往返, 单次约 150 ms)。所以:
    * 只能做**少量电平** (每周期几次跳变), 做不了三角形那种几十级的
    * 图上画的"实际波形"用的是**实测翻转时刻**, 不是设定值

**`--gen instr`（`--dry` 可验, **仍未实机跑通过**)** —— 仪器自己产生:
  恒定间隔的 `trigger.timer[1]` + `smua.trigger.source.listi({...})` 电平列表。
  时间基准是仪器的时钟, 不受 LAN 影响, 能出**任意波形**。

  ⚠️ **2026-09-16 状态更新:** 原来这里写"KIPulse 厂脚本调不通所以改走低层" ——
  **那个前提已经不成立了**: 厂脚本的失败原因查清了 (三条前置条件 + 返回值被丢),
  `sweep_pulse.py` 已修好并通过 `--check`。所以**低层这条路现在不是必需品**,
  优先级可以放低。

  低层这套原本有**三处确定的错**, 已照手册改掉 (详见 `instr_tsp()` 的 docstring):
  `trigger.timer[1].start.generate` 不存在、`source.listfunc` 不存在、
  `source.listi` 该用函数调用写法。**改完仍要先 `--dry` 再上真机确认。**

波形定义
--------
统一用**段表**: `[(电平占峰值比例, 持续 ms), ...]`, 一个周期循环。
    square      [(1, T/2), (0, T/2)]
    duty25      [(1, T/4), (0, 3T/4)]
    duty75      [(1, 3T/4), (0, T/4)]
    threelevel  [(0.5, T/3), (1, T/3), (0, T/3)]
    triangle    锯齿近似: 每周期 N 级线性上升再陡降
    burst       N 个窄脉冲 + 长间隔
或用 `--segs "1:50,0:150"` 自定义 (比例:毫秒)。

约束 (docs/脉冲电流实验方案.md §2.2)
    I_avg <= min( 45 nA , 50*(1-D) , 860 pC / T )
"""
import argparse
import math
import os
import sys
import threading
import time

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_set, smu_write, best_range,
                       board_open, board_measure, board_read_waveform,
                       board_warmup, board_set_window)


def smu_fixed_range(smu, i_peak):
    """关掉源 autorange, 固定到能覆盖峰值的档位。返回档位。

    ⚠️ **斩波必须这么做, 否则输出是坏的。** 2026-09-16 隔离实验实测:

        leveli 在 0 与 8 nA 之间来回时, autorange 会跟着在 **1 nA 档**与
        **10 nA 档**之间来回换 (best_range(0)=1nA, best_range(8nA)=10nA),
        而**带输出换档会把输出搞坏**:
            autorange ON           -> 装置读到 +0.003 nA  (期望 2 nA)
            autorange OFF + 固定档  -> 装置读到 +2.002 nA  (期望 2 nA)  ✓

        这一条和 `sweep_pulse.py:pulse_mode()` 是同一条坑 —— 那边是手册
        7-113 的前置条件, 这边是斩波特有的 (手册管不到, 因为这是普通直流输出)。

    **`smu_open()` 默认 autorange ON (`cal_sweep.py:184`)**, 所以走脉冲/斩波
    的路径都得自己关掉。不关的后果是装置读到噪声底, 而**脚本不报任何错**。
    """
    rng = best_range(i_peak)
    if rng is None:
        raise ValueError("峰值 %.4g A 超出最大档" % i_peak)
    smu_write(smu, 'smua.source.autorangei = smua.AUTORANGE_OFF')
    smu_write(smu, 'smua.source.rangei = %.9e' % rng)
    return rng

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

TICK_S = 160e-6          # TIM6 节拍
# 装置**当前**烧进去的常数 (09-16 写入的 162 点拟合值) —— 反推要用它,
# 用标称值会整体偏 1%。
Q_CODE = 15596e-18       # C/码
I_POS = 49712e-12        # A
I_NEG = -49739e-12


# ---------------------------------------------------------------- 波形定义
def waveform(name, period_ms, nlev=16):
    """-> [(电平占峰值比例, 持续 ms), ...], 加起来 = period_ms"""
    T = period_ms
    if name == 'square':
        return [(1.0, T / 2), (0.0, T / 2)]
    if name == 'duty25':
        return [(1.0, T / 4), (0.0, 3 * T / 4)]
    if name == 'duty75':
        return [(1.0, 3 * T / 4), (0.0, T / 4)]
    if name == 'threelevel':
        return [(0.5, T / 3), (1.0, T / 3), (0.0, T / 3)]
    if name == 'triangle':
        # 线性上升 N 级 + 一级陡降回 0。级数受 --gen 限制:
        # instr 路径可以多级; pc 路径每级一次 LAN 往返, 根本做不了。
        up = [(i / nlev, T / (nlev + 1)) for i in range(1, nlev + 1)]
        return up + [(0.0, T / (nlev + 1))]
    if name == 'burst':
        # 4 个窄脉冲 (各占周期 5%) + 剩下的空档
        w = T * 0.05
        gap = (T - 4 * w) / 5
        out = []
        for _ in range(4):
            out += [(0.0, gap), (1.0, w)]
        out += [(0.0, gap)]
        return out
    raise ValueError("未知波形 %s" % name)


def parse_segs(s, period_ms):
    """'1:50,0:150' -> [(1.0, 50.0), (0.0, 150.0)], 按比例缩放到 period_ms"""
    segs = []
    for part in s.split(','):
        lv, ms = part.split(':')
        segs.append((float(lv), float(ms)))
    tot = sum(d for _, d in segs)
    if tot <= 0:
        raise ValueError("段表总时长为 0")
    k = period_ms / tot
    return [(l, d * k) for l, d in segs]


def wave_desc(segs):
    return "  ".join("%g%%×%.1fms" % (l * 100, d) for l, d in segs)


# ---------------------------------------------------------------- 仪器产生
def instr_tsp(peak_a, segs, n_cycle, tick_ms=1.0):
    """恒定间隔定时器 + 电平列表 —— 时间基准是**仪器的时钟**, 不受 LAN 影响。
        电平列表  smua.trigger.source.listi({...})
        定时间隔  trigger.timer[1].delay
        每个 timer 事件推进一个列表点  smua.trigger.source.stimulus
    `tick_ms` 是列表的时间粒度 —— 段时长会被量化到它的整数倍。

    ⚠️ **仍未实机跑通过**, 但 2026-09-16 照手册 (2600BS-901-01 **Rev. C**,
    本地 `D:\\451374.pdf`) 改掉了三处**确定**的错误 —— 三处都有"手册 + 仪器"
    两条独立证据:

    ① `trigger.timer[1].start.generate = true` —— **该属性不存在。**
       手册全 919 页 **0 命中**; 仪器实测 `type(trigger.timer[1].start)` = nil,
       而探它直接报 `-286 attempt to index field 'start'`。
       自由跑的正规写法是 7-374 的 `count`:
           "Set count to zero (0) to cause the timer to generate trigger events
            indefinitely."
       定时器**怎么启动**靠 `stimulus` —— 手册 7-377 的 period_timer 例子用的是
       `period_timer.stimulus = smua.trigger.SWEEPING_EVENT_ID` + `passthrough = true`。

    ② `smua.trigger.source.listfunc = {}` —— **手册 0 命中**, 不存在。删掉。

    ③ `smua.trigger.source.listi = {...}` (赋值写法) —— 写错了。
       手册 7-266 的例子是**函数调用**:
           smua.trigger.source.listv({3, 1, 4, 5, 2})
       仪器实测 `type(smua.trigger.source.listi)` = **function** (不是属性),
       独立印证。本函数是电流源, 用 `listi`。

    ⚠️ **下面"重复跑"的写法仍需实机确认。** 手册的 period_timer 例子
    (7-377) 用 `endpulse` + `arm.count` 做重复; 这里用的是"定时器 count=0
    无限出 tick + `smua.trigger.count` 走列表 + `arm.count` 重复".
    **先 `--dry` 看下发了什么, 再上真机, 别直接信。**

    ⚠️ **另一个坑 (与指令无关但会静默污染): `smua.reset()` 管不着
    `trigger.timer`。** 它是**系统级**对象, 手册列的影响项是 "Instrument reset"
    而不是 SMU reset。2026-09-16 实测: `smua.reset()` 前后
    `trigger.timer[1].delay` 都是 0.05、`count` 都是 200, 纹丝不动。
    所以配置前必须 `trigger.timer[1].reset()` 显式清掉 —— 本函数已加。
    """
    # 段表 -> (点数, 电平) 对。**不在 Python 侧展开成完整列表** ——
    # 200 ms 周期 / 1 ms 粒度 = 200 个数, 展开成 TSP 字面量会撞
    #      -363  Input buffer overrun
    # (2636B 单条命令装不下, 2026-09-16 实测)。改成让**仪器侧**用两层循环
    # 展开, 命令长度只与**段数**有关, 与时间粒度无关 —— 想多细都行。
    pairs = [(max(1, int(round(d / tick_ms))), l * peak_a) for l, d in segs]
    npts = sum(n for n, _ in pairs)
    pairs_lua = ", ".join("{%d, %.9e}" % (n, v) for n, v in pairs)
    list_cmd = ('do local segs = {%s}  local t = {}  '
                'for _, s in ipairs(segs) do '
                'for i = 1, s[1] do table.insert(t, s[2]) end end  '
                'smua.trigger.source.listi(t) end' % pairs_lua)
    rng = best_range(peak_a)
    return [
        # 源: 电流模式, 不测量 (测量由装置做, 源表只管输出)
        'smua.source.func = smua.OUTPUT_DCAMPS',
        'smua.source.output = smua.OUTPUT_OFF',
        # 关 autorange + 固定档 (同 smu_fixed_range(): 电平列表在 0 与峰值之间
        # 跳变, autorange 会跟着来回换档, 带输出换档会把输出搞坏)
        'smua.source.autorangei = smua.AUTORANGE_OFF',
        'smua.source.rangei = %.9e' % (rng if rng else peak_a),
        # 定时器: 先 reset 清残留 (smua.reset() 够不着它, 见上)
        'trigger.timer[1].reset()',
        'trigger.timer[1].delay = %.9e' % (tick_ms / 1000.0),
        'trigger.timer[1].count = 0',        # 0 = 无限出事件 (手册 7-374)
        'trigger.timer[1].stimulus = smua.trigger.SWEEPING_EVENT_ID',
        'trigger.timer[1].passthrough = true',
        # 源的电平列表 (函数调用写法, 手册 7-266) + 由定时器推进。
        # 仪器侧循环展开, 见上面 pairs 的推导 (字面量会撞 -363)
        list_cmd,
        'smua.trigger.source.action = smua.ENABLE',
        'smua.trigger.source.stimulus = trigger.timer[1].EVENT_ID',
        'smua.trigger.source.limitv = 5.0',
        # 一个 arm 走完整个列表, 重复 n_cycle 次
        'smua.trigger.count = %d' % npts,
        'smua.trigger.arm.count = %d' % max(1, n_cycle),
    ]


# ---------------------------------------------------------------- PC 产生
class Chopper(threading.Thread):
    """PC 逐段翻转输出。记录每段的**实测**起止时刻。

    ⚠️ 每次 `smu.write` 都会过 LAN (实测单次约 150 ms), 所以实际段长 =
    设定值 + 开销。图上画的"实际波形"用**实测**时刻, 不用设定值。

    这里**故意用裸 `smu.write` 而不是 `smu_write`** —— 后者每次都多查一遍
    错误队列 (又一个 LAN 往返), 会让开销翻倍。代价是错误不再即时可见,
    所以循环结束前查一次。
    """
    def __init__(self, smu, peak_a, segs):
        super().__init__(daemon=True)
        self.smu, self.peak, self.segs = smu, peak_a, segs
        self.stop = False
        self.edges = []          # (t_s, 电平比例) 每段起点
        self.t0 = None
        self.err = None

    def run(self):
        self.t0 = time.perf_counter()
        first = True
        while not self.stop:
            for lv, d in self.segs:
                if self.stop:
                    break
                try:
                    self.smu.write('smua.source.leveli = %.9e' % (lv * self.peak))
                    if first:
                        self.smu.write('smua.source.output = smua.OUTPUT_ON')
                        first = False
                except Exception as e:
                    self.err = e
                    return
                self.edges.append((time.perf_counter() - self.t0, lv))
                time.sleep(d / 1000.0)
        try:
            self.smu.write('smua.source.output = smua.OUTPUT_OFF')
        except Exception:
            pass


# ---------------------------------------------------------------- 反推
def _slope(y):
    x = np.arange(len(y), dtype=float)
    x -= x.mean()
    return (y * x).sum() / (x * x).sum()


def invert(c, n):
    """滑窗反推 I(t)。积分器的**斜率就是电流**; "码上升 <-> SEL_POS" 是固件的
    死逻辑, 所以看斜坡往哪走就知道当时注入的是哪一路参考。"""
    ts, Is = [], []
    h = n // 2
    for i in range(h, len(c) - h):
        s = _slope(c[i - h:i + h + 1])
        iref = I_POS if s > 0 else I_NEG
        ts.append((i + 0.5) * TICK_S)
        Is.append(Q_CODE * s / TICK_S - iref)
    return np.array(ts), np.array(Is)


def fold(t_s, i_a, period_s, nb):
    ph = np.mod(t_s, period_s) / period_s
    b = np.clip((ph * nb).astype(int), 0, nb - 1)
    mid = np.full(nb, np.nan)
    se = np.full(nb, np.nan)
    for j in range(nb):
        v = i_a[b == j]
        # **>=1 而不是 >1** —— keep 过滤会丢掉一大半点 (参考切换处), 摊到 60 个
        # 相位格上, 有些格子只落得下 1 个点。原来要求 >1 就把它们留成 NaN,
        # 而 im 里只要有一个 NaN, 下面相位对齐的 corrcoef 就整体返回 NaN,
        # 对齐**静默失效** (表现为"相关系数 -2.000" 那个不可能的初值)。
        if len(v) >= 1:
            mid[j] = v.mean()
            if len(v) > 1:
                se[j] = v.std(ddof=1) / math.sqrt(len(v))
    return (np.arange(nb) + 0.5) / nb * period_s * 1e3, mid * 1e9, se * 1e9


def _corr(a, b):
    """NaN 安全的相关系数。任一侧是常数、或有效点太少 -> 返回 NaN。

    两侧都先 `asarray` —— `ref`(设定波形)是 Python list, 不转换的话
    布尔索引 `b[m]` 会报 "only integer scalar arrays can be converted"。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return float('nan')
    aa, bb = a[m], b[m]
    if aa.std() == 0 or bb.std() == 0:
        return float('nan')
    return float(np.corrcoef(aa, bb)[0, 1])


def acquire(a, segs):
    """跑一次真实采集, 返回 (波形码值, 装置读数行)。`--replay` 时不走这里。"""
    ser = board_open(a.port)
    smu = smu_open(a.visa)
    smu_set(smu, a.peak * 1e-9)
    # ⚠️ 必须在开始翻转**之前**关掉 autorange —— 见 smu_fixed_range() 的实测
    smu_fixed_range(smu, a.peak * 1e-9)
    board_set_window(ser, 1000)
    board_warmup(ser)

    if a.gen == 'instr':
        for c in instr_tsp(a.peak * 1e-9, segs, a.cycles, a.tick_ms):
            smu_write(smu, c)
        for q in ('print(smua.trigger.source.action)',
                  'print(trigger.timer[1].delay)',
                  'print(smua.trigger.count)'):
            print("   %-40s -> %s" % (q, smu.query(q).strip()))
        smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
        # ⚠️ **必须 initiate, 否则触发模型根本没启动**: 定时器不发事件 ->
        # 电平列表不推进 -> 输出停在 `leveli` 上 (恒定直流)。
        # 2026-09-16 实测就是这样: 装置读到 7.997 nA (= 峰值), 反推段2 也是 7.99,
        # 看着像"波形不对", 其实是**根本没在跑**。手册 7-377 的 period_timer
        # 例子结尾正是 `smua.source.output = smua.OUTPUT_ON` + `smua.trigger.initiate()`。
        smu_write(smu, 'smua.trigger.initiate()')
        time.sleep(0.6)
        d = board_measure(ser, 30.0)
        wv = board_read_waveform(ser, 'X')
        try:
            smu_write(smu, 'smua.abort()')
        except Exception:
            pass
        smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    else:
        ch = Chopper(smu, a.peak * 1e-9, segs)
        ch.start()
        time.sleep(0.6)
        d = board_measure(ser, 30.0)
        wv = board_read_waveform(ser, 'X')
        ch.stop = True
        ch.join(timeout=3.0)
        edges = np.array([e[0] for e in ch.edges])
        if ch.err:
            print("   ** 翻转线程出错: %s" % ch.err)
        # 每段的**实测**时长
        if len(edges) > 2:
            dt = np.diff(edges)
            print("实测每段时长:")
            for i, (lv, dseg) in enumerate(segs):
                sel = dt[(np.arange(len(dt)) % len(segs)) == i]
                if len(sel):
                    print("   第%d段 (设定 %g%%×%.1fms): 实测 %.1f ± %.1f ms"
                          % (i + 1, lv * 100, dseg, sel.mean() * 1e3, sel.std() * 1e3))
    smu_off(smu)
    ser.close()
    if d is None:
        sys.exit("装置无响应")
    return wv, d['line'][:80], float(d['i'])


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--wave', default='square',
                    choices=['square', 'duty25', 'duty75', 'threelevel',
                             'triangle', 'burst'])
    ap.add_argument('--segs', default=None,
                    help='自定义段表 "电平比例:毫秒,...", 覆盖 --wave')
    ap.add_argument('--period', type=float, default=200.0, help='周期 (ms)')
    ap.add_argument('--peak', type=float, default=8.0, help='峰值 (nA)')
    ap.add_argument('--gen', default='pc', choices=['pc', 'instr'],
                    help='pc = PC 翻转 (已验证, 只能少量电平); '
                         'instr = 仪器定时器+电平列表 (**没实机验证过**, 能做任意波形)')
    ap.add_argument('--tick-ms', type=float, default=1.0,
                    help='instr 模式的列表时间粒度 (ms)')
    ap.add_argument('--cycles', type=int, default=200,
                    help='instr 模式重复多少个周期 —— 必须覆盖装置的测量窗口 '
                         '(1 s 窗口 / 200 ms 周期 -> 5 个; 默认 200 留足余量)')
    ap.add_argument('--tau-ms', type=float, default=10.0, help='反推斜率跨度 (ms)')
    ap.add_argument('--dry', action='store_true', help='只打印会下发什么, 不连仪器')
    ap.add_argument('--replay', default=None, metavar='NPZ',
                    help='回放已存的 npz: 跳过采集, 用它存下的码值重算并出图 '
                         '(离线复算/换图用, 不碰硬件)')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--npz', default='data/pulse_real.npz')
    a = ap.parse_args()

    _rp = None
    # 标题/图例上显示的名字。回放时 **不能用 a.wave/a.gen** —— 那只是命令行
    # 的默认值, npz 里没存波形名和发生器名, 照抄会标成 "square / gen=pc",
    # 而数据其实是 duty25 / gen=instr (2026-09-16 实际标错过一次)。
    _wave_label, _gen_label = a.wave, a.gen
    if a.replay:
        # 回放: 峰值/周期/段表都以 **npz 里存的**为准 —— 命令行那几个参数
        # 在这条路上会被覆盖, 免得图和数据对不上。
        _rp = np.load(a.replay, allow_pickle=True)
        a.peak = float(_rp['peak'])
        a.period = float(_rp['period'])
        segs = [tuple(float(v) for v in x) for x in _rp['segs']]
        _wave_label, _gen_label = wave_desc(segs), 'replay'
        print("回放 %s:  峰值 %g nA, 周期 %g ms, 段表 %s"
              % (a.replay, a.peak, a.period, wave_desc(segs)))

    segs = (segs if a.replay else
            (parse_segs(a.segs, a.period) if a.segs
             else waveform(a.wave, a.period)))
    duty = sum(d for l, d in segs if l > 0.01) / a.period
    iavg = a.peak * sum(l * d for l, d in segs) / a.period
    lim = min(45.0, 50.0 * (1 - duty), 860.0 / a.period)
    out_png = a.out or ('data/pulse_real_%s.png' % a.wave)
    if a.replay and not a.out:
        # 回放的 --wave 多半没跟着改, 用源文件名更不容易认错
        out_png = os.path.splitext(a.replay)[0] + '_replay.png'

    print("=" * 78)
    print("脉冲波形反推验证")
    print("=" * 78)
    print("波形:   %s   %s" % (_wave_label, wave_desc(segs)))
    print("        周期 %g ms, 峰值 %g nA, 等效占空比 %.1f%%" % (a.period, a.peak, duty * 100))
    print("        I_avg = %.2f nA     约束上限 %.2f nA  -> %s"
          % (iavg, lim, "OK" if iavg <= lim else "** 超限! **"))
    print("发生:   %s%s" % (_gen_label if _gen_label == 'replay' else "--gen " + _gen_label,
          "  (回放, 未碰硬件)" if _gen_label == 'replay' else
          ("  (** 未实机验证 **)" if a.gen == 'instr' else "  (已验证)")))
    print("")

    if a.gen == 'instr' and not a.replay:
        cmds = instr_tsp(a.peak * 1e-9, segs, a.cycles, a.tick_ms)
        print("--- 会依次下发 ---")
        for c in cmds:
            print("   ", c[:100] + ("..." if len(c) > 100 else ""))
        print("")

    if a.dry:
        print("--dry: 不连仪器, 退出")
        return

    if a.replay:
        wv = _rp['wave_ext'].astype(float)
        dev_na = float(_rp['dev'])
        dev_line = "回放自 %s  (原装置读数 %.4f nA)" % (a.replay, dev_na)
    else:
        wv, dev_line, dev_na = acquire(a, segs)
    print("")
    print("装置读数: %s" % dev_line)
    print("波形: %d 点" % len(wv))

    # ---- 反推 ----
    h = max(2, int(a.tau_ms / 1000.0 / TICK_S / 2))
    t_s, i_a = invert(wv.astype(float), h)
    step = np.diff(wv.astype(float))
    bnd = np.unique(np.concatenate([[0],
                                    np.where(np.abs(step) > 32768)[0],
                                    np.where(np.abs(np.diff(np.sign(step))) > 0)[0] + 1,
                                    [len(wv) - 1]]))
    keep = np.ones(len(t_s), bool)
    for j, tk in enumerate((t_s / TICK_S).astype(int)):
        lo, hi = tk - h, tk + h
        k = np.searchsorted(bnd, lo, 'right') - 1
        keep[j] = (lo >= bnd[k]) and (hi < bnd[k + 1])

    per_m = a.period / 1000.0
    tf, im, ise = fold(t_s[keep], i_a[keep], per_m, 60)
    # 设定波形 (按段表展开到同一相位网格)
    ref = []
    for t in tf:
        ph = (t / a.period) % 1.0 * a.period
        acc = 0.0
        v = 0.0
        for lv, dd in segs:
            if ph < acc + dd:
                v = lv
                break
            acc += dd
        ref.append(v * a.peak)

    # 相位对齐 (只挪相位, 不改幅度)
    nb = len(tf)
    best, bestr = 0, float('nan')
    for sh in range(nb):
        c = _corr(np.roll(im, sh), ref)
        if np.isfinite(c) and (not np.isfinite(bestr) or c > bestr):
            bestr, best = c, sh
    im, ise = np.roll(im, best), np.roll(ise, best)
    if np.isfinite(bestr):
        print("相位对齐: %d/%d 格, 相关系数 %.3f" % (best, nb, bestr))
    else:
        print("相位对齐: **失败** —— 折叠后的点有效值太少或为常数。")
        print("          (反推的逐段数字不可信, 只是**没对齐**的折叠结果)")

    # (显示原点的滚动放在"逐段统计"**之后** —— 那段统计是按未滚动的 tf 选的格子,
    #  先滚会让它对不上。见下面 ---- 图 ---- 之前的那段。)
    print("")
    print("反推 vs 设定 (逐段):")
    acc = 0.0
    for i, (lv, dd) in enumerate(segs):
        sel = [(t >= acc) & (t < acc + dd) for t in tf]
        v = im[[j for j in range(nb) if sel[j]]]
        if len(v):
            print("  段%d  设定 %5.1f nA   反推 %6.2f nA   差 %+6.2f%%"
                  % (i + 1, lv * a.peak, np.nanmean(v),
                     (np.nanmean(v) - lv * a.peak) / a.peak * 100 if a.peak else 0))
        acc += dd

    # ---- 把**显示原点**挪到最长那一段的中间 (只影响画图, 不影响上面的统计) ----
    # 折叠的接缝落在显示区的**两端**。而接缝附近的格子上总会沾到参考切换的
    # 伪迹 —— 这是物理的, 不是算法的错: 电流一突变, 积分器立刻顶到阈值,
    # 参考电流换向, 而**换向瞬间的斜率反推不准**。所以伪迹总是贴着上升/下降沿。
    # 接缝落在平区中间时完全看不出来; 落在上升沿上, 就表现为"周期两端断裂"
    # (2026-09-16 用户指出的: 0 ms 读到 8.9 nA **超过峰值 8.0**, 200 ms 读到 1.4)。
    # 把原点挪到最长一段的中点后, 两端都是平区, 沿上的伪迹回到它们该在的位置。
    # 设定波形要跟着一起转, 否则两者会错开。
    ref = np.asarray(ref, dtype=float)
    acc, mid, longest = 0.0, 0.0, -1.0
    for lv, d in segs:
        if d > longest:
            longest, mid = d, acc + d / 2.0
        acc += d
    extra = int(round(mid / a.period * nb)) % nb
    im_p, ise_p, ref_p = im, ise, ref
    if extra:
        im_p, ise_p, ref_p = (np.roll(im, extra), np.roll(ise, extra),
                              np.roll(ref, extra))
        print("显示原点: 再挪 %d 格 (%.0f ms) —— 折叠接缝移进最长一段的中间"
              % (extra, mid))

    # ---- 图 ----
    fig, ax = plt.subplots(2, 1, figsize=(12, 8))
    ax[0].plot(t_s * 1e3, i_a * 1e9, lw=.6, color='0.7')
    ax[0].scatter(t_s[keep] * 1e3, i_a[keep] * 1e9, s=3, color='C0')
    ax[0].set_xlabel('窗口内时间 (ms)'); ax[0].set_ylabel('反推输入电流 (nA)')
    ax[0].set_title('反推的 I(t) 整窗 (1 s) —— 灰=未剔除参考切换处, 蓝=保留')
    ax[0].grid(alpha=.3)
    ax[1].errorbar(tf, im_p, yerr=ise_p, fmt='o-', ms=4, lw=1.2, color='C3',
                   ecolor='0.75', capsize=2, label='反推 (折叠, 均值±SE)')
    ax[1].plot(tf, ref_p, 'k--', lw=1.5, label='设定波形 %s' % _wave_label)
    ax[1].set_xlabel('周期内相位 (ms)   —— 原点取在最长一段的中点%s'
                     % (' (已挪 %d 格)' % extra if extra else ''))
    ax[1].set_ylabel('输入电流 (nA)')
    ax[1].set_title('折叠后的一个周期 —— 反推 vs 设定   相关系数 %.3f' % bestr)
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=9)
    fig.suptitle('脉冲波形反推   %s  峰值 %g nA  周期 %g ms  (%s)'
                 % (_wave_label, a.peak, a.period,
                    '回放' if a.replay else 'gen=' + a.gen),
                 fontsize=12, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_png, dpi=150)
    np.savez(a.npz, wave_ext=wv.astype(np.uint16), dev=dev_na,
             peak=a.peak, period=a.period, t_s=t_s, i_a=i_a, keep=keep,
             segs=np.array(segs))
    print("")
    print("已写", out_png, "和", a.npz)


if __name__ == '__main__':
    main()
