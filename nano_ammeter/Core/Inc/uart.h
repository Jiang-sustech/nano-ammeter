#ifndef __UART_H
#define __UART_H

#include "stm32l4xx_hal.h"

/* ============================================================
 * USART1: PA9=TX, PA10=RX, 115200 8N1 (板载 CH340)
 * 指令协议 (单字符, 大小写不敏感, 行结束符忽略, 测量期间忽略):
 *   S = 单次测量 (1s 窗口, <1nA 自动接 10s 双斜率, 测完自动回空闲)
 *   N = 底噪标定 (50s 纯积分)
 *   D = 查询最近结果
 *   B = 回传最近完整窗口原始波形 (WAVE 头 + 二进制数据 + 校验和)
 *   X = 回传同一窗口的 ADS8866 外部 16 位波形 (WAVEX 头, 格式同 B)
 *   E = ADS8866 观测通路诊断一行 (样本数/坏读数/SPI 超时计数)
 *   W = 回传最近一次底噪测量的逐秒序列 (NOISE 头, 格式同 B, 50 点)
 * 自动上报: 测量完成报一行结果; 模式二开始报一行; 底噪进度/结果各一行
 * ============================================================ */

extern UART_HandleTypeDef huart1;

void MX_USART1_UART_Init(void);
void UART_Init_RX(void);                /* 启动单字节 RX 中断 */
char UART_GetCommand(void);             /* 消费式读取指令, 无指令返回 0 */
void UART_SendString(const char *str);  /* ASCII 文本发送 */
void UART_SendBinary(const uint8_t *data, uint16_t len);  /* 二进制数据发送 */

/* 定点十进制格式化: value/10^decimals, 带符号, 如 (12345,3) -> "+12.345"
 * (避免浮点 printf, 全局共享工具) */
void UART_FormatScaled(int64_t value, uint8_t decimals, char *buf);

#endif /* __UART_H */
