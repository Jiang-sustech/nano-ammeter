# -*- coding: utf-8 -*-
"""检测下限：相对误差跨过 5% 的那个电流点

    python tools/detect_limit.py

判据 (用户定义): 在给定环境/积分时间/量程下, 相对误差不超过 5% 所对应的
最小输入电流。低电流端的相对误差 ≈ 偏置/电流, 所以

    I_下限 = |偏置| / 5%

**这个脚本存在的理由**: 段0 的偏置对**用哪一组三常数**很敏感 ——
固件里存的是整数 pA (49712 / -49739), 而最小二乘给的是小数
(49712.13 / -49738.60)。同一批 42 个段0 测量, 两者给出的偏置差 0.27 pA,
检测下限就差一倍。所以报告这个数**必须写明常数来源**。

三种口径并列:
    A. 固件常数     —— 装置实际报出来的数 (Q 命令存进 Flash 的那组)
    B. 拟合小数常数 —— 物理上更好的估计
    C. A 再扣掉段0偏置 —— 若启用分段修正 b0 (K0,1000000,-511 之类) 后的上限
"""
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

T = 160e-6
Q_FW, IP_FW, IN_FW = 15596e-18, 49712e-12, -49739e-12   # 固件里存的
IP_FIT, IN_FIT = 49.71213e-9, -49.73860e-9              # 拟合给的小数


def calc(c1, c2, m, n, q, ip, in_):
    nt = float(m + n)
    return q * (c2 - c1) / (nt * T) - (m * ip + n * in_) / nt


def load(d, path_filter=None):
    out = []
    p = os.path.join(d, 'raw.csv')
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p, encoding='utf-8')):
        if 'TIMEOUT' in r['line']:
            continue
        sp = float(r['set_nA'])
        if abs(sp) >= 1.0:            # 只看段0
            continue
        out.append((sp, float(r['true_nA']),
                    int(r['ext1']), int(r['ext2']), int(r['m']), int(r['n'])))
    return out


def crossing(pts, bias_pA=0.0, thr=5.0):
    """**按幅值** |I| 判: 返回 (跨 5% 的电流, 全部行)

    ⚠️ 必须按幅值, 不能按带符号的电流 —— 否则"第一个满足的点"会落到
    负电流那一侧, 报出一个负的"下限"(初版就是这个错)。
    恒定偏置下正负两侧的 |δ| 相同, 所以取幅值不丢信息。
    """
    by = {}
    for sp, std, c1, c2, m, n in pts:
        by.setdefault(round(abs(sp), 6), []).append((std, c1, c2, m, n))
    rows = []
    for mag in sorted(by):
        v = by[mag]
        # ⚠️ 单位: true_nA 是**纳安**, calc() 出来是**皮安**。必须统一到 pA,
        #    否则相对误差差 1000 倍 (本脚本第一版就踩了, 跑出 +165247%)。
        std = sum(abs(x[0]) for x in v) / len(v) * 1e3          # |回读|, pA
        # ⚠️ 偏差必须用**带符号的 装置−回读**, 不能用 |装置|−|回读| ——
        #    恒定偏置在幅值域会翻号 (正电流 |装置|>|回读|, 负电流相反),
        #    对幅值取平均会把偏置抵消掉。而 装置−回读 在正负两侧**同号**。
        d = sum(calc(x[1], x[2], x[3], x[4], Q_FW, IP_FW, IN_FW) - x[0] * 1e-9
                for x in v) / len(v) * 1e12
        d -= bias_pA
        rel = d / std * 100
        rows.append((std, d, rel))
    # 从大到小找第一个 |rel| <= 5%
    ok = [i for i, r in enumerate(rows) if abs(r[2]) <= thr]
    if not ok:
        return None, rows
    i = min(ok)
    return rows[i][0], rows


def report(tag, pts, bias=0.0, thr=5.0):
    lim, rows = crossing(pts, bias, thr)
    print("  %-22s" % tag, end='')
    if lim is None:
        print("没有点满足 |δ| <= %.1f%%" % thr)
        return
    print("下限 = %6.3f pA   (该点 δ = %+.2f%%)"
          % (lim, [r[2] for r in rows if r[0] == lim][0]))
    return lim


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    pts = load('data/lowI_pos') + load('data/lowI_neg')
    print("=" * 78)
    print("检测下限 (相对误差 <= 5%)")
    print("=" * 78)
    print("  %d 个段0 电流点 (来自 lowI_pos + lowI_neg)" % len({p[0] for p in pts}))
    print("")

    # 三种口径下的段0 偏置
    def bias_of(ip, in_):
        vs = []
        by = {}
        for sp, std, c1, c2, m, n in pts:
            by.setdefault(sp, []).append((std, c1, c2, m, n))
        for sp, v in by.items():
            std = sum(x[0] for x in v) / len(v) * 1e3        # nA -> pA (同上)
            dev = sum(calc(x[1], x[2], x[3], x[4], Q_FW, ip, in_)
                      for x in v) / len(v) * 1e12
            vs.append(dev - std)
        return sum(vs) / len(vs)

    b_fw = bias_of(IP_FW, IN_FW)
    b_fit = bias_of(IP_FIT, IN_FIT)
    print("## 先看偏置本身")
    print("   A 固件常数  (I+=49712,     I-=-49739)     偏置 = %+.4f pA" % b_fw)
    print("   B 拟合小数  (I+=49712.13,  I-=-49738.60)  偏置 = %+.4f pA" % b_fit)
    print("   两者差 %.4f pA  ->  检测下限就差 %.1f 倍"
          % (abs(b_fw - b_fit), abs(b_fw / b_fit) if b_fit else float('nan')))
    print("")
    print("## 三种口径的检测下限")
    report("A 固件常数 (现状)", pts, 0.0)
    report("B 拟合小数常数", pts, b_fw - b_fit)
    report("C 扣掉段0偏置", pts, b_fw)
    print("")
    print("## 参考: 未扣偏置时的逐点相对误差")
    _, rows = crossing(pts, 0.0)
    # 第 2 列是**装置−回读**的偏差, 不是装置读数 (低电流端它就是那个恒定偏置)
    print("   %10s %11s %9s" % ("|回读|pA", "偏差pA", "δ/%"))
    for std, dev, rel in rows[:10]:
        print("   %10.4f %11.4f %+8.2f%%" % (std, dev, rel))


if __name__ == '__main__':
    main()
