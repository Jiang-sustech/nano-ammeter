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

/* ---- CAL_ZERO: 每轮复位 + 积分 10 秒, 重复 CAL_ZERO_ROUNDS 轮 ----
 *
 * **为什么是短轮次而不是一次长积分**: 摆幅 8.6V / C=100pF -> 可积 860pC,
 * 按 5pA 算 172 秒就撞轨。单轮 1000 秒的话后段全平躺在轨上, 漂移恒为 0。
 * 而本底噪声要的是**轮间离散度**, 本来就得靠多轮重复 (报告 4.3.1 的方法:
 * 零输入 -> 连续采样 -> 平均值 = 零点偏差 -> 标准差 = 本底噪声)。
 *
 * 累积量用 double, t 以秒为单位 —— 最小二乘的两项在拍单位下都接近 1e19,
 * 贴着 uint64 上限且相减精度不可接受。
 */
static volatile double   sum_t, sum_v, sum_tt, sum_tv, sum_vv;
static volatile uint32_t tick_count;
static volatile uint8_t  cal_done;
static volatile uint16_t zmin_code, zmax_code;   /* 压轨检测: 撞轨时斜率恒为 0 */

static double mean_code, std_code, slope_code_per_s;

/* 轮间统计 (跨轮累加, 不清零) */
static uint32_t round_count;
static uint32_t last_progress_sec;
static volatile uint8_t progress_due;    /* ISR 置, 主循环清 —— 见 CalMode_Tick 的注释 */
static double   sum_means, sumsq_means;
static double   cum_zero_code, cum_noise_code;

#define CAL_ZERO_TICKS   (CAL_ZERO_SECONDS * 6250U)

static void CalZero_Compute(void)
{
    double n = (double)tick_count;
    double denom;

    mean_code = sum_v / n;
    std_code = sum_vv / n - mean_code * mean_code;
    std_code = (std_code > 0.0) ? sqrt(std_code) : 0.0;

    denom = n * sum_tt - sum_t * sum_t;
    /* t 已经是秒, 斜率直接就是 码/s */
    slope_code_per_s = (denom > 1e-9) ? (n * sum_tv - sum_t * sum_v) / denom : 0.0;
}

/* 一轮结束时把该轮均值并进轮间统计, 算出零点偏差与本底噪声 */
static void CalZero_Cumulative(void)
{
    double n, var;

    round_count++;
    sum_means   += mean_code;
    sumsq_means += mean_code * mean_code;

    n = (double)round_count;
    cum_zero_code = sum_means / n;
    var = sumsq_means / n - cum_zero_code * cum_zero_code;
    cum_noise_code = (var > 0.0) ? sqrt(var) : 0.0;
}

/* 进度行: 每 10 秒一行, 让 100 秒的单轮看得见在跑 */
static void CalZero_Progress(void)
{
    char buf[128];
    char v_adc[16];
    uint8_t railed = (zmax_code >= 0xFF00U || zmin_code <= 0x00FFU) ? 1U : 0U;

    UART_FormatScaled((int64_t)(mean_code * (3.3 / 65536.0) * 1e4), 4, v_adc);
    sprintf(buf, "CAL ZERO t=%lu/%us: MEAN=%lu Vadc=%sV CODE=%u..%u%s\r\n",
            (unsigned long)(tick_count / 6250U), (unsigned)CAL_ZERO_SECONDS,
            (unsigned long)(mean_code + 0.5), v_adc,
            (unsigned)zmin_code, (unsigned)zmax_code,
            railed ? " RAILED(已撞轨, 后段无效)" : "");
    UART_SendString(buf);
}

/* 单轮码 -> fA: 1 码 = 152.6uV(积分器域) -> 10 秒积分折合 1.526 fA */
#define CAL_ZERO_FA_PER_CODE   1.526

