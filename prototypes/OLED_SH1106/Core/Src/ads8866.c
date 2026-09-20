#include "ads8866.h"
#include "adc.h"        /* hadc1: PA3 直采路径 */
#include "uart.h"       /* UART_SendString: 校准失败提示 */

/* ============================================================
 * 采集路径切换:
 *   USE_MCU_ADC = 1: 单片机 PA3 (ADC1_IN8) 12 位直采, 粗略测试用
 *        (板载 3.3V 基准 U8 损坏期间, VDDA 需外接 3.3V LDO)
 *   USE_MCU_ADC = 0: ADS8866 外部 16 位 ADC (SPI1), 原设计
 *
 * 说明: PA3 与 ADS8866 在 PCB 上都接 V_OUT (网表确认), 切换只需改此宏
 * ============================================================ */
#define USE_MCU_ADC 1

#if USE_MCU_ADC

HAL_StatusTypeDef ADS8866_Init(void)
{
    uint32_t guard;

    /* 12 位 ADC 校准 (需 VDDA 已稳定供电).
     * 校准失败只提示, 不阻塞启动: 基准未接好时先让程序跑起来 */
    if (HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED) != HAL_OK)
    {
        UART_SendString("WARN: ADC calibration failed (VDDA?)\r\n");
    }

    /* 校准结束后 HAL 会把 ADC 关掉 (ADEN=0), 这里重新使能并等就绪,
     * 后续 ReadRaw 直接触发转换, 不再开关 ADC */
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    guard = 200000U;
    while ((ADC1->ISR & ADC_ISR_ADRDY) == 0U && guard-- > 0U)
    {
    }
    if (guard == 0U)
    {
        UART_SendString("WARN: ADC enable timeout\r\n");
    }

    return HAL_OK;
}

/* PA3 12 位读取 -> 左移 4 位归一到 16 位码域,
 * 与阈值宏/电压换算公式 (65536 满量程) 全部兼容.
 * 寄存器级轮询: 不依赖 HAL_GetTick (TIM6 中断与 SysTick 同优先级时
 * tick 会被饿死, HAL 的超时轮询会变成死循环——实测踩过) */
uint16_t ADS8866_ReadRaw(void)
{
    uint32_t guard;
    uint16_t raw;

    /* 防御: ADC 意外被关时重新使能 (正常路径 ADEN 常开) */
    if ((ADC1->CR & ADC_CR_ADEN) == 0U)
    {
        ADC1->ISR = ADC_ISR_ADRDY;
        ADC1->CR |= ADC_CR_ADEN;
        guard = 200000U;
        while ((ADC1->ISR & ADC_ISR_ADRDY) == 0U && guard-- > 0U)
        {
        }
    }

    /* 清 EOC/OVR, 软件触发单次转换 */
    ADC1->ISR = ADC_ISR_EOC | ADC_ISR_OVR;
    ADC1->CR |= ADC_CR_ADSTART;

    guard = 200000U;
    while ((ADC1->ISR & ADC_ISR_EOC) == 0U && guard-- > 0U)
    {
    }
    if (guard == 0U)
    {
        return 0U;   /* 转换超时: 返回 0, 绝不卡死 */
    }

    raw = (uint16_t)ADC1->DR;   /* 读 DR 自动清 EOC */
    return (uint16_t)((uint32_t)raw << 4);
}

float ADS8866_ReadVoltage(float vref)
{
    return (float)ADS8866_ReadRaw() * vref / 65536.0f;
}

#else /* ---- 原 ADS8866 SPI1 路径 ---- */

/* ------------------------------------------------------------
 * 数据手册 (SBAS614) 关键时序依据:
 *
 *  模式选择 : CONVST 上升沿时 DIN 为高 -> CS 模式(3线/4线);
 *             为低 -> 菊花链模式。3 线 CS 模式要求 DIN 恒高,
 *             CONVST 充当 CS。
 *
 *  tconv   : 500ns ~ 8.8us (内部时钟, 容差较大)
 *  tacq    : >= 1.2us (满量程阶跃建立到 16 位)
 *  tcyc    : >= 10us (两次转换最小间隔, 100kSPS)
 *  SCLK    : 最高 16MHz (最小周期 62.5ns), 空闲低电平;
 *             数据在 SCLK 下降沿移出, 上升沿稳定 (t_h_CK_DO 约 13ns),
 *             主机在上升沿采样即可。
 *  CONVST  : 上升沿采样输入并启动转换(转换用内部时钟, 与 CONVST 无关);
 *             转换结束瞬间若 CONVST 为低 -> DOUT 产生 busy 指示
 *             (需 10k 上拉, 本驱动不使用);
 *             为高 -> 无 busy 指示。
 *  CONVST 下降沿: DOUT 立即输出 MSB (12.3ns 内有效),
 *             此后每个 SCLK 下降沿移出下一位,
 *             第 16 个下降沿后(或 CONVST 变高)DOUT 回到三态。
 * ------------------------------------------------------------
 */

#define ADS8866_CONVST_PORT     GPIOA
#define ADS8866_CONVST_PIN      GPIO_PIN_4

#define ADS8866_T_CONV_US       9U    /* >= tconv-max 8.8us, 留余量 */
#define ADS8866_SPI_TIMEOUT_MS  100U

