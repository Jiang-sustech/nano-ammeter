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

/* ---- 参考电流 (实测) ----
 * 测法: 短接积分节点, 2100 DCV @10PLC 量 100MΩ 上的压降。
 *
 *   V+ = 4.99703 V (±0.004%, 2100 DCV 量程)
 *   V- = -4.99938 V
 *   R  = 99.48 MΩ  (±1%, 2100 的 100MΩ 量程 —— 这是弱环节)
 *
 * 拆开看修正量的来源:
 *   V 那部分 4.99703/5.000 = -0.059%    <- 实打实
 *   R 那部分 100/99.48      = +0.523%   <- 带 ±1%, 基本淹没在表的不确定度里
 *
 * **注: 这两个值不影响最终精度。** 读数 = q·Δc/T - (m·I₊ + n·I₋)/N,
 * 其中 I₊/I₋ 的"共同尺度"误差是纯增益(进 a)、"不对称"是纯偏移(进 b),
 * 而 a/b 是对着 2636B 灌的已知输入电流拟合出来的 —— 两者都被吸收。
 * 此处写实测值是为了**记录**仪器的真实状态, 以及进报告的不确定度预算。 */
#define I_POS            50.232e-9f
#define I_NEG           (-50.255e-9f)

/* 模式一 滞回阈值 (积分器域, 在 ±4.5V 线性区内) */
#define THRESH_INT_UPPER    4.3f
#define THRESH_INT_LOWER   (-4.3f)

/* 模式二 双斜率参数 */
#define HARD_TARGET_INT_V    2.0f       /* 上积目标 (积分器域) */
#define HARD_TIMEOUT_MS      10000U     /* 模式二总预算 10s (多循环取平均, 约 6pA 下限) */
#define HARD_RESET_GUARD     2000U      /* 模式二复位相最大读取次数 */

/* Current_PullToZero 的守卫: 每次 Adc_ReadRaw() 约 8us (ADC 时钟 80MHz,
 * 采样 640.5 周期)。最坏行程 5.3V —— 从电平移位饱和轨 (±5.3V) 拉到 0V,
 * 净电流 50nA -> 500V/s -> 10.6ms -> 约 1300 次读。
 * 取 10000 (约 80ms) = 7.5 倍余量; 即使输入与参考相抵到净 12nA 也够。
 * 这是防挂死保险丝, 不是量程判据 —— 超时即按当前状态继续, 不阻断测量。 */
#define PULLZERO_GUARD       10000U

/* ---- 模式一: 滞回电荷平衡 (连续循环开窗, 每秒一个结果) ---- */
/* 启动: 重置窗口计数 + 开 ADG, 然后**阻塞**执行拉回相 —— 把积分器从
 * 任意初始状态(典型是空闲期被被测电流推到的压轨)拉到接近 0V, 再开窗。
 * 目标是 0V 而不是某个阈值: 拉到边界是赌电流符号, 赌错那侧余量为 0、
 * 第一拍就撞轨; 0V 则两个方向各留 4.3V, 且第一个相位是正常长度。
 * 最坏阻塞约 106ms(45nA 输入与参考相抵), PRECOND_GUARD 约 400ms。
 * 必须在 HAL_TIM_Base_Start_IT 之前调用: 拉回期间 TIM6 未跑, 无 ISR 竞争 */
void Current_Start(void);

/* 拉回相未能在守卫内到位次数, 正常恒为 0 (仅诊断, 不参与控制) */
extern volatile uint32_t precond_timeout;
void Current_Stop(void);               /* 停止: 停 TIM6 + 关 ADG (空闲不注入参考) */
void Current_Process(void);            /* TIM6 中断每 160us 调用一次 */
/* 式(6), 指定用哪一路缓冲。v_first = 该路的窗口起点码 (拉回相最后一拍),
 * 由 Current_GetFirstCode() / Current_GetFirstCodeExt() 给 */
float Calculate_Current_From(const uint16_t *buf, uint16_t v_first);
uint16_t Current_GetFirstCode(void);     /* 窗口起点 V_01 (内置路) */
uint16_t Current_GetFirstCodeExt(void);  /* 窗口起点 V_01 (外部路) */
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
uint32_t Current_GetExtBadRead(void);            /* 外部 ADC 坏读计数 (0x0000/0xFFFF) */
uint32_t Current_GetMCount(void);           /* 最近完整窗口 POS 周期数 (标定用) */
uint32_t Current_GetNCount(void);           /* 最近完整窗口 NEG 周期数 (标定用) */
uint16_t Current_GetMinCode(void);          /* 最近窗口最小码 = 下阈值切换点 (标定用) */
uint16_t Current_GetMaxCode(void);          /* 最近窗口最大码 = 上阈值切换点 (标定用) */
float Current_GetSwingVoltage(void);   /* 1s 窗口内积分器电压峰峰值 (V) */

/* ---- 小电流模式: 纯积分 + 斜率 (1 pA ~ 1 nA) ----
 * 模式二的双斜率在 1 pA 下需要 200 秒才积得起 2V, 覆盖不到 pA 量程;
 * 小电流直接积分、测首尾两点即可 (1pA 积 3s 才 30mV, 离轨很远)。 */
void Current_PullToZero(void);      /* 阻塞把积分器拉到零位 (必须在 TIM6 未跑时调用) */
void SmallI_Start(void);            /* 阻塞复位 + 稳定 + 取起点, 然后开积分 */
uint8_t SmallI_IsFinished(void);
uint8_t SmallI_IsShort(void);       /* 撞轨提前收尾 (结果有效, 但积分时间短了) */
float SmallI_GetCurrentInt(void);   /* 内置 ADC 算出的电流 */
float SmallI_GetCurrentExt(void);   /* ADS8866 算出的电流 (表征以它为准) */
uint32_t SmallI_GetActualTicks(void); /* 实际积分拍数 (撞轨收尾时小于设定值) */
void SmallI_SetSeconds(uint8_t sec);  /* 积分时长 (1~30 秒, 默认 3) */
uint8_t SmallI_GetSeconds(void);



#endif
