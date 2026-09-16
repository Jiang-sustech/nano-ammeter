# -*- coding: utf-8 -*-
"""
make_fit_csv.py 的回归测试 (合成 npz, 不碰硬件)

    python tools/test_make_fit_csv.py
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "make_fit_csv.py")

fail = 0


def check(ok, label, detail=""):
    global fail
    if not ok:
        fail += 1
    print("%s %s%s" % ("ok  " if ok else "FAIL", label,
                       ("\n       " + detail) if detail and not ok else ""))


def write_npz(path, **kw):
    """写一个带全部字段的 npz; 用 kw 覆盖, 传 None 表示该字段不写"""
    base = dict(raw_m=3125, raw_n=3125, raw_int1=30780, raw_int2=30700,
                raw_ext1=30782, raw_ext2=30702, true_na=25.0,
                result_timeout=False)
    base.update(kw)
    d = {k: v for k, v in base.items() if v is not None}
    np.savez(path, **d)


with tempfile.TemporaryDirectory() as td:
    # --- 正常: 3 个文件 ---
    write_npz(os.path.join(td, "raw_09_13_+25.0nA.npz"), true_na=25.0)
    write_npz(os.path.join(td, "raw_09_13_-40.0nA.npz"), true_na=-40.0,
              raw_int1=2600, raw_int2=3200, raw_ext1=2602, raw_ext2=3202)
    write_npz(os.path.join(td, "raw_09_13_+1.0nA.npz"), true_na=1.0,
              raw_m=3000, raw_n=3250)

    # --- 该被跳过的四类 ---
    write_npz(os.path.join(td, "raw_09_13_old.npz"),
              raw_m=None, raw_n=None, raw_int1=None, raw_int2=None,
              raw_ext1=None, raw_ext2=None)                       # 没 RAW 段
    write_npz(os.path.join(td, "raw_09_13_notrue.npz"), true_na=None)  # 没真值
    write_npz(os.path.join(td, "raw_09_13_to.npz"), result_timeout=True)
    write_npz(os.path.join(td, "raw_09_13_mode2.npz"), raw_m=-1, raw_n=0)

    out = os.path.join(td, "fit_data.csv")
    r = subprocess.run([sys.executable, "-X", "utf8", TOOL, "-d", td, "-o", out],
                       capture_output=True, text=True, encoding="utf-8")

    check(r.returncode == 0, "跑通 (exit 0)",
          "stderr=%s" % (r.stderr or "")[-300:])
    check(os.path.exists(out), "生成了 CSV")

    if os.path.exists(out):
        lines = [l for l in open(out, encoding="utf-8").read().splitlines() if l]
        check(lines[0] == "I_true,c1,c2,m,n", "表头正确", lines[0])
        check(len(lines) == 4, "3 行数据 + 表头", "拿到 %d 行" % len(lines))

        body = [l.split(",") for l in lines[1:]]
        # 全是整数列, 除了第一列
        check(all(len(b) == 5 for b in body), "每行 5 列")
        ok_int = all(b[1].lstrip("-").isdigit() and b[2].lstrip("-").isdigit()
                     and b[3].isdigit() and b[4].isdigit() for b in body)
        check(ok_int, "c1/c2/m/n 都是整数 (没写成科学计数法)")

        # I_true 必须换算成安培
        vals = sorted(float(b[0]) for b in body)
        check(abs(vals[0] - (-40e-9)) < 1e-18 and abs(vals[-1] - 25e-9) < 1e-18,
              "I_true 从 nA 换算成 A 且没丢精度", str(vals))

        # 默认走 ext 那一路
        row25 = [b for b in body if abs(float(b[0]) - 25e-9) < 1e-18][0]
        check(row25[1] == "30782" and row25[2] == "30702",
              "默认用 ext (ADS8866) 那一路的端点码", str(row25))

        # 跳过原因要逐条说出来
        for kw in ("老固件", "没记已知真值", "TIMEOUT"):
            check(kw in r.stdout, "报出跳过原因含 %r" % kw)

    # --- --path int 要换一路 ---
    out2 = os.path.join(td, "int.csv")
    r2 = subprocess.run([sys.executable, "-X", "utf8", TOOL, "-d", td, "-o", out2,
                         "--path", "int"],
                        capture_output=True, text=True, encoding="utf-8")
    check(r2.returncode == 0, "--path int 跑通")
    if os.path.exists(out2):
        body2 = [l.split(",") for l in
                 open(out2, encoding="utf-8").read().splitlines()[1:] if l]
        row25 = [b for b in body2 if abs(float(b[0]) - 25e-9) < 1e-18][0]
        check(row25[1] == "30780", "--path int 用的是内置那一路", str(row25))

    # --- 空目录要明确报错, 不能静默出空文件 ---
    empty = os.path.join(td, "empty")
    os.makedirs(empty)
    r3 = subprocess.run([sys.executable, "-X", "utf8", TOOL, "-d", empty],
                        capture_output=True, text=True, encoding="utf-8")
    check(r3.returncode != 0, "空目录 -> 非零退出")
    check("没找到" in (r3.stdout + r3.stderr), "给了清楚的提示")

print("\n%s" % ("ALL PASS" if fail == 0 else "%d FAILED" % fail))
sys.exit(1 if fail else 0)
