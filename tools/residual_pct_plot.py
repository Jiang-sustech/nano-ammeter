# -*- coding: utf-8 -*-
"""
±5 nA ~ ±45 nA 的**残差百分比图**

    python tools/residual_pct_plot.py
    python tools/residual_pct_plot.py --out data/residual_pct_5_45nA.png

画什么
------
横轴 = 回读电流 I_std (nA, 正负都画), 纵轴两格:

    上  残差 (pA)             残差 = Ī_校准后 − I_回读
    下  残差百分比 (%)          = 残差 / I_回读 × 100

**为什么必须分三类画**(见 data/README.md「误差口径」与
memory/nanoammeter-accuracy-facts.md):

    ① 标定点     19 点 = 拟合三常数**用的就是它们** -> 样本内, 不计入统计
    ② 重合点     sweep_broad 里落在标定网格上的(10/15/.../40 nA) —— 与标定点
                 **同电流、但另采一轮**
    ③ 真交错点   插在标定网格**中间**的(6 7 8 9 / 12.5 17.5 22.5 27.5 32.5
                 37.5 nA, 正负各 10 个)

**②③ 都计入头条统计**(用户 2026-09-16 裁定): 第二轮是**重新测的**, 第一轮
拟合用的是另一轮的测量, 不存在"拿考题答案判考卷"那种直接泄漏。

但两者检验的**不是同一件事**, 所以图里分开画、数字分开报:

    真交错点 = 网格**之间**内插  -> 检验模型的**形状**(最严格)
    重合点   = 网格**上**复测    -> 检验**跨轮复现性**
                                  (电流已被第一轮拟合钉过, 偏乐观在这)

2026-09-16 曾把这三类混在一张表里报 "RMS 1.69 pA", 被用户抓出。这张图把它们
**分开画、分开算**, 就是为了不再犯。

两个聚合层级, 别混
------------------
同一个电流测 3 次, 于是有两种残差统计:

    **逐点均值**  先对 3 次取平均再算残差 (n=20 个点)  <- 本脚本的**头条数字**
                 提交 bc77373 用的是这个口径 (那里报 RMS 1.647 pA)
    **逐次**      60 次各自算 (n=60)                   <- 图上的小点, 供看重复性

逐次必然比逐点均值大 (均值把 √3 的噪声压掉了)。两者**都对**, 引用时必须
说明是哪个 —— 直接混用会凭空多出 9% 的差。

数据来源 (两轮独立采集, 相隔约 45 分钟):
    data/cal_sweep_fit.csv    2026-09-15 19:41  19 点 × 3 次  -> 定常数
    data/sweep_broad.csv      2026-09-15 20:28  34 点 × 3 次  -> 被预测
"""
import argparse
import collections
import csv
import math
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import load, fit, consts, pred_from

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 三类点的画法: (颜色, 记号, 实心?, 短名, 图例文字)
STYLE = {
    'cal':   ('0.55', 'o', False, '标定点  ',
              '标定点 — 拟合三常数用的就是它们 (样本内)'),
    'coin':  ('C1',   's', False, '重合点  ',
              '与标定点同电流、另采一轮 (伪样本内)'),
    'inter': ('C3',   'D', True,  '真交错点',
              '插在标定网格中间的真交错点 (只有这些算验证)'),
}
ORDER = ['cal', 'coin', 'inter']        # 越不算数的越先画(压在下面)


def load_broad(path):
    """读 sweep_broad.csv -> 逐次记录。

    用**外部 ADS8866** 的端点码 (ext1/ext2) —— 固件 USE_INTERNAL_ADC=0,
    报告值走的就是这一路 (实测 102 行的 int/ext 码值**逐位相同**, 两路斜率
    只差 2 ppm, 在这个码值上取整后区分不开)。
    """
    out = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                m, n = int(r['m']), int(r['n'])
                c1, c2 = int(r['ext1']), int(r['ext2'])
                It = float(r['true_nA']) * 1e-9
                sp = float(r['set_pA'])
            except (KeyError, ValueError):
                continue
            if m <= 0 or n <= 0 or c1 < 0 or c2 < 0 or not math.isfinite(It):
                continue
            out.append(dict(set_pA=sp, I=It, m=m, n=n, c1=c1, c2=c2))
    return out


def classify(set_pA, cal_nA, tol=0.1):
    """落在标定网格上 -> 'coin', 否则 -> 'inter'。tol 单位 nA。

    标定网格是 ±5,±10,...,±45 (步长 5)。10/15/... 离最近标定点只差
    0.002~0.004 nA, 而 12.5 差 2.5 nA —— 中间隔着 3 个数量级, 判别无歧义。
    """
    return ('coin' if min(abs(set_pA / 1000.0 - c) for c in cal_nA) < tol
            else 'inter')


