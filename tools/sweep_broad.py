# -*- coding: utf-8 -*-
"""
广泛测试扫描 (需求③): 从 pA 到 nA 铺开测, 出误差曲线 + 回归方程

点表 (需求原文):
    1~10 pA     每 1 pA
    10~100 pA   每 10 pA
    100 pA~1 nA 每 100 pA
    1~10 nA     每 1 nA
    10~40 nA    每 2.5 nA
相邻区间只取边界一次 (上面的端点已在下个区间的起点重复, 故从第二个点起)。

每点测 3 次 (与标定一致), 取平均; 逐次都在测量窗口内采源表回读求平均作真值。

输出:
    sweep_broad.csv        逐次原始记录
    sweep_broad_fit.png    误差曲线 (相对误差 vs 电流, 对数横轴)
    sweep_broad_reg.txt    回归方程

"回归方程" 给两条, 用途不同, 都要看:
    ① 读数 = a*真值 + b     <- 设备的线性度; a 偏离 1、b 偏离 0 的部分就是误差
    ② 相对误差 = f(电流)     <- 误差随电流的走向 (偏移型还是增益型, 一眼看出)
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import smu_open, smu_off, board_open, board_measure, ReadbackSampler


def default_points_pA():
    """需求③ 的点表, 单位 pA。相邻区间不重复取边界点。"""
    pts = []
    pts += list(range(1, 11))                  # 1..10 pA      (10 点)
    pts += list(range(20, 101, 10))            # 20..100 pA    (9 点)
    pts += list(range(200, 1001, 100))         # 200..1000 pA  (9 点)
    pts += list(range(2000, 10001, 1000))      # 2..10 nA      (9 点)
    pts += [12500 + 2500 * i for i in range(12)]   # 12.5..40 nA (12 点)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--points', default=None,
                    help='逗号分隔, 单位 **pA**。默认按需求③的表')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--settle', type=float, default=3.0)
    ap.add_argument('-o', '--out', default='data/sweep_broad.csv')
    a = ap.parse_args()

    pts_pA = ([float(x) for x in a.points.split(',')] if a.points else default_points_pA())

    print("=" * 70)
    print("广泛测试扫描   %d 点 x %d 次" % (len(pts_pA), a.n))
    print("  范围 %.3g pA ~ %.3g pA" % (min(pts_pA), max(pts_pA)))
    print("=" * 70)

    ser = board_open(a.port)
    smu = smu_open(a.visa)

    rows = []
    try:
        for pA in pts_pA:
            smu.write('smua.source.leveli = %.9e' % (pA * 1e-12))
            smu.write('smua.source.output = smu.OUTPUT_ON')
            import time as _t
            _t.sleep(a.settle)

            dev, rb = [], []
            for k in range(a.n):
                smp = ReadbackSampler(smu); smp.start()
                d = board_measure(ser)
                rb += smp.stop(10)
                if d is None:
                    continue
                dev.append(float(d['i']))
                rows.append(dict(set_pA=pA, rep=k + 1, mode=int(d['mode']),
                                 t_ms=int(d['t']) if d['t'] else 0,
                                 dev_nA=float(d['i']),
                                 dev_ext_nA=float(d['x']) if d['x'] else float('nan')))

            if not dev or not rb:
                print("  %-9.4g pA   ** 无有效数据 **" % pA); continue
            dav = sum(dev) / len(dev)
            rav = sum(rb) / len(rb) * 1e9        # nA
            err = (dav - rav) / rav * 100 if rav else float('nan')
            for r in rows:
                if r['set_pA'] == pA:
                    r['true_nA'] = rav
                    r['dev_avg_nA'] = dav
                    r['err_pct'] = err
            print("  %-9.4g pA  装置 %+11.6f nA  源表 %+11.6f nA  误差 %+8.3f %%  (MODE=%d)"
                  % (pA, dav, rav, err, rows[-1]['mode']))
    finally:
        print("\n关源表输出")
        smu_off(smu)
        ser.close()

    if not rows:
        print("没有任何数据"); return

    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("已写", a.out, "(%d 行)" % len(rows))

    # ---- 每点汇总 ----
    xs, ys, es, modes = [], [], [], []
    for pA in pts_pA:
        g = [r for r in rows if r['set_pA'] == pA and 'true_nA' in r]
        if not g:
            continue
        xs.append(g[0]['true_nA']); ys.append(g[0]['dev_avg_nA'])
        es.append(g[0]['err_pct']); modes.append(g[0]['mode'])

    if len(xs) < 2:
        print("点太少, 不做回归"); return

    # ---- 回归①: 读数 = a*真值 + b ----
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    A = sxy / sxx
    B = my - A * mx
    r = [y - (A * x + B) for x, y in zip(xs, ys)]
    rss = sum(v * v for v in r)
    sst = sum((y - my) ** 2 for y in ys)
    R2 = 1 - rss / sst if sst else float('nan')

    lines = []
    lines.append("回归① 读数 = a*真值 + b   (线性度与增益)")
    lines.append("    a = %.6f      b = %+.6f nA      R² = %.8f"
                 % (A, B, R2))
    lines.append("    残差 RMS = %.4f pA   最大 |残差| = %.4f pA"
                 % ((rss / n) ** 0.5 * 1e3, max(abs(v) for v in r) * 1e3))
    lines.append("    反解修正: I_corr = (I_dev - (%+.6f nA)) / %.6f" % (B, A))
    lines.append("    (a-1) = %+.4f %%   <- 增益误差;  b = %+.3f pA  <- 固定偏移"
                 % ((A - 1) * 100, B * 1e3))
    lines.append("")

    # ---- 回归②: 相对误差 vs log10(电流) ----
    import math
    lx = [math.log10(abs(x) * 1e9) for x in xs]      # x 是 nA -> pA 取对数
    ml = sum(lx) / n
    sl = sum((v - ml) ** 2 for v in lx)
    sl_e = sum((v - ml) * (e - sum(es) / n) for v, e in zip(lx, es))
    K = sl_e / sl if sl else float('nan')
    M = sum(es) / n - K * ml
    lines.append("回归② 相对误差(%%) = K*log10(电流/pA) + M   (误差的走向)")
    lines.append("    K = %+.4f   M = %+.4f" % (K, M))
    lines.append("    K 接近 0  -> 误差与电流无关, 是**纯增益/偏移**")
    lines.append("    K 明显非 0 -> 误差随电流变化, 小电流端更差 (偏向偏移型)")
    lines.append("")
    lines.append("逐点:")
    lines.append("  %12s %14s %14s %10s %6s" % ("设定(pA)", "真值(nA)", "读数(nA)", "误差%", "MODE"))
    for pA, x, y, e, m in zip(pts_pA, xs, ys, es, modes):
        lines.append("  %12.4g %14.6f %14.6f %+10.3f %6d" % (pA, x, y, e, m))

    reg_out = a.out.replace('.csv', '_reg.txt')
    with open(reg_out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print("\n已写", reg_out)

    # ---- 画图 ----
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(1, 2, figsize=(13.5, 5.5))
    ax[0].semilogx([abs(x) * 1e3 for x in xs], es, 'o-', ms=5)
    ax[0].axhline(0, color='k', ls='--', lw=1)
    ax[0].set_xlabel('电流 (pA)'); ax[0].set_ylabel('相对误差 (%)')
    ax[0].set_title('误差曲线'); ax[0].grid(alpha=.3, which='both')

    ax[1].loglog([abs(x) for x in xs], [abs(y) for y in ys], 'o', ms=5, label='读数')
    lim = [min(abs(x) for x in xs), max(abs(x) for x in xs)]
    ax[1].loglog(lim, lim, 'k--', lw=1, label='理想 1:1')
    ax[1].set_xlabel('真值 (nA)'); ax[1].set_ylabel('读数 (nA)')
    ax[1].set_title('输入—输出 (对数)'); ax[1].grid(alpha=.3, which='both'); ax[1].legend()
    fig.tight_layout()
    png = a.out.replace('.csv', '_fit.png')
    fig.savefig(png, dpi=150)
    print("已写", png)


if __name__ == '__main__':
    main()
