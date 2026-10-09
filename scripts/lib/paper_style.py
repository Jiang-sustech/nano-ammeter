# -*- coding: utf-8 -*-
"""
本次论文制图规范 —— 代码实现。规范正文见 image_paper/IMAGE_SPEC.md。

四张论文图统一走这里。**不要再各脚本各写一套 rcParams** —— 那样字号和图幅
必然走样, 上一版就是这么乱的。

模板: data/cal_162.png 的做法 (图名落正下方、正红/蓝/黑三色、单位写在轴标签里),
按论文要求删掉说明性文字, 并把字号与图幅钉死。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ---------------- 字体与配色 ----------------
FONT = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
RED, BLUE, BLACK = '#FF0000', '#0000FF', '#000000'

# ---------------- 图幅 (cm) ----------------
# 双栏跨栏图, 宽度全稿统一; 高度逐张定, 见 IMAGE_SPEC.md
W_CM = 17.0
MARGIN_LEFT_CM = 1.45       # 留给纵轴标签+刻度
MARGIN_RIGHT_CM = 0.30
MARGIN_TOP_CM = 0.20        # 顶部无标题时
TITLE_CM = 0.60             # 顶部有 (a)(b) 分格标题时

CAP_LINE_CM = 0.40          # 图名每行占高
CAP_BOTTOM_CM = 0.12        # 图名底边距画布底边
CAP_GAP_CM = 0.35           # 图名顶 与 横轴标签底 之间的间隙
TICK_CM = 0.45              # 横轴刻度数字占高  ← 一开始漏了这个, 图名和 xlabel 撞了
XLABEL_CM = 0.55            # 横轴标签占高

# ---------------- 字号 (pt) ----------------
FS_CAPTION = 10.0           # 图名
FS_LABEL = 9.0              # 轴标签
FS_PANEL = 9.0              # (a)(b) 分格标题
FS_TICK = 8.0               # 刻度数字
FS_LEGEND = 8.0             # 图例

# ---------------- 线宽 / 标记 ----------------
LW_MAIN = 1.2               # 主参考线
LW_THIN = 0.9               # 辅助线
LW_GRID = 0.4
MS = 4.0                    # 散点标记尺寸
MEW = 0.5                   # 标记描边宽

GRID_ALPHA = 0.22
DPI = 300                   # 投稿分辨率


def apply():
    """套用规范。**必须在所有 import 之后调用** —— 被 import 的旧脚本会在
    模块级改 rcParams, 这里要盖回去。"""
    plt.rcParams.update({
        "font.sans-serif": FONT,
        "axes.unicode_minus": False,
        "axes.labelsize": FS_LABEL,
        "axes.titlesize": FS_PANEL,
        "xtick.labelsize": FS_TICK,
        "ytick.labelsize": FS_TICK,
        "legend.fontsize": FS_LEGEND,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.major.size": 3.0,
        "ytick.major.size": 3.0,
        "grid.linewidth": LW_GRID,
        "lines.linewidth": LW_MAIN,
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
    })


def figure(h_cm, cap_lines=1, xlabel=True, title=False):
    """建画布并留好图名区。返回 (fig, cap_y) —— cap_y 是图名**底边**的位置
    (figure 分数), 交给 caption() 用。

    这里显式算边距、不用 tight_layout: tight_layout 会按它自己的规矩重排,
    图幅就不再是规范里定的那个尺寸了。论文图必须尺寸可复现。

    底边预算 = 图名距底 + 图名块 + 间隙 + (刻度数字 + 横轴标签)
    顶边预算 = 有分格标题 0.60cm / 无 0.20cm
    """
    fig = plt.figure(figsize=(W_CM / 2.54, h_cm / 2.54))
    top_cm = TITLE_CM if title else MARGIN_TOP_CM
    bottom_cm = (CAP_BOTTOM_CM + CAP_LINE_CM * cap_lines + CAP_GAP_CM
                 + (TICK_CM + XLABEL_CM if xlabel else 0.0))
    fig.subplots_adjust(
        left=MARGIN_LEFT_CM / W_CM,
        right=1 - MARGIN_RIGHT_CM / W_CM,
        top=1 - top_cm / h_cm,
        bottom=bottom_cm / h_cm,
    )
    return fig, CAP_BOTTOM_CM / h_cm


def caption(fig, cap_y, text, fs=FS_CAPTION):
    """图名落**正下方居中** (模板 cal_162 的做法)。"""
    fig.text(0.5, cap_y, text, ha='center', va='bottom',
             fontsize=fs, color=BLACK)


def grid(ax, which='major'):
    ax.grid(alpha=GRID_ALPHA, which=which, color=BLACK, lw=LW_GRID)
