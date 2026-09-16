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
    sweep_broad.csv        逐次原始记录 (含每点用的**档位**)
    sweep_broad_fit.png    四格图 (见下)
    sweep_broad_reg.txt    回归方程 + 修正前/后统计

图是 2x2:
    左上  误差曲线 (修正前 vs 修正后; 正负分开画)
    右上  输入—输出 (绝对值, 对数)
    **左下/右下  残差直方图 (横轴 = 误差百分比)**
                  ① 直接输出值 vs 回读平均值  -> 修正前的误差
                  ② 修正值 (读数-b)/a vs 回读值 -> 修正后还剩多少
     两张放一起才看得出"修正把**偏置**从多少压到了多少"。
     注意: 增益/偏移修正**只消偏置(均值), 不动散布(σ)** —— σ 基本不变是对的,
     要看的是均值。图题里直接给 N / σ / 均值÷σ, |均值/σ|<2 视作无偏。
     ⚠️ 修正用的 a/b 是从**同一批数据**拟合的 -> 偏乐观, 真正证明有效性要靠
        第 4 章 4.6 节的独立验证点。

"回归方程" 给两条, 用途不同, 都要看:
    ① 读数 = a*真值 + b     <- 设备的线性度; a 偏离 1、b 偏离 0 的部分就是误差
    ② 相对误差 = f(电流)     <- 误差随电流的走向 (偏移型还是增益型, 一眼看出)
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import (smu_open, smu_off, smu_write,
                        board_open, board_measure,
                        ReadbackSampler, board_warmup, smu_set)


