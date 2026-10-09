# -*- coding: utf-8 -*-
"""
光电效应原始数据图 —— 左表格 + 右散点

    python scripts/plotting/photo_data_fig.py                 # 默认 365 nm
    python scripts/plotting/photo_data_fig.py --lam 577 -o out.png

版式(用户 2026-09-23 指定):
    左半   两列原始数据表: 偏置电压 / V  与  电流 / pA
    右半   同一批数据的散点图

数据来源与 `scripts/plotting/photoelectric_fig.py` 的 DATA 同源(用户直接给出)。
本脚本**只画图, 不做任何拟合或修正** —— 原始值原样呈现。
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# (V, I/pA) —— 原样录入
DATA = {
    365.0: [
        (-1.998, -118.7), (-1.898, -97.8), (-1.798, -56.2), (-1.698, 8.9),
        (-1.598, 90.1), (-1.498, 197.2), (-1.398, 322.0), (-1.298, 450.0),
        (-1.198, 603.0), (-1.098, 775.0), (-0.998, 954.0), (-0.898, 1153.0),
        (-0.798, 1397.0), (-0.698, 1615.0), (-0.598, 1896.0), (-0.498, 2130.0),
        (-0.398, 2440.0), (-0.298, 2690.0), (-0.198, 3000.0), (-0.098, 3300.0),
    ],
    436.0: [
        (-1.998, -72.2), (-1.898, -69.5), (-1.798, -68.6), (-1.698, -66.6),
        (-1.598, -65.6), (-1.498, -60.7), (-1.398, -47.7), (-1.298, -17.8),
        (-1.198, 38.0), (-1.098, 121.6), (-0.998, 230.0), (-0.898, 354.0),
        (-0.798, 547.0), (-0.698, 675.0), (-0.598, 862.0), (-0.498, 1055.0),
        (-0.398, 1269.0), (-0.298, 1481.0), (-0.198, 1672.0), (-0.098, 1939.0),
    ],
    # 546 nm 用的是**带细扫的那份** (与 photoelectric_fig.py 的 DATA 同源),
    # 不是用户表格里那一列 —— 前者在拐点附近加了密, 后者是均匀 0.1 V。
    # 每点第三项是分组标签, 用来在散点图上把细扫段标出来。
    546.0: [
        (-1.103, -29.0, '粗扫'), (-1.000, -28.0, '粗扫'), (-0.902, -28.0, '粗扫'),
        (-0.800, -27.0, '粗扫'), (-0.700, -24.0, '粗扫'), (-0.601, -11.0, '粗扫'),
        (-0.500, 30.0, '粗扫'), (-0.400, 107.0, '粗扫'), (-0.300, 204.0, '粗扫'),
        (-0.200, 313.0, '粗扫'), (-0.100, 426.0, '粗扫'),
        (-0.040, 475.0, '中间段'), (-0.031, 485.0, '中间段'),
        (-0.020, 495.0, '中间段'), (-0.012, 506.0, '中间段'),
        (0.010, 522.0, '中间段'),
        (-0.590, -9.0, '拐点细扫'), (-0.580, -6.3145, '拐点细扫'),
        (-0.570, -3.0147, '拐点细扫'), (-0.560, 0.7745, '拐点细扫'),
        (-0.550, 4.8873, '拐点细扫'),
    ],
}

# 分组 -> (颜色, 标记, 大小); 只有带分组标签的数据才用得上
GROUP_STYLE = {
    '粗扫':     ('#C00000', 'o', 46),
    '中间段':   ('#C00000', 'o', 46),
    '拐点细扫': ('#0040C0', 's', 60),
}
GROUP_ORDER = ['粗扫', '中间段', '拐点细扫']   # 图例顺序


def main():
    ap = argparse.ArgumentParser(description="光电效应原始数据图 (左表右散点)")
    ap.add_argument('--lam', type=float, default=365.0, help='波长 nm')
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args()
    if a.out is None:
        a.out = 'data/photo_data_%gnm.png' % a.lam
    if a.lam not in DATA:
        sys.exit("没有 %g nm 的数据 (现有: %s)"
                 % (a.lam, ', '.join('%g' % k for k in sorted(DATA))))

    pts = DATA[a.lam]
    # 兼容 (V, I) 与 (V, I, 分组) 两种写法; 表格按电压升序, 便于查阅
    pts = [(p[0], p[1], p[2] if len(p) > 2 else '') for p in pts]
    pts.sort(key=lambda p: p[0])
    V = [p[0] for p in pts]
    I = [p[1] for p in pts]

    fig = plt.figure(figsize=(15.0, 8.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.55], wspace=0.16)
    axT = fig.add_subplot(gs[0, 0])
    axP = fig.add_subplot(gs[0, 1])

    # ---------------- 左: 数据表 ----------------
    axT.axis('off')
    axT.set_title('%g nm 原始数据' % a.lam, fontsize=14, pad=14)
    cell = [['%.3f' % v, '%.1f' % i] for v, i, _ in pts]
    tb = axT.table(cellText=cell,
                   colLabels=['偏置电压 / V', '电流 / pA'],
                   cellLoc='center', colLoc='center',
                   loc='center', bbox=[0.06, 0.02, 0.88, 0.90])
    tb.auto_set_font_size(False)
    tb.set_fontsize(11.5)
    for (r, c), cl in tb.get_celld().items():
        cl.set_edgecolor('#B0B0B0')
        cl.set_linewidth(0.7)
        if r == 0:                       # 表头
            cl.set_facecolor('#EDEDED')
            cl.set_text_props(weight='bold')
            cl.set_height(cl.get_height() * 1.25)
        elif r % 2 == 0:                 # 隔行浅灰, 便于横向读数
            cl.set_facecolor('#F8F8F8')

    # ---------------- 右: 散点 ----------------
    groups = [g for g in GROUP_ORDER if any(p[2] == g for p in pts)]
    for _, _, g in pts:                       # 未登记的组名也照画, 排在后面
        if g and g not in groups:
            groups.append(g)
    if groups:
        for g in groups:
            sub = [(v, i) for v, i, gg in pts if gg == g]
            col, mk, sz = GROUP_STYLE.get(g, ('#C00000', 'o', 46))
            axP.scatter([s[0] for s in sub], [s[1] for s in sub], s=sz,
                        facecolor='none', edgecolor=col, marker=mk,
                        linewidths=1.6, zorder=3,
                        label='%s（%d 点）' % (g, len(sub)))
    else:
        axP.scatter(V, I, s=52, facecolor='none', edgecolor='#C00000',
                    linewidths=1.6, zorder=3, label='实测点')
    axP.axhline(0, color='#666666', lw=0.9)
    axP.axvline(0, color='#666666', lw=0.9)
    axP.set_xlabel('偏置电压 / V', fontsize=13)
    axP.set_ylabel('电流 / pA', fontsize=13)
    axP.set_title('%g nm 伏安特性（%d 个点）' % (a.lam, len(pts)), fontsize=14, pad=14)
    axP.grid(alpha=.28, lw=.7)
    axP.tick_params(labelsize=10.5)
    axP.legend(fontsize=11, loc='lower right', framealpha=.95)

    # 细扫段在主图上挤成一小团, 加个放大窗才看得出"加密"这件事。
    # ⚠️ 位置: **左上角**是唯一的大片空白 —— 曲线从左上方的平台一路升到右上,
    #    右下角在 x>-0.3 处也没有点, 但那里要留给图例。先前放在中下偏右,
    #    正好压住 -0.6 ~ -0.2 V 那段实测点。
    fine = [(v, i) for v, i, g in pts if g == '拐点细扫']
    if len(fine) >= 3:
        axI = axP.inset_axes([0.05, 0.44, 0.36, 0.42])
        axI.scatter([p[0] for p in fine], [p[1] for p in fine], s=58,
                    facecolor='none', edgecolor='#0040C0', marker='s',
                    linewidths=1.7, zorder=3)
        axI.axhline(0, color='#666666', lw=0.9)
        axI.grid(alpha=.30, lw=.6)
        axI.tick_params(labelsize=8.5)
        axI.set_title('拐点细扫放大（步长 0.01 V）', fontsize=10, pad=5)
        axI.set_xlabel('V', fontsize=9); axI.set_ylabel('pA', fontsize=9)
        axP.indicate_inset_zoom(axI, edgecolor='#909090', alpha=.7)

    fig.savefig(a.out, dpi=150, bbox_inches='tight')
    print('已写', a.out, '(%d 个点)' % len(pts))


if __name__ == '__main__':
    main()
