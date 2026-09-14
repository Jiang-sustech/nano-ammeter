# -*- coding: utf-8 -*-
"""
脉冲输入测试: 验证「脉冲 ≡ 等效直流」

20 组脉冲测试点, 分五组 (A/B/C/D/E), 另配 8 个直流对照点。

    python tools/sweep_pulse.py --list                 # 只打印点表与约束检查
    python tools/sweep_pulse.py --visa ... --check     # 先跑这个: 验证脉冲 TSP
    python tools/sweep_pulse.py --visa ... --port COM8 # 正式采集
    python tools/sweep_pulse.py --visa ... --group A   # 只跑 A 组

判据 (E1/E2/E3 见 docs/脉冲电流实验方案.md §6.2):
    E1  脉冲读数 vs 计算平均 I_avg 的偏差   < 1.5%
    E2  峰值远超直流量程时读数仍正确
    E3  同一 I_avg 下 脉冲读数 vs 直流读数  < 0.5%   <- 核心

⚠️ 关于脉冲怎么产生
------------------
需要 2636B 的**脉冲源模式** (TSP: ConfigPulseIMeasureVSweepLin 一类)。
**没有实机验证过那几个 TSP 函数的参数顺序** —— 先跑 --check。

如果脉冲模式一时调不通, 用 --pc-chop 退回"PC 翻转输出": 它只适合**长脉冲**
(周期 >= 100 ms), 因为 LAN 往返 + Python 调度的抖动在 ms 量级, 占空比误差
= 抖动/周期。10 ms 周期上抖动就有 10% —— 只能做定性验证, 不能卡 0.5% 判据。
"""
import argparse
import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, board_open, board_measure,
                        ReadbackSampler, board_warmup, smu_set)

# ===========================================================================
# 约束
# ===========================================================================
# 三条, 推导见 docs/脉冲电流实验方案.md §2.2 与本次推导:
#
#   ① 电荷回收:  ton 期间灌入的电荷必须在 toff 期间被参考抽走, 参考只有 50 nA
#                   I_peak*t_on <= I_ref*t_off   ⟹   I_avg <= I_ref*(1-D)
#   ② 量程:       I_avg <= 45 nA
#   ③ 纹波撞轨:   每周期电荷纹波 I_avg*T 必须落在摆幅内 (100pF x 8.6V)
#                   I_avg*T <= 860 pC
#
# **在 T = 10 ms 下 ③ 给出 86 nA, 永远比 ①② 松** —— ③ 只在 T >= 20 ms 时才
# 开始起作用 (D3 就是贴着它的点)。合并后:
#
#       I_avg <= min( 45 nA , 50 nA*(1-D) , 860 pC / T )
#
# 即: 占空比 >10% 时"电荷回收"卡死; <=10% 时"量程"卡死。
REF_NA   = 50.0      # 参考电流幅值 (nA)
RANGE_NA = 45.0      # 直流量程上限 (nA)
SWING_PC = 860.0     # 100 pF x 8.6 V 摆幅电荷 (pC)
LONGW_NA = 1.0       # |I| < 1 nA 时固件自动换 10 s 窗口 (measurement_state.c:115)


def _pt(gid, peak_na, ton_ms, period_ms, note):
    duty = (ton_ms / 1000.0) / (period_ms / 1000.0)
    return dict(gid=gid, peak=float(peak_na), ton=float(ton_ms),
                period=float(period_ms), duty=duty,
                avg=float(peak_na) * duty, note=note)


