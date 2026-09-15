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

/* ---- 强制窗口长度 (2026-09-15 加) ----
 * 默认 0 = 自动 (1s, <1nA 时换 10s)。设了之后**跳过自动切换**, 每次 S 都用它。
 *
 * 为什么要这个: 1~20 pA 那一段信噪比太差, 10s 窗口不够, 要 50s。
 * 而**按幅值自动切换做不到** —— 20 pA 和 50 pA 都 <1nA, 区分不开。
 * 由上位机按设定值指定最干净。
 *
 * ticks: 0 = 回到自动; 否则为窗口拍数 (1 拍 = 160us, 所以 50s = 312500)。
 * 抽点间隔 = ticks/6250, 因此 ticks 最好是 6250 的整数倍, 否则窗口末尾会截掉
 * 一点 (不致命, 但 T= 会与设定不符)。 */
void MeasurementState_SetForcedWindowTicks(uint32_t ticks);
uint32_t MeasurementState_GetForcedWindowTicks(void);

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
