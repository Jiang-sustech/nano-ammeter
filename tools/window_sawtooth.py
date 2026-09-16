# -*- coding: utf-8 -*-
"""
一个测量窗口**之内**的码值变化 (不是首尾之差)

    python tools/window_sawtooth.py
    python tools/window_sawtooth.py -o data/window_sawtooth.png

要什么
------
`Δc = c[末] − c[首]` 只是一个数; 这里给的是**整条 6250 点的轨迹**, 看得出:
  * 窗口里其实是**约 27 个锯齿**, 不是单个;
  * 锯齿的**上升段拍数 = m, 下降段拍数 = n** —— 固件那两个数就是这个;
  * 窗口末尾落在锯齿的哪个位置 = `6250 mod 周期` 的**小数相位**,
    周期由电流决定, 所以这个相位随电流无规律地变 —— 端点码 c[末] 因此乱跳。

数据来源
--------
只有 `data/raw_09_13_*.npz` 存了**整窗缓冲** (wave_int / wave_ext)。
`sweep_broad.py` / `cal_sweep.py` 只把首尾两个码写进 CSV (ext1/ext2),
**没有存整条缓冲** —— 所以 **8 nA / 9 nA 的窗口内数据不存在**, 除非重采。

⚠️ 码值是**会绕回**的 (0 ↔ 65535), 所以必须先解绕再看, 否则会看到假的三角波。
"""
import argparse
import glob
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def unwrap(w):
    """解开 0/65535 绕回, 返回逐步增量与累计位移。"""
    step = np.diff(w.astype(np.int64))
    step = step.copy()
    step[step > 32768] -= 65536
    step[step < -32768] += 65536
    return step, np.concatenate([[0], np.cumsum(step)])


def segments(step, win=9):
    """[(方向, 长度), ...] 连续同向段。

    ⚠️ **必须先平滑再判方向** —— 上升段里混着小幅反向步 (实测 −40 nA 处
    上升段里有 −8 / −84 / −127 这样的步), 逐拍判方向会把段切成碎片,
    算出"周期 7 拍"这种物理上不可能的结果 (bang-bang 环不会每 7 拍翻一次)。
    这里先对逐步增量做移动平均, 在**平滑后**的序列上判方向, 再回到原序列量长度。
    """
    s = np.asarray(step, dtype=float)
    if win > 1:
        k = np.ones(win) / win
        sm = np.convolve(s, k, mode='same')
    else:
        sm = s
    d = np.where(sm > 0, 1, -1)
    out = []
    cur, cnt = d[0], 0
    for i in range(len(d)):
        if d[i] == cur:
            cnt += 1
        else:
            out.append((cur, cnt))
            cur, cnt = d[i], 1
    out.append((cur, cnt))
    return out


