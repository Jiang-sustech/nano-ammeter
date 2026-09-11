#include "ads8866.h"
#include "main.h"

/* ============================================================
 * ADS8866 位操作驱动 (SPI1, 寄存器级)
 *
 * 为什么不用 HAL_SPI_Receive: 它内部靠 HAL_GetTick 判超时, 而 TIM6 中断
 * 与 SysTick 同为优先级 0, 中断内 SysTick 会被饿死 -> tick 不前进 ->
 * 一旦 SPI 有任何异常就是死循环 (本工程在 ADC 上踩过同样的坑)。
 * 这里全部用循环计数守卫, 绝不依赖 tick。
 *
 * 模式选择: CONVST 上升沿时 DIN 必须为高 -> 选中 3 线 CS 模式
 * (DIN 为低会进菊花链模式)。因此每次传输都发 0xFFFF 维持 DIN 高。
 * ============================================================ */

#define CONVST_PORT     GPIOA
#define CONVST_PIN      GPIO_PIN_4

#define T_CONV_US       9U          /* >= tconv-max 8.8us, 留余量 */
#define SPI_GUARD       100000U

/* DWT 微秒延时 (80MHz) */
static void DWT_DelayUs(uint32_t us)
{
    uint32_t start, ticks;

    if ((CoreDebug->DEMCR & CoreDebug_DEMCR_TRCENA_Msk) == 0U)
    {
        CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    }
    if ((DWT->CTRL & DWT_CTRL_CYCCNTENA_Msk) == 0U)
    {
        DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    }

    start = DWT->CYCCNT;
    ticks = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < ticks)
    {
    }
}

void ADS8866_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    /* ---- CONVST: PA4 推挽输出, 空闲低 ---- */
    __HAL_RCC_GPIOA_CLK_ENABLE();

    gpio.Pin   = CONVST_PIN;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(CONVST_PORT, &gpio);
    HAL_GPIO_WritePin(CONVST_PORT, CONVST_PIN, GPIO_PIN_RESET);

    /* ---- SPI1: PA5=SCK, PA6=MISO, PA7=MOSI (AF5) ---- */
    __HAL_RCC_SPI1_CLK_ENABLE();

    gpio.Pin       = GPIO_PIN_5 | GPIO_PIN_6 | GPIO_PIN_7;
    gpio.Mode      = GPIO_MODE_AF_PP;
    gpio.Pull      = GPIO_NOPULL;
    gpio.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = GPIO_AF5_SPI1;
    HAL_GPIO_Init(GPIOA, &gpio);

    /* 16 位帧 = 一次传输正好 16 个连续 SCLK。
     * 80MHz / 8 = 10MHz (<= SCLK 上限 16MHz);
     * CPOL=0 (空闲低) + CPHA=0 (第一边沿/上升沿采样),
     * 对应手册 "数据在下降沿移出、上升沿稳定"。
     * 注意 STM32L4 是经典 SPI (CR1/CR2/SR/DR), 没有 CFG1/CFG2 */
    SPI1->CR1 = 0U;
    SPI1->CR2 = 0U;
    SPI1->CR1 = SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI
              | (0x2UL << SPI_CR1_BR_Pos);        /* fPCLK/8 = 10MHz */
    SPI1->CR2 = (0xFUL << SPI_CR2_DS_Pos);        /* 16 位数据帧 */
    SPI1->CR1 |= SPI_CR1_SPE;

    /* 首次传输: 置高 DIN (选 CS 模式) */
    (void)ADS8866_ReadRaw();
}

/* 诊断计数器 (SWD 读取用) */
volatile uint32_t ads_spi_txe_timeout;
volatile uint32_t ads_spi_rxne_timeout;
volatile uint32_t ads_spi_bsy_timeout;

/* 16 位全双工传输, DIN 恒高。
 * 守卫写法: while (cond && guard) guard--;  —— 不能用 guard-- > 0U 那种,
 * 它会在最后一次判断时把 guard 减到 0 后再减一次 -> 下溢成 0xFFFFFFFF,
 * 使 "guard == 0" 的超时判断永远不成立 */
static uint16_t SPI1_Xfer16(void)
{
    uint32_t guard;
    uint16_t rx;

    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_TXE) == 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_txe_timeout++;
        return 0U;
    }

    *(__IO uint16_t *)&SPI1->DR = 0xFFFFU;   /* DIN 保持高 */

    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_RXNE) == 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_rxne_timeout++;
        return 0U;
    }

    rx = *(__IO uint16_t *)&SPI1->DR;        /* 读 DR 清 RXNE */

    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_BSY) != 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_bsy_timeout++;               /* 仍返回已读到的数据 */
    }

    return rx;
}

uint16_t ADS8866_ReadRaw(void)
{
    uint16_t raw;

    /* 1) CONVST 上升沿: 采样输入并启动转换 (此刻 DIN 为高 -> CS 模式) */
    HAL_GPIO_WritePin(CONVST_PORT, CONVST_PIN, GPIO_PIN_SET);

    /* 2) 保持 CONVST 高至转换结束 (>= tconv-max);
     *    转换结束瞬间 CONVST 为高 -> 不产生 busy 指示 */
    DWT_DelayUs(T_CONV_US);

    /* 3) CONVST 下降沿: DOUT 立即输出 MSB */
    HAL_GPIO_WritePin(CONVST_PORT, CONVST_PIN, GPIO_PIN_RESET);

    /* 4) 16 个 SCLK 读出 16 位 */
    raw = SPI1_Xfer16();

    /* 5) tacq >= 1.2us 与 tcyc >= 10us 由 160us 采样周期天然满足 */

    return raw;
}

float ADS8866_ReadVoltage(float vref)
{
    return (float)ADS8866_ReadRaw() * vref / 65536.0f;
}