def default_points_pA(both=False):
    """需求③ 的点表, 单位 pA。相邻区间不重复取边界点。

    both=True 时**镜像出负向** (需求③原文只列了正电流, 但全量程表征缺了
    负向是不完整的 —— 正负不对称直接反映 I₊/I₋ 的不匹配)。
    """
    pts = []
    pts += list(range(1, 11))                  # 1..10 pA      (10 点)
    pts += list(range(20, 101, 10))            # 20..100 pA    (9 点)
    pts += list(range(200, 1001, 100))         # 200..1000 pA  (9 点)
    pts += list(range(2000, 10001, 1000))      # 2..10 nA      (9 点)
    pts += [12500 + 2500 * i for i in range(12)]   # 12.5..40 nA (12 点)
    if both:
        # 镜像: 顺序上正负交替容易被 2636B 的换向整定打断, 所以整段接在后面
        pts = pts + [-x for x in pts]
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--visa', required=True)
    ap.add_argument('--port', default=None)
    ap.add_argument('--points', default=None,
                    help='逗号分隔, 单位 **pA**。默认按需求③的表')
    ap.add_argument('--both', action='store_true',
                    help='正负都测 (把默认点表镜像到负向, 点数翻倍)' +
                         ' —— 需求③原文只列了正电流, 但正负不对称直接反映 '
                         'I+/I- 的不匹配, 全量程表征建议打开')
    ap.add_argument('--n', type=int, default=3)
    ap.add_argument('--settle', type=float, default=3.0)
    ap.add_argument('-o', '--out', default='data/sweep_broad.csv')
    a = ap.parse_args()

    pts_pA = ([float(x) for x in a.points.split(',')] if a.points
              else default_points_pA(both=a.both))

    print("=" * 70)
    print("广泛测试扫描   %d 点 x %d 次" % (len(pts_pA), a.n))
    print("  范围 %.3g pA ~ %.3g pA" % (min(pts_pA), max(pts_pA)))
    print("=" * 70)

    ser = board_open(a.port)
    smu = smu_open(a.visa)

    rows = []
    try:
        for pA in pts_pA:
            rng = smu_set(smu, pA * 1e-12)
            _rng = "%.3g A" % rng
            smu_write(smu, 'smua.source.output = smua.OUTPUT_ON')
            import time as _t
            _t.sleep(a.settle)

            # 预热: 空跑一次丢弃 —— 每批第一次的坏率特别高 (见 cal_sweep.py
            # 里 board_warmup 的注释: 五次独立实验都是"1 坏 2~n 好")
            board_warmup(ser)

            dev, rb = [], []
            tries = 0
            while (len(dev) < a.n) and (tries < a.n * 3):
                tries += 1
                smp = ReadbackSampler(smu); smp.start()
                d = board_measure(ser)
                rb += smp.stop(10)
                if d is None:
                    continue
                # 丢弃被标 TIMEOUT 的 (拉回超时 / 首拍在轨) —— 坏点以前会静默
                # 混进均值, 现在它能自己报出来, 这里补测
                if d.get('timeout'):
                    print("      (丢弃一次 TIMEOUT)"); continue
                dev.append(float(d['i']))
                # **端点码必须存** —— 2026-09-15 发现原来没存, 后果是这份数据
                # 只能做 2 参数的线性修正 (读数 = a*真值 + b), **套不了式(6)**,
                # 于是"验证点 vs 重合点"只能用 8 pA 的粗模型回答, 而式(6) 本身
                # 在标定数据上是 1.4 pA。缺的就是这几列。
                rows.append(dict(set_pA=pA, rep=len(dev), mode=int(d['mode']),
                                 t_ms=int(d['t']) if d['t'] else 0,
                                 dev_nA=float(d['i']),
                                 dev_ext_nA=float(d['x']) if d['x'] else float('nan'),
                                 m=int(d['m']) if d.get('m') else -1,
                                 n=int(d['n']) if d.get('n') else -1,
                                 int1=int(d['int1']) if d.get('int1') else -1,
                                 int2=int(d['int2']) if d.get('int2') else -1,
                                 ext1=int(d['ext1']) if d.get('ext1') else -1,
                                 ext2=int(d['ext2']) if d.get('ext2') else -1))

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
            print("  %-9.4g pA  装置 %+11.6f nA  源表 %+11.6f nA  误差 %+8.3f %%  (MODE=%d, 档位 %s)"
                  % (pA, dav, rav, err, rows[-1]['mode'], _rng))
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
    xs, ys, es, modes, pts_have = [], [], [], [], []
    for pA in pts_pA:
        g = [r for r in rows if r['set_pA'] == pA and 'true_nA' in r]
        if not g:
            continue
        pts_have.append(pA)          # 只记**有数据**的点 —— 下面 zip 要用它,
        xs.append(g[0]['true_nA'])   # 用完整的 pts_pA 去 zip 会让有点缺数据时
        ys.append(g[0]['dev_avg_nA'])  # 整张逐点表的"设定"列错位到下一个点上
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
    lx = [math.log10(abs(x) * 1e3) for x in xs]      # xs 是 nA -> pA 是 1e3
    #   ↑ 原来写的 1e9, 整体偏大 6。K 是中心化协方差不受影响, 但
    #     M = mean(es) - K*ml 会偏 -6K, 而 M 是要写进 reg.txt 报出去的。
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
    lines.append("  **修正前/修正后是同一件事的两个阶段**:")
    lines.append("    修正前 = (读数 - 回读)/回读;   修正后 = ((读数-a·回读-b)/a)/回读")
    lines.append("  %11s %13s %13s %12s %12s %6s"
                 % ("设定(pA)", "回读(nA)", "读数(nA)", "修正前%", "修正后%", "MODE"))
    # 修正后误差 = (修正值 - 回读)/回读 —— 与直方图②同一口径 (原来是"相对拟合直线",
    # 分母不同, 现在统一)
    ef = [(((y - B) / A - x) / x * 100 if x else float('nan'))
          for x, y in zip(xs, ys)]
    for pA, x, y, e, f, m in zip(pts_have, xs, ys, es, ef, modes):
        lines.append("  %11.4g %13.6f %13.6f %+12.3f %+12.4f %6d" % (pA, x, y, e, f, m))
    mr, mc = sum(es) / len(es), sum(ef) / len(ef)
    sr = (sum((v - mr) ** 2 for v in es) / max(len(es) - 1, 1)) ** 0.5
    sc = (sum((v - mc) ** 2 for v in ef) / max(len(ef) - 1, 1)) ** 0.5
    lines.append("")
    lines.append("  %-8s %12s %12s %12s" % ("", "均值(偏置)", "σ(散布)", "均值/σ"))
    lines.append("  %-8s %+11.4f%% %11.4f%% %12.2f" % ("修正前", mr, sr, mr / sr))
    lines.append("  %-8s %+11.4f%% %11.4f%% %12.2f" % ("修正后", mc, sc, mc / sc))
    lines.append("")
    lines.append("  判读: |均值/σ| > 2 就是**显著的系统偏置**(必须修); < 2 视作无偏。")
    lines.append("  注意 **增益/偏移修正只消偏置, 不动散布** —— 所以 σ 基本不变是对的;")
    lines.append("       期望看到的是**均值大幅下降**, 而不是 σ 下降。")
    lines.append("  本次: 偏置 %+.4f%% -> %+.4f%% (压低 %.0f 倍), 散布 %.4f%% -> %.4f%%"
                 % (mr, mc, abs(mr / mc) if mc else float('nan'), sr, sc))
    lines.append("  (注: 修正用的 a/b 是从**同一批数据**拟合的 -> 这里的改善偏乐观;")
    lines.append("   真正证明有效性要靠 4.6 节的独立验证点。见 docs 第4章。)")

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

    fig, ax = plt.subplots(2, 2, figsize=(14, 10))
    xpA = [abs(x) * 1e3 for x in xs]
    pos = [i for i, x in enumerate(xs) if x >= 0]
    neg = [i for i, x in enumerate(xs) if x < 0]

    # [0,0] 误差曲线 —— 正负分开画 (log 轴放不下负数, 而且分开才看得到不对称)
    for idx, lab, col in ((pos, '正电流', 'C3'), (neg, '负电流', 'C0')):
        if not idx:
            continue
        ax[0, 0].semilogx([xpA[i] for i in idx], [es[i] for i in idx],
                          'o-', ms=4, color=col, label='%s 修正前' % lab)
        ax[0, 0].semilogx([xpA[i] for i in idx], [ef[i] for i in idx],
                          's--', ms=3, color=col, alpha=.55, label='%s 修正后' % lab)
    ax[0, 0].axhline(0, color='k', ls='--', lw=1)
    ax[0, 0].set_xlabel('|电流| (pA)'); ax[0, 0].set_ylabel('相对误差 (%)')
    ax[0, 0].set_title('误差曲线 (修正前 vs 修正后; 正负分开)')
    ax[0, 0].grid(alpha=.3, which='both'); ax[0, 0].legend(fontsize=7)

    # [0,1] 输入—输出
    if pos:
        ax[0, 1].loglog([abs(xs[i]) for i in pos], [abs(ys[i]) for i in pos],
                        'o', ms=5, color='C3', label='正电流')
    if neg:
        ax[0, 1].loglog([abs(xs[i]) for i in neg], [abs(ys[i]) for i in neg],
                        's', ms=5, mfc='none', color='C0', label='负电流')
    lim = [min(abs(v) for v in xs), max(abs(v) for v in xs)]
    ax[0, 1].loglog(lim, lim, 'k--', lw=1, label='理想 1:1')
    ax[0, 1].set_xlabel('|真值| (nA)'); ax[0, 1].set_ylabel('|读数| (nA)')
    ax[0, 1].set_title('输入—输出 (绝对值, 对数)')
    ax[0, 1].grid(alpha=.3, which='both'); ax[0, 1].legend(fontsize=8)

    # ---- [1,0] / [1,1] 两张残差直方图 —— 横轴是**误差百分比** ----
    # 用户指定要这一对 (不是"准确度/非线性", 而是"修正前/修正后"):
    #   ① 直接输出值 vs 回读平均值      -> 修正前的原始误差
    #   ② 修正值     vs 回读值          -> 修正后还剩多少
    # 两张放一起才是完整证据链: 一眼看出修正把误差从多少压到了多少。
    #
    # 散点图看不出"偏置"和"分布形态", 直方图可以:
    #   峰是否落在 0   -> 有没有残留的系统偏置
    #   均值是几个 σ   -> 偏置是否显著 (|均值/σ| < 2 基本就是无偏)
    #   尾部有多厚     -> 离群点比例
    #
    # ⚠️ 修正用的 a/b 是**从同一批数据拟合出来的** -> 第二张图是**样本内**结果,
    #    会偏乐观。真正证明有效性要靠 4.6 节的**独立验证点**(未参与拟合)。
    d_raw = [(y - x) / x * 100 for x, y in zip(xs, ys)]
    d_cor = [(((y - B) / A - x) / x * 100) for x, y in zip(xs, ys)]

    for k, (arr, name, sub) in enumerate((
            (d_raw, '修正前', '直接输出值 vs 回读平均值'),
            (d_cor, '修正后', '修正值 (读数-b)/a vs 回读值'))):
        v = [x for x in arr if math.isfinite(x)]
        if not v:
            continue
        a2 = ax[1, k]
        nbin = max(8, min(40, int(len(v) ** 0.5) + 2))
        a2.hist(v, bins=nbin, color=('C3' if k == 0 else 'C0'),
                alpha=.75, edgecolor='k', lw=.5)
        mu = sum(v) / len(v)
        sd = (sum((x - mu) ** 2 for x in v) / max(len(v) - 1, 1)) ** 0.5
        a2.axvline(0, color='k', ls='--', lw=1, label='0')
        a2.axvline(mu, color='r', lw=1.5, label='均值 %+.4f%%' % mu)
        a2.set_xlabel('误差百分比 (%%) —— %s' % sub)
        a2.set_ylabel('点数')
        a2.set_title('%s  N=%d  σ=%.4f%%  均值/σ=%.2f'
                     % (name, len(v), sd, (mu / sd) if sd else float('nan')))
        a2.grid(alpha=.3); a2.legend(fontsize=8)
    fig.tight_layout()
    png = a.out.replace('.csv', '_fit.png')
    fig.savefig(png, dpi=150)
    print("已写", png)


if __name__ == '__main__':
    main()
