#ifndef __UART_H
#define __UART_H

#include "stm32l4xx_hal.h"

/* ============================================================
 * USART1: PA9=TX, PA10=RX, 115200 8N1 (板载 CH340)
 * 指令协议 (单字符, 大小写不敏感, 行结束符忽略):
 *   S = 启动测量 (模式一)
 *   X = 停止测量 (回空闲)
 *   N = 底噪标定 (50s 纯积分)
 *   D = 查询最近一次结果
 *   B = 回传当前窗口原始波形 (WAVE 头 + 二进制数据 + 校验和)
 * 自动上报: 模式一每秒窗口结束报一行结果;
 *           模式二开始/结束各报一行; 底噪结束报 IBIAS 一行
 * ============================================================ */

extern UART_HandleTypeDef huart1;

void MX_USART1_UART_Init(void);
void UART_Init_RX(void);                /* 启动单字节 RX 中断 */
char UART_GetCommand(void);             /* 消费式读取指令, 无指令返回 0 */
void UART_SendString(const char *str);  /* ASCII 文本发送 */
void UART_SendBinary(const uint8_t *data, uint16_t len);  /* 二进制数据发送 */

#endif /* __UART_H */