/* SPI1 句柄 (自包含, 不依赖 CubeMX 生成代码) */
static SPI_HandleTypeDef hspi1;

/* ------------------------------------------------------------
 * DWT 微秒延时 (80MHz 主频)
 * ------------------------------------------------------------ */
static void DWT_DelayUs(uint32_t us)
{
    if ((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) == 0U)
    {
        CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    }
    if ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) == 0U)
    {
        DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    }

    uint32_t start = DWT->CYCCNT;
    uint32_t ticks = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < ticks)
    {
    }
}

HAL_StatusTypeDef ADS8866_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    uint16_t dummy = 0xFFFFU;

    /* ---- CONVST: PA4 推挽输出 ---- */
    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitStruct.Pin   = ADS8866_CONVST_PIN;
    GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull  = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(ADS8866_CONVST_PORT, &GPIO_InitStruct);
    HAL_GPIO_WritePin(ADS8866_CONVST_PORT, ADS8866_CONVST_PIN, GPIO_PIN_RESET);

    /* ---- SPI1: PA5=SCK, PA6=MISO, PA7=MOSI (AF5) ---- */
    __HAL_RCC_SPI1_CLK_ENABLE();

    GPIO_InitStruct.Pin       = GPIO_PIN_5 | GPIO_PIN_6 | GPIO_PIN_7;
    GPIO_InitStruct.Mode      = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull      = GPIO_NOPULL;
    GPIO_InitStruct.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF5_SPI1;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    /* 16 位帧 = 一次传输正好 16 个连续 SCLK;
     * 80MHz / 8 = 10MHz <= 16MHz;
     * CPOL=0(空闲低) + CPHA=0(上升沿采样), 对应手册"数据下降沿移出"
     */
    hspi1.Instance               = SPI1;
    hspi1.Init.Mode              = SPI_MODE_MASTER;
    hspi1.Init.Direction         = SPI_DIRECTION_2LINES;
    hspi1.Init.DataSize          = SPI_DATASIZE_16BIT;
    hspi1.Init.CLKPolarity       = SPI_POLARITY_LOW;
    hspi1.Init.CLKPhase          = SPI_PHASE_1EDGE;
    hspi1.Init.NSS               = SPI_NSS_SOFT;
    hspi1.Init.BaudRatePrescaler = SPI_BAUDRATEPRESCALER_8;
    hspi1.Init.FirstBit          = SPI_FIRSTBIT_MSB;
    hspi1.Init.TIMode            = SPI_TIMODE_DISABLE;
    hspi1.Init.CRCCalculation    = SPI_CRCCALCULATION_DISABLE;
    hspi1.Init.CRCPolynomial     = 7;
    hspi1.Init.CRCLength         = SPI_CRC_LENGTH_DATASIZE;
    hspi1.Init.NSSPMode          = SPI_NSS_PULSE_DISABLE;
    /* 注意: HAL_SPI_Init 会调用 spi.c 里的 HAL_SPI_MspInit,
     * 该函数只处理 SPI2, 对 SPI1 是 no-op, 时钟/GPIO 已由本函数配置 */
    if (HAL_SPI_Init(&hspi1) != HAL_OK)
    {
        return HAL_ERROR;
    }

    /* 发一次 0xFFFF: 置高并保持 MOSI(DIN)——
     * 3 线 CS 模式要求 DIN 恒高, 且保证首次 CONVST 上升沿时
     * DIN 已为高 (选择 CS 模式而非菊花链模式) */
    if (HAL_SPI_TransmitReceive(&hspi1, (uint8_t *)&dummy, (uint8_t *)&dummy,
                                1, ADS8866_SPI_TIMEOUT_MS) != HAL_OK)
    {
        return HAL_ERROR;
    }

    return HAL_OK;
}

uint16_t ADS8866_ReadRaw(void)
{
    uint16_t raw = 0U;

    /* 1) CONVST 上升沿: 采样输入并启动转换
     *    (此时 DIN 为高 -> CS 模式) */
    HAL_GPIO_WritePin(ADS8866_CONVST_PORT, ADS8866_CONVST_PIN, GPIO_PIN_SET);

    /* 2) 保持 CONVST 高至转换结束 (>= tconv-max 8.8us)。
     *    转换结束瞬间 CONVST 为高 -> 不产生 busy 指示 */
    DWT_DelayUs(ADS8866_T_CONV_US);

    /* 3) CONVST 下降沿: DOUT 输出 MSB (12.3ns 内有效) */
    HAL_GPIO_WritePin(ADS8866_CONVST_PORT, ADS8866_CONVST_PIN, GPIO_PIN_RESET);

    /* 4) 16 个 SCLK 读出 16 位 (发送 0xFFFF, DIN 保持高) */
    if (HAL_SPI_Receive(&hspi1, (uint8_t *)&raw, 1, ADS8866_SPI_TIMEOUT_MS)
        != HAL_OK)
    {
        return 0U;
    }

    /* 5) 采集时间 tacq >= 1.2us: 本次读取 16bit@10MHz 约 1.6us 已满足;
     *    两次转换间隔需 >= tcyc 10us, 由调用方节奏保证 */

    return raw;
}

float ADS8866_ReadVoltage(float vref)
{
    return (float)ADS8866_ReadRaw() * vref / 65536.0f;
}

#endif /* USE_MCU_ADC */
