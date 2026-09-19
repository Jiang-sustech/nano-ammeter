# -*- coding: utf-8 -*-
"""
脉冲输入测试 —— 2x2 图：各组「程序设定波形」vs「由装置读数反推的波形」

    python tools/paper_pulse_fig.py

数据 (2026-09-16 重跑 r1)
------------------------
  data/sweep_pulse_r1.csv     A 组 12 点 + P 组 11 点 + K 组 + 直流对照
  data/sweep_pulse_r1_K.csv   **K 组重跑**（用它, 不用 r1.csv 里那份 K）

排版按《论文制图规范》(tools/paper_style.py)：17 cm 宽、正红/蓝/黑三色。
**本图经用户批准放宽一条**：允许组标签（gid）—— 叠画要标 12 组必然重叠,
所以改用瀑布图, gid 直接做纵轴刻度。

两条波形是什么
--------------
  蓝 = **程序设定的输出波形** —— 由 (peak_nA, ton_ms, period_ms) 画出。
       本轮**无采样波形**(CSV 只有每组一个标量), 所以是"推测的"。
  红 = **由装置读数反推的波形** —— 保持同一 ton/T, 峰值取 dev_nA / duty,
       即"要产生这个读数、源需输出多高"。两线等高差即脉冲下的读数误差。

x 轴范围逐族定（各族观察变量不同）
---------------------------------
  A 组  变量是 t_on (T 固定 10ms)      -> 画满一个周期 0~10.5ms
  P 组  变量是 T (占空比固定 25%)       -> 放大到脉冲区 0~14.4ms, 脉宽 ∝ 周期
  K 组  变量是峰值 (t_on 仅 0.2ms)      -> 放大到脉冲区 0~1.15ms
"""
import csv
import os
import sys

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_style as ps                    # noqa: E402

ps.apply()

R1 = 'data/sweep_pulse_r1.csv'
R1K = 'data/sweep_pulse_r1_K.csv'
OUT = 'image_paper/fig05_pulse_waveform.png'


def load(path):
    g = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            g.setdefault(r['gid'], []).append(r)
    out = {}
    for k, v in g.items():
        r = v[0]
        rb = [float(x['rb_nA']) for x in v if x['rb_nA'] not in ('', 'nan')]
        out[k] = dict(
            kind=r['kind'], peak=float(r['peak_nA']), ton=float(r['ton_ms']),
            T=float(r['period_ms']), duty=float(r['duty']),
            avg=float(r['avg_nA']), true_avg=float(r['true_avg_nA']),
            dev=sum(float(x['dev_nA']) for x in v) / len(v),
            rb=sum(rb) / len(rb) if rb else float('nan'), n=len(v))
    return out


def wave(peak, ton, T, t0=0.0, n=1):
    t, y = [], []
    for c in range(n):
        b = t0 + c * T
        t += [b, b, b + ton, b + ton]
        y += [0.0, peak, peak, 0.0]
    t.append(t0 + n * T); y.append(0.0)
    return np.array(t), np.array(y)


def waterfall(ax, ids, D, xmax, title):
    """每组一条带, gid 做纵轴刻度; 带内 蓝=设定 / 红虚线=反推。"""
    pk = max(D[g]['peak'] for g in ids)
    sp = pk * 1.30                      # 带间距 > 最高峰值, 免得叠在一起
    for i, gid in enumerate(ids):
        d = D[gid]
        base = (len(ids) - 1 - i) * sp  # 第一组排最上
        t, y = wave(d['peak'], d['ton'], d['T'])
        ax.plot(t, y + base, '-', color=ps.BLUE, lw=1.1,
                label='程序设定波形' if i == 0 else None)
        impl = d['dev'] / d['duty'] if d['duty'] else float('nan')
        t2, y2 = wave(impl, d['ton'], d['T'])
        ax.plot(t2, y2 + base, '--', color=ps.RED, lw=1.1,
                label='由装置读数反推' if i == 0 else None)
        # 右侧标**设定峰值** —— 带是纵向错开的, 纵轴没有数值刻度,
        # 不标就完全读不出幅度。单位统一放进纵轴标签, 这里只写数字。
        # ⚠️ 标在**基线高度**(与左侧 gid 同一行), 不标在 base+peak:
        #    标在脉冲顶端时, 文字落在上面那条带的空隙里, 归属看不出。
        ax.annotate('%g' % d['peak'], xy=(xmax, base),
                    xytext=(-3, 0), textcoords='offset points',
                    ha='right', va='center', fontsize=ps.FS_TICK, color=ps.BLACK)
    ax.set_yticks([(len(ids) - 1 - i) * sp for i in range(len(ids))])
    ax.set_yticklabels(ids)
    # 底部多留一带, 让右下角图例落在空处 —— 否则会压住最后一组(如 A11/A12)
    # 的平段。脉冲本身在最左端, 不会被遮, 但图例盖在线上终归不整齐。
    ax.set_ylim(-sp * 1.9, (len(ids) - 1) * sp + pk * 1.75)
    ax.set_xlim(0, xmax)
    ax.set_xlabel('时间   (ms)')
    ps.grid(ax)
    ax.set_title(title, color=ps.BLACK)


