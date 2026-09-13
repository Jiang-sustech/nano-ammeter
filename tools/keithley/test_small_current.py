"""
阶段 2：安全的小电流输出测试。

=====================================================================
上电前请先读这段
=====================================================================
默认参数是"先空载自测"用的。**如果被测电路已经接在 2636B 的输出上，
请先确认它的输入能承受 COMPLIANCE_V 的电压**。本脚本默认把它压在 2.0 V。

最关键的一条：输出一律先关、再改限压、最后才开。序列里没有任何一步
会出现"从 0 直接跳到目标电流"。

跑完无论如何都会关输出（try/finally 保证）。

用法：
    python test_small_current.py                  # 用默认的安全小电流序列
    python test_small_current.py --dry-run        # 只打印将要做什么，不连仪器
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from instrument import Keithley2636B, Keithley2636BError  # noqa: E402

# =====================================================================
# 安全参数 —— 改之前想清楚
# =====================================================================

# 电压合规上限。被测板输入级是 ADA4530-1 的高阻输入，务必保持低值。
COMPLIANCE_V = 2.0

# 每个点的稳定等待时间。pA 级需要更久让积分器建立。
SETTLE_S = 2.0

# 扫描序列。刻意从小到大、含正负号，且绝对不超过 10 nA。
# 这是"验证链路"用的，不是标定序列——标定序列等阶段 4 再说。
CURRENTS_A = [
    0.0,
    1e-12,      # 1 pA
    -1e-12,
    10e-12,     # 10 pA
    -10e-12,
    100e-12,    # 100 pA
    -100e-12,
    1e-9,       # 1 nA
    -1e-9,
]

# 硬性天花板：无论上面怎么改，超过这个值就直接拒绝执行。
SAFETY_CEILING_A = 50e-9


def human(a: float) -> str:
    """把安培数写成 pA / nA / µA，方便肉眼核对。"""
    if a == 0:
        return "0 A"
    # 从大到小匹配，第一个装得下的就是最合适的单位
    for scale, unit in ((1e-3, "mA"), (1e-6, "µA"), (1e-9, "nA"), (1e-12, "pA")):
        if abs(a) >= scale:
            return f"{a / scale:+.4g} {unit}"
    return f"{a:+.4g} A"


def main() -> int:
    ap = argparse.ArgumentParser(description="2636B 安全小电流输出测试")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不连仪器")
    ap.add_argument("--resource", default=None, help="VISA 资源串（默认自动探测）")
    args = ap.parse_args()

    # 天花板检查放在最前面——写错了就直接停，而不是跑到一半才发现
    worst = max(abs(c) for c in CURRENTS_A)
    if worst > SAFETY_CEILING_A:
        print(f"✗ 序列最大电流 {human(worst)} 超过安全天花板 {human(SAFETY_CEILING_A)}，拒绝执行。")
        return 2

    print("=" * 68)
    print("阶段 2：2636B 安全小电流输出测试")
    print("=" * 68)
    print(f"  电压合规  : {COMPLIANCE_V} V")
    print(f"  稳定等待  : {SETTLE_S} s / 点")
    print(f"  点数      : {len(CURRENTS_A)}")
    print(f"  最大电流  : {human(worst)}")
    print(f"  关断限压  : {Keithley2636B.DEFAULT_OFF_LIMIT_V} V（出厂默认是 40 V，已压低）")

    if args.dry_run:
        print("\n--- dry-run：以下是将要执行的动作 ---")
        for c in CURRENTS_A:
            print(f"    关输出 -> 限压 {COMPLIANCE_V} V -> 设电流 {human(c)} -> 开输出 "
                  f"-> 等 {SETTLE_S} s -> 回读实际值")
        print("\n（dry-run，未连接仪器）")
        return 0

    input("\n确认被测电路能承受上述条件后，按回车开始（Ctrl-C 取消）... ")

    rows = []
    try:
        with Keithley2636B(args.resource) as smu:
            print(f"\n已连接：{smu.identify()}")
            smu.initialize()

            print(f"\n{'设定值':>14} {'实际输出电流':>18} {'实际电压':>14} {'源量程':>10}")
            print("-" * 68)

            for setpoint in CURRENTS_A:
                # apply_current 内部保证顺序：关输出 -> 限压 -> 设电平 -> 开输出 -> 稳定
                actual = smu.apply_current(
                    setpoint, compliance_v=COMPLIANCE_V, settle_s=SETTLE_S
                )
                voltage = smu.measure_voltage()
                rng = smu.source_range()

                rows.append((setpoint, actual, voltage))
                print(f"{human(setpoint):>14} {actual:>18.6e} {voltage:>14.6e} {rng:>10.4g}")

                err = smu.read_error()
                if not err.startswith("0"):
                    print(f"    ⚠ 仪器报错：{err}")

            print("-" * 68)
            print("\n注意最后一列和第一列的差别：**标定要用『实际输出电流』那一列，")
            print("不是『设定值』那一列。** 两者在 pA 级会差得比你预期多。")

    except Keithley2636BError as exc:
        print(f"\n✗ {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n\n用户中断。")
        return 130

    finally:
        # 无论上面怎么退出，都要保证输出被关掉
        try:
            with Keithley2636B(args.resource) as smu:
                smu.output_off()
                print("\n输出已关闭。")
        except Exception:  # noqa: BLE001
            print("\n⚠ 无法确认输出已关闭——请手动检查 2636B 前面板的 OUTPUT 指示灯。")

    if rows:
        print(f"\n共 {len(rows)} 点完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