# ===========================================================================
# 20 组测试点
# ===========================================================================
# 组 A  占空比扫描 (峰值固定 40 nA, T=10 ms)      -> 证明 I_avg = I_peak x D
# 组 B  峰值扫描 (占空比 10%, t_on=1 ms)          -> 幅度线性 + 超量程主线
# 组 C  峰值扫描 (占空比 20%, t_on=2 ms)          -> 第二条占空比上的线性, 卡约束角点
# 组 D  周期扫描 (峰值 40 nA, 25%, I_avg 恒 10)   -> 周期无关性 + 折叠周期数
# 组 E  极限状态 (2 组)                            -> E1 大峰值 / E2 越界
POINTS = [
    _pt('A1',  40, 5.00, 10, "占空比扫描 基准"),
    _pt('A2',  40, 2.50, 10, ""),
    _pt('A3',  40, 1.00, 10, ""),
    _pt('A4',  40, 0.50, 10, ""),
    _pt('A5',  40, 0.25, 10, "最小占空比"),

    _pt('B1',   1, 1.00, 10, "微弱脉冲, 落 10s 窗口"),
    _pt('B2',   5, 1.00, 10, "微弱脉冲, 落 10s 窗口"),
    _pt('B3',  10, 1.00, 10, "跨窗口边界"),
    _pt('B4', 100, 1.00, 10, "峰值已 2.2 倍量程"),
    _pt('B5', 200, 1.00, 10, "峰值 4.4 倍量程"),
    _pt('B6', 400, 1.00, 10, "峰值 8.9 倍量程; 回收余量仅 11%"),

    _pt('C1',   5, 2.00, 10, ""),
    _pt('C2',  20, 2.00, 10, ""),
    _pt('C3', 100, 2.00, 10, "峰值 2.2 倍量程"),
    _pt('C4', 200, 2.00, 10, "约束角点: 回收余量 0%"),

    _pt('D1',  40, 2.50, 10, "100 周期/窗口"),
    _pt('D2',  40, 5.00, 20, "50 周期/窗口"),
    _pt('D3',  40, 10.0, 40, "25 周期/窗口; 纹波 400 pC 逼近撞轨"),

    _pt('E1', 1000, 0.40, 10, "极限-大峰值: 峰值 22 倍量程, 应当通过"),
    _pt('E2',  120, 5.00, 10, "极限-越界: 平均 60 nA 超量程, 应当失败"),
]

# 直流对照点 (E3 需要)。20 组脉冲的 I_avg 全部落在这个集合里。
DC_POINTS = [0.1, 0.5, 1.0, 2.0, 4.0, 10.0, 20.0, 40.0]

# 若干点标为"核心" —— 定结论用, 其余是趋势点。--n-core/--n 可分别设重复次数。
CORE = {'A1', 'A2', 'A3', 'B1', 'B4', 'B6', 'C4', 'E1', 'E2'}


def limit_of(p):
    """该点的 I_avg 上限 (nA) 与三个分项"""
    l_bal = REF_NA * (1.0 - p['duty'])          # 电荷回收 (nA)
    l_rng = RANGE_NA                            # 量程 (nA)
    # 纹波撞轨: I_avg*T <= 860 pC  ->  I_avg <= 860 pC / T。
    # T 的单位就是 ms, 而 1 pC/ms = 1e-12 C/1e-3 s = 1e-9 A = 1 nA, 所以直接相除即得 nA
    l_swg = SWING_PC / p['period']              # (nA)
    return min(l_bal, l_rng, l_swg), dict(bal=l_bal, rng=l_rng, swg=l_swg)


def window_of(p):
    return "10 s" if abs(p['avg']) < LONGW_NA else "1 s"


