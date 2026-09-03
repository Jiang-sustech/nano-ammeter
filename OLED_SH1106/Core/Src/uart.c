#include "uart.h"
#include "main.h"
#include <string.h>

UART_HandleTypeDef huart1;

void MX_USART1_UART_Init(void)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    huart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    if (HAL_UART_Init(&huart1) != HAL_OK)
    {
        Error_Handler();
    }
}

void HAL_UART_MspInit(UART_HandleTypeDef *uartHandle)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};

    if (uartHandle->Instance == USART1)
    {
        /* USART1 时钟源: PCLK2 */
        PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_USART1;
        PeriphClkInit.Usart1ClockSelection = RCC_USART1CLKSOURCE_PCLK2;
        if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
        {
            Error_Handler();
        }

        __HAL_RCC_USART1_CLK_ENABLE();
        __HAL_RCC_GPIOA_CLK_ENABLE();

        /* PA9 -> USART1_TX, PA10 -> USART1_RX */
        GPIO_InitStruct.Pin = GPIO_PIN_9 | GPIO_PIN_10;
        GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
        GPIO_InitStruct.Pull = GPIO_NOPULL;
        GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
        GPIO_InitStruct.Alternate = GPIO_AF7_USART1;
        HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

        /* 串口接收中断 (优先级低于 TIM6 采样节拍 0,0) */
        HAL_NVIC_SetPriority(USART1_IRQn, 1, 0);
        HAL_NVIC_EnableIRQ(USART1_IRQn);
    }
}

void HAL_UART_MspDeInit(UART_HandleTypeDef *uartHandle)
{
    if (uartHandle->Instance == USART1)
    {
        __HAL_RCC_USART1_CLK_DISABLE();
        HAL_GPIO_DeInit(GPIOA, GPIO_PIN_9 | GPIO_PIN_10);
    }
}

/* ---- 串口指令接收 (单字节中断 + 环形队列, 消费式读取) ----
 * 只认指令字符, 其余字节(含行结束符)忽略, 队列满时丢弃最旧指令 */
#define CMD_RING_SIZE 8

static volatile uint8_t rx_byte;
static volatile char    cmd_buf[CMD_RING_SIZE];
static volatile uint8_t cmd_head, cmd_tail;

void UART_Init_RX(void)
{
    cmd_head = cmd_tail = 0;
    HAL_UART_Receive_IT(&huart1, (uint8_t *)&rx_byte, 1);
}

char UART_GetCommand(void)
{
    char cmd;

    if (cmd_head == cmd_tail)
    {
        return 0;
    }
    cmd = cmd_buf[cmd_tail];
    cmd_tail = (cmd_tail + 1) % CMD_RING_SIZE;
    return cmd;
}

void UART_SendString(const char *str)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)str, strlen(str), 1000);
}

void UART_SendBinary(const uint8_t *data, uint16_t len)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)data, len, 5000);
}

/* 定点十进制格式化 (共享工具, 避免浮点 printf) */
void UART_FormatScaled(int64_t value, uint8_t decimals, char *buf)
{
    char digits[24];
    int idx = 0;
    int64_t v;
    uint8_t n = 0;
    char *p = buf;

    if (value < 0)
    {
        *p++ = '-';
        v = -value;
    }
    else
    {
        *p++ = '+';
        v = value;
    }

    do
    {
        digits[idx++] = (char)('0' + (v % 10));
        v /= 10;
        n++;
    } while (v > 0);

    while (n <= decimals)
    {
        digits[idx++] = '0';
        n++;
    }

    while (idx > 0)
    {
        *p++ = digits[--idx];
        if (decimals > 0 && idx == decimals)
        {
            *p++ = '.';
        }
    }
    *p = '\0';
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    char cmd = 0;

    if (huart->Instance != USART1)
    {
        return;
    }

    switch (rx_byte)
    {
    case 'S': case 's': cmd = 'S'; break;
    case 'N': case 'n': cmd = 'N'; break;
    case 'D': case 'd': cmd = 'D'; break;
    case 'B': case 'b': cmd = 'B'; break;
    default: break;   /* 行结束符等忽略 */
    }

    if (cmd != 0 && (cmd_head + 1) % CMD_RING_SIZE != cmd_tail)
    {
        cmd_buf[cmd_head] = cmd;
        cmd_head = (cmd_head + 1) % CMD_RING_SIZE;
    }

    /* 重新武装下一个字节 */
    HAL_UART_Receive_IT(&huart1, (uint8_t *)&rx_byte, 1);
}
