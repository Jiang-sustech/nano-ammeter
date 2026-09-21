# -*- coding: utf-8 -*-
"""
光电探测器 I-V 曲线 —— 2636B 的 A 路源电压、B 路当电流表

    python tools/sweep_iv.py --visa "TCPIP0::192.168.0.2::5025::SOCKET"
    python tools/sweep_iv.py --visa ... --single            # 单通道 (只用 A 路)
    python tools/sweep_iv.py --visa ... --oneway            # 只去程 21 点
    python tools/sweep_iv.py --visa ... --n 3 --nplc 1      # 每点 3 次取平均
    python tools/sweep_iv.py --visa ... --dry               # 只连不扫, 看配置

两种接线 (--single 决定)
------------------------
  双通道 (默认)  A 路源电压 + B 路串联当电流表
                 A.HI → 探测器 → B.HI,  B.LO → A.LO     (4 根线)
                 代码上: 电流读 `smub.measure.i()`

  单通道         A 路自己"源电压 + 测电流" (源表的本职, 不用拆两个通道)
                 A.HI → 探测器 → A.LO                   (2 根线)
                 代码上: 电流读 `smua.measure.i()`

  ⚠️ 别把被测件接到 `SENSE HI/LO` 上 —— 那是四线制的**高阻电压敏感线**,
     不走电流, 接上去电流测量的通路就是断的 (读数停在几十 fA 的开路本底)。
     电流必须走 **FORCE HI/LO**。

配置 (用户 2026-09-21 定)
------------------------
    A 路 (smua)  源**电压** -2.00 V → 0 V, 步长 0.1 V
    往返扫描     去程 21 点 + 回程 20 点 = 41 点 (回程不重复 0 V)
    真值         取 `smua.measure.v()` 的**回读值**, 不是设定值

⚠️ 与 cal_sweep.py 的**关键区别**: 那边 smua 源电流、被测件是纳安表;
   这边 smua 源电压、被测件是光电探测器。所以 `smu_open()` 里那套
   为 ADA4530 输入级定的 `limitv=5V` / 电流源配置**这里不适用**, 下面
   整个重配。复用的只是**连接层** (smu_open 的 VISA/清残留/型号校验)。

安全
----
- A 路限流 `--limiti` 默认 **1 mA**: 光电探测器能承受的典型值。
  它平时远够用 (光电流通常 nA~µA), 只在探测器短路时起作用。
- A 路关输出后是「0 A 电流源 + 限压 2 V」, 不是留一个电压在探测器上。
- B 路合规电压 `--vlimit-b` 默认 2 V —— 电流表只该有极小压降,
  给它 2 V 是万一被测回路断开时的兜底, 不是工作点。
- **任何异常 (含 Ctrl-C) 都会走 finally 关输出**, 不会把电压留在探测器上。

数据
----
CSV 表头用**纯 ASCII** —— 这样 Excel 和 pandas 都能直接读, 不必纠结 BOM。
(本仓库 report_table.py 生成的**中文表头**表为了 Excel 才必须带 BOM;
 但那些是给人看的报告表, 而这里是要进分析脚本的原始数据。)
"""
import argparse
import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 复用 cal_sweep 的连接层 —— 不重写 pyvisa 那套 (清残留应答、型号校验在这儿)
from cal_sweep import smu_open, smu_off, smu_write, smu_check_errors  # noqa: E402


def build_points(v0, v1, step, oneway):
    """点表。往返时回程**不重复终点 0 V** —— 两个 0 V 点连在一起没有信息量,
    却会让"同一电压去/回各一次"这个对照关系在末点失效。"""
    n = int(round(abs(v1 - v0) / step))
    fwd = [round(v0 + step * k, 10) for k in range(n + 1)]
    out = [(v, 'fwd') for v in fwd]
    if not oneway:
        out += [(v, 'rev') for v in reversed(fwd[:-1])]
    return out


