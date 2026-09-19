# -*- coding: utf-8 -*-
"""把网页控制台渲染成一张带**真实数据**的 PNG, 供论文插图使用

    python tools/console_figure.py             # 深色版 (界面原样)
    python tools/console_figure.py --light     # 浅色版 (白底, 给白底论文用)

**为什么不用"打开页面直接截"**: 空白的控制台只有布局、没有内容, 放进论文
说明不了任何事。这里把一次**真实测量**的波形、结果行与协议日志注入进去,
截出来的是界面的实际工作状态。

数据来源: data/raw_09_13_+10.101nA.npz —— 它同时存了内置路 (wave_int) 与
外部路 (wave_ext) 两路各 6250 点, 所以"双路叠图"那一栏是真的两条曲线,
而不是复制一份。

浅色版的做法: 页面颜色全部走 CSS 变量 + 少量字面量, 所以**只改生成的副本**,
把调色板做一次文本替换即可, 交付的 HTML 一个字不动。注意 `CLR` 是 canvas
绘图用的 const 对象, 不能重新赋值, 但它里面的颜色串同样被文本替换覆盖到。
"""
import argparse
import io
import json
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_HTML = os.path.join(REPO, '纳安表控制台.html')
SRC_NPZ = os.path.join(REPO, 'data', 'raw_09_13_+10.101nA.npz')

CODE_LOWER = round(0.131 / 3.3 * 65536)
CODE_UPPER = round(2.969 / 3.3 * 65536)

# 深色 -> 浅色。键都是深色主题里的字面量, 互不包含, 替换顺序无影响。
# 前九个是 :root / 内联样式 / canvas 里的同一套调色板 (GitHub Dark -> Light)。
LIGHT_MAP = [
    ('#0d1117', '#ffffff'),      # 页面底 / 输入框底 / canvas 底
    ('#161b22', '#f6f8fa'),      # 卡片底
    ('#30363d', '#d0d7de'),      # 边框
    ('#e6edf3', '#1f2328'),      # 正文
    ('#8b949e', '#656d76'),      # 次级文字 / 坐标轴
    ('#58a6ff', '#0969da'),      # 强调 / 外部路曲线
    ('#3fb950', '#1a7f37'),      # 正常 / 内置路曲线
    ('#f85149', '#cf222e'),      # 错误
    ('#d29922', '#9a6700'),      # 警告 / 阈值线
    ('#21262d', '#eaeef2'),      # 普通按钮底 (漏了它按钮会变成黑块、字看不见)
    ('#1f6feb', '#0969da'),      # 主按钮底
    ('#2f81f7', '#0860ca'),      # 主按钮 hover
    ('rgba(48,54,61,0.7)', 'rgba(208,215,222,0.9)'),    # 网格
    ('rgba(210,153,34,0.65)', 'rgba(154,103,0,0.75)'),  # 阈值虚线
]


def build_driver(log_items, wi, we, line):
    return """
<script>
/* ===== 论文插图用: 注入一次**真实测量**的界面状态 =====
 * 本文件由 tools/console_figure.py 生成, 不是交付件, 改它没有意义。 */
(function () {
  var LOG = %s;
  var WI = %s, WE = %s;
  var RESULT = %s;

  setConnected(true, 'CH340 (COM8)');
  for (var i = 0; i < LOG.length; i++) { log(LOG[i][0], LOG[i][1]); }

  setStatus(RESULT);
  setStatus('EXT N=6250 ERR=37 TXE=0 RXNE=0 BSY=0 PRE=0 TINT=0ns TEXT=18512ns FFFF=0 F0=0 FL=0');

  waveData = Uint16Array.from(WI);
  waveExtData = Uint16Array.from(WE);
  drawChart('WAVEX');
  var wi = document.getElementById('waveInfo');
  wi.textContent = '6250 点 × 160µs ≈ 1000 ms · 阈值线: code_lower=' + %d +
                   ' (0.131V), code_upper=' + %d + ' (2.969V)';
  wi.style.display = 'block';
  document.getElementById('btnCsv').disabled = false;
  document.getElementById('btnPng').disabled = false;
  document.getElementById('btnMeta').disabled = false;
})();
</script>
""" % (json.dumps(log_items, ensure_ascii=False),
       json.dumps(wi), json.dumps(we), json.dumps(line),
       CODE_LOWER, CODE_UPPER)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument('--light', action='store_true', help='输出浅色(白底)版')
    a = ap.parse_args()

    tag = '_light' if a.light else ''
    out_html = os.path.join(REPO, 'data', '_console_figure%s.html' % tag)
    out_png = os.path.join(REPO, 'data', 'console_figure%s.png' % tag)

    html = io.open(SRC_HTML, encoding='utf-8').read()
    if a.light:
        n = 0
        for old, new in LIGHT_MAP:
            c = html.count(old)
            if c:
                html = html.replace(old, new)
                n += c
        print("浅色: 替换 %d 处颜色字面量" % n)

    d = np.load(SRC_NPZ)
    wi = d['wave_int'].astype(int).tolist()
    we = d['wave_ext'].astype(int).tolist()
    line = str(d['result_line'])

    # 日志: 一次真实会话的命令-应答序列。
    # ⚠️ 行数要**刚好装满 200px 的日志面板**(约 9 行, 长行会折行) ——
    #    多了会把顶行切成半截, 插图上很难看; 少了显得空。
    log_items = [
        ('已连接 CH340, 115200 8N1', 'sys'),
        ('→ S', 'tx'),
        ('← START MODE1', 'rx'),
        ('← ' + line, 'rx'),
        ('→ X', 'tx'),
        ('接收 WAVEX 完成: 6250 点, 校验和 OK ✓', 'rx'),
    ]

    assert html.count('</body>') == 1, "HTML 结构变了, </body> 不是唯一"
    io.open(out_html, 'w', encoding='utf-8').write(
        html.replace('</body>', build_driver(log_items, wi, we, line) + '</body>'))

    print("已写 %s" % out_html)
    print("  注入: %d 行日志, 两路各 %d 点波形" % (len(log_items), len(wi)))
    print("")
    print("截图:")
    print('  "/c/Program Files/Google/Chrome/Application/chrome.exe" --headless \\')
    print('    --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \\')
    print('    --screenshot="%s" --window-size=1000,2400 \\' % out_png.replace('\\', '/'))
    print('    "file:///%s"' % out_html.replace('\\', '/'))


if __name__ == '__main__':
    main()
