#ifndef CURRENT_H
#define CURRENT_H

#include "stdint.h"

/* 电平移位映射 (与正式版一致):
 * V_adc = 0.33 * V_int + 1.55 */
#define LEVELSHIFT_GAIN        0.33f
#define LEVELSHIFT_V_ADC_ZERO  1.55f

/* 滞回阈值 (积分器域) — 安全阈值, 最高优先级 */
#define THRESH_INT_UPPER    4.3f
#define THRESH_INT_LOWER   (-4.3f)

/* 采样参数 */
#define ADS8866_VREF          3.3f     /* ADC 满量程 (PA3 用 VDDA=3.3V) */
#define TOTAL_CYCLE           6250U    /* 1 秒窗口采样数 (TIM6 160us) */

/* ADS8866 并行采集开关: 1 = 每个采样点同时读外部 16 位 ADC,
 * 存入第二条缓冲 voltage_buf_ext (两条路径同拍, 可直接对比) */
#define USE_ADS8866_PARALLEL  1

void Current_Start(void);              /* 开 ADG + 启动 bang-bang */
void Current_Process(void);            /* TIM6 中断每 160us 调用 */
uint32_t Current_GetSampleIndex(void);
uint16_t Current_GetSample(uint32_t index);
uint32_t Current_GetM(void);           /* POS 周期数 (调试) */
uint32_t Current_GetN(void);           /* NEG 周期数 (调试) */

#if USE_ADS8866_PARALLEL
uint16_t Current_GetExtSample(uint32_t index);   /* ADS8866 同拍原始码 */
uint32_t Current_GetExtErrorCount(void);         /* ADS8866 读回全 0/全 1 的次数 */
#endif

#endif
