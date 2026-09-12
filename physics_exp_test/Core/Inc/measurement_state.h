#ifndef MEASUREMENT_STATE_H
#define MEASUREMENT_STATE_H

#include "stm32l4xx_hal.h"

typedef enum
{
    MEASUREMENT_STATE_IDLE = 0,     /* 空闲: ADG 关断, 等待串口指令/按键 */
    MEASUREMENT_STATE_MODE1,        /* 连续滞回电荷平衡 (每秒一个结果) */
    MEASUREMENT_STATE_MODE2         /* 双斜率硬积分 (10s 内多循环取平均) */
} MeasurementState;

typedef enum
{
    MEASUREMENT_PROCESSING = 0,
    MEASUREMENT_RESULT_READY,       /* 有新结果 (模式一每秒 / 模式二最终) */
    MEASUREMENT_MODE2_STARTED       /* 刚自动切入模式二 (<1nA 触发) */
} MeasurementProcessResult;

void MeasurementState_Init(void);

/* 启动单次测量 (串口 'S'): 开 ADG + 模式一 1s 窗口;
 * >=1nA 直接出结果; <1nA 自动接模式二 10s; 测完自动回 IDLE */
void MeasurementState_StartCommand(void);

MeasurementProcessResult MeasurementState_Process(void);
MeasurementState MeasurementState_GetState(void);
float MeasurementState_GetResult(void);
float MeasurementState_GetResultExt(void);  /* 同一结果由 ADS8866 算出 */
uint8_t MeasurementState_ResultIsHard(void);    /* 最近一次结果是否来自双斜率 */
uint8_t MeasurementState_ResultIsTimeout(void);   /* 最近结果是否为模式二超时 (无有效循环, 数值无意义) */

#endif
