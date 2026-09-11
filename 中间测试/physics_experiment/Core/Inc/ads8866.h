#ifndef ADS8866_H
#define ADS8866_H

#include "stdint.h"

/* ============================================================
 * ADS8866 外部 16 位 ADC (SPI1) — 与单片机内部 ADC (PA3) 并行采集
 *
 * 接线 (网表确认):
 *   PA4 -> ADS8866 CONVST (pin 6)
 *   PA5 -> ADS8866 SCLK   (pin 8)
 *   PA6 -> ADS8866 DIN    (pin 7)
 *   PA7 -> ADS8866 DOUT   (pin 9)
 *   V_OUT -> AINP (pin 4);  基准 REF (pin 1)
 *   PA3 -> V_OUT (与 ADS8866 同一节点, 内部 12 位 ADC 旁路对比)
 *
 * 时序 (手册 SBAS614): tconv 最大 8.8us; tcyc 最小 10us;
 * SCLK 最高 16MHz, 数据在 SCLK 下降沿移出、上升沿稳定。
 * ============================================================ */

void     ADS8866_Init(void);
uint16_t ADS8866_ReadRaw(void);              /* 16 位原始码 */
float    ADS8866_ReadVoltage(float vref);

#endif /* ADS8866_H */
