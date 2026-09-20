# -*- coding: utf-8 -*-
"""列出全盘所有「纳安表控制台.html」副本, 并判断各自是哪一版

    python tools/console_copies.py

用途: 这份 HTML 在仓库外还有几份副本 (桌面交付目录、release、backup)。
改了仓库里那份之后, 如果浏览器打开的是别的副本, 表现就是"改了没生效"
—— 2026-09-17 实际踩到, 白查了一轮。
"""
import datetime
import io
import os
import re

CANDIDATES = [
    r"C:\Users\40512\STM32Toolchain\nanoammeter_repo\console.html",
    r"C:\Users\40512\Desktop\物理实验竞赛相关\07_上位机软件\纳安表控制台.html",
    r"C:\Users\40512\STM32Toolchain\nano-ammeter-release\纳安表控制台.html",
    r"C:\Users\40512\STM32Toolchain\backup\nano_v2_2026-09-03\纳安表控制台.html",
]


def probe(path):
    if not os.path.exists(path):
        return None
    s = io.open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"cmd: 'S', line: 'I=',\s*tag: null,\s*timeout: (\d+)", s)
    return dict(
        mtime=datetime.datetime.fromtimestamp(os.path.getmtime(path)),
        size=len(s),
        has_x=("X=[+-][0-9]+" in s),          # 结果行正则是否容下 firmware-v1 的 X= 段
        s_timeout=(m.group(1) if m else "?"),
        footer_50=("50 秒长窗" in s),
    )


def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("%-52s %-17s %8s %9s %10s %9s"
          % ("文件", "改时间", "字节", "含X=段", "S步超时", "页脚50秒"))
    print("-" * 112)
    for p in CANDIDATES:
        r = probe(p)
        short = p.replace("C:\\Users\\40512\\", "")
        if len(short) > 50:
            short = "…" + short[-49:]
        if r is None:
            print("%-52s  (不存在)" % short)
            continue
        print("%-52s %-17s %8d %9s %10s %9s"
              % (short, r["mtime"].strftime("%m-%d %H:%M:%S"), r["size"],
                 "是" if r["has_x"] else "**否**", r["s_timeout"] + " ms",
                 "是" if r["footer_50"] else "**否**"))
    print("")
    print("打开浏览器时要选**仓库里那份**; 其余是历史副本, 改了也不会生效。")


if __name__ == "__main__":
    main()
