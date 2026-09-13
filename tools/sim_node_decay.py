# -*- coding: utf-8 -*-
"""
仿真「100MΩ 电阻输入端节点」的电压波形 —— 即昨天示波器探头看到的那个信号。

场景
----
ADG1219 单刀双掷，`EN=0` 时两路都断。节点(开关输出端 = 100MΩ 的一端)上挂着
寄生电容 C_p：

    接通 +5V : 经开关的通态电阻 Ron(~100Ω) 给 C_p 充电
               tau_rise = Ron*C_p ~ 1ns  ->  上升沿"正常"(看着就是阶跃)
    断开(0)  : 没有任何主动驱动, C_p 上的电荷只能经 R=100MΩ 泄放到虚地
               V_node(t) = 5V * exp(-t/tau),  tau = R*C_p
               -> 下降沿"指数衰减"

**关键**: 示波器探头自己就有 ~10pF, 它并在节点上, 直接变成 C_p 的一部分。
所以示波器看到的 tau 主要是**探头造出来的**, 不是板子的性质。

    C_p = 10pF (仅探头)          -> tau = 1.00 ms   <- 昨天看到的量级
    C_p = 1pF  (仅板子)          -> tau = 100 us   <- 快 10 倍, 看着像阶跃
    C_p = 11pF (板子+探头)       -> tau = 1.10 ms

这个仿真的用途: 换算出"板子真实的 C_p 是多少"以及"注入多少电荷"。

用法: python tools/sim_node_decay.py
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

R_OHM = 100e6       # 限流电阻
V_REF = 5.0         # 参考电压 (节点被驱动到的电压)
RON   = 100.0       # ADG1219 通态电阻 (欧姆, 数据手册量级)
Q_PER_CODE = 1.5546e-14

# 各情形: (C_p, 标签, 是否高亮)
CASES = [
    (10e-12, "C_p = 10 pF  （示波器探头自己）", True),
    (11e-12, "C_p = 11 pF  （板子 1pF + 探头）", True),
    (1e-12,  "C_p = 1 pF   （只有板子）",        True),
    (20e-12, "C_p = 20 pF  （1× 探头量级）",     False),
    (3e-12,  "C_p = 3 pF   （板子实测估的）",     False),
]


def v_node(t, cp, falling=True):
    """节点电压。falling: 开关断开后的泄放; 否则是接通时的充电。"""
    if falling:
        return V_REF * np.exp(-t / (R_OHM * cp))
    return V_REF * (1.0 - np.exp(-t / (RON * cp)))


def main():
    fig, ax = plt.subplots(1, 2, figsize=(14, 5.6))

    # ---- 左: 断开后的泄放 (示波器昨天看到的下降沿) ----
    t = np.linspace(0, 4e-3, 4000)          # 0~4 ms
    for cp, lab, hl in CASES:
        y = v_node(t, cp)
        ax[0].plot(t * 1e3, y, lw=2.2 if hl else 1.2,
                   ls="-" if hl else "--", alpha=1.0 if hl else 0.55,
                   label="%s   tau=%.0f us" % (lab, R_OHM * cp * 1e6))
    ax[0].axvline(1.0, color="k", ls=":", lw=1)
    ax[0].text(1.03, 4.6, "tau=1ms\n(探头造出来的)", fontsize=8)
    ax[0].set_xlabel("断开后时间 (ms)")
    ax[0].set_ylabel("100MΩ 输入端的节点电压 (V)")
    ax[0].set_title("断开(0)后的泄放 —— 示波器看到的\"指数衰减\"")
    ax[0].grid(alpha=.3); ax[0].legend(fontsize=8, loc="upper right")

    # ---- 右: 接通时的上升沿 (放大到 ns 尺度) ----
    t2 = np.linspace(0, 20e-9, 2000)        # 0~20 ns
    for cp, lab, hl in CASES:
        ax[1].plot(t2 * 1e9, v_node(t2, cp, falling=False),
                   lw=2.2 if hl else 1.2, ls="-" if hl else "--",
                   alpha=1.0 if hl else 0.55,
                   label="C_p = %.0f pF  tau=%.2f ns" % (cp * 1e12, RON * cp * 1e9))
    ax[1].set_xlabel("接通后时间 (ns)")
    ax[1].set_ylabel("节点电压 (V)")
    ax[1].set_title("接通(+5V)时的上升沿 —— 纳秒尺度, \"正常\"")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8, loc="lower right")

    fig.suptitle("ADG1219 输出节点: 0 <-> +5V 切换时, 100MΩ 输入端的电压 "
                 "(仿真)", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig("node_decay_sim.png", dpi=150)

    # ---- 数值表 ----
    print("=" * 74)
    print("节点下降沿 (断开后经 100MΩ 泄放) —— tau 与总注入电荷")
    print("=" * 74)
    print("%-34s %10s %12s %12s" % ("情形", "tau", "总电荷 Q", "折合码"))
    print("-" * 74)
    for cp, lab, _ in CASES:
        tau = R_OHM * cp
        Q = cp * V_REF
        print("%-34s %8.0f us %9.1f pC %11.0f" % (lab, tau * 1e6, Q * 1e12, Q / Q_PER_CODE))

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print("  * 上升沿: tau_rise = Ron*C_p ~ 1ns —— 任何 C_p 下都是\"阶跃\",")
    print("    与示波器观察的\"上升沿正常\"一致。")
    print("  * 下降沿: tau_fall = R*C_p。探头 10pF -> 1.00ms, 正好是 ms 量级,")
    print("    在示波器上就是一条看得清的指数尾巴。")
    print("  * 若只有板子的寄生电容(~1pF), tau = 100us, 在 ms 挡上几乎与阶跃无异。")
    print("  => 昨天示波器看到的指数衰减, **主要成分是探头自己的电容**。")
    print()
    print("  但板子自己的 C_p 仍有贡献: 它决定真正注入积分器的电荷 Q = C_p*5V,")
    print("  这才是会污染\"纯积分\"测量的那一项。要定量就得上位机测(见")
    print("  tools/measure_transient.py), 示波器测不了这件事。")
    print()
    print("  已存 node_decay_sim.png")


if __name__ == "__main__":
    main()
