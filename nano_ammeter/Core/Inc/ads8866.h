#ifndef ADS8866_H
#define ADS8866_H

#include "stm32l4xx_hal.h"

/* REF 引脚电压 (V) —— 按板上 ADS8866 REF 引脚实际连接的基准电压修改!
 * 常见取值: 2.5 / 3.3 / 5.0
 * (工程里积分器零位 VO_ZERO=1.65V, 若 REF=3.3V 则零位正好是中点) */
#define ADS8866_VREF 3.3f

/* ============================================================
 * TI ADS8866 (16 位 100kSPS SAR ADC) 驱动 —— SPI1, 纯寄存器级
 * 数据手册: SBAS614 (www.ti.com/lit/ds/symlink/ads8866.pdf)
 *
 * 硬件接线 (网表确认; MCU 侧引脚功能由 SPI1 复用关系决定):
 *   PA4 -> ADS8866 CONVST (pin 6)   GPIO 推挽输出, 空闲低
 *   PA5 -> ADS8866 SCLK   (pin 8)   SPI1_SCK
 *   PA6 -> ADS8866 DOUT   (pin 9)   SPI1_MISO  <- 主机输入
 *   PA7 -> ADS8866 DIN    (pin 7)   SPI1_MOSI  -> 主机输出
 *   V_OUT -> AINP (pin 4);  基准 REF (pin 1)
 *   PA3 -> V_OUT (与 ADS8866 同一节点, 内部 12 位 ADC 旁路对比,
 *          该路径已迁至 adc.c, 本驱动不再涉及)
 *
 * CS: VSSOP-10 封装无独立 CS 引脚, 采用 3 线 CS 模式
 *     (DIN 恒高, CONVST 充当 CS)
 *
 * 中断安全: 全部等待都是循环计数守卫, 不依赖 HAL_GetTick —— 调用点
 * 在 TIM6 中断里, 且 TIM6 与 SysTick 同为优先级 0, tick 会被饿死,
 * 任何基于 tick 的超时都会退化成死循环。
 *
 * 注意 (接口变更): ADS8866_Init() 的返回类型从 HAL_StatusTypeDef
 * 改为 void —— 初始化不再可能失败 (不再有 HAL_SPI_Init / 超时返回值),
 * 调用方若还在用返回值 (如 `if (ADS8866_Init() != HAL_OK)`) 需自行调整。
 *
 * 注意 (代码生成冲突): 本文件不再自带 SPI_HandleTypeDef, SPI1 由本驱动
 * 直接配寄存器。若以后在 CubeMX 里启用 SPI1, 会生成 spi.c 里的 hspi1,
 * 两套配置会互相覆盖, 需二选一。
 * ============================================================ */

/* 初始化 GPIO(CONVST) + SPI1 寄存器, 自包含; 需在 HAL_Init 之后调用 */
void     ADS8866_Init(void);

/* 读一次转换结果 (阻塞, 单次约 10us)
 * 返回 16 位原始码 0~65535, 对应 0~VREF。
 * 读到 0x0000 / 0xFFFF 时 ads_bad_read 自增, 但函数仍返回真实码 */
uint16_t ADS8866_ReadRaw(void);

/* 读一次并换算为电压, vref 单位 V */
float    ADS8866_ReadVoltage(float vref);

/* ---- 诊断计数器: 声明为全局符号, 便于 SWD 按名字直接读 ---- */
extern volatile uint32_t ads_spi_txe_timeout;   /* 等 TXE 超时次数 */
extern volatile uint32_t ads_spi_rxne_timeout;  /* 等 RXNE 超时次数 */
extern volatile uint32_t ads_spi_bsy_timeout;   /* 等 BSY 落超时次数 */
extern volatile uint32_t ads_bad_read;          /* 坏码 (0x0000/0xFFFF) 次数 */

#endif /* ADS8866_H */
