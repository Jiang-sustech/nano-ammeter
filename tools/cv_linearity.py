# -*- coding: utf-8 -*-
"""
线性度的**样本外**评估 —— 标定组与验证组必须分开

    python tools/cv_linearity.py --fit data/cal_sweep_fit.csv
    python tools/cv_linearity.py --fit data/cal_sweep_fit.csv --loo

为什么单独有这个脚本
--------------------
2026-09-15 用户指出: "标定组和用来验证线性模型的要分开"。

当时我报的线性度 0.00252 % FS 是**样本内残差** —— 拟合用的 3 个参数就是照着
同一组 57 行调出来的, 拿它的残差当线性度是**循环论证**。用户当场要求把两组
彻底分开。

**另一个问题更严重**: 那个拆分当时是我临时写的 heredoc, 没存文件、没提交,
所以那个数字**不可复现**。用户质疑得对。这个脚本就是把它固化成代码。

两种拆分 (都只做**事后分析**, 数据一次性采完即可 —— 用户明确说过可以):

  --split odd    固定奇偶拆分: 按电流排序后取偶数索引做标定、奇数索引做验证。
                 简单直观, 但**任意** —— 换个拆法结论会变, 所以还要看 LOO。
  --loo          留一交叉验证: 每次留出一个**电流点**(它全部 3 次重复一起留出),
                 用其余 18 点拟合再预测被留出的点。更稳健, 是首选。

⚠️ 留出的单位是**电流点**不是**行**: 同一个电流的 3 次重复不独立
(同相位、同档位), 按行留一会把信息泄漏进训练集, 结果偏乐观。

判读
----
本脚本报的是**模型预测能力**: 用一组电流定出的 q/I± 去预测另一组电流,
偏差多大。这正是"装置的线性度"该有的定义。
"""
import argparse
import collections
import csv
import glob
import math
import os
import sys

T_INT = 160e-6          # TIM6 周期 (s)
VREF = 3.3              # ADS8866 基准 (V)


def load(path):
    """读 _fit.csv -> {电流(nA, 圆整): [(a1, a2, I_true), ...]}"""
    pts = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            I = float(r['I_true'])
            c1, c2 = float(r['c1']), float(r['c2'])
            m, n = float(r['m']), float(r['n'])
            nt = m + n
            a1 = VREF * (c2 - c1) / 65536.0 / (nt * T_INT)
            a2 = m / nt
            pts.setdefault(round(I * 1e9, 4), []).append((a1, a2, I))
    return pts


def pred_from(c1, c2, m, n, x):
    """式(6)。x = [M, b0, b2] (安培制)"""
    nt = m + n
    a1 = VREF * (c2 - c1) / 65536.0 / (nt * T_INT)
    return x[0] * a1 + x[1] + x[2] * (m / nt)


def eval_npz(pattern, x, max_bad=3):
    """拿**旧的实测 npz** 做独立验证。

    比 --loo 更硬: 旧 npz 来自**另一个会话、另一版固件、源表手动设的**,
    与拟合所用的那批数据在时间和流程上都完全独立。
    排除 result_mode != 1 (M0/M3 手动模式) 和坏读多的文件。
    """
    import numpy as np
    by = collections.defaultdict(list)
    used = skipped = 0
    for f in sorted(glob.glob(pattern)):
        d = np.load(f, allow_pickle=True)
        t_nA = float(d['true_na'])
        if not math.isfinite(t_nA):
            skipped += 1; continue
        if int(d['result_mode']) != 1:
            skipped += 1; continue
        m, n = int(d['raw_m']), int(d['raw_n'])
        c1, c2 = int(d['raw_ext1']), int(d['raw_ext2'])
        if min(m, n) < 0 or c1 < 0:
            skipped += 1; continue
        if (d['wave_ext'] >= 65535).sum() > max_bad:
            skipped += 1; continue
        by[round(t_nA, 3)].append((pred_from(c1, c2, m, n, x) - t_nA * 1e-9) * 1e12)
        used += 1
    return by, used, skipped


