#ifndef CAL_MODE_H
#define CAL_MODE_H

#include <stdint.h>

/* ============================================================
 * 分模块标定模式 (编译期宏, 烧录专用标定固件)
 *
 *   CAL_NONE  正常测量 (默认, 日常固件)
 *   CAL_ZERO  零位检查: ADG 全断, 积分器应停在 0V -> V_OUT=1.55V
 *             采样 10s 后上报均值/标准差/漂移斜率 (兼作短时底噪评估)
 *   CAL_BANG  无输入 bang-bang: 复用模式一锯齿波 (需确保 RF1 无输入),
 *             每秒上报 m/n 计数、摆幅、波峰波谷码 (即切换阈值码)。
 *             配合万用表 min/max 实测波峰波谷电压, 得到电平移位
 *             传递函数的两点标定: 真实增益/偏移填入
 *             LEVELSHIFT_GAIN / LEVELSHIFT_V_ADC_ZERO 宏
 *
 * 说明: 固定电流斜坡不做 (50nA/30pF = 1.67V/ms, 3ms 即撞轨,
 *       无慢斜坡可用); 锯齿波波峰波谷天然停在切换阈值, 标定效果更好
 * ============================================================ */
#define CAL_NONE  0
#define CAL_ZERO  1
#define CAL_BANG  2

#define CAL_MODE  CAL_NONE

/* CAL_ZERO 单轮采样时长 (秒) */
/* 单轮时长 (秒)。**不要设得太长**: 摆幅 8.6V / C=100pF -> 可积 860pC,
 * 按 5pA 算 172 秒就撞轨, 单轮超过它后段全平躺、漂移恒为 0。
 * 1000 秒的系统表征靠**多轮重复**得到 (标准差要的是轮间离散度)。 */
#define CAL_ZERO_SECONDS 10U
#define CAL_ZERO_ROUNDS  100U   /* 100 轮 x 10 秒 = 1000 秒 */

/* 标定主流程 (CAL_MODE != CAL_NONE 时由 main.c 调用) */
void CalMode_Start(void);        /* 配置 ADG/TIM6, 启动标定采样 */
void CalMode_Task(void);         /* 主循环每轮调用: 完成检测 + 上报 (自动循环) */

/* TIM6 节拍 (由 HAL_TIM_PeriodElapsedCallback 路由, 见 current.c) */
void CalMode_Tick(void);

#endif /* CAL_MODE_H */
