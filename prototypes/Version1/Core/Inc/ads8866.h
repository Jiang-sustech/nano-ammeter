#ifndef ADS8866_H
#define ADS8866_H

#include "stm32l4xx_hal.h"

/* REF 引脚电压 (V) —— 按板上 ADS8866 REF 引脚实际连接的基准电压修改!
 * 常见取值: 2.5 / 3.3 / 5.0
 * (工程里积分器零位 VO_ZERO=1.65V, 若 REF=3.3V 则零位正好是中点) */
#define ADS8866_VREF 3.3f

/* ============================================================
 * TI ADS8866 (16 位 100kSPS SAR ADC) 驱动
 * 数据手册: SBAS614 (www.ti.com/lit/ds/symlink/ads8866.pdf)
 *
 * 硬件接线 (用户 PCB):
 *   DIN    -> PA7  (SPI1_MOSI)
 *   SCLK   -> PA5  (SPI1_SCK)
 *   DOUT   -> PA6  (SPI1_MISO)
 *   CONVST -> PA4  (GPIO 推挽输出)
 *   CS     : 该芯片 VSSOP-10 封装无独立 CS 引脚,
 *            采用 3 线 CS 模式 (DIN 恒高, CONVST 充当 CS)
 *
 * 注意: 若以后在 CubeMX 里启用了 SPI1, 会生成 spi.c 中的 hspi1,
 *       请删除本文件里的 hspi1 定义并改用 spi.h 中的全局句柄。
 * ============================================================ */

/* 初始化 GPIO(CONVST) + SPI1, 需在 HAL_Init 之后调用 */
HAL_StatusTypeDef ADS8866_Init(void);

/* 读一次转换结果 (阻塞, 单次约 10us)
 * 返回 16 位原始码 0~65535, 对应 0~VREF
 */
uint16_t ADS8866_ReadRaw(void);

/* 读一次并换算为电压, vref 单位 V */
float ADS8866_ReadVoltage(float vref);

#endif /* ADS8866_H */
