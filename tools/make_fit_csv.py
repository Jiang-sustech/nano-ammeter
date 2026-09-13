# -*- coding: utf-8 -*-
"""
把 nanoammeter_capture.py 采下的一堆 npz 汇总成 fit_constants.m 要的 CSV。

    python tools/make_fit_csv.py                     # 扫当前目录
    python tools/make_fit_csv.py -d 采集数据/         # 扫指定目录
    python tools/make_fit_csv.py --path int          # 用内置 ADC 那一路
    python tools/make_fit_csv.py -o fit_data.csv

输出表头: I_true,c1,c2,m,n

    I_true  已知输入电流 (A)        <- npz 里的 true_na (nA) 换算过来
    c1,c2   窗口两端原始码          <- 结果行 RAW 段的 INT1/INT2 或 EXT1/EXT2
    m,n     POS/NEG 周期数          <- 同一个 RAW 段

**为什么不能手工抄**: 每个 npz 要取 4 个数, 而且必须和"当时的已知输入电流"
严格配对。抄错一行整条拟合就歪了, 而且歪得不明显。让机器配对。

哪些文件会被跳过 (脚本会逐条说明原因):
  * 没有 RAW 段        —— 老固件, 或者跑的是小电流模式 (MODE=2)
  * 没有 --true=       —— 采的时候没记真值, 事后对不上
  * 结果行是 TIMEOUT   —— 数值本身无意义
"""
import argparse
import glob
import os

import numpy as np

RAW_KEYS = ("raw_m", "raw_n", "raw_int1", "raw_int2", "raw_ext1", "raw_ext2")


def load_one(path, use_ext):
    """读一个 npz; 返回 (I_true_A, c1, c2, m, n) 或 (None, 跳过原因)"""
    try:
        z = np.load(path, allow_pickle=True)
    except Exception as e:                       # noqa: BLE001
        return None, "读不开: %s" % e

    missing = [k for k in RAW_KEYS if k not in z.files]
    if missing:
        return None, "没有 RAW 段 (老固件?)"

    if "true_na" not in z.files:
        return None, "没记已知真值 (采的时候没加 --true=)"
    true_na = float(z["true_na"])
    if not np.isfinite(true_na):
        return None, "没记已知真值 (采的时候没加 --true=)"

    try:
        if bool(z["result_timeout"]):
            return None, "结果行是 TIMEOUT, 数值无意义"
    except KeyError:
        pass

    m = int(z["raw_m"])
    n = int(z["raw_n"])
    if m < 0 or n < 0:
        return None, "RAW 段不完整"

    if use_ext:
        c1, c2 = int(z["raw_ext1"]), int(z["raw_ext2"])
        if c1 == -1 or c2 == -1:
            return None, "外部那一路没有 RAW 数据"
    else:
        c1, c2 = int(z["raw_int1"]), int(z["raw_int2"])
        if c1 == -1 or c2 == -1:
            return None, "内置那一路没有 RAW 数据"

    return (true_na * 1e-9, c1, c2, m, n), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--dir", default=".", help="npz 所在目录 (默认当前目录)")
    ap.add_argument("-o", "--out", default="fit_data.csv", help="输出 CSV")
    ap.add_argument("--path", choices=("int", "ext"), default="ext",
                    help="用哪一路的端点码 (默认 ext = ADS8866, 实验的标准)")
    a = ap.parse_args()

    pat = os.path.join(a.dir, "raw_*.npz")
    files = sorted(glob.glob(pat))
    if not files:
        raise SystemExit("在 %s 里没找到 raw_*.npz" % os.path.abspath(a.dir))

    rows, skipped = [], []
    for f in files:
        r, why = load_one(f, a.path == "ext")
        if r is None:
            skipped.append((os.path.basename(f), why))
        else:
            rows.append((os.path.basename(f),) + r)

    if not rows:
        print("一个可用的都没有。逐条原因:")
        for name, why in skipped:
            print("  %-40s %s" % (name, why))
        raise SystemExit(1)

    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("I_true,c1,c2,m,n\n")
        for _, I, c1, c2, m, n in rows:
            # I_true 用足够位数写, 免得 nA -> A 换算丢有效位
            fh.write("%.10e,%d,%d,%d,%d\n" % (I, c1, c2, m, n))

    print("写入 %s : %d 行 (端点码用 %s 那一路)\n"
          % (os.path.abspath(a.out), len(rows),
             "ADS8866 外部" if a.path == "ext" else "内置 ADC"))
    print("  %-42s %12s %8s %8s" % ("文件", "I_true(nA)", "m+n", "c2-c1"))
    for name, I, c1, c2, m, n in rows:
        print("  %-42s %12.4f %8d %8d" % (name, I * 1e9, m + n, c2 - c1))

    if skipped:
        print("\n跳过 %d 个:" % len(skipped))
        for name, why in skipped:
            print("  %-42s %s" % (name, why))

    # 提醒两个最容易让拟合失真的情况
    Is = np.array([r[1] for r in rows])
    if np.all(Is >= 0) or np.all(Is <= 0):
        print("\n!! 输入的已知电流全同号 —— 正负都要有, 否则 I+/I- 分不开")
    if len(rows) < 10:
        print("\n!! 只有 %d 组, 太少了。建议每个量程几十组" % len(rows))


if __name__ == "__main__":
    main()
