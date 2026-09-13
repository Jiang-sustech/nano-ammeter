#!/usr/bin/env bash
# 跑 MATLAB 并把输出从 GBK 转成 UTF-8。
#
# 为什么需要:
#   中文 Windows 上, MATLAB R2026b 的 stdout **实际写 GBK 字节** —— 即使
#   feature('DefaultCharacterSet') 报的是 UTF-8。实测 `独立实测` 输出
#   B6 C0 C1 A2 CA B5 B2 E2 (GBK), 而 UTF-8 应为 E7 8B AC E7 AB 8B ...。
#   终端按 UTF-8 解 → 满屏 `����`。
#   `matlab -X utf8` 这个版本不认 (报「无法识别的命令行选项: X」);
#   `chcp 65001` 也不顶用。iconv 是实测可行的解。
#
# 用法:
#   bash tools/matlab_utf8.sh "cd('tools'); fit_constants('../fit_data.csv')"
#
# 除输出编码外, 其余行为与直接 `matlab -batch "<命令>"` 完全一致;
# MATLAB 的退出码原样透传, 便于脚本判断成败。
set -uo pipefail

if [ $# -eq 0 ]; then
  echo "用法: bash tools/matlab_utf8.sh \"<MATLAB 命令>\"" >&2
  exit 2
fi

# -c: 遇到非法序列跳过而不是中断 (MATLAB 的警告偶尔会混进非 GBK 字节)
matlab -batch "$*" 2>&1 | iconv -f GBK -t UTF-8 -c
exit "${PIPESTATUS[0]}"
