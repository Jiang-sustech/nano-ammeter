# -*- coding: utf-8 -*-
"""CH340 DTR#/RTS# -> BOOT0/NRST 自动进入电路: 上板验证工具 (2026-09-13)

用途
----
`uart_flash.py` 的自动进入序列到底成不成立, 用这个脚本量, 不靠猜:

  python boot0_probe.py map     # 扫 4 个静态组合 + 全部 12 种有序跳转
  python boot0_probe.py seq     # 只跑"网表推导线" (握复位 -> 只翻 DTR 放复位)
  python boot0_probe.py state   # 打印当前电平下芯片的状态

它同时用两种互相独立的判据, 因为本板实测发现"串口探针"会骗人:
  S1 串口 8E1 @115200 发 0x7F -> 收到 0x79   = ROM bootloader 在应答
  S2 串口 8N1 发 "E\\n"      -> 收到 "EXT N=" = app 在跑 (**注意: 只要往 8E1 写过一次
     0x7F, app 的 UART 接收就失聪了, 必须复位才能恢复 —— 所以 S2 在 S1 之后不可信**)
  W1 SWD halt 读 PC: 0x0800xxxx = 跑 app; 0x1FFF0xxx = ROM bootloader; halt 不上 = 被按在复位里
  W2 SWD 把 PH3(=BOOT0, U6.44) 配成数字输入后读 GPIOH_IDR.bit3
     (GPIO 复位值是 analog, 输入缓冲关闭, IDR 恒 0, 所以必须先改 MODER)
     同一个电平下再开内部 40k 下拉/上拉各读一次, 用来判断节点是"被推着"还是"高阻":
        无拉/下拉/上拉 = 0/0/0  -> BOOT0 被 R16(10k) 拉在地 (Q2 截止)
        无拉/下拉/上拉 = 0/0/1  -> BOOT0 被 10k/10k 分压抬到 VDD/2 (Q2 导通)  <== 本板实测
  W3 RCC_CSR(0x40021094) 复位标志: PIN = 期间真的发生过引脚复位

本板 2026-09-13 实测结论 (Q1 NPN + Q2 PNP/P-MOS 高边, pyserial True=引脚拉低):
  dtr=_,   rts=True  -> NRST 被握在低    (W1: halt 不上)
  dtr=_,   rts=False -> NRST 高, 跑 app
  (dtr=F,rts=T) -> (dtr=T,rts=T)  = 网表推导线: 复位发生 (W3:PIN) 但进的是 app
  BOOT0 只有 1.65V => 低于 STM32L431 VIH(min)=2.31V => 自动进入物理上不可能。
  根因: R23=10k 串在 BOOT0 与 Q2 之间, BOOT0 又对地有 R16=10k -> 1:1 分压。
  改法: R23 换 0Ω(或 ≤1k)。改完跑 `python boot0_probe.py seq` 应看到 W1 = ROM bootloader。
"""
import re
import subprocess
import sys
import time

import serial
import serial.tools.list_ports

OCD = r'C:\Users\40512\STM32Toolchain\openocd\bin\openocd.exe'
OCD_CWD = r'C:\Users\40512\STM32Toolchain\openocd\bin'
CFG = ['-f', 'interface/stlink.cfg', '-f', 'target/stm32l4x.cfg']

VIDPID = (0x1A86, 0x7523)
BAUD = 115200
COMBO = [(0, 0), (0, 1), (1, 0), (1, 1)]

# 把 GPIO 复位值(analog) 改成 PH3=数字输入 / 无拉 所需的寄存器值, 其余位不动
GPIOH_MODER, GPIOH_PUPDR, GPIOH_IDR = 0x48001C00, 0x48001C0C, 0x48001C10
MODER_IN_PH3 = 0x0000000F          # 从实测读数 0x000000CF 清掉 bit7:6
MODER_RESTORE = 0x000000CF
RCC_CSR = 0x40021094
CSR_FLAGS = [(31, 'LPWR'), (30, 'WWDG'), (29, 'IWDG'), (28, 'SFT'), (27, 'BOR'),
             (26, 'PIN'), (25, 'POR'), (24, 'OBL'), (23, 'V18PWR')]


def find_port():
    for p in serial.tools.list_ports.comports():
        if (p.vid, p.pid) == VIDPID:
            return p.device
    return None


def ocd(cmds, timeout=90):
    args = [OCD] + CFG + ['-c', 'init']
    for c in cmds:
        args += ['-c', c]
    args += ['-c', 'shutdown']
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=OCD_CWD)
    return (p.stdout or '') + (p.stderr or '')


def _words(out):
    vals = []
    for line in out.splitlines():
        m = re.match(r'\s*0x[0-9a-fA-F]+:\s*(.*)$', line)
        if m:
            vals += [int(x, 16) for x in re.findall(r'\b[0-9a-fA-F]{8}\b', m.group(1))]
    return vals