def print_table(points, dc):
    print("=" * 96)
    print("脉冲测试点表  (%d 组)" % len(points))
    print("=" * 96)
    print("%4s %8s %8s %8s %7s %9s %9s %8s %6s  %s"
          % ("#", "峰值nA", "t_on", "周期", "占空比", "I_avg", "上限", "余量",
             "窗口", "说明"))
    print("-" * 96)
    bad = []
    for p in points:
        lim, parts = limit_of(p)
        marg = (lim - p['avg']) / lim * 100 if lim > 0 else -999
        flag = "  ** 越界 **" if marg < 0 else ("  (紧)" if marg < 12 else "")
        why = min(parts, key=lambda k: parts[k])
        if marg < 0:
            bad.append(p['gid'])
        print("%4s %8.4g %7.2fms %7.0fms %6.1f%% %8.4f  %8.4f  %7.1f%% %6s  %s%s"
              % (p['gid'], p['peak'], p['ton'], p['period'], p['duty'] * 100,
                 p['avg'], lim, marg, window_of(p),
                 p['note'] or ("卡:" + why), flag))
    print("-" * 96)
    print("直流对照点: " + "  ".join("%.4g" % v for v in dc) + "  nA")
    print("  每个直流点配到的脉冲组:")
    for v in dc:
        g = [p['gid'] for p in points if abs(p['avg'] - v) < 1e-9]
        print("    %6.4g nA <- %s" % (v, " ".join(g) if g else "** 无 **"))
    if bad:
        print("")
        print("  ** 越界点 (预期失败, 用来标定失效模式): %s **" % " ".join(bad))
        print("     若这些点也通过了, 说明约束推导有误, 要回头查。")
    print("")
    print("  注: B1/B2 落 10 s 窗口, 固件抽点后 1.6 ms/点, 而 t_on=1 ms < 1.6 ms")
    print("      -> 平台在存储波形里不存在, **平均照样准, 但不能做波形反演**。")
    nc = sum(1 for p in points if p['gid'] in CORE)
    print("  核心点 %d 个 (%s), 其余 %d 个为趋势点。"
          % (nc, " ".join(sorted(CORE)), len(points) - nc))
    print("  预估: 核心点 3 次 + 趋势点 1 次 + 8 个直流点 3 次 ≈ 1.5 h;")
    print("        全部 3 次 ≈ 3 h。")


def build_pulse_tsp(i_peak, ton, toff, n_pulse):
    """生成配置脉冲串的 TSP。**参数顺序待与 2636B 参考手册核对** —— 用 --check 验证。

    思路: 用 ConfigPulseIMeasureVSweepLin 把起止电流设成同一个值, 得到等幅脉冲串。
    """
    return (
        "ConfigPulseIMeasureVSweepLin("
        "smua, %.9e, %.9e, %d, %.6f, %.6f, smua.nvbuffer1, 1)"
        % (i_peak, i_peak, n_pulse, ton, toff)
    )


def pulse_start(smu, i_peak, ton, toff, n_pulse):
    prng = smu_set(smu, i_peak)    # 档位要覆盖**峰值** (1uA 峰值 -> 10uA 档)
    print("      脉冲段 峰值 %.4g nA -> 档位 %.4g A" % (i_peak * 1e9, prng))
    smu.write(build_pulse_tsp(i_peak, ton, toff, n_pulse))
    smu.write('InitiatePulseTest(1)')
    return prng


def pc_chop_start(smu, ton, toff, stop_flag):
    """退化方案: PC 直接翻转输出。只在长脉冲下可用 (见文件头)。"""
    import threading

    def loop():
        while not stop_flag['stop']:
            smu.write('smua.source.output = smu.OUTPUT_ON')
            time.sleep(ton)
            smu.write('smua.source.output = smu.OUTPUT_OFF')
            time.sleep(toff)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def measure_once(ser, smu, sample_readback=True):
    """测一次装置读数。

    sample_readback 只在**直流**段为 True。脉冲段必须关掉, 两个原因:
      ① 2600B 在脉冲模式下**只在 ton 末尾测量** —— 它的回读是**峰值**, 不是平均,
         拿来当"实际输出"是错的 (真值应当由 I_peak x 占空比 算);
      ② 我们自己额外发 measure.i() 可能干扰脉冲时序。
    """
    smp = None
    if sample_readback:
        smp = ReadbackSampler(smu)
        smp.start()
    d = board_measure(ser)
    rb = smp.stop(10) if smp else []
    return d, (sum(rb) / len(rb) if rb else float('nan'))


