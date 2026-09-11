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

/* 启动测量 (串口 'S' / 按键短按): 开 ADG + 连续模式一 */
void MeasurementState_StartCommand(void);

/* 停止测量 (串口 'X'): 停 TIM6 + 关 ADG, 回空闲 */
void MeasurementState_StopCommand(void);

MeasurementProcessResult MeasurementState_Process(void);
MeasurementState MeasurementState_GetState(void);
float MeasurementState_GetResult(void);
uint8_t MeasurementState_ResultIsHard(void);    /* 最近一次结果是否来自双斜率 */

#endif