def setup(smu, a):
    """把两个通道配成「A 源电压 + B 电流表」。连接已在 smu_open() 里建好。"""
    # ---------- A 路: 源电压 ----------
    smu_write(smu, 'smua.reset()')
    # 关输出的状态: 0 A 电流源 + 限压 2 V。沿用 cal_sweep 的写法 —— reset()
    # 会把 offlimitv 打回出厂 40 V, 而"关输出"之后线上仍挂着一台源, 40 V
    # 对任何被测件都不是个安全状态。不用 OUTPUT_HIGH_Z: 手册警告它磨损
    # 输出继电器, 扫描时反复开关不可取。
    smu_write(smu, 'smua.source.offmode   = smua.OUTPUT_NORMAL')
    smu_write(smu, 'smua.source.offfunc   = smua.OUTPUT_DCAMPS')
    smu_write(smu, 'smua.source.offlimitv = 2')
    smu_write(smu, 'smua.source.func   = smua.OUTPUT_DCVOLTS')
    smu_write(smu, 'smua.source.levelv = 0')
    # ★ 保护限流。对电压源, 合规量是**电流**; 设错这一条才是真会烧东西的地方。
    smu_write(smu, 'smua.source.limiti = %.9e' % a.limiti)
    smu_write(smu, 'smua.source.autorangev = smua.AUTORANGE_ON')
    # ⚠️ **不要写 `smua.measure.func = smua.MEASURE_DCVOLTS`** —— 2600B 上没有
    #    这个属性。`smua.measure` 是**只读表**: `rangev`/`nplc` 等是它已有的键
    #    (可赋值), 而 `func` 不是 -> 报 "Cannot modify read only table",
    #    `smua.MEASURE_DCVOLTS` 本身也是 nil。测量功能不靠属性切换,
    #    直接调 `measure.v()` / `measure.i()` 即可, 量程各走各的 autorange。
    #    (2026-09-21 实测确认, 见 docs/2636B源表接入.md 第一节。)
    smu_write(smu, 'smua.measure.autorangev = smua.AUTORANGE_ON')
    # 单通道模式下 A 路还要测电流, 所以它的电流量程也必须配好。
    # 双通道模式下这两行无害 (A 的电流没人读), 就不分情况了。
    smu_write(smu, 'smua.measure.autorangei = smua.AUTORANGE_ON')
    smu_write(smu, 'smua.measure.nplc = %g' % a.nplc)
    smu_write(smu, 'smua.measure.autozero = %g' % a.autozero)
    smu_write(smu, 'smua.measure.delay = %g' % a.delay)

    if a.single:
        # ---------- 单通道: 不碰 B 路 ----------
        # 只把它关掉, **不 reset** —— B 上可能挂着别的东西, 不该被我们重置。
        smu_write(smu, 'smub.source.output = smub.OUTPUT_OFF')
        smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
        smu_check_errors(smu, "配置 A 路 (单通道)")
        return

    # ---------- B 路: 电流表 ----------
    smu_write(smu, 'smub.reset()')
    smu_write(smu, 'smub.source.offmode   = smub.OUTPUT_NORMAL')
    smu_write(smu, 'smub.source.offfunc   = smub.OUTPUT_DCAMPS')
    smu_write(smu, 'smub.source.offlimitv = 2')
    # 0 A 源 + 开输出 = 零压降电流表。这是 2600B 上"把 SMU 当安培计用"的标准做法:
    # 不走 2400 仿真模式, 也不需要额外的电流表量程命令。
    smu_write(smu, 'smub.source.func   = smub.OUTPUT_DCAMPS')
    smu_write(smu, 'smub.source.leveli = 0')
    smu_write(smu, 'smub.source.limitv = %g' % a.vlimit_b)
    smu_write(smu, 'smub.source.autorangei = smub.AUTORANGE_ON')
    # 同上: 无 measure.func。直接调 smub.measure.i()。
    smu_write(smu, 'smub.measure.autorangei = smub.AUTORANGE_ON')
    smu_write(smu, 'smub.measure.nplc = %g' % a.nplc)
    smu_write(smu, 'smub.measure.autozero = %g' % a.autozero)
    # ★ 这里是整个脚本最大的一个坑 (2026-09-21 实测)。
    #   出厂 `measure.delay = -1` = 仪器自动算延时。对**电流**测量它算得
    #   极其保守: 实测每点固定多花 **~900 ms**, 与 NPLC 无关
    #   (NPLC 0.01 → 902 ms, NPLC 10 → 1103 ms, 那 900 ms 纹丝不动),
    #   也与电流档位、自动量程、输出开关全都无关。
    #   同一条 900 ms 在**电压**测量上不存在 (measure.v() 只随 NPLC 走)。
    #   设成 0 → 每题 4.3 ms, **快 210 倍**, 读数不变。
    #   我们本来就有显式 --settle, 不需要仪器再插一段。
    smu_write(smu, 'smub.measure.delay = %g' % a.delay)

    # 两路都先关着 —— 扫描开始时再一起开
    smu_write(smu, 'smua.source.output = smua.OUTPUT_OFF')
    smu_write(smu, 'smub.source.output = smub.OUTPUT_OFF')
    smu_check_errors(smu, "配置两个通道")