def ylabel(ax):
    """只给左列标纵轴 —— 右列再标一遍会挤到左列的图区里去。"""
    ax.set_ylabel('各组波形（纵向错开，右侧为设定峰值 / nA）')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    D = load(R1)
    DK = load(R1K)                      # K 组重跑, 覆盖 r1.csv 里那份

    fig, cap_y = ps.figure(18.5, cap_lines=1, title=True)
    axes = [fig.add_subplot(2, 2, i + 1) for i in range(4)]
    fig.subplots_adjust(hspace=0.34, wspace=0.34)

    A = ['A%d' % i for i in range(1, 13)]
    P = ['P%d' % i for i in range(1, 12)]
    K = ['K%d' % i for i in range(1, 10)]
    missing = [g for g in A + P if g not in D] + [g for g in K if g not in DK]
    if missing:
        sys.exit('缺组: %s' % missing)

    waterfall(axes[0], A, D, 10.5,
              '(a) A 组　占空比扫描（T = 10 ms 固定，$t_{on}$ ↓）')
    waterfall(axes[1], P, D, 14.4,
              '(b) P 组　周期扫描（占空比 25%，脉宽 ∝ 周期）')
    waterfall(axes[2], K, DK, 1.15,
              '(c) K 组　峰值扫描（$t_{on}$ ≈ 0.2 ms，x 轴放大到脉冲区）')
    ylabel(axes[0]); ylabel(axes[2])
    # ncol=2 -> 单行, 高度减半。瀑布图 12 条带占满全格, 右下角没有空位,
    # 两行图例必然压住最后一组。
    axes[0].legend(loc='lower right', framealpha=.95, ncol=2)

    ax = axes[3]
    dc = sorted((d['avg'], d['dev']) for d in D.values() if d['kind'] == 'dc')
    x = np.array([a for a, _ in dc]); y = np.array([b for _, b in dc])
    lim = np.array([0.4, 50.0])
    ax.plot(lim, lim, '-', color=ps.BLACK, lw=ps.LW_MAIN,
            label='理想 $I_{dev}=I_{std}$')
    ax.plot(x, y, 'o', color=ps.BLUE, ms=ps.MS, mec=ps.BLUE, mew=ps.MEW,
            label='装置读数')
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel('直流设定电流   (nA)')
    ax.set_ylabel('装置读数   (nA)')
    ps.grid(ax, which='both')
    ax.legend(loc='lower right', framealpha=.95)
    ax.set_title('(d) 直流对照　$I_{dev}$ vs $I_{std}$', color=ps.BLACK)

    ps.caption(fig, cap_y, '脉冲输入的设定波形与由装置读数反推的波形')
    os.makedirs('image_paper', exist_ok=True)
    fig.savefig(OUT)
    print('已写', OUT)

    print('\n%-6s %8s %8s %9s %10s %10s' % ('gid', 'peak', 'ton', 'T', '读数', '偏差%'))
    for gid in A + P + K:
        d = (DK if gid in K else D)[gid]
        print('%-6s %8.1f %8.2f %9.2f %10.4f %+10.3f'
              % (gid, d['peak'], d['ton'], d['T'], d['dev'],
                 (d['dev'] - d['true_avg']) / d['true_avg'] * 100))


if __name__ == '__main__':
    main()
