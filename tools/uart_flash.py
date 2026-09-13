# STM32 ROM bootloader UART 烧录器 (AN3155 协议)
# 用法: python uart_flash.py <bin路径> [COM口] [波特率] [--wait] [--sweep]
#       [--hold=秒] [--release=秒]
# 板子: STM32L431CCT6 + CH340(PA9/PA10), Bootloader 要求 8E1 @115200
#
# =====================================================================
# 进入 bootloader: 自动序列 (2026-09-13 用网表推导 + 上板实测确定)
# =====================================================================
# 网表推导 (Q1/Q2 都是 SOT-23-3, 按标准脚位 1=B/2=E/3=C 的 BJT, 或 1=G/2=S/3=D 的 MOS,
# 两种假设对下面结论一致 —— 见"实测验证"里对 2 脚接 3V3 的判定):
#
#   Q1: 1(B/G) <-R19(1k)- DTR#      2(E/S) = RTS#        3(C/D) -> R17(10k 上拉 3V3) + D1 -> NRST
#       => NPN/高边电平开关: 只有 DTR# 高 *且* RTS# 低 (Vbe=+3.3V) 时 Q1 导通, 把 NRST 拉低;
#          RTS# 高或 DTR# 低时 Q1 截止, NRST 被 R27 上拉回 3V3。D1 只让"拉低"过去, 不回流。
#   Q2: 1(B/G) <-R20(1k)- RTS#      2(E/S) = 3V3         3(C/D) -R23(10k)-> BOOT0
#       => 高边开关 (2 脚接电源, 必是 PNP 或 P-MOS): RTS# 低时导通, 把 BOOT0 往上抬;
#          RTS# 高时截止, BOOT0 被 R16(10k) 拉回地。
#
#   pyserial 语义 (CH340, 实测): ser.dtr/ser.rts = True => 引脚被拉低(0V); False => 引脚 3V3。
#
#   于是"NRST 低 + BOOT0 高"只能靠唯一一组静态电平:  dtr=False, rts=True
#   而"释放复位时 BOOT0 还得保持高"只能靠**只翻 DTR**:    dtr=True,  rts=True
#   (若连 RTS 一起翻, BOOT0 会先掉下去, 芯片就正常跑 app 了 —— 这也是"多相时序"的由来)
#
# =====================================================================
# 实测验证 (COM8, 115200, 判据 = SWD 读 PC 落在 0x1FFF0000 段 + 8E1 收到 0x79)
# =====================================================================
#   静态四态 + 全部 12 种有序跳转都扫过 (复现工具: python tools/boot0_probe.py map), 结论:
#     dtr=_,   rts=True  -> NRST 被握在低 (SWD 连 halt 都 halt 不住 = 芯片在复位里)
#     dtr=_,   rts=False -> NRST 高, 芯片正常跑 app
#     跳转 (0,1)->(1,1)  [只翻 DTR, 推导线] -> 复位确实发生 (RCC_CSR.PINRSTF),
#                                               但芯片**跑的是 app**, 没进 bootloader
#   BOOT0 引脚(接 PH3/U6.44, 用 SWD 把 PH3 配成数字输入后读 GPIOH_IDR.bit3):
#     无上下拉 = 0, 内部下拉(40k) = 0, 内部上拉(40k) = 1   <-- 只有 Q2 导通那一态
#     这正是"被 10k/10k 分压抬到 VDD/2 = 1.65V"的指纹 (上拉后再抬到 ~1.83V 才够翻过门限)。
#
#   => 根因在硬件: BOOT0 与 Q2 之间有 R23=10k, BOOT0 又对地挂 R16=10k,
#      两者 1:1 分压, 自动路径最高只能把 BOOT0 抬到 VDD/2 ≈ 1.65V,
#      而 STM32L431 的 VIH(min) = 0.7*VDD = 2.31V —— 芯片在复位释放时把它当**低**采样,
#      于是永远进不了 bootloader。手动 SW1 是把 BOOT0 直连 3V3 (无分压), 所以手动一直好用。
#      硬件改法: R23 换成 0Ω(或 ≤1k) 即可 (保留 R16=10k 下拉); 把 R16 换 100k 也勉强,
#      但那会被 BOOT0 内部下拉影响, 不如动 R23 稳。
#
# 所以本脚本的自动序列在**当前硬件上物理不可能成功**, 保留它是为了:
#   ① R23 改小以后无需再改代码, 直接就能自动进;
#   ② 失败时给出明确提示, 而不是让人以为极性猜错了。
# 其余部分 (AN3155 协议: GET/擦除/写入/回读/GO) 与进入方式无关, 不变。
import serial
import serial.tools.list_ports
import sys
import time

# ---- 时序参数 (可命令行覆盖: --hold=0.25 --release=0.3) ----
def _arg(name, default):
    for a in sys.argv:
        if a.startswith('--%s=' % name):
            return float(a.split('=', 1)[1])
    return default


HOLD_S = _arg('hold', 0.25)      # "NRST 低 + BOOT0 高" 保持多久 (脉冲宽度)
RELEASE_S = _arg('release', 0.30)  # 释放 NRST 后等多久再发 0x7F
#   (NRST 上升沿被 C41=100nF * R27=10k 拖慢, tau≈1ms, 所以要留给 bootloader 启动时间)
PROBE_TRIES = 30                 # 握手 0x7F 重试次数
PROBE_GAP = 0.05                 # 每次重试间隔

# 位置参数是 <bin> [COM口] [波特率]; 开关 (--xxx / --xxx=yyy) **必须先剔掉**再按位取,
# 否则 `uart_flash.py fw.bin COM8 --sweep` 会把 '--sweep' 当成波特率而崩
# (int('--sweep') -> ValueError, 实测踩过两次)。
_POS = [a for a in sys.argv[1:] if not a.startswith('--')]
BIN = _POS[0] if len(_POS) > 0 else 'sawtooth_uart.bin'
BAUD = int(_POS[2]) if len(_POS) > 2 else 115200
FLASH_BASE = 0x08000000

