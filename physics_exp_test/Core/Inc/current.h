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

/* 硬件常量 (计算与标定模块共用) */
/* 积分电容: 100pF 为标称值 (用户按丝印确认; 早期 30pF 是错的假设) */
#define C_INT            100e-12f   /* 积分电容 (F) */
#define I_POS            50e-9f     /* 正向参考电流 (A), ±5V/100MΩ 标称 */
#define I_NEG           (-50e-9f)   /* 负向参考电流 (A) */

/* 模式一 滞回阈值 (积分器域, 在 ±4.5V 线性区内) */
#define THRESH_INT_UPPER    4.3f
#define THRESH_INT_LOWER   (-4.3f)

/* 模式二 双斜率参数 */
#define HARD_TARGET_INT_V    2.0f       /* 上积目标 (积分器域) */
#define HARD_TIMEOUT_MS      10000U     /* 模式二总预算 10s (多循环取平均, 约 6pA 下限) */
#define HARD_RESET_GUARD     2000U      /* 复位相最大读取次数 */

/* ---- 模式一: 滞回电荷平衡 (连续循环开窗, 每秒一个结果) ---- */
/* 启动: 重置窗口计数 + 开 ADG, 然后**阻塞**执行拉回相 —— 把积分器从
 * 任意初始状态(典型是空闲期被被测电流推到的压轨)拉到上阈值 V_o=-4.3V,
 * 再以 SEL_NEG 开窗, 保证窗口首个样本就是有效读数(Vo1 已知)且全程
 * 不超过两阈值。最坏阻塞约 40ms, 有 PRECOND_GUARD 防挂死。
 * 必须在 HAL_TIM_Base_Start_IT 之前调用: 拉回期间 TIM6 未跑, 无 ISR 竞争 */
void Current_Start(void);

/* 拉回相未能在守卫内到位次数, 正常恒为 0 (仅诊断, 不参与控制) */
extern volatile uint32_t precond_timeout;
void Current_Stop(void);               /* 停止: 停 TIM6 + 关 ADG (空闲不注入参考) */
void Current_Process(void);            /* TIM6 中断每 160us 调用一次 */
float Calculate_Current(void);         /* 式(6), 用内置 ADC 缓冲 (控制路径) */
float Calculate_Current_From(const uint16_t *buf);  /* 同式(6), 指定用哪一路缓冲 */
float Current_GetWindowResultExt(void); /* 同一窗口由 ADS8866 算出的结果 */
uint8_t Current_WindowFinished(void);  /* 窗口完成脉冲 (每秒一次, 消费式) */
void Current_ClearWindowFlag(void);
float Current_GetWindowResult(void);   /* 上一窗口电流结果 (中断内算好, 无竞态) */
uint16_t Current_GetSample(uint32_t index); /* 调试: 读取存储的原始码 */
uint32_t Current_GetSampleCount(void);      /* 当前窗口已采样数 (B 指令回传用) */

/* ---- ADS8866 外部 16 位并行观测 (只读, 不参与控制) ----
 * 与 voltage_buf 同拍采集 (外部读取在内置 ADC 之后约 11us, 存在固定相位偏斜),
 * 数值域相同 (0~65535), 可直接与 Current_GetSample() 逐点对照。 */
uint16_t Current_GetExtSample(uint32_t index);   /* 外部 16 位原始码 */
uint32_t Current_GetExtSampleCount(void);        /* 与 GetSampleCount 同口径 */
uint16_t Current_GetExtTrace(uint32_t index);    /* 模式二逐 tick 波形 (内置 ADC) */
uint16_t Current_GetExtTraceExt(uint32_t index); /* 模式二逐 tick 波形 (外部 ADC) */
uint32_t Current_GetTraceCount(void);            /* 模式二波形已记录点数 (上限 2048) */
uint32_t Current_GetExtBadRead(void);            /* 外部 ADC 坏读计数 (0x0000/0xFFFF) */
uint32_t Current_GetMCount(void);           /* 最近完整窗口 POS 周期数 (标定用) */
uint32_t Current_GetNCount(void);           /* 最近完整窗口 NEG 周期数 (标定用) */
uint16_t Current_GetMinCode(void);          /* 最近窗口最小码 = 下阈值切换点 (标定用) */
uint16_t Current_GetMaxCode(void);          /* 最近窗口最大码 = 上阈值切换点 (标定用) */
float Current_GetSwingVoltage(void);   /* 1s 窗口内积分器电压峰峰值 (V) */

/* ---- 小电流模式: 纯积分 + 斜率 (1 pA ~ 1 nA) ----
 * 模式二的双斜率在 1 pA 下需要 200 秒才积得起 2V, 覆盖不到 pA 量程;
 * 小电流直接积分、测首尾两点即可 (1pA 积 3s 才 30mV, 离轨很远)。 */
void SmallI_Start(void);            /* 阻塞复位 + 稳定 + 取起点, 然后开积分 */
uint8_t SmallI_IsFinished(void);
uint8_t SmallI_IsShort(void);       /* 撞轨提前收尾 (结果有效, 但积分时间短了) */
float SmallI_GetCurrentInt(void);   /* 内置 ADC 算出的电流 */
float SmallI_GetCurrentExt(void);   /* ADS8866 算出的电流 (表征以它为准) */
uint32_t SmallI_GetActualTicks(void); /* 实际积分拍数 (撞轨收尾时小于设定值) */
void SmallI_SetSeconds(uint8_t sec);  /* 积分时长 (1~30 秒, 默认 3) */
uint8_t SmallI_GetSeconds(void);

/* ---- 模式二: 双斜率硬积分 (10s 内多循环取平均) ---- */
void HardIntegral_Start(void);          /* 阻塞复位积分器 + 启动第一个上积循环 */
uint8_t HardIntegral_IsFinished(void);  /* 完成(预算用尽, 至少一个有效循环)或超时 */
uint8_t HardIntegral_IsTimeout(void);   /* 超时 (一个有效循环都没有, 约 <6pA) */
float HardIntegral_GetCurrent(void);    /* 多循环平均电流 (含电荷注入补偿) */


#endif
