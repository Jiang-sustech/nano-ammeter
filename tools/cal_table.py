# -*- coding: utf-8 -*-
"""
逐点标定表: 设定 / 回读 / 三次读数 / 平均 / s / **校准后** / 残差 / 相对残差

    python tools/cal_table.py
    python tools/cal_table.py --sign + --out data/cal_table.csv

为什么有这个脚本
----------------
用户手上有一张表 (设定值/回读值/读数1-3/平均值/s/Δ/δ), 但**缺"用三常数标定后"
的那一列** —— 表里的 Δ 是残差, 不是校准结果本身。

⚠️ 单位坑 (写这个脚本时踩到): `cv_linearity.pred_from()` 返回的是**安培**,
   而从 CSV 读的 `true_nA` / `dev_nA` 是**纳安**。混着算会得到 1e9 倍的
   荒数 (第一次就输出 −5.0e12 pA)。这里统一: 读数一律先转安培再进式(6)。

⚠️ **数据来源必须标明**: 下面每个点标了它来自哪一轮采集。
   5 nA 与 45 nA 只存在于 `cal_sweep` (第一轮), 6~40 nA 来自 `sweep_broad`
   (第二轮)。而三常数是由 **cal_sweep 全部 19 点**拟合的 —— 所以:
       cal_sweep 来的点 = **样本内** (拟合用的就是它们, 残差被压小)
       sweep_broad 来的点 = **样本外**
   **这两类不能混在一张表里当验证结果引用。** 见 data/README.md「误差口径」。
"""
import argparse
import collections
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cv_linearity import load, fit, consts, pred_from

# 三常数由它拟合 (19 点标定)
CAL = 'data/cal_sweep_fit.csv'


def collect(path, key, dev, tru, sign):
    """-> {设定值: [(dev_nA, true_nA, m, n, c1, c2), ...]}"""
    g = collections.OrderedDict()
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                k = float(r[key])
                d = float(r[dev])
                t = float(r[tru])
                m, n = float(r['m']), float(r['n'])
                c1, c2 = float(r['ext1']), float(r['ext2'])
            except (KeyError, ValueError):
                continue
            if k * sign <= 0:
                continue
            g.setdefault(abs(k) / 1000.0 if abs(k) > 100 else abs(k), []).append(
                (d, t, m, n, c1, c2))
    return g


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--sign', default='+', choices=['+', '-'])
    ap.add_argument('--out', default='data/cal_table.csv')
    a = ap.parse_args()
    sg = 1.0 if a.sign == '+' else -1.0

    cp = load(CAL)
    ck = sorted(cp)
    x = fit([r for k in ck for r in cp[k]])
    q, i_pos, i_neg = consts(x)

    # 两轮的数据: cal_sweep 是标定轮, sweep_broad 是另一轮
    cs = collect('data/cal_sweep_raw.csv', 'point_nA', 'dev_nA', 'true_nA', sg)
    sb = collect('data/sweep_broad.csv', 'set_pA', 'dev_ext_nA', 'true_nA', sg)

    print("三常数 (由 %s 的全部 %d 个电流点拟合):" % (CAL, len(ck)))
    print("    q = %.6e C/码    I+ = %+.4f nA    I- = %+.4f nA"
          % (q, i_pos * 1e9, i_neg * 1e9))
    print("方向: %s" % ("正电流" if sg > 0 else "负电流"))
    print("")
    hdr = ("%4s %8s %10s %9s %9s %9s %10s %7s %9s %9s %9s %8s"
           % ("序号", "设定/nA", "回读/nA", "读数1", "读数2", "读数3", "平均Īm",
              "s/pA", "校准后/nA", "Δ/pA", "δ/%", "来源"))
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for i, k in enumerate(sorted(set(cs) | set(sb)), 1):
        # 优先用 sweep_broad (样本外); 只有它没有的点才回落到 cal_sweep
        if k in sb:
            g, src = sb[k], "sweep_broad"
        else:
            g, src = cs[k], "**cal_sweep(样本内)**"
        dev = [d for d, _, _, _, _, _ in g]
        tru = [t for _, t, _, _, _, _ in g]
        im = sum(dev) / len(dev)
        ist = sum(tru) / len(tru)
        s = (math.sqrt(sum((v - im) ** 2 for v in dev) / (len(dev) - 1)) * 1e3
             if len(dev) > 1 else 0.0)
        # ⚠️ 式(6) 吃安培: 先把 nA 转安培
        cal = [pred_from(c1, c2, m, n, x) * 1e9 for _, _, m, n, c1, c2 in g]
        ic = sum(cal) / len(cal)
        D = (ic - ist) * 1e3                      # pA
        d = D / (ist * 1e3) * 100                 # %
        print("%4d %8g %10.4f %9.4f %9.4f %9.4f %10.4f %7.2f %9.4f %+9.3f %+9.4f  %s"
              % (i, sg * k, sg * ist, sg * dev[0], sg * dev[1], sg * dev[2],
                 sg * im, s, sg * ic, D, d, src))
        rows.append(dict(idx=i, set_nA=sg * k, std_nA=sg * ist,
                         m1=sg * dev[0], m2=sg * dev[1], m3=sg * dev[2],
                         im_nA=sg * im, s_pA=s, cal_nA=sg * ic,
                         delta_pA=D, delta_pct=d, source=src))

    print("-" * len(hdr))
    print("Δ = 校准后 − 回读;  δ = Δ/回读 x 100")
    print("")

    oos = [r for r in rows if r['source'] == 'sweep_broad']
    ins = [r for r in rows if r['source'] != 'sweep_broad']
    def rep(tag, v):
        if not v:
            return
        rms = math.sqrt(sum(r['delta_pA'] ** 2 for r in v) / len(v))
        mx = max(abs(r['delta_pA']) for r in v)
        print("  %-24s n=%2d   RMS %6.3f pA   max|Δ| %6.3f pA"
              % (tag, len(v), rms, mx))
    print("分来源统计 (**不要混着引用**):")
    rep("样本外 (sweep_broad)", oos)
    rep("样本内 (cal_sweep)", ins)
    print("")
    print("  未校准口径对照: 校准后 vs 回读 的差已在上表; '校准前'就是 Īm,")
    print("  它与回读的差 = (Īm − 回读), 上表自己减一下即可 (含 +1% 增益误差)。")

    if a.out:
        with open(a.out, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("")
        print("已写", a.out)


if __name__ == '__main__':
    main()
