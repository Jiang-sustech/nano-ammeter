# -*- coding: utf-8 -*-
"""
**只诊断, 不改任何现有文件** —— 分辨「KIPulse 脉冲配置没生效」还是「读早了」。

    python tools/pulse_probe.py --visa "TCPIP0::192.168.0.2::5025::SOCKET"

问题背景
--------
`docs/脉冲电流实验方案.md` §4.1 记着一次失败:

    ConfigPulseIMeasureVSweepLin 返回 `true  Pulse 1 configured.`
    但 trigger.source.action = 0 (未使能)、trigger.count = 1
    手动设成 ENABLE + count=10 之后, InitiatePulseTest(1) 的**缓冲仍是 0 点**
    -> 结论写成"配置没生效", 没再往下查。

**但那个结论和另一个解释完全相容, 而两者导向相反的修法:**

  假说 A  配置真的没生效          -> 要去查触发模型 / 换低层 TSP
  假说 B  配置生效了, 只是**读早了** -> 什么都不用改, 等就行

`InitiatePulseTest()` 大概率是**异步**的: 它把触发跑起来就返回。而那一轮
的检查是**配置完立刻读缓冲** —— 1000 个脉冲 x 10 ms = 10 s 的串才刚刚开头,
缓冲当然是 0 点。**0 点这个观测对 A 和 B 是等价的, 它分不出两者。**

本探针用**轮询缓冲点数**分辨: 配置 -> initiate -> 每 0.5 s 读一次
`smua.nvbuffer1.n`, 一直看到超时。
    点数**从 0 长起来** -> 假说 B (读早了), 配置一直是好的
    点数**从头到尾是 0** -> 假说 A, 再往下查触发模型

顺带把 initiate **前后**的触发模型状态各拍一张快照 —— §4.1 那张快照是
initiate **之前**拍的, 而 KIPulse 很可能把触发模型留到 InitiatePulseTest
里去建, 那么"initiate 前 action=0"本身就是**正常**的, 不是故障证据。

安全
----
输出会真的打开 (默认 40 nA 峰值, limitv 5 V, 关断态由 cal_sweep.smu_open 设好)。
`finally` 里保证 abort + 关输出。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import smu_open, smu_off, smu_write, smu_set, smu_check_errors
from sweep_pulse import build_pulse_tsp

# initiate 前后各拍一张的状态项. 这些是判断"触发有没有被建起来"的全部可见面:
#   source.action     电平列表使能 (KIPulse 走的就是这条)
#   source.stimulus   谁推进列表 —— 应当是 timer[1] 的 EVENT_ID
#   trigger.count     每个 initiate 走多少个点
#   pulsewidth        老 --check 回读是 nil, 说明这个字段 KIPulse 不用
#   timer[1].*        恒定间隔定时器 (低层 TSP 路线 pulse_real --gen instr 用的同一个)
SNAP = [
    'smua.trigger.source.action',
    'smua.trigger.source.stimulus',
    'smua.trigger.source.pulsewidth',
    'smua.trigger.measure.stimulus',
    'smua.trigger.count',
    'trigger.timer[1].delay',
    'trigger.timer[1].count',
    # ⚠️ 这里**曾经**有一行 `trigger.timer[1].start.generate` —— 已删除。
    # 该属性在 2600B 上**不存在**(手册全 919 页 0 命中, 仪器实测 type = nil),
    # 探它会让仪器抛 `-286 attempt to index field 'start'` 并**留在错误队列里**。
    # 而错误队列**跨连接持久**, 所以每跑一次诊断就往上堆一条 —— 2026-09-16
    # 用户就是被这个误导, 以为程序在频繁报 -286。定位使命已完成, 不该再探。
    'smua.nvbuffer1.n',
    'smua.nvbuffer1.appendmode',
    'smua.source.output',
    'smua.source.func',
    'smua.source.leveli',
    'smua.source.rangei',
    'smua.source.limitv',
    'errorqueue.count',
]


def snap(smu, title):
    print("  --- %s ---" % title)
    out = {}
    for q in SNAP:
        try:
            v = smu.query('print(%s)' % q).strip()
        except Exception as e:
            v = "** %s" % str(e).splitlines()[0][:50]
        out[q] = v
        print("    %-36s -> %s" % (q, v))
    return out


def exists(smu, name):
    """厂脚本函数/变量在不在。KIPulse 没加载时 InitiatePulseTest 是 nil。"""
    try:
        return smu.query('print(%s ~= nil)' % name).strip()
    except Exception as e:
        return "** %s" % str(e).splitlines()[0][:40]


# 手册 7-113 官方配方相对现状的三处差异。做单因素拆分用。
FIX_RANGES = 'ranges'       # 关 autorange, 显式固定档
FIX_NPLC = 'nplc'           # NPLC 10 -> 0.01
FIX_AZ = 'autozero'         # autozero 2 -> AUTOZERO_ONCE
ALL_FIXES = (FIX_RANGES, FIX_NPLC, FIX_AZ)


def set_recipe(smu, fixes, i_peak):
    """按 fixes 设置脉冲前的仪器状态, 返回下发的命令, 便于打印对照。

    手册 7-113 页 `InitiatePulseTest()` 的官方配方逐条:
        smua.source.rangev = 5          smua.measure.rangev = 5
        smua.source.rangei = 1          smua.measure.rangei = 1
        smua.measure.nplc = 0.01        smua.measure.autozero = AUTOZERO_ONCE
        smua.nvbuffer1.clear()          smua.nvbuffer1.appendmode = 1
        smua.source.output = OUTPUT_ON
    手册例子 rangei 写 1 A (那是给 5 A 脉冲用的); 我们 40 nA 峰值换小档 ——
    **档位具体值不是重点, "固定不自动"才是**。手册四条前置条件里两条就是
    "源/测 autorange 关掉"。
    """
    cmds = []
    if FIX_RANGES in fixes:
        cmds += ['smua.source.autorangei = smua.AUTORANGE_OFF',
                 'smua.measure.autorangei = smua.AUTORANGE_OFF',
                 'smua.source.rangev = 5',
                 'smua.source.rangei = %.9e' % i_peak,
                 'smua.measure.rangev = 5',
                 'smua.measure.rangei = %.9e' % i_peak]
    else:
        cmds += ['smua.source.autorangei = smua.AUTORANGE_ON',
                 'smua.measure.autorangei = smua.AUTORANGE_ON']
    cmds += ['smua.measure.nplc = 0.01' if FIX_NPLC in fixes
             else 'smua.measure.nplc = 10']
    cmds += ['smua.measure.autozero = smua.AUTOZERO_ONCE' if FIX_AZ in fixes
             else 'smua.measure.autozero = 2']
    cmds += ['smua.source.leveli = 0',
             'smua.nvbuffer1.clear()',
             'smua.nvbuffer1.appendmode = 1',
             'smua.source.output = smua.OUTPUT_ON']
    for c in cmds:
        smu_write(smu, c, ctx=c)
    return cmds


def run_recipe(smu, a, fixes, label):
    """按 fixes 跑一次脉冲串。返回是否成功让缓冲长出点来。"""
    print("=" * 78)
    print("配方测试   %s   fixes = %s"
          % (label, ",".join(fixes) if fixes else "(无 —— 现状)"))
    print("=" * 78)
    ton, toff = a.ton / 1000.0, a.toff / 1000.0
    # ⚠️ 先把错误队列读空。**错误队列是跨连接持久的**, 上一轮探针留下的错会在
    # 这里冒出来冒充本次失败 —— 实测撞到过: `smu_open` 只清"残留应答"不清
    # "残留错误", 于是探 `.start.generate` 产生的那 2 条把 `smua.reset()` 顶崩了。
    try:
        n = int(float(smu.query('print(errorqueue.count)')))
        for _ in range(n):
            smu.query('print(errorqueue.next())')
        if n:
            print("(开场清掉 %d 条残留错误 —— 上一轮探针留下的, 非本次)" % n)
    except Exception:
        pass
    print("峰值 %.4g nA, ton %g ms / toff %g ms, %d 个脉冲 -> 串长 %.2f s"
          % (a.peak, a.ton, a.toff, a.points, a.points * (ton + toff)))
    if a.peak * 1e-9 > a.range_a:
        sys.exit("峰值 %.4g nA 超出给定档位 %.4g A —— 档位必须覆盖峰值"
                 % (a.peak, a.range_a))
    print("")

    for c in set_recipe(smu, fixes, a.range_a):
        print("    %s" % c)
    print("")

    # Config: **必须把 f, msg 读回来** —— 走返回值, 不走错误队列 (见 §4.1 坑②)
    r = smu.query('print(tostring(%s))' % build_pulse_tsp(
        a.peak * 1e-9, ton, toff, a.points))
    print("  Config 返回: %s" % r.strip())

    # InitiatePulseTest **自己也返回 f, msg** (手册 7-113: f2, msg2 = InitiatePulseTest(1))
    # 现状代码用 smu_write 把它丢了 —— 这是本次要改的最后一处。
    try:
        # ⚠️ VISA 超时要按串长放宽 —— smu_open 设的只有 5 s, 而
        # InitiatePulseTest **阻塞整串时长**。串一过 5 s 这个查询必然超时。
        # (sweep_pulse.py 的 pulse_initiate 已修, 这里当时漏了。)
        _old_to = smu.timeout
        smu.timeout = int((a.points * (ton + toff) + 60.0) * 1000)
        t_i = time.perf_counter()
        try:
            r2 = smu.query(
                'local f, m = InitiatePulseTest(1)  '
                'print(tostring(f) .. " | " .. tostring(m))').strip()
        finally:
            smu.timeout = _old_to
        dt_i = time.perf_counter() - t_i
        print("  Initiate 返回: %s" % r2)
        # **它到底阻不阻塞 = 整串时长?** 这条决定了 run_point 能不能"先启动
        # 脉冲再测装置" —— 若阻塞, 等它返回时脉冲已经跑完, 装置测到的是 0。
        print("  Initiate 耗时: %.2f s  (脉冲串设定 %.2f s)  -> %s"
              % (dt_i, a.points * (ton + toff),
                 "**阻塞**" if dt_i > a.points * (ton + toff) * 0.5 else "非阻塞"))
        init_ok = r2.lower().startswith('true')
    except Exception as e:
        print("  Initiate 抛异常: %s" % str(e).splitlines()[0][:120])
        init_ok = False

    print("")
    t0 = time.perf_counter()
    nmax = 0
    while (time.perf_counter() - t0) < a.watch:
        try:
            n = int(float(smu.query('print(smua.nvbuffer1.n)')))
        except Exception:
            time.sleep(a.interval)
            continue
        nmax = max(nmax, n)
        print("    t=%6.2f s   nvbuffer1.n = %d" % (time.perf_counter() - t0, n))
        if n >= a.points:
            break
        time.sleep(a.interval)

    try:
        smu_write(smu, 'smua.abort()')
    except Exception:
        pass
    time.sleep(0.2)
    n_err = smu.query('print(errorqueue.count)').strip()
    print("  错误队列: %s 条" % n_err)
    if n_err not in ('0', '0.00000e+00'):
        for _ in range(min(int(float(n_err)), 5)):
            print("    %s" % smu.query('print(errorqueue.next())').strip())

    if nmax > 0:
        try:
            v = smu.query('print(smua.nvbuffer1[1].v)').strip()
            print("  缓冲首点电压读数: %s V" % v)
        except Exception:
            pass
    print("")
    print("  >>> 结论: 缓冲点数 %d / %d  %s"
          % (nmax, a.points, "**成功**" if nmax > 0 else "**失败 (0 点)**"))
    return nmax > 0


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--peak', type=float, default=40.0, help='峰值 (nA)')
    ap.add_argument('--ton', type=float, default=5.0, help='脉宽 (ms)')
    ap.add_argument('--toff', type=float, default=5.0, help='间隔 (ms)')
    ap.add_argument('--points', type=int, default=1000, help='脉冲个数')
    ap.add_argument('--watch', type=float, default=25.0,
                    help='轮询总时长 (s); 要 >= points*(ton+toff)/1000 才看得到跑完')
    ap.add_argument('--interval', type=float, default=0.5, help='轮询间隔 (s)')
    ap.add_argument('--recipe', action='store_true',
                    help='按手册 7-113 配方测 InitiatePulseTest (而非只拍状态快照)')
    ap.add_argument('--variant', default='manual', choices=['manual', 'current'],
                    help='manual = 手册配方 (固定档 + NPLC 0.01 + autozero ONCE); '
                         'current = 现状设置 (autorange ON + NPLC 10) 作对照')
    ap.add_argument('--bisect', action='store_true',
                    help='单因素拆分: 逐个单独应用三处改动, 找出哪处是必需的')
    ap.add_argument('--only', default=None,
                    help='只应用指定改动, 逗号分隔: ranges,nplc,autozero')
    ap.add_argument('--range-a', type=float, default=100e-9,
                    help='固定档位 (A); 手册要求先关 autorange, 必须覆盖峰值')
    a = ap.parse_args()

    iavg = a.peak * a.ton / (a.ton + a.toff)
    total = a.points * (a.ton + a.toff) / 1000.0
    print("=" * 78)
    print("KIPulse 脉冲配置探针")
    print("=" * 78)
    print("峰值 %.4g nA, ton %g ms / toff %g ms -> 周期 %g ms, I_avg = %.4g nA"
          % (a.peak, a.ton, a.toff, a.ton + a.toff, iavg))
    print("脉冲 %d 个 -> 串长 %.2f s;  轮询 %.0f s, 每 %.1f s 一次"
          % (a.points, total, a.watch, a.interval))
    if a.watch < total:
        print("** 警告: 轮询时长 < 串长 —— 看不到跑完, 只能看到它在长 **")
    print("")
    print("判别: 点数从 0 长起来 = **读早了**(假说B), 一直是 0 = **真没跑**(假说A)")
    print("")

    smu = smu_open(a.visa)

    if a.recipe:
        if a.only:
            want = tuple(s.strip() for s in a.only.split(',') if s.strip())
            bad = [w for w in want if w not in ALL_FIXES]
            if bad:
                sys.exit("--only 里有未知项 %s, 可选: %s"
                         % (bad, ",".join(ALL_FIXES)))
            runs = [("--only " + a.only, want)]
        elif a.bisect:
            # 单因素拆分: 先跑现状(基线), 再逐个只应用一处改动。
            # 目的是找出**哪一处是必需的** —— 三处都改当然能跑通, 但那不能说明
            # 每处都必要; 尤其 autorange 是 smu_open 故意开着的(注释里有理由),
            # 若它其实不必要, 就不该动它。
            runs = [("基线(现状)", ())]
            runs += [("只改 " + f, (f,)) for f in ALL_FIXES]
            runs += [("只留 " + f, tuple(x for x in ALL_FIXES if x != f))
                     for f in ALL_FIXES]
            runs += [("手册全配方", ALL_FIXES)]
        elif a.variant == 'manual':
            runs = [("手册全配方", ALL_FIXES)]
        else:
            runs = [("现状(对照组)", ())]

        results = []
        try:
            for label, fixes in runs:
                try:
                    ok = run_recipe(smu, a, fixes, label)
                except Exception as e:
                    print("  ** 该组异常: %s" % str(e).splitlines()[0][:100])
                    ok = False
                results.append((label, ok))
                print("")
        finally:
            # 收尾走 cal_sweep 的 smu_off —— 不自己拼命令 (见 docs/2636B源表接入.md)
            try:
                smu_write(smu, 'smua.abort()')
            except Exception:
                pass
            smu_off(smu)
            smu.close()
        print("已关输出。")
        if len(results) > 1:
            print("")
            print("=" * 78)
            print("汇总 (%d 组, 每组 100 个脉冲 / 1 s)" % len(results))
            print("=" * 78)
            for label, ok in results:
                print("  %-22s %s" % (label, "成功" if ok else "**失败 (0 点)**"))
            print("")
            print("  读法: 只有单独一处改动就让结果翻过来的, 才是**必需**的那处;")
            print("        三处全改能成功但单改都不行, 说明是**联合条件**。")
        sys.exit(0 if results and results[-1][1] else 1)

    try:
        print("厂脚本函数在不在:")
        for f in ('InitiatePulseTest', 'ConfigPulseIMeasureVSweepLin',
                  'WaitComplete', 'PulseSweepConfig', 'PulseTestConfig'):
            print("    %-28s -> nil? %s" % (f, exists(smu, f)))
        print("")

        smu_set(smu, a.peak * 1e-9)     # 档位覆盖峰值
        print("档位: %.4g A (要覆盖峰值 %.4g nA)" % (
            float(smu.query('print(smua.source.rangei)')), a.peak))
        print("")

        # appendmode 必须在配置**之前**开 —— 厂脚本会检查它 (§4.1 坑①)
        smu_write(smu, 'smua.nvbuffer1.appendmode = 1')
        r = smu.query('print(%s)' % build_pulse_tsp(
            a.peak * 1e-9, a.ton / 1000.0, a.toff / 1000.0, a.points))
        print("厂脚本返回: %s" % r.strip())
        print("")

        before = snap(smu, "initiate 之前")

        # ---- initiate, 然后只管等 ----
        print("")
        print(">>> InitiatePulseTest(1) ...")
        t0 = time.perf_counter()
        try:
            smu_write(smu, 'InitiatePulseTest(1)', ctx='InitiatePulseTest')
        except Exception as e:
            print("** initiate 抛异常: %s" % e)
        t_init = time.perf_counter() - t0
        print("    返回耗时 %.3f s%s" % (t_init, "   <- **是阻塞的**" if t_init > 1
                                       else "   <- 立刻返回, 异步"))
        print("")

        hist = []
        while True:
            el = time.perf_counter() - t0
            if el > a.watch:
                break
            try:
                n = int(float(smu.query('print(smua.nvbuffer1.n)')))
            except Exception as e:
                print("    查 n 失败: %s" % e)
                break
            hist.append((el, n))
            print("    t=%6.2f s   nvbuffer1.n = %d" % (el, n))
            if n >= a.points:
                print("    -> **已跑满 %d 点**" % a.points)
                break
            time.sleep(a.interval)

        # ---- 收尾 ----
        print("")
        smu_write(smu, 'smua.abort()')
        time.sleep(0.3)
        after = snap(smu, "abort 之后")

        # ---- 判别 ----
        print("")
        print("=" * 78)
        print("判别结果")
        print("=" * 78)
        nmax = max((n for _, n in hist), default=-1)
        if nmax > 0:
            grew = [t for t, n in hist if n > 0]
            print("** 假说 B 成立: 配置是好的, 之前读到 0 点是因为读早了。**")
            print("   缓冲点数从 0 长到 %d, 首次非零出现在 t=%.2f s。"
                  % (nmax, grew[0] if grew else float('nan')))
            print("   -> §4.1 那条「配置没生效」的结论要更正: 它是**异步读早了**,")
            print("      不是触发模型没建起来。sweep_pulse 只要等缓冲满再读就行。")
        elif nmax == 0:
            print("** 假说 A 成立: 触发确实没跑起来。**")
            print("   轮询 %.1f s 内 nvbuffer1.n 始终是 0, 而串长只有 %.2f s ——")
            print("   时间上足够跑完, 所以不是读早了。")
            print("   下一步查:")
            print("     ① initiate 前后 trigger.source.action / stimulus 的**差别**")
            print("        (上面两张快照对比: 若 initiate 也没让 action 变成 1,")
            print("         说明 KIPulse 没接上触发模型 -> 改走低层 TSP)")
            print("     ② 若 initiate 前 action=0 而 initiate 后变 1 -> 说明快照拍早了,")
            print("        §4.1 拿 initiate 前的 action=0 当故障证据是**误判**。")
        else:
            print("** 没有取到任何点数 —— 查询就失败了, 先解决通信。**")

        # 首尾快照的关键差异
        print("")
        print("initiate 前后差异 (只看变化项):")
        any_diff = False
        for q in SNAP:
            b, af = before.get(q), after.get(q)
            if b != af:
                any_diff = True
                print("    %-36s %s  ->  %s" % (q, b, af))
        if not any_diff:
            print("    (无 —— 触发模型在 initiate 前后完全相同)")
    finally:
        try:
            smu_write(smu, 'smua.abort()')
        except Exception:
            pass
        # **收尾必须把错误队列读空** —— 队列跨连接持久, 留着会污染下一轮
        # (下一轮的 smu_check_errors 会把它当成本次的失败, 直接抛异常)。
        try:
            n = int(float(smu.query('print(errorqueue.count)')))
            for _ in range(n):
                smu.query('print(errorqueue.next())')
            if n:
                print("(收尾清掉 %d 条错误 —— 不留给下一轮)" % n)
        except Exception:
            pass
        smu_off(smu)
        smu.close()
        print("")
        print("已关输出。")


if __name__ == '__main__':
    main()