# ---- 找 CH340 ----
port = None
if len(_POS) > 1:
    port = _POS[1]
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
# 网表 + 实测确定的序列 (推导见文件头), 两步:
#   (dtr=False, rts=True)  = NRST 拉低 + BOOT0 抬高   -> 握住复位
#   (dtr=True,  rts=True)  = 只翻 DTR, NRST 释放而 BOOT0 保持 -> 芯片在此刻采样 BOOT0
# 顺序不能颠倒/合并: 连 RTS 一起翻会让 BOOT0 在 NRST 上升前掉下去。
ENTER_SEQ = [(False, True, HOLD_S), (True, True, RELEASE_S)]

# 兜底盲扫 (别的板子/别的驱动极性时用 --sweep 打开):
# 四态静态 + ESP 式交叉, 都试一遍。
SWEEP_SEQS = [
    ('静态 dtr=F rts=T',      [(False, True, 0.25)]),
    ('静态 dtr=T rts=T',      [(True, True, 0.25)]),
    ('静态 dtr=T rts=F',      [(True, False, 0.25)]),
    ('静态 dtr=F rts=F',      [(False, False, 0.25)]),
    ('ESP 式交叉 (T,F)->(F,T)', [(True, False, 0.1), (False, True, 0.25)]),
    ('ESP 式交叉 (F,T)->(T,F)', [(False, True, 0.1), (True, False, 0.25)]),
]


def apply(d, r_):
    """pyserial 语义: True=引脚拉低(有效), False=引脚 3V3 (CH340 实测)"""
    s.dtr = d
    s.rts = r_


def handshake():
    """发 0x7F (8E1) 等 0x79"""
    s.reset_input_buffer()
    for _ in range(PROBE_TRIES):
        s.write(b'\x7f')
        r_ = s.read(1)
        if r_ and r_[0] == 0x79:
            return True
        time.sleep(PROBE_GAP)
    return False


def try_enter(label, phases):
    for (d, r_, delay) in phases:
        apply(d, r_)
        time.sleep(delay)
    ok = handshake()
    print('     %s -> %s' % (label, '[OK] 收到 0x79' if ok else '无 0x79'))
    return ok


print('自动进入 bootloader (握手序列见文件头) ...')
entered = False
seqs = [('网表推导线 (F,T)->(T,T)', ENTER_SEQ)]
if '--sweep' in sys.argv:
    seqs += SWEEP_SEQS
for label, phases in seqs:
    if try_enter(label, phases):
        print('[OK] 进入 bootloader (生效序列: %s)' % label)
        entered = True
        break
    # 每次尝试前把线收回"BOOT0 低"的安全态, 免得上一次余留的电平干扰下一次
    apply(True, False)
    time.sleep(0.1)

if not entered:
    # --wait: 不设截止时间, 一直等按键。用于"手上没空, 什么时候按都行"的场合;
    #          不加则只等 90 秒 (CI/无人值守时不会挂死)。
    wait_s = 3600.0 if '--wait' in sys.argv else 90.0
    print('自动进入失败 —— **这是本板已知的硬件问题, 不是极性/时序没试对**:')
    print('  BOOT0 与 Q2 之间串了 R23=10k, BOOT0 又对地挂 R16=10k, 1:1 分压 ->')
    print('  自动路径最高只到 VDD/2 ≈ 1.65V < STM32L431 的 VIH(min)=0.7*VDD=2.31V,')
    print('  芯片复位释放时把 BOOT0 采样成低, 于是照常跑 app。')
    print('  (上面那步会把 NRST 真的握到低, 也会产生真正的复位脉冲 —— 见文件头实测表)')
    print('  彻底修好: 把 R23 换成 0Ω (或 ≤1k)；R16 保留 10k 下拉。改完本脚本自动可用。')
    print('临时绕过: 手动进 —— 手动 SW1 是把 BOOT0 直连 3V3, 没有分压, 一直好使。')
    print('回退手动 (%s):'
          % ('无限等待, Ctrl-C 中止' if wait_s > 1000 else '90 秒窗口'))
    print('  请执行: 按住 SW1(BOOT0) -> 点一下 SW3(RST) -> 松开 SW1')
    print('  或者: BOOT0 拉高 -> 按一下复位 -> 松开 BOOT0')
    # 手动按完键以后芯片是靠自己开机进 bootloader 的, 这里把 DTR/RTS 放掉,
    # 免得残留电平继续跟手动路径打架 (NOTE: 本板上 8E1 的 0x7F 会让正在跑
    # 的 app 的 UART 接收失聪, 只有复位才能恢复 —— 不影响 bootloader 里的握手)。
    apply(False, False)
    time.sleep(0.1)
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
# 必须做: dtr=False rts=True 那组是"NRST 被握低", 留着不放芯片就一直在复位里;
# 而 rts=True 还意味着 BOOT0 被抬高 (本板只有 1.65V, 但改小 R23 后就是真高),
# 那样下一次复位又会进 bootloader 而不是跑刚烧进去的 app。
# 放回 (dtr=False, rts=False): RTS# 回到 3V3 -> Q2 截止 -> BOOT0 被 R16 拉回地;
# Q1 也截止 -> NRST 被 R27 上拉回 3V3。
try:
    apply(False, False)
except Exception:
    pass
time.sleep(0.1)
print('已释放 DTR/RTS (NRST 放开, BOOT0 回到下拉)。建议再发一次 E 确认 app 在跑。')
print('=== FLASH DONE ===')
s.close()