def agg(rows):
    """[(I, e)] -> (逐点均值 [(I,e)], 逐次 [(I,e)])。

    按 |I| 分组(同一个电流的 3 次)。分成两种聚合层级, 见模块 docstring。
    """
    g = collections.defaultdict(list)
    for I, e in rows:
        g[round(I * 1e12, 1)].append((I, e))
    means = [(sum(I for I, _ in v) / len(v), sum(e for _, e in v) / len(v))
             for v in g.values()]
    return means, rows


def groups(rows):
    """[(I, e)] -> [(I, [e_1, e_2, e_3]), ...] 按电流分组, 保留**逐次**的值。

    同一个电流的 3 次共享一个 I (CSV 里的 true_nA 就是该点 3 次回读的平均),
    所以按 I 分组是精确的, 不会串组。
    """
    g = collections.defaultdict(list)
    for I, e in rows:
        g[round(I * 1e12, 1)].append((I, e))
    return sorted(((v[0][0], [e for _, e in v]) for v in g.values()),
                  key=lambda t: t[0])


def draw_bars(path, raw, q, i_pos, i_neg):
    """柱状图: 每一组 3 次各一根 (不再压成均值)。"""
    sets = [('第一轮 (标定点, 19 点)', raw['cal']),
            ('第二轮 (另采一轮, 34 点)', raw['coin'] + raw['inter'])]
    fig, axs = plt.subplots(2, 2, figsize=(16.5, 9.5))
    w = 0.28

    for r, (rowname, rows) in enumerate(sets):
        for c, (colname, sel) in enumerate(
                (('正电流', [p for p in rows if p[0] > 0]),
                 ('负电流', [p for p in rows if p[0] < 0]))):
            axx = axs[r][c]
            gs = groups(sel)
            xs = list(range(len(gs)))
            for j in range(3):
                # groups() 里的 e 是**安培**, 这里必须换成 pA 再画 ——
                # 不换的话 matplotlib 会自己加个 1e-12 的轴偏移, 刻度数字
                # 看着刚好等于 pA, 但轴标注的含义是错的。
                ys = [g[1][j] * 1e12 if j < len(g[1]) else 0.0 for g in gs]
                axx.bar([i + (j - 1) * w for i in xs], ys, width=w,
                        color='C%d' % j, edgecolor='k', linewidth=.4,
                        label='第 %d 次' % (j + 1))
            axx.axhline(0, color='k', lw=1)
            axx.set_xticks(xs)
            axx.set_xticklabels(['%.4g' % (g[0] * 1e9) for g in gs],
                                rotation=90, fontsize=6.5)
            e = [v for g in gs for v in g[1]]
            rms = math.sqrt(sum(t * t for t in e) / len(e)) * 1e12 if e else 0
            axx.set_title('%s — %s   %d 点 × 3 次   逐次 RMS %.3f pA'
                          % (rowname, colname, len(gs), rms), fontsize=10)
            axx.set_xlabel('电流 (nA)')
            axx.set_ylabel('残差 (pA)')
            axx.grid(alpha=.3, axis='y')
            if r == 0 and c == 0:
                axx.legend(fontsize=9)

    fig.suptitle('nano-ammeter  ±5 nA ~ ±45 nA 残差 — 每一组的 3 次测量'
                 '      (q=%.5e C/码, I+=%+.3f nA, I−=%+.3f nA)'
                 % (q, i_pos * 1e9, i_neg * 1e9), fontsize=11.5, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(path, dpi=150)
    print("已写", path)


def stats(pairs):
    if not pairs:
        return None
    e = [r for _, r in pairs]
    rel = [r / I * 100.0 for I, r in pairs if abs(I) > 1e-12]
    d = dict(n=len(e),
             rms=math.sqrt(sum(t * t for t in e) / len(e)) * 1e12,
             mx=max(abs(t) for t in e) * 1e12,
             mean=sum(e) / len(e) * 1e12)
    if rel:
        d.update(rms_pct=math.sqrt(sum(t * t for t in rel) / len(rel)),
                 mx_pct=max(abs(t) for t in rel))
    return d


def line(tag, s, indent="      "):
    if not s:
        print("%s%s: (无点)" % (indent, tag))
        return
    extra = ("  相对 RMS %.4f%%  max %.4f%%" % (s['rms_pct'], s['mx_pct'])
             if 'rms_pct' in s else "")
    print("%s%-22s n=%-4d 残差均值 %+7.3f pA   RMS %6.3f pA   max|·| %6.3f pA%s"
          % (indent, tag, s['n'], s['mean'], s['rms'], s['mx'], extra))


def main():
    # Windows 控制台默认 GBK, 而下面输出里有 U+2212 (−) 。不强制 UTF-8
    # 会在 print 处直接 UnicodeEncodeError 崩掉。
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--cal', default='data/cal_sweep_fit.csv',
                    help='标定数据 (I_true,c1,c2,m,n), 三常数由它拟合')
    ap.add_argument('--val', default='data/sweep_broad.csv',
                    help='另一轮独立采集的宽扫数据, 含端点码')
    ap.add_argument('--fs-na', type=float, default=90.0, help='全量程 (nA)')
    ap.add_argument('--bar', action='store_true',
                    help='改画柱状图: 每一组的 3 次各一根, 不压成均值')
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args()
    if a.out is None:
        a.out = ('data/residual_bar_5_45nA.png' if a.bar
                 else 'data/residual_pct_5_45nA.png')

    for p in (a.cal, a.val):
        if not os.path.exists(p):
            sys.exit("找不到 %s" % p)

    # ---------------- 标定: 只用 cal 文件的 19 点 ----------------
    cpts = load(a.cal)
    ckeys = sorted(cpts)
    x = fit([r for k in ckeys for r in cpts[k]])
    q, i_pos, i_neg = consts(x)
    cal_nA = list(ckeys)

    print("=" * 78)
    print("±5 nA ~ ±45 nA 残差百分比图")
    print("=" * 78)
    print("三常数 (只由 %s 的 %d 个电流点 / %d 行拟合):"
          % (os.path.basename(a.cal), len(ckeys),
             sum(len(v) for v in cpts.values())))
    print("    q  = %.6e C/码" % q)
    print("    I+ = %+.4f nA     I- = %+.4f nA" % (i_pos * 1e9, i_neg * 1e9))
    print("")

    raw = {'cal': [], 'coin': [], 'inter': []}

    # 样本内: 标定点自己(重读原始行 —— load() 已把端点码折成 a1/a2, 这里要用码值)
    with open(a.cal, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            I = float(r['I_true'])
            raw['cal'].append((I, pred_from(float(r['c1']), float(r['c2']),
                                            float(r['m']), float(r['n']), x) - I))

    # 样本外: sweep_broad 另采的那一轮
    for r in load_broad(a.val):
        e = pred_from(r['c1'], r['c2'], r['m'], r['n'], x) - r['I']
        raw[classify(r['set_pA'], cal_nA)].append((r['I'], e))

    agg_m, agg_r = {}, {}
    for k in ORDER:
        agg_m[k], agg_r[k] = agg(raw[k])

    allI = [I for k in ORDER for I, _ in raw[k] if abs(I) > 1e-12]
    print("数据: %s (标定点) + %s (另采一轮)"
          % (os.path.basename(a.cal), os.path.basename(a.val)))
    print("      %d 次测量 -> %d 个电流点, |I| 范围 %.3f ~ %.3f nA"
          % (len(allI), sum(len(agg_m[k]) for k in ORDER),
             min(abs(I) for I in allI) * 1e9, max(abs(I) for I in allI) * 1e9))
    print("")

    # ---------------- 统计 ----------------
    print("--- 残差 = Ī_校准后 − I_回读   (按来源分开算) ---")
    for k in ORDER:
        line(STYLE[k][3], stats(agg_m[k]))
    print("")

    # 第二轮的**全部**点 —— 第一轮拟合完全没见过这些测量。
    # 用户 2026-09-16 裁定: 重合点也算, 理由是第一轮拟合用的是**另一轮**的
    # 测量, 第二轮这些是重新测的, 不存在"用考题答案判考卷"那种直接泄漏。
    #
    # 保留的保留意见(不是反对, 是说明它检验的是**什么**): 重合点的电流
    # 被第一轮拟合**钉过** —— 装置在 10 nA 的读数两轮几乎一样(重复性 ~1 pA),
    # 所以拟合早把那个工作点的残差压到 0 附近了。因此:
    #     真交错点 = 网格**之间**内插  -> 检验模型的**形状**
    #     重合点   = 网格**上**复测    -> 检验**跨轮复现性**
    # 两个都报, 引用时说明拿的是哪一个。
    merged_m = agg_m['inter'] + agg_m['coin']
    merged_r = agg_r['inter'] + agg_r['coin']
    sm, sr = stats(merged_m), stats(merged_r)

    print("--- 头条数字: 第二轮测的全部点 (第一轮拟合没见过的测量) ---")
    line("逐点均值 (n=%d)" % len(merged_m), sm)
    line("逐次     (n=%d)" % len(merged_r), sr)
    print("      逐次比逐点均值大 %.1f%% —— 均值把重复性噪声压掉了 √3,"
          " 引用时必须说明用哪个" % ((sr['rms'] / sm['rms'] - 1) * 100))
    print("")
    line("  ├ 真交错点   ", stats(agg_m['inter']), indent="  ")
    line("  └ 重合点(复测)", stats(agg_m['coin']), indent="  ")
    print("      真交错点 = 网格**之间**内插 (最严格);")
    print("      重合点   = 网格**上**复测 (检验跨轮复现, 偏乐观的那部分在这)。")
    print("")
    pos = [(I, e) for I, e in merged_m if I > 0]
    neg = [(I, e) for I, e in merged_m if I < 0]
    line("  正向", stats(pos))
    line("  负向", stats(neg))
    if pos and neg:
        mp, mn = stats(pos)['mean'], stats(neg)['mean']
        print("      -> 正负不对称: 均值差 %.3f pA。解出 偏置 %.3f pA,"
              " 增益项 %.5f pA/nA" % (mp - mn, (mp + mn) / 2, (mp - mn) / 50.0))
    print("")
    print("  (参考) 第一轮标定点 (样本内, 拟合用的就是它们) RMS %.3f pA"
          " —— 最小, 因为它是自己评自己" % stats(agg_m['cal'])['rms'])
    print("")
    print("      残差是\"装置 vs 回读值\"之差, **不是\"装置 vs 真值\"** ——")
    print("      回读的系统误差已被三常数拟合吸收, 残差里看不见它。")

    # ---------------- 画图 ----------------
    if a.bar:
        draw_bars(a.out, raw, q, i_pos, i_neg)
        return

    fig, ax = plt.subplots(2, 1, figsize=(11.5, 8.8), sharex=True)

    for k in ORDER:
        col, mk, filled, _short, lab = STYLE[k]
        mfc = col if filled else 'none'
        z = 4 if k == 'inter' else 3

        # 逐次: 小、淡 —— 用来看重复性
        Is_r = [I * 1e9 for I, _ in agg_r[k]]
        es_r = [e * 1e12 for _, e in agg_r[k]]
        ax[0].scatter(Is_r, es_r, s=13, marker='o', facecolors='none',
                      edgecolors=col, alpha=.45, linewidths=.8, zorder=z - 1)
        rel_r = [(I * 1e9, e / I * 100.0) for I, e in agg_r[k]
                 if abs(I) > 1e-12]
        ax[1].scatter([p[0] for p in rel_r], [p[1] for p in rel_r],
                      s=13, marker='o', facecolors='none', edgecolors=col,
                      alpha=.45, linewidths=.8, zorder=z - 1)

        # 逐点均值: 大、实 —— 头条统计量
        Is = [I * 1e9 for I, _ in agg_m[k]]
        es = [e * 1e12 for _, e in agg_m[k]]
        ax[0].scatter(Is, es, s=52, marker=mk, facecolors=mfc, edgecolors=col,
                      linewidths=1.4, zorder=z, label=lab)
        rel = [(I * 1e9, e / I * 100.0) for I, e in agg_m[k] if abs(I) > 1e-12]
        ax[1].scatter([p[0] for p in rel], [p[1] for p in rel],
                      s=52, marker=mk, facecolors=mfc, edgecolors=col,
                      linewidths=1.4, zorder=z, label=lab)

    # 第二轮全部点的 ±RMS 带 -> "能引用的散布"一眼可见
    for v, axx in ((sm['rms'], ax[0]), (sm['rms_pct'], ax[1])):
        for sgn in (+1, -1):
            axx.axhline(sgn * v, color='C3', ls=':', lw=1.2, alpha=.8, zorder=1)
        axx.axhline(0, color='k', ls='--', lw=1, zorder=1)

    ax[0].set_ylabel('残差 (pA)\n= 校准后装置值 − 2636B 回读值')
    ax[0].set_title('残差 (pA)      虚线 = 0,  点线 = 第二轮全部点的 ±RMS '
                    '(%.3f pA, 逐点均值 n=%d)' % (sm['rms'], len(merged_m)))
    ax[1].set_ylabel('残差百分比 (%)\n= 残差 / 回读值 × 100')
    ax[1].set_title('残差百分比 (%%)      点线 = ±%.4f %% RMS (逐点均值)'
                    % sm['rms_pct'])
    ax[1].set_xlabel('回读电流 I_std (nA) —— 正负都画')

    for axx in ax:
        axx.grid(alpha=.3)
        axx.axvline(0, color='k', lw=.8, alpha=.4)
        axx.legend(fontsize=8.5, loc='upper left', framealpha=.92)
    ax[1].set_xlim(-48, 48)

    fig.suptitle('nano-ammeter  ±5 nA ~ ±45 nA 残差      '
                 '(q=%.5e C/码, I+=%+.3f nA, I−=%+.3f nA)'
                 % (q, i_pos * 1e9, i_neg * 1e9), fontsize=11, weight='bold')
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(a.out, dpi=150)
    print("")
    print("已写", a.out)


if __name__ == '__main__':
    main()
