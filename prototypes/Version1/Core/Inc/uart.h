#ifndef __UART_H
#define __UART_H

#include "main.h"

void UART_SendString(char *str);

/* ============================================================
 * 串口指令接收 (USART1 中断, 单字符指令):
 *   'S'/'s' = 启动测量 (开启 ADG1219 + 连续电荷平衡)
 *   'X'/'x' = 停止测量 (关断 ADG1219, 回空闲)
 * 需在 MX_USART1_UART_Init 之后调用 UART_Init_RX 开启接收
 * ============================================================ */
void UART_Init_RX(void);

/* 消费式读取指令: 'S' | 'X' | 0(无) */
char UART_GetCommand(void);

#endif