def lstsq(A, y):
    """三列正规方程的高斯消元解 (只解 3x3, 不引 numpy 依赖)"""
    k = len(A[0])
    G = [[sum(A[i][p] * A[i][q] for i in range(len(A))) for q in range(k)]
         for p in range(k)]
    b = [sum(A[i][p] * y[i] for i in range(len(A))) for p in range(k)]
    M = [G[i][:] + [b[i]] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        for cc in range(c, k + 1):
            M[c][cc] /= pv
        for r in range(k):
            if r != c:
                f = M[r][c]
                for cc in range(c, k + 1):
                    M[r][cc] -= f * M[c][cc]
    return [M[i][k] for i in range(k)]


def fit(rows):
    A = [[a1, 1.0, a2] for a1, a2, _ in rows]
    y = [I for _, _, I in rows]
    return lstsq(A, y)


def pred(x, a1, a2):
    return x[0] * a1 + x[1] + x[2] * a2


def consts(x):
    q = x[0] * VREF / 65536.0
    i_neg = -x[1]
    return q, i_neg - x[2], i_neg


def report(tag, pairs):
    """pairs: [(I_true_A, residual_A)]"""
    e = [r for _, r in pairs]
    if not e:
        print("  (无点)")
        return
    rms = math.sqrt(sum(t * t for t in e) / len(e))
    mx = max(abs(t) for t in e)
    print("  %s: n=%d  RMS %.3f pA  max|.| %.3f pA" % (tag, len(e), rms * 1e12, mx * 1e12))
    return rms, mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fit', default='data/cal_sweep_fit.csv',
                    help='被评估的数据 (验证组)')
    ap.add_argument('--train', default=None,
                    help='用**另一个文件**的全部数据做标定, 再拿 --fit 验证。'
                         '这是最强的一种样本外 —— 跨电流区间外推, 例如用 '
                         '>=5 nA 的标定去预测 <1 nA 的数据。')
    ap.add_argument('--split', default='odd',
                    help="'odd' = 按电流排序后偶数索引做标定 / 奇数索引做验证; "
                         "或直接给标定点的逗号分隔列表 (nA)")
    ap.add_argument('--fs-na', type=float, default=90.0, help='全量程 (nA), 用于算 %%FS')
    ap.add_argument('--loo', action='store_true', help='再做一次留一交叉验证')
    ap.add_argument('--npz', default=None,
                    help='用旧的实测 npz (glob) 做**跨会话**独立验证, 例如 '
                         "data/raw_09_13_*.npz 。比 --loo 更硬 —— "
                         "那批数据来自另一版固件、另一个会话。")
    a = ap.parse_args()

    if not os.path.exists(a.fit):
        sys.exit("找不到 %s" % a.fit)
    pts = load(a.fit)
    keys = sorted(pts)
    nrow = sum(len(v) for v in pts.values())

    print("=" * 78)
    print("线性度的样本外评估   (标定组 / 验证组 严格分开)")
    print("=" * 78)
    print("数据: %s   %d 个电流点, %d 行" % (a.fit, len(keys), nrow))
    print("")

    # ---------------- 跨文件验证 (--train) ----------------
    if a.train:
        tpts = load(a.train)
        tkeys = sorted(tpts)
        x = fit([r for k in tkeys for r in tpts[k]])
        q, i_pos, i_neg = consts(x)
        print("--- 跨文件验证: 用 %s 的**全部 %d 点**做标定 ---"
              % (a.train, len(tkeys)))
        print("  **标定组**: %s" % "  ".join("%+g" % k for k in tkeys))
        print("  q  = %.6e C/码   I+ = %+.4f nA   I- = %+.4f nA"
              % (q, i_pos * 1e9, i_neg * 1e9))
        print("")
        print("--- 拿它预测 %s 的**全部 %d 点** (完全没参与拟合) ---"
              % (a.fit, len(keys)))
        print("%12s %14s %14s %10s" % ("真值(nA)", "残差(pA)", "相对(%)", "MODE"))
        pairs = []
        for k in keys:
            for a1, a2, I in pts[k]:
                e = pred(x, a1, a2) - I
                pairs.append((I, e))
                print("%12.5f %14.3f %14.4f"
                      % (I * 1e9, e * 1e12, e / I * 100 if abs(I) > 1e-12 else float('nan')))
        r = report("样本外", pairs)
        if r:
            print("      max|残差|/FS = %.5f %%   (FS = %g nA)"
                  % (r[1] / (a.fs_na * 1e-9) * 100, a.fs_na))
        return

    # ---------------- 拆分 ----------------
    if a.split == 'odd':
        cal_k = [k for i, k in enumerate(keys) if i % 2 == 0]
        val_k = [k for i, k in enumerate(keys) if i % 2 == 1]
        how = "固定奇偶拆分 (按电流排序后取偶数索引做标定)"
    else:
        want = [round(float(x), 4) for x in a.split.split(',')]
        cal_k = [k for k in keys if k in want]
        val_k = [k for k in keys if k not in want]
        how = "显式指定标定点"

    print("--- 拆分方式: %s ---" % how)
    print("  **标定组 (%2d 点)**: %s" % (len(cal_k), "  ".join("%+g" % k for k in cal_k)))
    print("  **验证组 (%2d 点)**: %s" % (len(val_k), "  ".join("%+g" % k for k in val_k)))
    print("")

    if not cal_k or not val_k:
        sys.exit("拆分结果有一边为空, 检查 --split")

    x = fit([r for k in cal_k for r in pts[k]])
    q, i_pos, i_neg = consts(x)
    print("  用**标定组 %d 行**拟合出的常数:" % sum(len(pts[k]) for k in cal_k))
    print("      q  = %.6e C/码" % q)
    print("      I+ = %+.4f nA     I- = %+.4f nA" % (i_pos * 1e9, i_neg * 1e9))
    print("")

    # ---------------- 验证组 ----------------
    print("--- 在**未参与拟合**的验证组上评估 ---")
    print("%12s %14s %14s" % ("真值(nA)", "残差(pA)", "相对(%)"))
    pairs = []
    for k in val_k:
        for a1, a2, I in pts[k]:
            e = pred(x, a1, a2) - I
            pairs.append((I, e))
            if abs(I) > 1e-12:
                print("%12.4f %14.3f %14.4f" % (I * 1e9, e * 1e12, e / I * 100))
            else:
                print("%12.4f %14.3f %14s" % (I * 1e9, e * 1e12, "(零点)"))
    r = report("样本外", pairs)
    if r:
        print("      max|残差|/FS = %.5f %%   (FS = %g nA)"
              % (r[1] / (a.fs_na * 1e-9) * 100, a.fs_na))

    # ---------------- 留一交叉验证 ----------------
    if a.loo:
        print("")
        print("--- 留一交叉验证 (每次留出 1 个**电流点**, 它的 3 次重复一起留出) ---")
        loo = []
        for k in keys:
            tr = [r for kk in keys if kk != k for r in pts[kk]]
            xk = fit(tr)
            for a1, a2, I in pts[k]:
                e = pred(xk, a1, a2) - I
                loo.append((k, I, e))
        print("%12s %14s %14s" % ("留出的点", "残差均值(pA)", "相对(%)"))
        for k in keys:
            v = [e for kk, I, e in loo if kk == k]
            if not v:
                continue
            ev = [e for kk, I, e in loo if kk == k and abs(I) > 1e-12]
            if ev:
                I0 = [I for kk, I, e in loo if kk == k][0]
                print("%12.4f %14.3f %14.4f"
                      % (k, sum(ev) / len(ev) * 1e12, (sum(ev) / len(ev)) / I0 * 100))
            else:
                print("%12.4f %14.3f %14s" % (k, sum(v) / len(v) * 1e12, "(零点)"))
        pairs = [(I, e) for _, I, e in loo if abs(I) > 1e-12]
        r = report("样本外", pairs)
        if r:
            print("      max|残差|/FS = %.5f %%   (FS = %g nA)"
                  % (r[1] / (a.fs_na * 1e-9) * 100, a.fs_na))

    # ---------------- 跨会话独立验证 (--npz) ----------------
    if a.npz:
        print("")
        print("--- 跨会话独立验证: %s ---" % a.npz)
        # 用**全部**数据拟合常数 (这是实际会烧进固件的那组)
        xall = fit([r for k in keys for r in pts[k]])
        q, i_pos, i_neg = consts(xall)
        print("  常数来自: %s 的全部 %d 点 (q=%.6e)" % (a.fit, len(keys), q))
        by, used, skipped = eval_npz(a.npz, xall)
        print("  用到 %d 个文件, 跳过 %d 个 (非 MODE=1 / 缺字段 / 坏读多)" % (used, skipped))
        if used == 0:
            print("  ** glob 没匹配到可用文件 —— 检查路径。"
                  "注意 cal_sweep.py 只写 CSV 不写 npz。**")
            return
        print("%12s %6s %16s %14s" % ("真值(nA)", "n", "残差均值(pA)", "相对(%)"))
        allr = []
        for k in sorted(by):
            v = by[k]; mu = sum(v) / len(v); allr += v
            print("%12.3f %6d %16.3f %14.4f" % (k, len(v), mu, mu / (k * 1e3) * 100))
        # 注意: allr 里的数**已经是 pA**, 不要再走 report() (它按安培换算)
        print("      样本外(跨会话): n=%d  RMS %.3f pA  max|.| %.3f pA"
              % (len(allr), math.sqrt(sum(e * e for e in allr) / len(allr)),
                 max(abs(e) for e in allr)))
        pos = [e for k in by if k > 0 for e in by[k]]
        neg = [e for k in by if k < 0 for e in by[k]]
        if pos and neg:
            print("      正电流 %+.3f pA (n=%d)   负电流 %+.3f pA (n=%d)"
                  % (sum(pos) / len(pos), len(pos), sum(neg) / len(neg), len(neg)))
            print("      -> 两者不等 = 极性相关偏置; 解出 偏置 %.3f pA, 增益项 %.5f pA/nA"
                  % ((sum(pos) / len(pos) + sum(neg) / len(neg)) / 2,
                     (sum(pos) / len(pos) - sum(neg) / len(neg)) / (2 * 25.0)))

    print("")
    print("=" * 78)
    print("**只有上面这些样本外数字可以引用。**")
    print("直接拿拟合用的那组数据算残差(样本内)会偏乐观 —— 拟合参数就是照着")
    print("那些点调的, 那等于用考题答案去判考卷。")


if __name__ == '__main__':
    main()
