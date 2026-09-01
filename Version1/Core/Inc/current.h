#ifndef CURRENT_H
#define CURRENT_H

#include "stdint.h"

/* ============================================================
 * 电平移位电路映射 (反相求和器, 论文确认):
 *   V_o=+4.5V -> V_ad=0.065V; V_o=-4.5V -> V_ad=3.035V
 *   V_adc = LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * V_int
 *   V_int = (LEVELSHIFT_V_ADC_ZERO - V_adc) / LEVELSHIFT_GAIN
 * 若电阻值改动, 只需改这两个宏
 * ============================================================ */
#define LEVELSHIFT_GAIN        0.33f
#define LEVELSHIFT_V_ADC_ZERO  1.55f    /* 积分器 0V 对应 ADC 电压 */

/* 模式一 滞回阈值 (积分器域, 在 ±4.5V 线性区内) */
#define THRESH_INT_UPPER    4.3f
#define THRESH_INT_LOWER   (-4.3f)

/* 模式二 双斜率参数 */
#define HARD_TARGET_INT_V    2.0f       /* 上积目标 (积分器域) */
#define HARD_TIMEOUT_MS      10000U     /* 模式二总预算 10s (多循环取平均, 约 6pA 下限) */
#define HARD_RESET_GUARD     2000U      /* 复位相最大读取次数 */

/* ---- 模式一: 滞回电荷平衡 (连续循环开窗, 每秒一个结果) ---- */
void Current_Start(void);              /* 启动: 开 ADG + 选正向, 重置窗口计数 */
void Current_Stop(void);               /* 停止: 停 TIM6 + 关 ADG (空闲不注入参考) */
void Current_Process(void);            /* TIM6 中断每 160us 调用一次 */
float Calculate_Current(void);         /* 式(6), 窗口结束时在中断内调用 */
uint8_t Current_WindowFinished(void);  /* 窗口完成脉冲 (每秒一次, 消费式) */
void Current_ClearWindowFlag(void);
float Current_GetWindowResult(void);   /* 上一窗口电流结果 (中断内算好, 无竞态) */
uint8_t Current_Is_Finished(void);     /* 兼容旧调用: 同 Current_WindowFinished */
uint16_t Current_GetSample(uint32_t index); /* 调试: 读取存储的原始码 */
float Current_GetSwingVoltage(void);   /* 1s 窗口内积分器电压峰峰值 (V) */
float Current_GetNoiseVoltage(void);   /* 去趋势后每采样噪声 RMS (V) */

/* ---- 模式二: 双斜率硬积分 (10s 内多循环取平均) ---- */
void HardIntegral_Start(void);          /* 阻塞复位积分器 + 启动第一个上积循环 */
uint8_t HardIntegral_IsFinished(void);  /* 完成(预算用尽, 至少一个有效循环)或超时 */
uint8_t HardIntegral_IsTimeout(void);   /* 超时 (一个有效循环都没有, 约 <6pA) */
float HardIntegral_GetCurrent(void);    /* 多循环平均电流 (含电荷注入补偿) */

/* ---- 底噪/偏置电流测量 (长按按键触发) ----
 * ADG 全断(无输入无参考), 纯积分 NOISE_MEASURE_SECONDS 秒,
 * 每秒存 1 点, 最小二乘拟合漂移斜率, I_bias = C * dV/dt */
#define NOISE_MEASURE_SECONDS 50U       /* 积分时长 (秒), 临时底噪测试用 (正式标定 1000s) */
void Noise_Start(void);
uint8_t Noise_IsFinished(void);
float Noise_GetBiasCurrent(void);       /* 拟合结果 (A) */
uint32_t Noise_GetElapsedSec(void);     /* 进度 (秒) */
uint16_t Noise_GetSample(uint32_t index); /* 调试: 读取 1s 采样原始码 */

#endif
