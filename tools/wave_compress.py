# -*- coding: utf-8 -*-
"""
整窗波形到底能压多少 —— 用实测数据量, 不猜

    python tools/wave_compress.py

背景
----
每次测量回传整窗 6250 点 x 2 字节 = 12.5 kB, 115200 波特下 1.1 秒, 2 Mbps 下 0.063 秒。
用户问: 小电流时相邻码值会重复, 能不能靠这个把数据量压下去?

⚠️ 先验一个前提: 目标电流段是 **5~45 nA**, 不是 pA。这一段锯齿的斜率是
   **每拍几百个码**, 相邻点**根本不相等**(实测 +10 nA 处"相邻差 = 0"的个数是 **0**)。
   所以"压重复值"在这个段上不成立 —— 但**别的路子成立得多**, 见下。

本脚本比较四种编码:
    (a) 原始        每点 16 bit
    (b) 差分+位打包  存 c[i]-c[i-1], 按实际取值范围定宽
    (c) 差分+RLE    对重复的差分做游程
    (d) 转折点法     只存**参考换向的拍号**, 曲线靠分段线性重建
                     —— 因为波形本质是"斜率恒定的锯齿", 全部信息就是换向时刻
"""
import glob
import math
import sys
from collections import Counter

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')


def unwrap_step(w):
    s = np.diff(w.astype(np.int64))
    s = s.copy()
    s[s > 32768] -= 65536
    s[s < -32768] += 65536
    return s


def turnarounds(step, win=9):
    """参考换向的拍号 (斜率符号变化的点)。返回下标数组。"""
    k = np.ones(win) / win
    sm = np.convolve(np.asarray(step, dtype=float), k, mode='same')
    d = np.where(sm > 0, 1, -1)
    idx = np.nonzero(d[1:] != d[:-1])[0] + 1
    return idx


def bits_needed(lo, hi):
    return max(1, int(math.ceil(math.log2(max(abs(lo), abs(hi), 1) * 2 + 1))))


