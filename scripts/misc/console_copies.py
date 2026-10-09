# -*- coding: utf-8 -*-
"""列出各个「纳安表控制台」副本, 并判断各自是哪一版

    python scripts/misc/console_copies.py [额外的副本路径 ...]

用途: 控制台常被复制到仓库外 (桌面交付目录、release、backup)。
改了仓库里那份之后, 如果浏览器打开的是别的副本, 表现就是"改了没生效"
—— 2026-09-17 实际踩到, 白查了一轮。

默认只检查仓库内的 `pc-console/console.html`; 要对比仓库外的副本,
把它们作为参数传进来:

    python scripts/misc/console_copies.py "/d/交付/纳安表控制台.html" ~/backup/console.html
"""
import datetime
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANDIDATES = [os.path.join(REPO, "pc-console", "console.html")] + sys.argv[1:]


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
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("%-52s %-17s %8s %9s %10s %9s"
          % ("文件", "改时间", "字节", "含X=段", "S步超时", "页脚50秒"))
    print("-" * 112)
    for p in CANDIDATES:
        r = probe(p)
        short = p
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
