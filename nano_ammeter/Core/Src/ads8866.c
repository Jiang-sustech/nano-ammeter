#include "ads8866.h"
#include "main.h"

/* ============================================================
 * ADS8866 位操作驱动 (SPI1, 纯寄存器级)
 *
 * 为什么不用 HAL_SPI_TransmitReceive / HAL_SPI_Receive: 它们内部靠
 * HAL_GetTick 判超时, 而本工程所有调用点都在 TIM6 中断里。TIM6 中断
 * 优先级 0 高于 SysTick 的 15 (tim.c:75 / stm32l4xx_hal_conf.h:185),
 * 中断执行期间 SysTick 无法抢占 -> HAL_GetTick 停走 ->
 * 一旦 SPI 有任何异常, HAL 的超时判断永远不成立, 直接变成死循环
 * (本工程在 ADC 上踩过同样的坑)。这里全部用循环计数守卫, 绝不依赖 tick。
 *
 * 模式选择: CONVST 上升沿时 DIN 必须为高 -> 选中 3 线 CS 模式
 * (DIN 为低会进菊花链模式)。因此每次传输都发 0xFFFF 维持 DIN 高。
 *
 * 手册时序依据 (SBAS614):
 *   tconv  : 500ns ~ 8.8us (内部时钟, 容差较大)
 *   tacq   : >= 1.2us (满量程阶跃建立到 16 位)
 *   tcyc   : >= 10us (两次转换最小间隔, 100kSPS)
 *   SCLK   : 最高 16MHz, 空闲低; 数据在下降沿移出、上升沿稳定
 *   CONVST : 上升沿采样并启动转换; 转换结束瞬间 CONVST 为高 ->
 *            不产生 busy 指示; 下降沿后 DOUT 立即输出 MSB
 * ============================================================ */

#define CONVST_PORT     GPIOA
#define CONVST_PIN      GPIO_PIN_4

#define T_CONV_US       9U          /* >= tconv-max 8.8us, 留余量 */
#define SPI_GUARD       100000U

/* DWT 微秒延时 (80MHz 主频)。
 * 这里同样不能用 HAL_Delay: 见文件头的 tick 饿死说明。
 * CYCCNT 是自由运行的周期计数器, 与中断无关, 中断里也准 */
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

/* 诊断计数器 (SWD 按符号读取, 不参与控制流) */
volatile uint32_t ads_spi_txe_timeout;
volatile uint32_t ads_spi_rxne_timeout;
volatile uint32_t ads_spi_bsy_timeout;

/* ------------------------------------------------------------
 * 16 位全双工传输, DIN 恒高 (3 线 CS 模式的前提)
 *
 * 守卫写法必须是 while ((cond) && (guard != 0U)) { guard--; }
 * —— 不能写成 while ((cond) && (guard-- > 0U)): 那种写法在最后一次
 * 判断里已经把 guard 减到 0 之后还会再减一次 -> 下溢成 0xFFFFFFFF,
 * 于是 "guard == 0" 的超时判断永远不成立 (超时形同虚设), 循环要再
 * 转 40 多亿次才退出。所以先判 guard != 0 再减, 判断和递减分开。
 * ------------------------------------------------------------ */
static uint16_t SPI1_Xfer16(void)
{
    uint32_t guard;
    uint16_t rx;

    /* 等发送缓冲空: 上次残留数据未发完就写 DR 会丢帧 */
    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_TXE) == 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_txe_timeout++;
        return 0U;                  /* 绝不卡死, 返回 0 让调用方看得见 */
    }

    *(__IO uint16_t *)&SPI1->DR = 0xFFFFU;   /* DIN 保持高 */

    /* 等接收缓冲非空: 16 个 SCLK 走完才有完整一帧 */
    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_RXNE) == 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_rxne_timeout++;
        return 0U;
    }

    rx = *(__IO uint16_t *)&SPI1->DR;        /* 读 DR 清 RXNE */

    /* 等 BSY 落: 保证下一次传输前时钟已停 */
    guard = SPI_GUARD;
    while (((SPI1->SR & SPI_SR_BSY) != 0U) && (guard != 0U)) { guard--; }
    if (guard == 0U)
    {
        ads_spi_bsy_timeout++;               /* 仍返回已读到的数据 */
    }

    return rx;
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

    /* 自包含: 直接写寄存器, 不走 HAL_SPI_Init —— HAL_SPI_Init 依赖
     * spi.c 里的 HAL_SPI_MspInit (该文件只处理 SPI2, 对 SPI1 是 no-op),
     * 且 HAL 句柄的状态机对本驱动毫无用处。
     *
     * 16 位帧 = 一次传输正好 16 个连续 SCLK。
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

    /* 首次传输: 把 DIN (MOSI) 置高并保持。
     * 必须在第一次 CONVST 上升沿之前完成 —— AF 推挽输出在没写过 DR
     * 之前 DIN 是低电平, 若此时就发 CONVST, 芯片会进菊花链模式。
     * 这里只走底层传输而不调 ADS8866_ReadRaw(): 此刻还没有有效转换
     * (CONVST 从未拉高), 读回来必然是 0x0000/0xFFFF 之类的无效码,
     * 调 ReadRaw 会平白让调用方记上一笔假故障 */
    (void)SPI1_Xfer16();
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

    /* 5) 原样返回读到的码 —— 调用方需要看到真实原始值来判断故障类型,
     *    替它改成漂亮值反而藏起了问题。
     *
     *    **这里不判坏读。** 0xFFFF 既是"MISO 断线 / DOUT 常高"的特征,
     *    也是合法的满量程码; 只看这一路分不开, 输入撞轨时会把 100% 的有效
     *    读数记成坏读 (实测 ERR 虚涨到 312537)。判据上移到 current.c 的
     *    ReadBoth() —— 那里同时看得到内置那一路, 才能区分"真撞轨"和"真断线" */

    /* 6) tacq >= 1.2us 与 tcyc >= 10us 由调用方的采样节奏天然满足 */

    return raw;
}

float ADS8866_ReadVoltage(float vref)
{
    return (float)ADS8866_ReadRaw() * vref / 65536.0f;
}