def main():
    fs = sorted(glob.glob('data/raw_09_13_*.npz'))
    rows = []
    for f in fs:
        d = np.load(f, allow_pickle=True)
        t = float(d['true_na'])
        if not math.isfinite(t) or int(d['result_mode']) != 1:
            continue
        w = d['wave_ext'].astype(np.int64)
        if len(w) != 6250 or (w >= 65535).sum() > 3:
            continue
        rows.append((t, w))
    rows.sort(key=lambda r: r[0])
    if not rows:
        sys.exit("没找到可用的整窗数据")

    n = len(rows[0][1])
    raw_bits = n * 16
    print("=" * 92)
    print("整窗波形可压缩性 —— 实测 (%d 条波形, 每条 %d 点)" % (len(rows), n))
    print("=" * 92)
    print("%9s %9s %9s %9s %10s %11s %11s"
          % ("真值nA", "相邻相等", "差分种数", "熵bit/点", "差分位打包",
             "差分+RLE", "转折点法"))
    print("%9s %9s %9s %9s %10s %11s %11s"
          % ("", "(个数)", "", "", "(字节)", "(字节)", "(字节)"))
    print("-" * 92)

    tot = [0, 0, 0, 0, 0]
    for t, w in rows:
        step = unwrap_step(w)
        eq = int((step == 0).sum())

        lo, hi = int(step.min()), int(step.max())
        bw = bits_needed(lo, hi)
        pack_bytes = math.ceil(n * bw / 8.0)

        cnt = Counter(step.tolist())
        ent = -sum(c / n * math.log2(c / n) for c in cnt.values())
        # 差分熵 -> 熵编码的**理论上限**(Huffman/算术编码能到的最小字节数)
        ent_bytes = math.ceil(n * ent / 8.0)

        # 差分 + RLE: 对每个"值"存 (值, 连续次数)。用 varint 粗估:
        # 值按 bw 位, 次数 1 字节(超过 255 再用变长, 这里实测很少)
        runs = []
        cur, run = step[0], 1
        for s in step[1:]:
            if s == cur:
                run += 1
            else:
                runs.append((cur, run))
                cur, run = s, 1
        runs.append((cur, run))
        rle_bytes = len(runs) * (math.ceil(bw / 8.0) + 1)

        ta = turnarounds(step)
        ta_bytes = len(ta) * 2          # 拍号 0..6249 用 2 字节

        tot[0] += pack_bytes
        tot[1] += rle_bytes
        tot[2] += ta_bytes
        tot[3] += 1
        tot[4] += ent_bytes
        print("%+9.3f %9d %9d %9.1f %10d %11d %11d"
              % (t, eq, len(cnt), ent, pack_bytes, rle_bytes, ta_bytes))

    m = len(rows)
    print("-" * 92)
    print("%9s %9s %9s %9s %10d %11d %11d   (原始 = %d 字节)"
          % ("平均", "", "", "", tot[0] // m, tot[1] // m, tot[2] // m, raw_bits // 8))
    print("")
    print("压缩比 (相对原始 %d 字节):" % (raw_bits // 8))
    for nm, v in (("差分位打包", tot[0] // m), ("差分+RLE", tot[1] // m),
                  ("转折点法", tot[2] // m), ("熵编码(理论上限)", tot[4] // m)):
        print("    %-18s %6d 字节   = 原来的 %.1f%%  (省 %.0f%%)"
              % (nm, v, 100.0 * v / (raw_bits // 8), 100.0 * (1 - v / (raw_bits // 8))))
    print("")
    print("⚠️ 转折点法那一列只是**编码长度**, 没算重建误差 —— 见下面那节。")
    print("")
    report_reconstruct(rows)


def report_reconstruct(rows):
    """转折点法重建误差 —— 省 99% 是没用的, 除非端点码精度够。

    式(6) 用的就是端点码, 而残差结构才 1.3 pA (= 83 个码, 1 码 = 0.0156 pA)。
    """
    print("=" * 92)
    print("转折点法的**重建误差** (中位斜率线性重建 vs 真实曲线)")
    print("=" * 92)
    print("%9s %8s %12s %12s %12s %14s"
          % ("真值nA", "转折点数", "max|误差|(码)", "RMS(码)", "端点误差(码)", "端点误差(pA)"))
    print("-" * 92)
    mx = []
    for t, w in rows:
        ta = turnarounds(unwrap_step(w))
        r = reconstruct_error(w, ta)
        if r is None:
            continue
        mx.append(r["end"])
        print("%+9.3f %8d %12.0f %12.0f %12.0f %14.2f"
              % (t, r["n_ta"], r["max_abs"], r["rms"], r["end"], r["end"] * 0.0156))
    print("-" * 92)
    if mx:
        print("  端点误差绝对值平均 %.0f 码 = %.1f pA"
              % (np.mean(np.abs(mx)), np.mean(np.abs(mx)) * 0.0156))
    print("  参照: 要解释的残差结构是 **1.3 pA** (= 83 码); 1 码 = 0.0156 pA")


def reconstruct_error(w, ta):
    """用**中位斜率**在转折点之间线性重建, 量它偏多少。

    这是转折点法能不能用的唯一判据: 省 99% 是没用的, 除非重建出来的
    端点码精度够 —— 式(6) 用的就是端点码, 而残差结构才 1.3 pA
    (= 83 个码, 因为 1 码 = 0.0156 pA)。
    """
    step = unwrap_step(w)
    segs = np.split(step, ta)
    ups = [s for s in segs if len(s) and s.mean() > 0]
    dns = [s for s in segs if len(s) and s.mean() < 0]
    if not ups or not dns:
        return None
    su = float(np.median([np.median(s) for s in ups]))
    sd = float(np.median([np.median(s) for s in dns]))
    rec = np.empty(len(w), dtype=float)
    rec[0] = w[0]
    bounds = list(ta) + [len(w) - 1]
    prev = 0
    for b in bounds:
        slope = su if (b > prev and step[prev:b].mean() > 0) else sd
        for i in range(prev + 1, b + 1):
            rec[i] = rec[i - 1] + slope
        prev = b
    # 逐点比, 但要先解绕真实值
    tru = w[0] + np.concatenate([[0], np.cumsum(step)]).astype(float)
    err = rec - tru
    return dict(max_abs=float(np.abs(err).max()),
                rms=float(np.sqrt((err ** 2).mean())),
                end=float(err[-1]), n_ta=len(ta))


if __name__ == '__main__':
    main()