def run_point(ser, smu, p, kind, n, stop_flag, rows, pc_chop=False):
    """跑一组 (pulse 或 dc)。返回装置读数列表 (nA)。"""
    dev, rb = [], []
    if kind == 'pulse':
        ton, toff = p['ton'] / 1000.0, (p['period'] - p['ton']) / 1000.0
        if pc_chop:
            stop_flag['stop'] = False
            pc_chop_start(smu, ton, toff, stop_flag)
            print("      (PC 翻转 ton=%.0fms toff=%.0fms —— 占空比精度受限)"
                  % (ton * 1e3, toff * 1e3))
            time.sleep(1.0)
        else:
            smu.write('smua.source.output = smu.OUTPUT_ON')
            pulse_start(smu, p['peak'] * 1e-9, ton, toff, 100000)
            time.sleep(0.3)
    else:
        smu.write('smua.source.output = smu.OUTPUT_ON')
        rng = smu_set(smu, p['avg'] * 1e-9)
        print("      直流段 %.4g nA -> 档位 %.4g A" % (p['avg'], rng))
        time.sleep(2.0)

    # 预热: 空跑一次丢弃 —— 每批第一次的坏率特别高 (见 cal_sweep 的 board_warmup)
    board_warmup(ser)

    tries = 0
    while (len(dev) < n) and (tries < n * 3):
        tries += 1
        d, r_avg = measure_once(ser, smu, sample_readback=(kind == 'dc'))
        if d is None:
            continue
        if d.get('timeout'):    # 坏点补测
            print("      (丢弃一次 TIMEOUT)")
            continue
        dev.append(float(d['i']))
        rb.append(r_avg)
        rows.append(dict(gid=p['gid'], kind=kind, peak_nA=p['peak'],
                         ton_ms=p['ton'], period_ms=p['period'],
                         duty=p['duty'], avg_nA=p['avg'], rep=len(dev),
                         mode=int(d['mode']), t_ms=int(d['t']) if d['t'] else 0,
                         dev_nA=float(d['i']), rb_nA=r_avg * 1e9))

    if pc_chop:
        stop_flag['stop'] = True
        time.sleep(0.3)
    # 脉冲串停掉, 再设直流 —— 否则脉冲还在跑, 直流设不进去
    try:
        smu.write('smua.abort()')
    except Exception:
        pass
    time.sleep(0.2)

    if not dev:
        print("      %-5s ** 无数据 **" % kind)
        return []
    print("      %-5s 装置 %+10.5f nA   源表 %+10.5f nA   (%d 次, MODE=%d)"
          % (kind, sum(dev) / len(dev), sum(rb) / len(rb) * 1e9,
             len(dev), rows[-1]['mode']))
    return dev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', default=None)
    ap.add_argument('--port', default=None)
    ap.add_argument('--n', type=int, default=3, help='趋势点的测量次数')
    ap.add_argument('--n-core', type=int, default=3, help='核心点的测量次数')
    ap.add_argument('--group', default=None,
                    help='只跑某些组, 逗号分隔 (如 A,C 或 E1,E2)')
    ap.add_argument('--list', action='store_true', help='只打印点表, 不连硬件')
    ap.add_argument('--check', action='store_true',
                    help='只配置脉冲并打印仪器回读, 不采集 (先跑这个!)')
    ap.add_argument('--pc-chop', action='store_true',
                    help='退回 PC 翻转输出的方案 (只适合周期 >= 100 ms)')
    ap.add_argument('--no-dc', action='store_true', help='不跑直流对照点')
    ap.add_argument('-o', '--out', default='data/sweep_pulse.csv')
    a = ap.parse_args()

    pts = POINTS
    if a.group:
        want = [s.strip().upper() for s in a.group.split(',') if s.strip()]
        pts = [p for p in POINTS
               if p['gid'].upper() in want or p['gid'][0].upper() in want]
        if not pts:
            sys.exit("--group %s 没匹配到任何点" % a.group)

    print("=" * 96)
    print("脉冲输入测试" + ("  [--list]" if a.list else "")
          + ("  [--check 只自检]" if a.check else "")
          + ("  [--pc-chop]" if a.pc_chop else ""))
    print("=" * 96)
    print_table(pts, DC_POINTS if not a.no_dc else [])

    if a.list:
        return

    if a.pc_chop and min(p['period'] for p in pts) < 100:
        print("** 警告: --pc-chop 用于周期 < 100 ms 的点时, 占空比误差达 "
              "抖动/周期 >= 1%%, 不能用来判 E3 的 0.5%% **")
    if not a.visa:
        sys.exit("需要 --visa (或加 --list 只看点表)")

    smu = smu_open(a.visa)

    # ---- --check: 把脉冲配置下去, 让仪器自己回读 ----
    if a.check:
        p = pts[0]
        ton, toff = p['ton'] / 1000.0, (p['period'] - p['ton']) / 1000.0
        cmd = build_pulse_tsp(p['peak'] * 1e-9, ton, toff, 1000)
        print("即将下发 (用第 1 个点 %s):" % p['gid'])
        print("   ", cmd)
        try:
            smu_set(smu, p['peak'] * 1e-9)
            smu.write('smua.source.output = smu.OUTPUT_ON')
            smu.write(cmd)
            print("配置成功。仪器回读:")
            for q in ('print(smua.trigger.source.pulsewidth)',
                      'print(smua.source.leveli)',
                      'print(smua.source.func)'):
                try:
                    print("   %-42s -> %s" % (q, smu.query(q).strip()))
                except Exception as e:
                    print("   %-42s -> ** %s" % (q, e))
            print("")
            print(">>> 现在用示波器/万用表确认波形。对了再跑正式采集。")
            print(">>> 若上面报错, 说明这个 TSP 函数的参数与本机固件版本不符,")
            print(">>> 请按 2636B 参考手册改 build_pulse_tsp() 里的参数顺序。")
        except Exception as e:
            print("** 配置失败: %s" % e)
            print("   -> 手册里确认 ConfigPulseIMeasureVSweepLin 的参数顺序")
        finally:
            smu_off(smu)
        # 注意 return 不能写在 finally 里 —— 那会吞掉 try 里抛出的异常
        return

    ser = board_open(a.port)
    rows = []
    stop_flag = {'stop': False}

    # ---- 按 I_avg 分组交织: 同一平均电流下的脉冲点跑完, 紧跟该点的直流对照 ----
    # 不按"每个脉冲点各跑一次直流"是为了省时间; 不按"全部脉冲跑完再跑全部直流"
    # 是为了**抑制源表漂移** —— 相隔三小时的两次读数比较是不可信的。
    avgs = sorted({p['avg'] for p in pts})
    try:
        for avg in avgs:
            grp = [p for p in pts if abs(p['avg'] - avg) < 1e-9]
            print("")
            print("╔══ I_avg = %.4g nA ══════════════════════════════════════" % avg)
            for p in grp:
                n = a.n_core if p['gid'] in CORE else a.n
                print("║ [%s] 峰值 %.4g nA / t_on %.2f ms / T %.0f ms / D %.1f%%  (n=%d)"
                      % (p['gid'], p['peak'], p['ton'], p['period'],
                         p['duty'] * 100, n))
                run_point(ser, smu, p, 'pulse', n, stop_flag, rows, a.pc_chop)
            if not a.no_dc:
                dc = dict(DC_POINTS=0, gid='D:%g' % avg, peak=avg, ton=0.0,
                          period=0.0, duty=1.0, avg=avg,
                          note='直流对照')
                n = a.n_core if avg in (0.1, 1.0, 40.0) else a.n
                print("║ [直流对照 %.4g nA]  (n=%d)" % (avg, n))
                run_point(ser, smu, dc, 'dc', n, stop_flag, rows)
            print("╚" + "═" * 60)
            smu_off(smu)
    finally:
        stop_flag['stop'] = True
        smu_off(smu)
        ser.close()

    if not rows:
        print("没有任何数据")
        return

    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("\n已写", a.out, "(%d 行)" % len(rows))

    # ---- 判据 E1 / E3 ----
    print("")
    print("=" * 96)
    print("E1: 脉冲读数 vs 计算平均值 I_avg")
    print("=" * 96)
    print("%5s %10s %9s %14s %8s" % ("#", "峰值nA", "I_avg", "装置读数", "偏差"))
    worst1, bad1, seen1 = 0.0, False, 0
    for p in pts:
        v = [r['dev_nA'] for r in rows
             if r['gid'] == p['gid'] and r['kind'] == 'pulse']
        if not v:
            continue
        seen1 += 1
        mu = sum(v) / len(v)
        dd = (mu - p['avg']) / p['avg'] * 100 if p['avg'] else float('nan')
        if math.isfinite(dd):
            worst1 = max(worst1, abs(dd))
        else:
            bad1 = True
        print("%5s %10.4g %9.4f %+13.5f %+7.3f%%%s"
              % (p['gid'], p['peak'], p['avg'], mu, dd,
                 "" if math.isfinite(dd) else "  **无效**"))
    print("-" * 96)
    # 没有数据时 worst 停在 0.0 会**假报通过** —— 必须显式拦住
    if seen1 == 0:
        print("** 没有任何脉冲数据 —— 判据不成立, 不能算通过 **")
    else:
        print("最大 |偏差| = %.3f %%   (判据 < 1.5%%, %d/%d 点有数据)  -> %s"
              % (worst1, seen1, len(pts), "**差值无效**" if bad1 else
                 ("通过" if worst1 < 1.5 else "**未通过**")))

    print("")
    print("=" * 96)
    print("E3 (核心): 同一 I_avg 下, 脉冲 vs 直流")
    print("=" * 96)
    print("%18s %10s %14s %14s %9s" % ("组", "I_avg", "脉冲", "直流", "差"))
    worst3, bad3, seen3 = 0.0, False, 0
    for avg in avgs:
        dv = [r['dev_nA'] for r in rows
              if r['kind'] == 'dc' and abs(r['avg_nA'] - avg) < 1e-9]
        if not dv:
            continue
        dmu = sum(dv) / len(dv)
        for p in [q for q in pts if abs(q['avg'] - avg) < 1e-9]:
            pv = [r['dev_nA'] for r in rows
                  if r['gid'] == p['gid'] and r['kind'] == 'pulse']
            if not pv:
                continue
            pmu = sum(pv) / len(pv)
            dd = (pmu - dmu) / dmu * 100 if dmu else float('nan')
            if math.isfinite(dd):
                worst3 = max(worst3, abs(dd))
                seen3 += 1
            else:
                bad3 = True
            tag = "  <- 峰值 %.0f nA" % p['peak'] if p['peak'] > 45 else ""
            print("%18s %10.4f %+13.5f %+13.5f %+8.3f%%%s%s"
                  % (p['gid'], avg, pmu, dmu, dd,
                     "" if math.isfinite(dd) else "  **无效**", tag))
    print("-" * 96)
    if seen3 == 0:
        print("** 没有可比较的 脉冲/直流 配对 —— 判据不成立, 不能算通过 **")
        if a.no_dc:
            print("   (本次带了 --no-dc, 所以没有直流对照)")
    else:
        print("最大 |差| = %.3f %%   (判据 < 0.5%%, %d 对)  -> %s"
              % (worst3, seen3, "**差值无效**" if bad3 else
                 ("通过" if worst3 < 0.5 else "**未通过**")))
    print("")
    print("注: 标了 '<- 峰值 ... nA' 的行是**峰值超出 ±45 nA 直流量程**的点。")
    print("    这些行若同样落在 0.5% 内, 就是\"脉冲调制不影响准确度\"的最强证据。")
    if a.pc_chop:
        print("")
        print("** 本次用了 --pc-chop: 占空比精度受 PC 调度抖动限制 (约 1-3 ms),")
        print("   这个误差会 1:1 进 E1/E3, 所以不能用来判 0.5% —— 只作定性验证。**")


if __name__ == '__main__':
    main()
