# -*- coding: utf-8 -*-
"""
KIPulse 内省 —— **纯只读, 不开输出, 不改任何状态**(除最后可选的一次 reset)。

    python tools/pulse_introspect.py --visa "TCPIP0::192.168.0.2::5025::SOCKET"

为什么需要它
------------
`pulse_probe.py` 拿到硬证据:`InitiatePulseTest(1)` 抛
    -286  attempt to index field 'start' (a nil value)
即**它要读的配置表里没有 `start`**, 而 `ConfigPulseIMeasureVSweepLin` 却
返回 `true  Pulse 1 configured.`。**"配置"和"执行"这对厂脚本函数没对上。**

那只说明"对不上", 不说明**为什么**。两种可能, 修法完全不同:

  ① 配置函数把参数存进了**别的**变量名 -> 改调用方式即可
  ② 仪器上装的是**另一版** KIPulse -> 得按实际源码改

所以要把仪器上的实际情况读出来:
  * `script.user.catalog()`  —— 装了哪些脚本
  * 那几个候选配置全局的**类型与内容** —— 配置到底存哪了
  * `InitiatePulseTest` / `ConfigPulseIMeasureVSweepLin` 本身的存在性

另外验一条对 `pulse_real.py --gen instr` **很关键**的:
  `trigger.timer[1]` 不受 `smua.reset()` 管辖 (它是**系统级**对象, 不带 smua 前缀),
  所以它的 delay=0.05/count=200 很可能是**上一轮的残留**, 会**静默污染**下一次
  配置。本脚本在 reset 前后各读一次来确认这一点。
"""
import argparse
import sys

import pyvisa

