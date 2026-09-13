#ifndef MEASUREMENT_STATE_H
#define MEASUREMENT_STATE_H

#include "stm32l4xx_hal.h"

/* 2026-09-13 重构: **全量程一套测量逻辑** (电荷平衡), 小电流端只是把窗口
 * 从 1s 拉到 10s。所以状态只剩两个 —— 原来的 MEASUREMENT_STATE_SMALLI
 * (独立的小电流模式) 已随那套逻辑一起删除。 */
typedef enum
{
    MEASUREMENT_STATE_IDLE = 0,     /* 空闲: ADG 关断, 等待串口指令/按键 */
    MEASUREMENT_STATE_MODE1         /* 测量窗口 (长度 1s 或 10s, 由电流大小定) */
} MeasurementState;

typedef enum
{
    MEASUREMENT_PROCESSING = 0,
    MEASUREMENT_RESULT_READY,       /* 有新结果 */
    MEASUREMENT_LONGW_STARTED       /* 刚发现 <1nA, 换成 10s 窗口重测 */
} MeasurementProcessResult;

void MeasurementState_Init(void);

/* 启动单次测量 (串口 'S'): 开 ADG + 1s 窗口;
 * >=1nA 直接出结果; <1nA 自动换 10s 窗口重测一次; 测完自动回 IDLE */
void MeasurementState_StartCommand(void);

MeasurementProcessResult MeasurementState_Process(void);
MeasurementState MeasurementState_GetState(void);
float MeasurementState_GetResult(void);
float MeasurementState_GetResultExt(void);  /* 同一结果由 ADS8866 算出 */
uint32_t MeasurementState_GetTicks(void);   /* 本次窗口的实际拍数 (结果行 T= 用它) */
uint8_t MeasurementState_ResultIsLongW(void);   /* 结果是否来自 10s 长窗口 */
uint8_t MeasurementState_ResultIsTimeout(void); /* 预留 (当前恒 0) */

#endif
