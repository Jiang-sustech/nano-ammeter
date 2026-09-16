#include "uart.h"
#include "main.h"
#include <string.h>

UART_HandleTypeDef huart1;

/* 串口波特率 —— **保持 115200**。提不上去, 实测过两档, 都不行。
 *
 * =====================================================================
 * 2026-09-16 实测记录 (整窗 12500 字节回传, 每档至少 16 次)
 * =====================================================================
 *   115200   1.085 s   **全清** —— 本工程从 09-13 起跑了几百次, 没坏过一次
 *   921600   0.147 s   89% 一次成功, 11% 需重传一次; 重传后 **0 丢失**
 *   2000000  0.073 s   **1/16 一次成功, 13/16 连 4 次重传全失败** -> 不可用
 *
 * **失败率随波特率单调上升** (0% -> 11% -> 81%), 所以这是**速率/带宽**问题,
 * 不是接地问题 —— 中途怀疑过地线松, 接好后重测 2 Mbps **反而更差** (19%),
 * 排除了接地。
 *
 * 丢的字节**再也没来**, 每次少 1~5 个, 尾字节缺失。固件用的是**阻塞式**
 * HAL_UART_Transmit, MCU 侧不可能丢 —— 丢在 **CH340G -> USB -> 主机**这一段。
 * 板载是 CH340G 克隆片, 2 Mbps 是它的规格上限, 顶到头就不稳; 921600 已经在
 * 边缘 (89%), 只有 115200 是完全干净的。
 *
 * (921600 + 重传其实能用, 但每 9 次就有 1 次要多花 3.9 s, 收益不划算 ——
 *  用户 2026-09-16 裁定: **慢一点可以接受**, 保住"全清"这一条更重要。)
 *
 * USART1 挂 PCLK2 = 80 MHz (main.c:551 APB2CLKDivider = DIV1)。
 * MCU 侧从来不是瓶颈: USART 上限 fCK/8 = 10 Mbaud —— 瓶颈全在 CH340G。
 *
 * ⚠️ **RX 的隐患 (与波特率无关, 但记在这里)**: STM32L4 的 USART **没有 FIFO**,
 *    只有一个接收数据寄存器。TIM6 采样中断每 160 µs 就来一次、执行时间远超
 *    一字节的时间 —— **如果上位机在测量期间发字节, 会丢**。
 *    现在能工作是因为协议是"一问一答": 发完 S 之后上位机只读不发。
 *    以后若加了测量中交互的指令, 这一条会立刻变成 bug。
 *    真需要的话得开 DMA 收, 或者把 USART 中断优先级提到 TIM6 之上。
 *
 * 回退: 串口打不通也不怕 —— SWD (ST-Link) 始终能烧。
 */
#define BOARD_BAUD  115200U

void MX_USART1_UART_Init(void)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = BOARD_BAUD;
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

/* ---- 整行缓冲 (参数化指令用) ----
 * 单字符环形队列只认字母, 装不下 "K0,998765,-123" 这类带参指令。
 * 这里再挂一层行缓冲: 收到 '\n' 就把整行标记为可取。
 *
 * 并发: 缓冲区写在 ISR 里、读在主循环里。用 line_ready 做闸门 ——
 * 上一行还没被取走时, 新到的字节直接丢弃, 避免主循环读到半截。
 * (指令是人敲的, 不会连发; 丢弃比竞态安全) */
static volatile char    line_buf[UART_LINE_MAX];
static volatile uint8_t line_len;
static volatile uint8_t line_ready;
static volatile uint8_t line_drop;      /* 本行超长, 丢弃到下一个换行 */

uint8_t UART_GetLine(char *out, uint8_t max)
{
    uint8_t i;

    if (!line_ready)
    {
        return 0U;
    }

    for (i = 0U; i < line_len && i < (uint8_t)(max - 1U); i++)
    {
        out[i] = line_buf[i];
    }
    out[i] = '\0';

    line_len = 0U;
    line_ready = 0U;
    return 1U;
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    char cmd = 0;
    char c;

    if (huart->Instance != USART1)
    {
        return;
    }

    c = (char)rx_byte;

    /* ---- 行缓冲 ---- */
    if (c == '\n')
    {
        if (line_drop)
        {
            line_drop = 0U;
            line_len = 0U;
        }
        else if (line_len > 0U && !line_ready)
        {
            line_buf[line_len] = '\0';
            line_ready = 1U;
        }
        else
        {
            line_len = 0U;              /* 上一行没人取, 或空行 */
        }
    }
    else if (c != '\r' && !line_drop)
    {
        if (line_ready)
        {
            /* 上一行还没被取走: 丢弃, 免得主循环读到半截 */
        }
        else if (line_len < (UART_LINE_MAX - 1U))
        {
            line_buf[line_len++] = c;
        }
        else
        {
            line_drop = 1U;             /* 超长: 丢弃整行 */
            line_len = 0U;
        }
    }

    /* ---- 单字符指令 (保留原路径) ---- */
    switch (rx_byte)
    {
    case 'S': case 's': cmd = 'S'; break;
    case 'D': case 'd': cmd = 'D'; break;
    case 'B': case 'b': cmd = 'B'; break;
    case 'X': case 'x': cmd = 'X'; break;   /* ADS8866 并行观测波形 */
    case 'E': case 'e': cmd = 'E'; break;   /* 观测通路诊断 */
    default: break;   /* 参数化指令与行结束符走上面的行缓冲 */
    }

    if (cmd != 0 && (cmd_head + 1) % CMD_RING_SIZE != cmd_tail)
    {
        cmd_buf[cmd_head] = cmd;
        cmd_head = (cmd_head + 1) % CMD_RING_SIZE;
    }

    /* 重新武装下一个字节 */
    HAL_UART_Receive_IT(&huart1, (uint8_t *)&rx_byte, 1);
}