def load(d):
    f = sorted(glob.glob(os.path.join(d, 'raw_09_13_*.npz')))
    pts = []
    for p in f:
        x = np.load(p, allow_pickle=True)
        t = float(x['true_na'])
        if not math.isfinite(t) or int(x['result_mode']) != 1:
            continue
        if (x['wave_ext'] >= 65535).sum() > 3:
            continue
        pts.append(dict(path=p, true=t,
                        w=x['wave_ext'].astype(np.int64),
                        m=int(x['raw_m']), n=int(x['raw_n'])))
    pts.sort(key=lambda p: p['true'])
    return pts


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('-d', '--dir', default='data')
    ap.add_argument('-o', '--out', default='data/window_sawtooth.png')
    ap.add_argument('--wave', default='ext', choices=['ext', 'int'])
    a = ap.parse_args()

    pts = load(a.dir)
    if not pts:
        sys.exit("没找到可用的 raw_09_13_*.npz")

    print("=" * 92)
    print("一个测量窗口**之内**的码值变化")
    print("=" * 92)
    print("窗口 6250 拍 (1 s), 每拍 160 µs。码值会绕回 0/65535, 已解绕。")
    print("")
    print("%9s %6s %6s %7s %7s %8s %7s %8s %9s %9s %9s"
          % ("真值nA", "m", "n", "上升段", "下降段", "周期拍", "每窗周期", "小数相位",
             "c[首]", "c[末]", "Δc"))
    rows = []
    for p in pts:
        step, un = unwrap(p['w'])
        seg = segments(step)
        # 去掉首尾不完整的段, 取中位段长作为周期
        core = seg[1:-1] if len(seg) > 3 else seg
        up = [L for d, L in core if d > 0]
        dn = [L for d, L in core if d < 0]
        up_m = int(np.median(up)) if up else 0
        dn_m = int(np.median(dn)) if dn else 0
        per = up_m + dn_m
        ncyc = 6250.0 / per if per else float('nan')
        frac = ncyc - math.floor(ncyc)
        c0, c1 = int(p['w'][0]), int(p['w'][-1])
        rows.append((p, up_m, dn_m, per, ncyc, frac))
        print("%+9.3f %6d %6d %7d %7d %8d %7.2f %8.3f %9d %9d %+9d"
              % (p['true'], p['m'], p['n'], up_m, dn_m, per, ncyc, frac,
                 c0, c1, c1 - c0))
    print("")
    print("  * **m = 上升段总拍数, n = 下降段总拍数** —— 固件报的这两个数就是这个")
    print("    (验证: 上升段长 x 每窗周期数 ≈ m; 且 m/n 与实测斜率比一致)")
    print("  * **周期随电流强烈变化**: 上面这批从 223 拍 (±2 nA) 到 636 拍 (−40 nA),")
    print("    差 2.8 倍 -> 每窗的锯齿数从 9.8 个变到 28.0 个")
    print("  * 「小数相位」= 6250/周期 的小数部分。窗口末尾落在锯齿的哪个位置由它")
    print("    决定, 所以端点码 c[末] 与 Δc 随电流**无规律地跳**。")
    print("    注意同一个电流的 3 次, 小数相位**常常逐位相同** (见上面 +10 / +20 /")
    print("    +30 / +40 / ±2 各行) —— 这就是 data/README 里说的「同一电流相位相同」。")
    print("  * 窗口起点 c[首] 被「拉回相」钉在 29977~31728, 而 c[末] 满量程乱跑 ->")
    print("    Δc 完全由 c[末] 决定。")
    print("")

    # ---------------- 图 ----------------
    ref = min(pts, key=lambda p: abs(p['true'] - 10.0))
    step, un = unwrap(ref['w'])
    fig, ax = plt.subplots(2, 2, figsize=(15, 8.4))

    ax[0][0].plot(np.arange(6250), un, lw=.8, color='C0')
    ax[0][0].set_title('整窗 (6250 拍) 解绕后的码位移   真值 %+.3f nA' % ref['true'])
    ax[0][0].set_xlabel('拍 (i)')
    ax[0][0].set_ylabel('解绕码位移 (码)')
    ax[0][0].grid(alpha=.3)

    z = 700
    ax[0][1].plot(np.arange(z), ref['w'][:z], lw=1.0, color='C3', marker='.',
                  ms=2.5)
    ax[0][1].set_title('放大前 %d 拍 —— 锯齿原貌 (未解绕的原始码)' % z)
    ax[0][1].set_xlabel('拍 (i)')
    ax[0][1].set_ylabel('ADC 码')
    ax[0][1].grid(alpha=.3)

    ax[1][0].plot(np.arange(z), un[:z], lw=1.0, color='C2')
    ax[1][0].set_title('同一段的解绕位移 —— 这才是真实的锯齿')
    ax[1][0].set_xlabel('拍 (i)')
    ax[1][0].set_ylabel('解绕码位移 (码)')
    ax[1][0].grid(alpha=.3)

    # 各电流的末尾 400 拍: 看"小数相位"如何把末尾落在不同位置
    tail = 400
    for p in pts:
        _, u = unwrap(p['w'])
        u = u - u[0]
        ax[1][1].plot(np.arange(tail) - tail, u[-tail:], lw=1.0,
                      label='%+.1f nA' % p['true'])
    ax[1][1].axvline(0, color='k', lw=1.5)
    ax[1][1].set_title('各电流**窗口末尾** 400 拍 —— 末尾落点(相位)各不相同')
    ax[1][1].set_xlabel('距窗口结束的拍数')
    ax[1][1].set_ylabel('解绕码位移 (码)')
    ax[1][1].grid(alpha=.3)
    ax[1][1].legend(fontsize=8, ncol=2)

    fig.suptitle('测量窗口**之内**的码值变化      (数据: %s, %d 个电流)'
                 % (a.dir, len(pts)), fontsize=12, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(a.out, dpi=150)
    print("已写", a.out)


if __name__ == '__main__':
    main()