static void CalZero_Report(uint8_t brief)
{
    char buf[176];
    char v_adc[16], drift_uv[16], ib_fa[16], zero_fa[16], noise_fa[16];
    double v_adc_mean = mean_code * (3.3 / 65536.0);
    double dv_raw_dt = -slope_code_per_s * (3.3 / 65536.0) / (double)LEVELSHIFT_GAIN;
    double ibias = (double)C_INT * dv_raw_dt;
    uint8_t railed = (zmax_code >= 0xFF00U || zmin_code <= 0x00FFU) ? 1U : 0U;

    UART_FormatScaled((int64_t)(v_adc_mean * 1e4), 4, v_adc);
    UART_FormatScaled((int64_t)(dv_raw_dt * 1e6), 2, drift_uv);
    UART_FormatScaled((int64_t)(ibias * 1e15), 1, ib_fa);

    if (brief)
    {
        sprintf(buf, "CAL ZERO R=%lu/%u: MEAN=%lu Vadc=%sV STD=%lu DRIFT=%suV/s IB=%sfA CODE=%u..%u%s\r\n",
                (unsigned long)round_count, (unsigned)CAL_ZERO_ROUNDS,
                (unsigned long)(mean_code + 0.5), v_adc,
                (unsigned long)(std_code + 0.5), drift_uv, ib_fa,
                (unsigned)zmin_code, (unsigned)zmax_code,
                railed ? " RAILED(本轮不可信)" : "");
        UART_SendString(buf);
        return;
    }

    /* 轮间汇总: 零点偏差 = 各轮均值的平均; 本底噪声 = 各轮均值的标准差 */
    UART_FormatScaled((int64_t)(cum_zero_code * (3.3 / 65536.0) * 1e4), 4, v_adc);
    UART_FormatScaled((int64_t)(cum_zero_code * CAL_ZERO_FA_PER_CODE), 2, zero_fa);
    UART_FormatScaled((int64_t)(cum_noise_code * CAL_ZERO_FA_PER_CODE), 2, noise_fa);
    sprintf(buf, "CAL ZERO SUM n=%lu T=%lus: ZERO=%lu (%sV, %sfA) NOISE=%lu (%sfA RMS)\r\n",
            (unsigned long)round_count,
            (unsigned long)(round_count * CAL_ZERO_SECONDS),
            (unsigned long)(cum_zero_code + 0.5), v_adc, zero_fa,
            (unsigned long)(cum_noise_code + 0.5), noise_fa);
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
    sum_t = sum_v = sum_tt = sum_tv = sum_vv = 0.0;
    tick_count = 0U;
    cal_done = 0U;
    zmin_code = 0xFFFFU;
    zmax_code = 0U;
    last_progress_sec = 0U;
    progress_due = 0U;

    if (round_count == 0U)          /* 只在本轮序列的最开头清零一次 */
    {
        sum_means = sumsq_means = 0.0;
        cum_zero_code = cum_noise_code = 0.0;
    }

    /* 必须先复位: 空闲态 ADG 断开, 积分器被输入电流一直推着走。不复位就
     * 开始积分, 起点可能在轨上 (码饱和 -> 码到电压的映射失效), 算出来的
     * dV 是假的。必须在 TIM6 未跑时做 (阻塞)。 */
    Current_PullToZero();
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
    /* 走 Current_ReadControl(): 随 USE_INTERNAL_ADC 一起切, 不写死内置路 */
    uint16_t raw = Current_ReadControl();
    double t = (double)tick_count / 6250.0;      /* 秒 */

    sum_t  += t;
    sum_v  += (double)raw;
    sum_tt += t * t;
    sum_tv += t * (double)raw;
    sum_vv += (double)raw * (double)raw;
    if (raw < zmin_code) { zmin_code = raw; }
    if (raw > zmax_code) { zmax_code = raw; }
    tick_count++;

    /* 每 10 秒报一次进度 —— **只置标志, 计算与打印都不在中断里做**。
     *
     * 原来这里直接调 CalZero_Compute() + CalZero_Progress():
     *   CalZero_Progress() 里有 sprintf 和 HAL_UART_Transmit, 而后者靠
     *   HAL_GetTick 判超时 —— TIM6 优先级 0 会把 SysTick(优先级 15) 饿死,
     *   UART 一旦异常就是**中断里的死循环**, 正是 adc.c/ads8866.c 反复警告的坑。
     *   就算一切正常, 60 字节一行也要挡住中断约 5ms -> 每次上报丢约 30 拍;
     *   而时间 t = tick_count/6250 是按**拍**算的, 少采 30 拍就少算 4.8ms,
     *   于是 DRIFT/IB 每次上报偏大约 0.5%。
     *   CalZero_Compute() 是双精度最小二乘, 同样吃拍。
     * 两件事都搬到 CalMode_Task() (主循环) 里做。 */
    if ((tick_count / 6250U) >= (last_progress_sec + 10U))
    {
        last_progress_sec = tick_count / 6250U;
        progress_due = 1U;
    }

    if (tick_count >= CAL_ZERO_TICKS)
    {
        HAL_TIM_Base_Stop_IT(&htim6);
        ADG_Disable();
        cal_done = 1U;              /* 收尾那次计算也交给主循环 */
    }
#endif
    /* CAL_BANG: 采样由 Current_Process 完成 */
}

/* 主循环每轮调用: 完成检测 + 上报, 自动进入下一轮 */
void CalMode_Task(void)
{
#if CAL_MODE == CAL_ZERO
    /* 进度上报 (从 ISR 搬过来的, 见 CalMode_Tick 的注释) */
    if (progress_due)
    {
        progress_due = 0U;
        CalZero_Compute();
        CalZero_Progress();
    }

    if (cal_done)
    {
        CalZero_Compute();      /* 收尾那次计算也从 ISR 搬到主循环 */
        CalZero_Cumulative();   /* 把本轮均值并进轮间统计 */
        /* 每轮都报; 每 10 轮附一次轮间汇总 (否则要等到跑完才看得到趋势) */
        CalZero_Report(1U);
        if ((round_count % 10U) == 0U) { CalZero_Report(0U); }

        if (round_count >= CAL_ZERO_ROUNDS)
        {
            CalZero_Report(0U);     /* 最终汇总 */
            UART_SendString("CAL ZERO DONE\r\n");
            /* 停在这里, 不重开一轮 */
        }
        else
        {
            CalMode_Start();
        }
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
