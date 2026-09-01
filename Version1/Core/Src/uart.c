#include "uart.h"
#include "usart.h"
#include "string.h"


void UART_SendString(char *str)
{
    HAL_UART_Transmit(&huart1,(uint8_t*)str,strlen(str),1000);
}

/* ---- 串口指令接收 (单字节中断 + 消费式读取) ---- */
static volatile uint8_t rx_byte;
static volatile char cmd_pending;

void UART_Init_RX(void)
{
    cmd_pending = 0;
    HAL_UART_Receive_IT(&huart1, (uint8_t *)&rx_byte, 1);
}

char UART_GetCommand(void)
{
    char cmd = cmd_pending;
    cmd_pending = 0;
    return cmd;
}

/* USART1 每收到 1 字节触发 (HAL 回调): 识别 'S'/'X' 单字符指令 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART1)
    {
        return;
    }

    switch (rx_byte)
    {
    case 'S':
    case 's':
        cmd_pending = 'S';
        break;
    case 'X':
    case 'x':
        cmd_pending = 'X';
        break;
    default:
        break;
    }

    /* 重新武装下一个字节 */
    HAL_UART_Receive_IT(&huart1, (uint8_t *)&rx_byte, 1);
}
