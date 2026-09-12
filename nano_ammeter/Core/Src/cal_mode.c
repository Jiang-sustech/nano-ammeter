#include "cal_mode.h"
#include "main.h"
#include "adg4530.h"
#include "adc.h"        /* Adc_ReadRaw: 零位检查测的是控制通路 (内置 PA3) */
#include "current.h"
#include "tim.h"
#include "uart.h"
#include <math.h>
#include <stdio.h>

#if CAL_MODE != CAL_NONE

#if CAL_MODE == CAL_ZERO

/* ---- CAL_ZERO: 在线最小二乘拟合量 (无缓冲, 中断内累加) ---- */
static volatile uint64_t sum_t, sum_v, sum_tt, sum_tv, sum_vv;
static volatile uint32_t tick_count;
static volatile uint8_t cal_done;
static double mean_code, std_code, slope_code_per_s;

static void CalZero_Compute(void)
{
    double n = (double)tick_count;
    double s_t = (double)sum_t, s_v = (double)sum_v;
    double s_tt = (double)sum_tt, s_tv = (double)sum_tv, s_vv = (double)sum_vv;
    double var, denom;

    mean_code = s_v / n;
    var = s_vv / n - mean_code * mean_code;
    std_code = (var > 0.0) ? sqrt(var) : 0.0;
    denom = n * s_tt - s_t * s_t;
    /* 每 tick 斜率 × 6250 tick/s = 码/s */
    slope_code_per_s = (denom > 1e-9)
                       ? (n * s_tv - s_t * s_v) / denom * 6250.0 : 0.0;
}

static void CalZero_Report(void)
{
    char buf[128];
    char v_adc[16], drift_uv[16], ib_fa[16];
    double v_adc_mean = mean_code * (3.3 / 65536.0);
    /* 码/s -> V_raw 域 V/s (反相映射): dV_raw/dt = -(dV_adc/dt)/GAIN */
    double dv_raw_dt = -slope_code_per_s * (3.3 / 65536.0) / (double)LEVELSHIFT_GAIN;
    double ibias = (double)C_INT * dv_raw_dt;   /* A */

    UART_FormatScaled((int64_t)(v_adc_mean * 1e4), 4, v_adc);
    UART_FormatScaled((int64_t)(dv_raw_dt * 1e6), 2, drift_uv);  /* µV/s */
    UART_FormatScaled((int64_t)(ibias * 1e15), 1, ib_fa);        /* fA */

    sprintf(buf, "CAL ZERO: N=%lu MEAN=%lu Vadc=%sV STD=%lu DRIFT=%suV/s IB=%sfA\r\n",
            (unsigned long)tick_count, (unsigned long)(mean_code + 0.5), v_adc,
            (unsigned long)(std_code + 0.5), drift_uv, ib_fa);
    UART_SendString(buf);
}

#endif /* CAL_ZERO */

#if CAL_MODE == CAL_BANG

static void CalBang_Report(void)
{
    char buf[128];
    char vpp[16], i_na[16];
    float swing = Current_GetSwingVoltage();
    float current = Current_GetWindowResult();

    UART_FormatScaled((int64_t)((double)swing * 100.0), 2, vpp);
    UART_FormatScaled((int64_t)((double)current * 1e12), 3, i_na);

    sprintf(buf, "CAL BANG: m=%lu n=%lu VPP=%sV MIN=%u MAX=%u I=%snA\r\n",
            (unsigned long)Current_GetMCount(), (unsigned long)Current_GetNCount(),
            vpp, (unsigned)Current_GetMinCode(), (unsigned)Current_GetMaxCode(), i_na);
    UART_SendString(buf);
}

#endif /* CAL_BANG */

void CalMode_Start(void)
{
#if CAL_MODE == CAL_ZERO
    sum_t = sum_v = sum_tt = sum_tv = sum_vv = 0U;
    tick_count = 0U;
    cal_done = 0U;
    ADG_Disable();                  /* 全断: 零位检查 */
    HAL_TIM_Base_Start_IT(&htim6);
#elif CAL_MODE == CAL_BANG
    Current_Start();                /* 模式一连续窗口 (不经单次状态机) */
    HAL_TIM_Base_Start_IT(&htim6);
#endif
}

/* TIM6 每 160us 调用 (HAL_TIM_PeriodElapsedCallback 路由, 见 current.c) */
void CalMode_Tick(void)
{
#if CAL_MODE == CAL_ZERO
    uint16_t raw = Adc_ReadRaw();       /* 控制通路; 零位检查不加观测扰动 */
    uint64_t t = tick_count;

    sum_t  += t;
    sum_v  += raw;
    sum_tt += t * t;
    sum_tv += t * (uint64_t)raw;
    sum_vv += (uint64_t)raw * raw;
    tick_count++;

    if (tick_count >= (uint32_t)CAL_ZERO_SECONDS * 6250U)
    {
        HAL_TIM_Base_Stop_IT(&htim6);
        ADG_Disable();
        CalZero_Compute();
        cal_done = 1U;
    }
#endif
    /* CAL_BANG: 采样由 Current_Process 完成 */
}

/* 主循环每轮调用: 完成检测 + 上报, 自动进入下一轮 */
void CalMode_Task(void)
{
#if CAL_MODE == CAL_ZERO
    if (cal_done)
    {
        CalZero_Report();
        CalMode_Start();
    }
#elif CAL_MODE == CAL_BANG
    if (Current_WindowFinished())
    {
        Current_ClearWindowFlag();
        CalBang_Report();
    }
#endif
}

#endif /* CAL_MODE != CAL_NONE */
