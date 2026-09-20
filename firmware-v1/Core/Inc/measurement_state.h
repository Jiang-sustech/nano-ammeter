#ifndef MEASUREMENT_STATE_H
#define MEASUREMENT_STATE_H

#include "stm32l4xx_hal.h"

/* 双斜率硬积分已于 2026-09-13 废弃 (理由见 current.h 顶部)。
 * 原来的 MEASUREMENT_STATE_MODE2 / MEASUREMENT_MODE2_STARTED 两个名字
 * 实际承载的一直是**小电流模式**, 只是沿用了旧名 —— 现在改成实名。
 * 状态机结构未变, 只是不再有"模式二"这个说法。 */
typedef enum
{
    MEASUREMENT_STATE_IDLE = 0,     /* 空闲: ADG 关断, 等待串口指令/按键 */
    MEASUREMENT_STATE_MODE1,        /* 连续滞回电荷平衡 (每秒一个结果) */
    MEASUREMENT_STATE_SMALLI        /* 小电流模式: 纯积分 + 首尾斜率 (1pA~1nA) */
} MeasurementState;

typedef enum
{
    MEASUREMENT_PROCESSING = 0,
    MEASUREMENT_RESULT_READY,       /* 有新结果 (模式一每秒 / 小电流模式最终) */
    MEASUREMENT_SMALLI_STARTED      /* 刚自动切入小电流模式 (<1nA 触发) */
} MeasurementProcessResult;

void MeasurementState_Init(void);

/* 启动单次测量 (串口 'S'): 开 ADG + 模式一 1s 窗口;
 * >=1nA 直接出结果; <1nA 自动接小电流模式; 测完自动回 IDLE */
void MeasurementState_StartCommand(void);

MeasurementProcessResult MeasurementState_Process(void);
MeasurementState MeasurementState_GetState(void);
float MeasurementState_GetResult(void);
float MeasurementState_GetResultExt(void);  /* 同一结果由 ADS8866 算出 */
uint32_t MeasurementState_GetTicks(void);   /* 小电流模式实际积分拍数 (报告要的 T) */
uint8_t MeasurementState_ResultIsSmallI(void);  /* 最近一次结果是否来自小电流模式 */
uint8_t MeasurementState_ResultIsTimeout(void); /* 最近结果是否因超时无有效积分 (数值无意义) */

#endif
