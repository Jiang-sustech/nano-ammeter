# -*- coding: utf-8 -*-
"""
nanoammeter_capture.py 回归测试 (纯离线, 不碰串口)

    python tools/test_capture.py

覆盖三处曾经/容易出错的地方:

1. RESULT_RE —— 结果行的两代格式。physics_exp_test 在 I= 和 MODE= 之间插了
   `X=<外部> nA`, 用 `nA MODE=` 直接匹配会落空, 于是 capture_name 把文件名
   退化成 raw_09_12_NO_RESULT.npz、result_na 变 NaN —— **静默**丢数据。
2. 参数解析 —— `--no-noise` 是开关, 不能顶掉 PORT 的位置参数。
3. plot_only —— 有/无底噪两条分支都要能出图 (--no-noise 走的是后者)。
"""
import importlib.util
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "nanoammeter_capture.py")

fail = 0


def check(ok, label, detail=""):
    global fail
    if not ok:
        fail += 1
    print("%s %s%s" % ("ok  " if ok else "FAIL", label,
                       ("\n       " + detail) if detail and not ok else ""))


def load(argv):
    """按给定 argv 重新导入一次 —— 模块在 import 期就读 sys.argv"""
    sys.argv = ["nanoammeter_capture.py"] + argv
    spec = importlib.util.spec_from_file_location("cap%d" % len(argv), SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1. 结果行解析 ----------
mod = load([])

RESULT_CASES = [
    # (行, I, X, MODE, T, TIMEOUT)
    # physics_exp_test (新格式)
    ("I=+25.274 nA X=+25.271 nA MODE=1 T=1000ms CAL=+25.270",
     "+25.274", "+25.271", "1", "1000", False),
    ("I=+40.228 nA X=+40.231 nA MODE=1 T=1000ms",
     "+40.228", "+40.231", "1", "1000", False),
    ("I=-40.241 nA X=-40.240 nA MODE=1 T=1000ms CAL=-40.235",
     "-40.241", "-40.240", "1", "1000", False),
    ("I=-0.012 nA X=-0.013 nA MODE=2 T=3000ms",           # 小电流模式
     "-0.012", "-0.013", "2", "3000", False),
    ("I=+99.999 nA X=+99.998 nA MODE=1 T=412ms TIMEOUT",  # 撞轨提前收尾
     "+99.999", "+99.998", "1", "412", True),
    # nano_ammeter (老格式, 无 X= / T=)
    ("I=+25.274 nA MODE=1", "+25.274", None, "1", None, False),
    ("I=-0.847 nA MODE=2", "-0.847", None, "2", None, False),
    ("I=+1.234 nA MODE=2 TIMEOUT", "+1.234", None, "2", None, True),
    # D 查询回的 RESULT 前缀行 (search 不锚行首)
    ("RESULT I=+25.274 nA MODE=1", "+25.274", None, "1", None, False),
    # 带 RAW 段 (physics_exp_test MODE=1) —— 追加字段不能破坏前面的解析
    ("I=+40.228 nA X=+40.231 nA MODE=1 T=1000ms CAL=+40.235 "
     "RAW m=5003 n=6251 INT1=58962 INT2=2601 EXT1=58900 EXT2=2540",
     "+40.228", "+40.231", "1", "1000", False),
    ("I=-40.241 nA X=-40.240 nA MODE=1 T=1000ms "
     "RAW m=6251 n=5003 INT1=2601 INT2=58962 EXT1=2540 EXT2=58900",
     "-40.241", "-40.240", "1", "1000", False),
]

print("--- RESULT_RE ---")
for line, ei, ex, emode, ems, eto in RESULT_CASES:
    m = mod.RESULT_RE.search(line)
    if m is None:
        check(False, line, "no match")
        continue
    got = (m.group("i"), m.group("x"), m.group("mode"),
           m.group("t"), bool(m.group("timeout")))
    check(got == (ei, ex, emode, ems, eto), line,
          "got %s want %s" % (got, (ei, ex, emode, ems, eto)))

# ---------- 1b. 原始量字段 ----------
print("\n--- raw_fields ---")
RAW_CASES = [
    # (行, 期望的 dict; 只列关心的项, 其余按 0 校验)
    ("I=+40.228 nA X=+40.231 nA MODE=1 T=1000ms "
     "RAW m=5003 n=6251 INT1=58962 INT2=2601 EXT1=58900 EXT2=2540",
     {"raw_m": 5003, "raw_n": 6251, "raw_int1": 58962, "raw_int2": 2601,
      "raw_ext1": 58900, "raw_ext2": 2540}),
    # ★ 带 CAL= 的同一行 —— 固件只要写过 Q/K (或 Z 清除过, 它会把 a=1,b=0
    #   存回 Flash 并保留 magic), CAL= 就恒存在, 所以这是**常态**。
    #   正则漏掉这一段会"匹配成功但 RAW 六字段全 None", 而那正是整轮标定
    #   静默产出空表的成因 —— 上面那条用例有 CAL 但不查 RAW, 下面这条查 RAW
    #   却原本没有 CAL, 两半从没合起来过, 所以一直 ALL PASS。
    ("I=+40.228 nA X=+40.231 nA MODE=1 T=1000ms CAL=+40.235 "
     "RAW m=5003 n=6251 INT1=58962 INT2=2601 EXT1=58900 EXT2=2540",
     {"raw_m": 5003, "raw_n": 6251, "raw_int1": 58962, "raw_int2": 2601,
      "raw_ext1": 58900, "raw_ext2": 2540}),
    # 10 s 长窗口 (MODE=2) 现在也出 RAW —— 重构前 MODE=2 是另一套不算 m/n 的模式
    ("I=-0.001 nA X=-0.001 nA MODE=2 T=10000ms "
     "RAW m=31257 n=31243 INT1=31088 INT2=29632 EXT1=31114 EXT2=29645",
     {"raw_m": 31257, "raw_n": 31243, "raw_int1": 31088, "raw_int2": 29632,
      "raw_ext1": 31114, "raw_ext2": 29645}),
    # 老固件 / 小电流模式: 没有 RAW 段 -> 全 -1 (不能伪造成 0)
    ("I=+25.274 nA MODE=1",
     {"raw_m": -1, "raw_n": -1, "raw_int1": -1, "raw_int2": -1,
      "raw_ext1": -1, "raw_ext2": -1}),
    ("I=-0.012 nA X=-0.013 nA MODE=2 T=3000ms",
     {"raw_m": -1, "raw_n": -1, "raw_int1": -1, "raw_int2": -1,
      "raw_ext1": -1, "raw_ext2": -1}),
]
for line, want in RAW_CASES:
    got = mod.raw_fields(mod.RESULT_RE.search(line))
    check(got == want, "%-46s -> m=%s n=%s" % (line[:46], got["raw_m"], got["raw_n"]),
          "got %s" % got)
# 完全匹配不上时 (空行) 也要给 -1, 不能抛异常
check(mod.raw_fields(None) == {k: -1 for k in
      ("raw_m", "raw_n", "raw_int1", "raw_int2", "raw_ext1", "raw_ext2")},
      "raw_fields(None) -> 全 -1")

# ---------- 2. 文件名 ----------
print("\n--- capture_name ---")
NAME_CASES = [
    ("I=+40.228 nA X=+40.231 nA MODE=1 T=1000ms", "+40.228nA"),
    ("I=+25.274 nA MODE=1", "+25.274nA"),
    ("I=+1.234 nA MODE=2 TIMEOUT", "TIMEOUT"),
    ("", "NO_RESULT"),
]
with tempfile.TemporaryDirectory() as td:
    cwd = os.getcwd()
    os.chdir(td)                      # capture_name 用相对路径探重名
    try:
        for line, want in NAME_CASES:
            name = mod.capture_name(line)
            # raw_<MM>_<DD>_<tag>.npz -> 取回 tag (日期自己带一个下划线)
            tag = os.path.basename(name).split("_", 3)[3][:-len(".npz")]
            check(tag == want, "%-44s -> %s" % (line[:44], tag),
                  "got %s want %s" % (tag, want))
    finally:
        os.chdir(cwd)

# ---------- 3. 参数解析 ----------
print("\n--- argv ---")
ARG_CASES = [
    ([], "COM7", True),
    (["COM3"], "COM3", True),
    (["--no-noise"], "COM7", False),
    (["COM3", "--no-noise"], "COM3", False),
    (["--no-noise", "COM3"], "COM3", False),   # 开关在前也不能吃掉端口
]
for argv, want_port, want_noise in ARG_CASES:
    m2 = load(argv)
    check((m2.PORT, m2.DO_NOISE) == (want_port, want_noise),
          "argv=%-26s -> PORT=%s DO_NOISE=%s" % (argv, m2.PORT, m2.DO_NOISE))

# ---------- 4. 出图两条分支 ----------
print("\n--- plot_only ---")


def synth(n=6250, period=300, lo=2600, hi=58960):
    """合成一段电荷平衡三角波, 免得测试依赖仓库外的实采文件"""
    tri = np.abs(((np.arange(n) % period) / float(period)) * 2.0 - 1.0)
    return (lo + tri * (hi - lo)).astype(np.int64)


wi = synth()
we = wi + np.random.RandomState(0).randint(-90, 90, len(wi))
noise = np.linspace(65520, 65519, 50).astype(np.int64)

with tempfile.TemporaryDirectory() as td:
    for label, ns in (("有底噪/3 行", noise),
                      ("--no-noise/2 行", np.zeros(0, dtype=np.int64))):
        p = os.path.join(td, "t.npz")
        try:
            mod.plot_only(wi, we, ns, p)
            ok = os.path.exists(p[:-4] + ".png") and os.path.getsize(p[:-4] + ".png") > 0
        except Exception as e:                       # noqa: BLE001 - 测试就是要抓它
            ok = False
            print("       %s: %s" % (type(e).__name__, e))
        check(ok, label)

print("\n%s" % ("ALL PASS" if fail == 0 else "%d FAILED" % fail))
sys.exit(1 if fail else 0)
