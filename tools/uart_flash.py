# STM32 ROM bootloader UART 烧录器 (AN3155 协议)
# 用法: python uart_flash.py <bin路径> [COM口] [波特率]
# 板子: STM32L431CCT6 + CH340(PA9/PA10), Bootloader 要求 8E1
#
# 进入方式: **自动** (DTR/RTS 经双管电路驱动 NRST/BOOT0)。
# 据 2026-09-13 网表:
#   U7.14 RTS# --R20(1k)--> Q2 栅极;  Q2 源极=3V3, 漏极 --R23--> BOOT0
#   U7.13 DTR# --R19(1k)--> Q1 栅极;  Q1 源极=RTS#, 漏极 --R17/D1--> NRST
#   静偏置: R16=10k 把 BOOT0 下拉到 AGND, R27=10k 把 NRST 上拉到 3V3
# 失败时回退到手动: BOOT0 拉高 -> 按一下复位 -> 松开 BOOT0
import serial
import serial.tools.list_ports
import sys
import time

BIN = sys.argv[1] if len(sys.argv) > 1 else 'sawtooth_uart.bin'
BAUD = int(sys.argv[3]) if len(sys.argv) > 3 else 115200
FLASH_BASE = 0x08000000

# ---- 找 CH340 ----
port = None
if len(sys.argv) > 2:
    port = sys.argv[2]
else:
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid == 0x7523:
            port = p.device
            break
print('可用串口:', [p.device for p in serial.tools.list_ports.comports()])
if not port:
    print('未找到 CH340 串口'); sys.exit(1)
print('使用', port)

s = serial.Serial(port, BAUD, timeout=1.0, parity=serial.PARITY_EVEN)

def tx(b, n=1):
    s.reset_input_buffer()
    s.write(b)
    return s.read(n)

# ---- 1. 握手: 先试自动进 bootloader, 不行再回退手动 ----
# RTS 有效时 Q2 把 BOOT0 抬到 3V3; DTR/RTS 交叉翻转产生 NRST 脉冲。
# **极性不猜**: pyserial 的 True/False 与 CH340 引脚有效电平的对应关系
# 随驱动而异, 所以下面依次试几组常见序列, 哪组先收到 0x79 就用哪组并打印出来。
BOOT_SEQS = [
    ('DTR/RTS 交叉 (ESP 式)', [(False, True), (True, False), (False, False)]),
    ('反相交叉',              [(True, False), (False, True), (True, True)]),
    ('同时有效 -> 释放',      [(True, True), (False, False)]),
]


def try_enter(seq):
    for d, r_ in seq:
        s.dtr = d
        s.rts = r_
        time.sleep(0.08)
    s.reset_input_buffer()
    for _ in range(30):
        s.write(b'\x7f')
        r_ = s.read(1)
        if r_ and r_[0] == 0x79:
            return True
        time.sleep(0.05)
    return False


print('自动进入 bootloader ...')
entered = False
for label, seq in BOOT_SEQS:
    if try_enter(seq):
        print(f'[OK] 进入 bootloader (生效序列: {label})')
        entered = True
        break
    print(f'     {label} 未握手')

if not entered:
    # --wait: 不设截止时间, 一直等按键。用于"手上没空, 什么时候按都行"的场合;
    #          不加则只等 90 秒 (CI/无人值守时不会挂死)。
    wait_s = 3600.0 if '--wait' in sys.argv else 90.0
    print('自动进入失败, 回退手动 (%s):'
          % ('无限等待, Ctrl-C 中止' if wait_s > 1000 else '90 秒窗口'))
    print('  请执行: 按住 SW1(BOOT0) -> 点一下 SW3(RST) -> 松开 SW1')
    print('  或者: BOOT0 拉高 -> 按一下复位 -> 松开 BOOT0')
    deadline = time.time() + wait_s
    r = b''
    while time.time() < deadline:
        r = tx(b'\x7f')
        if r and r[0] == 0x79:
            break
        time.sleep(0.5)
    if not r or r[0] != 0x79:
        print('握手失败:', r.hex(' ') if r else '无响应'); sys.exit(1)
    print('[OK] handshake ACK (手动)')

# ---- 2. GET / GET ID (精确长度读取, 避免超时读阻塞) ----
def cmd2(cmd, timeout=2.0):
    s.reset_input_buffer()
    s.timeout = timeout
    s.write(bytes([cmd, cmd ^ 0xFF]))
    r = s.read(1)
    if not r or r[0] != 0x79:
        return None
    n = s.read(1)
    if not n:
        return b'\x79'
    data = s.read(n[0] + 1)
    s.read(1)   # 尾部 ACK
    return b'\x79' + n + data