# 候选的配置全局名 —— KIPulse 各版本用过的都列上, 找不到就是名字不同
CANDIDATES = [
    'PulseSweepConfig', 'PulseTestConfig', 'PulseConfig',
    'PulseTrainConfig', 'KIPulseConfig', 'PulseIVConfig',
    'SweepConfig', 'PulseSweepTSPConfig',
]
# 系统级触发对象 —— 判断 reset 管不管得着它们
SYS_OBJS = [
    'trigger.timer[1].delay',
    'trigger.timer[1].count',
    # `trigger.timer[1].start.generate` 已删 —— 该属性不存在, 探它会产生
    # -286 并残留在(跨连接持久的)错误队列里。pulse_probe.py 同样处理。
    'trigger.blender[1].stimulus',
    'trigger.timer[1].EVENT_ID',
]
# smua 作用域内、reset 应当清掉的一些项 (作对照: 证明 reset 确实生效了)
SMU_OBJS = [
    'smua.source.leveli',
    'smua.source.limitv',
    'smua.trigger.source.action',
    'smua.trigger.count',
    'smua.nvbuffer1.appendmode',
    'smua.nvbuffer1.n',
]


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--pairs', action='store_true',
                    help='只枚举触发对象到底有哪些字段 (定位 "no field start")')
    ap.add_argument('--attrs', action='store_true',
                    help='只逐条查属性在不在 (决定低层列表路线能不能走)')
    a = ap.parse_args()

    rm = pyvisa.ResourceManager('@py')
    smu = rm.open_resource(a.visa)
    smu.read_termination = '\n'
    smu.write_termination = '\n'
    smu.timeout = 8000

    def q(expr, label=None):
        """print(<expr>), 失败返回错误串而不是抛 —— 探测本来就会碰到不存在的名字"""
        try:
            v = smu.query('print(%s)' % expr).strip()
        except Exception as e:
            v = "** %s" % str(e).splitlines()[0][:60]
        if label:
            print("  %-44s -> %s" % (label, v))
        return v

    def drain(tag="本脚本"):
        """把错误队列读空。**凡是故意探不存在属性的模式, 收尾都必须调它。**
        错误队列跨连接持久, 留着会被下一轮的 smu_check_errors 当成本次失败。"""
        try:
            n = int(float(smu.query('print(errorqueue.count)')))
            for _ in range(n):
                smu.query('print(errorqueue.next())')
            if n:
                print("  (%s 收尾清掉 %d 条错误 —— 探测本身产生的, 不留给下一轮)"
                      % (tag, n))
        except Exception:
            pass

    def dump_table(name):
        """把表逐项打出来。TSP 里 print(表) 只给地址, 必须自己遍历。"""
        r = q('type(%s)' % name)
        print("  %-40s type=%s" % (name, r))
        if 'table' not in r:
            return
        try:
            out = smu.query(
                'local t = %s  local s = ""  '
                'for k, v in pairs(t) do s = s .. tostring(k) .. " = " '
                '.. tostring(v) .. " | " end  print(s)' % name).strip()
            print("       %s" % (out[:400] or "(空表)"))
        except Exception as e:
            print("       遍历失败: %s" % str(e).splitlines()[0][:60])

    def fields(target):
        """列出 <target> 的字段名与类型 —— `pairs` 对 TSP 属性块有效。

        这是为了回答「`trigger.timer[1].start` 到底存不存在」: 直接读它会报
        `attempt to index field 'start'`, 但**读不到**和**字段不存在**是两件事,
        枚举一遍才分得清。
        """
        try:
            return smu.query(
                'local t = %s  local s = ""  '
                'for k, v in pairs(t) do s = s .. tostring(k) .. "(" '
                '.. type(v) .. ") " end  print(s)' % target).strip()
        except Exception as e:
            return "** %s" % str(e).splitlines()[0][:70]

    if a.attrs:
        # 低层"电平列表 + 定时器"路线 (pulse_real.py --gen instr) 依赖的属性清单。
        # 逐条查 type(): nil = 这台仪器上**不存在** -> 那条路走不通。
        # 这比"读回值是多少"更重要 —— 缺的那个属性会让整条路线作废。
        print("=" * 82)
        print("属性可用性 (只读; nil = 这台仪器上没有)")
        print("=" * 82)
        for grp, names in [
            ('源(直流部分, 已确认可用)', [
                'smua.source.leveli', 'smua.source.limitv', 'smua.source.func',
                'smua.source.output', 'smua.source.rangei', 'smua.source.autorangei',
                'smua.source.offmode', 'smua.source.offfunc', 'smua.source.offlimitv']),
            ('源列表 (低层任意波形靠它)', [
                'smua.trigger.source.action', 'smua.trigger.source.stimulus',
                'smua.trigger.source.listi', 'smua.trigger.source.listv',
                'smua.trigger.source.listfunc', 'smua.trigger.source.pulsewidth',
                'smua.trigger.source.limitv']),
            ('trigger 主体', [
                'smua.trigger.initiate', 'smua.trigger.count', 'smua.trigger.model',
                'smua.trigger.EVENT_ID', 'smua.trigger.measure.stimulus',
                'smua.trigger.measure.action', 'smua.trigger.endpulse.stimulus']),
            ('timer[1] (定时基准)', [
                'trigger.timer[1].delay', 'trigger.timer[1].count',
                'trigger.timer[1].stimulus', 'trigger.timer[1].overrun',
                'trigger.timer[1].start', 'trigger.timer[1].start.generate',
                'trigger.timer[1].start.stimulus', 'trigger.timer[1].EVENT_ID']),
        ]:
            print("")
            print("  [%s]" % grp)
            for e in names:
                t = q('type(%s)' % e)
                mark = "  <<< 缺失" if t == 'nil' else ""
                print("    %-44s %s%s" % (e, t, mark))
        drain("--attrs")
        smu.close()
        return

    if a.pairs:
        print("=" * 82)
        print("触发对象字段枚举 (只读, 只在末尾 reset 一下 timer[1])")
        print("=" * 82)
        print("--- 有哪些字段 ---")
        for tgt in ('trigger.timer[1]', 'trigger.timer', 'smua.trigger',
                    'smua.trigger.source', 'smua.trigger.measure',
                    'trigger.blender[1]', 'smua.source'):
            print("  %-26s : %s" % (tgt, fields(tgt)))

        print("")
        print("--- 直接探 start 这条路 (KIPulse 就死在这) ---")
        for e in ('trigger.timer[1].start',
                  'trigger.timer[1].start.generate',
                  'trigger.timer[1].generate',
                  'trigger.timer[1].stimulus',
                  'trigger.timer[1].overrun',
                  'trigger.timer[1].EVENT_ID'):
            print("  %-40s -> %s" % (e, q(e)))

        print("")
        print("--- timer[1].reset() 之后再看 (有没有属性要 reset 后才出现) ---")
        try:
            smu.write('trigger.timer[1].reset()')
        except Exception as e:
            print("  reset 失败: %s" % e)
        print("  %-26s : %s" % ('trigger.timer[1]', fields('trigger.timer[1]')))
        for e in ('trigger.timer[1].start',
                  'trigger.timer[1].start.generate',
                  'trigger.timer[1].generate'):
            print("  %-40s -> %s" % (e, q(e)))

        print("")
        try:
            n = smu.query('print(errorqueue.count)').strip()
            print("  错误队列: %s 条" % n)
            for _ in range(min(int(float(n)), 8)):
                print("    %s" % smu.query('print(errorqueue.next())').strip())
        except Exception:
            pass
        drain("--pairs")
        smu.close()
        return

    try:
        print("=" * 82)
        print("KIPulse 内省 (只读)")
        print("=" * 82)
        print("型号/固件:")
        q('localnode.model', 'localnode.model')
        q('localnode.version', 'localnode.version')
        q('localnode.serialno', 'localnode.serialno')
        q('localnode.linefreq', 'localnode.linefreq')

        print("")
        print("--- reset 之前 (仪器现在的真实状态) ---")
        for e in SYS_OBJS:
            q(e, e)
        for e in SMU_OBJS:
            q(e, e)

        print("")
        print("--- 装了哪些脚本 ---")
        cat = q('script.user.catalog()')
        try:
            names = smu.query(
                'local t = script.user.catalog()  local s = ""  '
                'for k, v in pairs(t) do s = s .. tostring(k) .. ":" '
                '.. tostring(v) .. " | " end  print(s)').strip()
            print("  目录: %s" % (names[:500] or "(空)"))
        except Exception as e:
            print("  目录遍历失败: %s" % str(e).splitlines()[0][:60])

        print("")
        print("--- 厂脚本函数在不在 (nil = 没加载) ---")
        for f in ('InitiatePulseTest', 'ConfigPulseIMeasureVSweepLin',
                  'ConfigPulseIMeasureV', 'ConfigPulseVMeasureI',
                  'ConfigPulseIMeasureVSweepList', 'WaitComplete',
                  'ConfigPulseIMeasureVSweepLin_Train'):
            q('%s ~= nil' % f, f)

        print("")
        print("--- 配置全局到底存哪了 ---")
        for c in CANDIDATES:
            dump_table(c)

        print("")
        print("--- initiate 那条错误路的现场: 触发模型实际长相 ---")
        for e in ('smua.trigger.source.action', 'smua.trigger.source.stimulus',
                  'smua.trigger.source.listfunc', 'smua.trigger.source.pulsewidth',
                  'smua.trigger.measure.stimulus', 'smua.trigger.count',
                  'smua.trigger.initiate', 'smua.trigger.model'):
            q(e, e)

        print("")
        print("--- reset 之后 (对照) ---")
        smu.write('smua.reset()')
        for e in SYS_OBJS:
            q(e, e)
        for e in SMU_OBJS:
            q(e, e)
        print("")
        print("  判读:")
        print("    SYS_OBJS 里 delay/count **reset 前后不变** -> 确认它不受 reset 管辖,")
        print("      是残留源, 下一次配置前必须显式清掉 (对 --gen instr 同样成立)。")
        print("    SYS_OBJS 若被清零 -> 那 delay=0.05/count=200 就是 KIPulse 自己写的,")
        print("      反倒说明配置函数**确实动过** timer[1]。")

        print("")
        n = q('errorqueue.count')
        if n and n.strip() not in ('0', '0.00000e+00'):
            print("  错误队列有 %s 条:" % n)
            for _ in range(int(float(n)) if n.replace('.', '').isdigit() else 5):
                print("    %s" % q('errorqueue.next()'))
        else:
            print("  错误队列空。")
    finally:
        smu.close()
        # ⚠️ 上面那次 reset() 把 **offlimitv 打回了出厂 40 V** —— 出厂 offmode 是
        # OUTPUT_NORMAL, 即"关输出"之后线上仍挂着一台 0 A 电流源, 其限压 40 V。
        # 对 ADA4530-1 这类高阻输入级是危险的 (见 docs/2636B源表接入.md 第三节)。
        # 所以收尾**必须**再走一遍 smu_open: 它 reset 之后紧接着设安全关断态。
        # 不复用 smu_open 自己拼这几条 —— 那正是那份文档要求不要做的事。
        try:
            from cal_sweep import smu_open, smu_off
            s2 = smu_open(a.visa)
            smu_off(s2)
            s2.close()
            print("")
            print("已恢复安全关断态 (offmode=OUTPUT_NORMAL / offlimitv=2V, 输出关)。")
        except Exception as e:
            print("")
            print("** 恢复安全关断态失败: %s" % e)
            print("** 仪器现在停在出厂态 (offlimitv=40V), 请手动处理再接线!")


if __name__ == '__main__':
    main()
