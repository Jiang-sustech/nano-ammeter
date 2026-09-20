#ifndef BUTTON_H
#define BUTTON_H

#include "stm32l4xx_hal.h"

/* ============================================================
 * 按键驱动 (PB4 = SW2, 外部 10k 上拉到 3.3V, 按键接地)
 *  未按下 = HIGH, 按下 = LOW
 *
 * 短按: 启动测量 (空闲时) / 刷新显示 (测量中)
 * 长按(>3s): 底噪/偏置电流测量
 *
 * 状态机 (与测量程序配合, 全程非阻塞):
 *
 *   IDLE ──短按松开──> MEASURING (发启动事件)
 *   IDLE ──长按 3s──> MEASURING (发长按事件)
 *   IDLE <──有效松开── WAIT_RELEASE (防长按松开时误触发下一次)
 *
 * 主循环轮询实现, 不占用中断; 软件消抖 30ms (HAL_GetTick)
 * ============================================================ */

typedef enum
{
    BUTTON_STATE_IDLE = 0,      /* 等待按下 */
    BUTTON_STATE_MEASURING,     /* 测量中, 忽略按键 */
    BUTTON_STATE_WAIT_RELEASE   /* 测量结束, 等待松开 */
} ButtonState;

void Button_Init(void);

/* 主循环每轮调用: 消抖 + 状态机推进 */
void Button_Task(void);

/* 消费式事件: IDLE 中检测到有效短按松开时返回 1 (调用一次消耗一次) */
uint8_t Button_StartRequested(void);

/* 消费式事件: 长按(>3s)触发底噪/偏置电流测量模式, 返回 1 */
uint8_t Button_LongPressRequested(void);

/* 测量程序结束时调用: MEASURING -> WAIT_RELEASE */
void Button_MeasurementDone(void);

ButtonState Button_GetState(void);

#endif /* BUTTON_H */