def probe():
    """SWD: halt -> 读 PC + BOOT0 三态电平 + 复位标志 -> resume (并清标志)"""
    cmds = ['halt',
            'mww 0x%08X 0x%08X' % (GPIOH_MODER, MODER_IN_PH3),
            'mww 0x%08X 0x%08X' % (GPIOH_PUPDR, 0x00), 'mdw 0x%08X 1' % GPIOH_IDR,
            'mww 0x%08X 0x%08X' % (GPIOH_PUPDR, 0x80), 'sleep 30', 'mdw 0x%08X 1' % GPIOH_IDR,
            'mww 0x%08X 0x%08X' % (GPIOH_PUPDR, 0x40), 'sleep 30', 'mdw 0x%08X 1' % GPIOH_IDR,
            'mww 0x%08X 0x%08X' % (GPIOH_PUPDR, 0x00),
            'mww 0x%08X 0x%08X' % (GPIOH_MODER, MODER_RESTORE),
            'reg pc', 'mdw 0x%08X 1' % RCC_CSR,
            'mww 0x%08X 0xFF800000' % RCC_CSR,      # 清复位标志(写1清)
            'resume']
    out = ocd(cmds)
    m = re.findall(r'pc:\s*(0x[0-9a-fA-F]+)\s+msp:\s*(0x[0-9a-fA-F]+)', out)
    w = _words(out)
    if m:
        pc = int(m[-1][0], 16)
        cls = ('ROM-BOOTLOADER' if 0x1FFF0000 <= pc < 0x1FFF8000 else
               'FLASH(app)' if 0x08000000 <= pc < 0x08040000 else
               'RAM' if 0x20000000 <= pc < 0x20010000 else 'PC=0x%08x' % pc)
    else:
        pc, cls = None, 'NO-HALT(在复位里?)'
    csr = w[3] if len(w) >= 4 else None
    return {'pc': pc, 'cls': cls,
            'boot0': tuple((w[i] >> 3) & 1 for i in range(3)) if len(w) >= 3 else None,
            'rst': ','.join(n for (b, n) in CSR_FLAGS if csr and (csr >> b) & 1) or '-'}


class Board(object):
    def __init__(self):
        self.s = serial.Serial(find_port(), BAUD, timeout=0.3)

    def lines(self, st, dl=0.25):
        self.s.dtr, self.s.rts = bool(st[0]), bool(st[1])
        time.sleep(dl)

    def boot_probe(self, tries=4, gap=0.08):
        s = self.s
        s.parity, s.timeout = serial.PARITY_EVEN, 0.25
        s.reset_input_buffer()
        got = b''
        for _ in range(tries):
            s.write(b'\x7f')
            x = s.read(1)
            if x:
                got += x
                if x[0] == 0x79:
                    return '0x79-OK'
            time.sleep(gap)
        return got.hex(' ') if got else 'silent'

    def close(self):
        self.s.close()


def clean(b):
    """恢复: 线放到 (0,0) + SWD 复位, 确认 app 活了"""
    b.lines((0, 0), 0.2)
    ocd(['reset', 'resume'])
    time.sleep(0.4)


def show(tag, b, w):
    print('%-22s | PC=%-13s BOOT0 无拉/下拉/上拉=%s | ser:%s | RST:%s'
          % (tag, w['cls'], '/'.join(str(x) for x in (w['boot0'] or ('?',) * 3)),
             b.boot_probe() if b else '-', w['rst']), flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'map'
    b = Board()
    clean(b)

    if mode == 'state':
        show('当前电平', b, probe())

    elif mode == 'seq':
        hold = 0.25
        for a in sys.argv:
            if a.startswith('--hold='):
                hold = float(a.split('=', 1)[1])
        print('握复位 (dtr=False, rts=True) %.2fs -> 只翻 DTR 放复位 (True,True) ...' % hold)
        b.lines((0, 0), 0.2)
        b.lines((0, 1), hold)
        b.lines((1, 1), 0.35)
        show('推导线结果', b, probe())
        clean(b)

    elif mode == 'map':
        print('=== 4 个静态组合 ===')
        for st in COMBO:
            b.lines((0, 0), 0.2)
            b.lines(st, 0.4)
            show('static dtr=%d rts=%d' % st, b, probe())
        print('=== 12 种有序跳转 (A 0.3s -> B 0.35s) ===')
        for a in COMBO:
            for c in COMBO:
                if a == c:
                    continue
                b.lines(a, 0.3)
                b.lines(c, 0.35)
                show('%s->%s' % (a, c), b, probe())
            clean(b)
        clean(b)

    b.close()


main()
