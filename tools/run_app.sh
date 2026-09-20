#!/usr/bin/env bash
# 从调试器启动 app —— 绕开"复位后进 ROM bootloader"的问题
#
# 背景: 这块板子复位后 PC 落在 0x1FFF_xxxx (系统存储区), 不启动 flash 里的 app。
#       用 SWD 手动把 PC 灌进 app 的 Reset_Handler 就能正常跑 (实测 uwTick 2s 走 1992 拍,
#       串口 E 指令正常应答)。
#
# 用法:  bash tools/run_app.sh
#        bash tools/run_app.sh <elf路径>      # 默认 firmware-v1/build/Debug
#
# 注意: 必须先设 VTOR = 0x08000000。只改 PC/MSP 不设 VTOR 的话, 一旦出错硬故障向量
#       仍从 ROM 的向量表取, PC 会掉回 0x1FFF_xxxx —— 那次"灌 PC 也没用"的误判就是这么来的。
set -euo pipefail

OPENOCD="${OPENOCD:-/c/Users/40512/STM32Toolchain/openocd/bin/openocd}"
SCRIPTS="${OPENOCD_SCRIPTS:-/c/Users/40512/STM32Toolchain/openocd/share/openocd/scripts}"

ELF="${1:-$(dirname "$0")/../firmware-v1/build/Debug/firmware-v1.elf}"
[ -f "$ELF" ] || { echo "找不到 ELF: $ELF" >&2; exit 1; }

# 向量表前两个字就是初始 MSP 与 Reset_Handler —— 直接问芯片, 不用手工抄
"$OPENOCD" -s "$SCRIPTS" \
  -f interface/stlink.cfg -f target/stm32l4x.cfg \
  -c "init" -c "halt" \
  -c 'set vt  [mdw 0x08000000 2]' \
  -c 'set msp "0x[lindex $vt 1]"' \
  -c 'set pc  "0x[lindex $vt 2]"' \
  -c 'echo "vector table: MSP=$msp  Reset=$pc"' \
  -c "mww 0xE000ED08 0x08000000" \
  -c 'reg msp $msp' \
  -c 'reg pc  $pc' \
  -c "resume" \
  -c "shutdown"

echo "已放开运行。用 tools/nanoammeter_capture.py 或串口发 E 验证。"