s.timeout = 1.0
r = cmd2(0x00)
print('版本应答:', r.hex(' ') if r else '无响应')
if not r:
    sys.exit(1)
time.sleep(0.05)
r = cmd2(0x02)
print('芯片ID应答:', r.hex(' ') if r else '无响应')
if not r:
    sys.exit(1)
time.sleep(0.05)

# ---- 3. 扩展擦除 (0x44) 全片 ----
s.reset_input_buffer()
s.timeout = 1.0
s.write(bytes([0x44, 0xBB])); r = s.read(1)
print('擦除命令 ACK:', r.hex(' ') if r else '无响应')
if not r or r[0] != 0x79:
    sys.exit(1)
time.sleep(0.05)
s.write(bytes([0xFF, 0xFF, 0x00]))  # 0xFFFF = 全片, 校验 0x00
s.timeout = 30.0
r = s.read(1)
print('整片擦除 ACK:', r.hex(' ') if r else '无响应')
if not r or r[0] != 0x79:
    print('擦除失败'); sys.exit(1)
print('擦除完成 (等待 2 秒) ...')
time.sleep(2.0)

# ---- 4. 写入 (每块 256 字节) ----
data = open(BIN, 'rb').read()
blocks = (len(data) + 255) // 256
print(f'固件 {len(data)} 字节, {blocks} 块, 开始写入...')
t0 = time.time()
for i in range(blocks):
    chunk = (data[i*256:(i+1)*256] + b'\xff' * 256)[:256]
    a = FLASH_BASE + i * 256
    ab = a.to_bytes(4, 'big')
    ck = ab[0] ^ ab[1] ^ ab[2] ^ ab[3]
    r = tx(bytes([0x31, 0xCE]) + ab + bytes([ck]))
    if not r or r[0] != 0x79:
        print(f'块{i} 地址ACK失败: {r.hex(" ") if r else "无响应"}'); sys.exit(1)
    body = b'\xff' + chunk
    x = 0
    for b in body:
        x ^= b
    r = tx(body + bytes([x]))
    if not r or r[0] != 0x79:
        print(f'块{i} 数据ACK失败: {r.hex(" ") if r else "无响应"}'); sys.exit(1)
    if (i + 1) % 16 == 0 or i + 1 == blocks:
        print(f'[OK] block {i + 1}/{blocks}')
print(f'[OK] write done ({time.time()-t0:.1f}s)')

# ---- 5. 回读验证 (首 256 字节抽查) ----
# AN3155 READ 流程: cmd ACK -> 地址+校验 ACK -> N+校验 ACK -> N+1 字节数据
s.write(bytes([0x11, 0xEE]))
r = s.read(1)
if not r or r[0] != 0x79:
    print('读命令 ACK 失败'); sys.exit(1)
ab = FLASH_BASE.to_bytes(4, 'big')
ck = ab[0] ^ ab[1] ^ ab[2] ^ ab[3]
s.write(ab + bytes([ck]))
r = s.read(1)
if not r or r[0] != 0x79:
    print('读地址 ACK 失败'); sys.exit(1)
N = 0xFF  # 读 256 字节 (N = 字节数-1)
s.write(bytes([N, N ^ 0xFF]))
r = s.read(1)
if not r or r[0] != 0x79:
    print('读长度 ACK 失败'); sys.exit(1)
back = s.read(N + 1)
ok = back == data[:len(back)]
print(f'回读首 {len(back)} 字节: {"一致 OK" if ok else "不一致 FAIL"}')
if not ok:
    print('期望:', data[:len(back)].hex(' '))
    print('读回:', back.hex(' '))
    sys.exit(1)

# ---- 6. GO 跳转运行 ----
s.write(bytes([0x21, 0xDE]) + FLASH_BASE.to_bytes(4, 'big') + bytes([0x08]))
r = s.read(1)
print('GO 应答:', r.hex(' ') if r else '(无回应, 芯片已在运行)')

# ---- 7. 释放 DTR/RTS ----
# 必须做: 还留在"进 bootloader"那组电平上的话, BOOT0 仍是高, 下次复位又进
# bootloader 而不是跑刚烧进去的 app。
try:
    s.dtr = False
    s.rts = False
except Exception:
    pass
time.sleep(0.1)
print('已释放 DTR/RTS (BOOT0 回到下拉)。建议再发一次 E 确认 app 在跑。')
print('=== FLASH DONE ===')
s.close()