def read_point(smu, v_set, a):
    """设电压 -> 等稳定 -> 读回读电压 + 电流。返回 dict。

    电流从哪个通道读, 就是**双通道/单通道唯一的实质区别**:
        双通道  smub.measure.i()   —— B 路串联在回路里当电流表
        单通道  smua.measure.i()   —— 源电压的那个通道自己读电流
    """
    smu_write(smu, 'smua.source.levelv = %.9e' % v_set)
    time.sleep(a.settle)

    ch = 'smua' if a.single else 'smub'
    vs, iss, ri = [], [], None
    for _ in range(a.n):
        # 回读电压: **这就是该点的真值** (用户口径)。设定值另存一列供对照 ——
        # 线损/源内阻会让两者不等, 报告里写 `I(V)` 时 V 必须是回读的那个。
        vs.append(float(smu.query('print(smua.measure.v())')))
        iss.append(float(smu.query('print(%s.measure.i())' % ch)))
        # 电流量程每点都记 —— 回读精度随档位变 (1 nA 档 240 fA / 10 nA 档 3 pA),
        # 进不确定度预算要用。
        # **电压量程不记**: 它由设定的 V 唯一决定、全程不变, 每点查一次只是白花
        # 一次 socket 往返 (2026-09-21 实测每次 query 只约 2 ms, 省不出多少,
        # 但也没必要查)。整段只需查一次, 见 main() 里扫描之后那一行。
        ri = float(smu.query('print(%s.measure.rangei)' % ch))
    return dict(v_read=sum(vs) / len(vs), i=sum(iss) / len(iss),
                i_range=ri, n=len(vs))


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(
        description="光电探测器 I-V 曲线: A 路源电压, B 路测电流")
    ap.add_argument('--visa', required=True,
                    help='2636B 资源串, 如 TCPIP0::192.168.0.2::5025::SOCKET')
    ap.add_argument('--v-start', type=float, default=-2.00, help='起点电压 V (默认 -2.00)')
    ap.add_argument('--v-stop', type=float, default=0.00, help='终点电压 V (默认 0.00)')
    ap.add_argument('--v-step', type=float, default=0.10, help='步长 V (默认 0.10)')
    ap.add_argument('--oneway', action='store_true',
                    help='只走单程 (默认往返: 去程 + 回程, 用来看迟滞)')
    ap.add_argument('--n', type=int, default=1,
                    help='每点测量次数, 取均值 (默认 1 = 单次快扫)')
    ap.add_argument('--nplc', type=float, default=1.0,
                    help='每路积分时间 PLC (默认 1 = 20ms; 弱信号可调大到 10)')
    ap.add_argument('--autozero', type=float, default=1.0,
                    help='autozero (默认 1; 2 = 每次测量都自校, 更慢更准)')
    ap.add_argument('--settle', type=float, default=0.2,
                    help='换点后等源稳定的秒数 (默认 0.2)')
    ap.add_argument('--delay', type=float, default=0.0,
                    help='仪器的 measure.delay, 单位 s。默认 0 = 不插延时。'
                         '-1 = 让仪器自动算 —— 实测流测量会被算成 ~900 ms/点, '
                         '41 点白花 37 s, 且与读数无关。除非发现读数跳变否则别改')
    ap.add_argument('--limiti', type=float, default=1e-3,
                    help='★ A 路(电压源)的限流, 单位 A。默认 1 mA —— '
                         '光电探测器的典型安全值。这是唯一在故障时保护被测件的设置')
    ap.add_argument('--vlimit-b', type=float, default=2.0,
                    help='B 路(电流表)的合规电压 V, 默认 2。只是兜底, 不是工作点')
    ap.add_argument('--single', action='store_true',
                    help='单通道模式: 只用 A 路, 它自己"源电压 + 测电流"。'
                         '接线只需 A.HI -> 探测器 -> A.LO 两根线, 不用 B 路。'
                         '默认是双通道 (A 源电压, B 串联当电流表)')
    ap.add_argument('-o', '--out', default='data/iv_curve.csv',
                    help='输出 CSV (默认 data/iv_curve.csv)')
    ap.add_argument('--dry', action='store_true', help='只连接 + 配置 + 打印, 不扫描')
    a = ap.parse_args()

    if a.n < 1:
        sys.exit("--n 至少要 1 (否则取均值时除零)")
    if a.v_step <= 0:
        sys.exit("--v-step 必须为正")
    if abs(a.v_stop - a.v_start) < a.v_step / 2:
        sys.exit("起点与终点差不足一个步长, 一个点都没有")

    pts = build_points(a.v_start, a.v_stop, a.v_step, a.oneway)
    print("=" * 78)
    print("光电探测器 I-V 曲线   2636B: A 路源电压 / B 路测电流")
    print("=" * 78)
    print("  点表     %+.2f V → %+.2f V, 步长 %.3f V, %s, 共 %d 点"
          % (a.v_start, a.v_stop, a.v_step,
             "单程" if a.oneway else "往返(含迟滞对照)", len(pts)))
    print("  每点     %d 次取均值, NPLC %g, autozero %g, 稳定延时 %g s"
          % (a.n, a.nplc, a.autozero, a.settle))
    print("  ★ 限流   A 路 %.4g A   (故障时的唯一保护)" % a.limiti)
    if a.single:
        print("  接线     单通道: A.HI → 探测器 → A.LO  (两根线; B 路不用)")
    else:
        print("  接线     双通道: A.HI → 探测器 → B.HI, B.LO → A.LO")
        print("  B 路合规 %.4g V" % a.vlimit_b)
    # 耗时估算 —— 系数是 2026-09-21 两轮实跑反解出来的, 不是拍的:
    #   默认 (settle 0.2 / n 1 / nplc 1 / delay 0) 实测 27.5 s / 41 点
    #   delay=-1                                  实测 58.5 s / 41 点
    # 构成: settle + 约 0.35 s (换压后源内部稳定 + 两次 measure) + NPLC 项。
    # ⚠️ 曾经写下"主要开销是 socket 往返 × 0.4 s" —— 那是**错的**, 实测
    #    每次 query 只约 2 ms; 真凶是 measure.delay 的出厂自动值 900 ms。
    print("  预计耗时 约 %.0f s"
          % (len(pts) * (a.settle + 0.35 + a.n * (0.05 + a.nplc * 0.04)
                         + (0.9 if a.delay < 0 else 0.0)) + 2))
    print()

    smu = smu_open(a.visa)
    rows = []
    t0 = time.time()
    try:
        setup(smu, a)
        if a.dry:
            print("  --dry: 配置完成, 未开输出、未扫描。")
            return
        if not a.single:
            smu_write(smu, 'smub.source.output = smub.OUTPUT_ON')  # 电流表先就绪
        smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')      # 再上电压
        print("[扫描]")
        print("  %3s %4s %10s %11s %9s %14s %12s"
              % ("#", "方向", "设定V", "回读V", "偏差mV", "电流A", "电流nA"))
        print("  " + "-" * 72)
        for k, (v, leg) in enumerate(pts, 1):
            d = read_point(smu, v, a)
            dv = (d['v_read'] - v) * 1e3
            rows.append(dict(idx=k, leg=leg, v_set_V=v, v_read_V=d['v_read'],
                             dv_mV=dv, i_A=d['i'], i_nA=d['i'] * 1e9,
                             v_range_V=None, i_range_A=d['i_range'],
                             t_s=time.time() - t0))
            print("  %3d %4s %10.4f %11.6f %+9.3f %14.6e %12.6f"
                  % (k, "去" if leg == 'fwd' else "回", v, d['v_read'],
                     dv, d['i'], d['i'] * 1e9))
        # 电压量程整段查一次就够 (设定电压唯一决定它), 回填给所有行
        vrange = float(smu.query('print(smua.measure.rangev)'))
        for r in rows:
            r['v_range_V'] = vrange
        smu_check_errors(smu, "扫描")
    finally:
        # 无论正常结束、异常还是 Ctrl-C —— 都要把输出关掉。
        # 留着电压在被测件上是最不能接受的状态。
        print()
        print("[关输出]")
        try:
            smu_off(smu)
        except Exception as e:                                  # noqa: BLE001
            print("  ⚠️ 关输出时出错: %s —— 请手动确认源表面板" % e)

    if not rows:
        sys.exit("没采到任何点")

    # ---------------- 落盘 ----------------
    # 表头纯 ASCII, 见文件头「数据」一节
    cols = ['idx', 'leg', 'v_set_V', 'v_read_V', 'dv_mV', 'i_A', 'i_nA',
            'v_range_V', 'i_range_A', 't_s']
    d = os.path.dirname(a.out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ('%.10e' % r[c] if c in ('i_A', 'i_nA', 'v_range_V',
                                                    'i_range_A')
                            else ('%.9f' % r[c] if c in ('v_read_V', 'dv_mV')
                                  else r[c]))
                        for c in cols})
    print("  已写 %s  (%d 行, 表头 ASCII, Excel/pandas 都能直接读)"
          % (a.out, len(rows)))

    # ---------------- 汇总 ----------------
    fwd = [r for r in rows if r['leg'] == 'fwd']
    rev = [r for r in rows if r['leg'] == 'rev']
    iall = [r['i_A'] for r in rows]
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    print("  总耗时   %.1f s" % (time.time() - t0))
    print("  电流范围 %.4e A  ~  %.4e A" % (min(iall), max(iall)))
    print("  电流量程 %s" % ("、".join(sorted({"%.3g A" % r['i_range_A']
                                             for r in rows}))))
    # 回读与设定的最大偏差 —— 用户要"以回读值为准", 就该知道它偏离多少
    dvmax = max(abs(r['dv_mV']) for r in rows)
    print("  回读−设定 最大 %.3f mV  (回读值是真值, 设定值只是要求)" % dvmax)

    # 0 V 处 = 短路电流。光电探测器的关键工作点之一。
    zero = [r for r in rows if abs(r['v_read_V']) < abs(a.v_step) / 2]
    for r in zero:
        print("  %s到 0 V 附近: 回读 %+.6f V, 短路电流 %.4e A (%.4f nA)"
              % ("去程" if r['leg'] == 'fwd' else "回程",
                 r['v_read_V'], r['i_A'], r['i_nA']))

    # 迟滞 —— 用户选往返就是为了看这个
    if rev:
        # ⚠️ 按**设定值**配对, 不能按回读值 —— 同一电压去/回两次的回读值相差
        #    约 10 µV (2026-09-21 首跑实测: -0.300121 vs -0.300116), 用
        #    round(回读, 6) 当字典键会让 20 个回程点只配上 3 个。设定值是我们
        #    自己下的, 两次必然逐位相同, 才是稳的配对键。
        by = {round(r['v_set_V'], 9): r for r in fwd}
        print()
        print("  迟滞 (同一设定电压, 回程 − 去程):")
        print("  %9s %12s %12s %14s %14s %12s"
              % ("设定V", "去程回读V", "回程回读V", "去程I/A", "回程I/A", "差/A"))
        worst, wv, miss = 0.0, None, 0
        for r in rev:
            f0 = by.get(round(r['v_set_V'], 9))
            if f0 is None:
                miss += 1
                continue
            dlt = r['i_A'] - f0['i_A']
            if abs(dlt) > abs(worst):
                worst, wv = dlt, r['v_set_V']
            print("  %9.4f %12.6f %12.6f %14.6e %14.6e %+12.3e"
                  % (r['v_set_V'], f0['v_read_V'], r['v_read_V'],
                     f0['i_A'], r['i_A'], dlt))
        if miss:
            print("  ⚠️ 有 %d 个回程点没配上对应的去程点" % miss)
        if wv is not None:
            print("  → 最大迟滞 %+.3e A, 出现在设定 %+.4f V" % (worst, wv))


if __name__ == '__main__':
    main()
