#include "current.h"
#include "ads8866.h"
#include "adg4530.h"
#include "tim.h"
#include <math.h>

/* ---- 硬件常量 (沿用现有参数, 硬件改动需同步) ---- */
#define C_INT        30e-12f    /* 积分电容 */
#define T_INT        160e-6f    /* 积分周期 (TIM6 160us) */
#define I_POS        50e-9f     /* 正向参考电流 */
#define I_NEG        (-50e-9f)  /* 负向参考电流 */
#define TOTAL_CYCLE  6250U      /* 1 秒窗口总采样数 */

/* ADC 域阈值电压 (反相映射: V_ad = 1.55 - 0.33*V_o)
 * V_o=+4.3 (正轨) -> V_ad=0.131 = code_lower
 * V_o=-4.3 (负轨) -> V_ad=2.969 = code_upper
 * V_o=+2.0 (模式二目标) -> V_ad=0.890 = code_target */
#define V_ADC_UPPER  (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_LOWER)  /* 2.969 */
#define V_ADC_LOWER  (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_UPPER)  /* 0.131 */
#define V_ADC_TARGET (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * HARD_TARGET_INT_V) /* 0.890 */

#define HARD_TIMEOUT_TICKS  (HARD_TIMEOUT_MS * 1000U / 160U)  /* 62500 tick = 10s 总预算 */
#define HARD_DOWN_MAX_TICKS 6250U                             /* 单次下放相安全超时 1s */
#define HARD_BASE_TICKS     3U                                /* 循环间基线采样 tick 数 */

/* ---- 模式一 状态 ---- */
static uint16_t voltage_buf[TOTAL_CYCLE];   /* 1 秒全部采样原始码, 12.5KB */
static uint32_t sample_index;
static uint32_t m_count;                    /* POS(升压方向) 施加周期数 */
static uint32_t n_count;                    /* NEG(降压方向) 施加周期数 */
volatile uint8_t finish_flag;
static volatile float window_current;       /* 窗口结束瞬间(中断内)算好的电流结果 */

#define SEL_POS 0U
#define SEL_NEG 1U
static volatile uint8_t sel_state;

/* ---- 模式二 状态 (10s 内多循环) ---- */
enum { HARD_IDLE = 0, HARD_UP, HARD_DOWN, HARD_BASE, HARD_DONE, HARD_TIMEOUT };
static volatile uint8_t hard_phase = HARD_IDLE;
static uint32_t up_ticks, down_ticks, base_ticks;
static uint32_t total_ticks;                /* 模式二已消耗 tick (10s 预算) */
static uint32_t base_sum;                   /* 循环间基线累加器 */
static uint32_t cycles_done;                /* 有效上积+下放循环数 */
static float sum_current;                   /* 各循环电流累加 (求平均) */
static uint16_t prev_raw;
static float t1_s, t2_s;

/* ---- 底噪/偏置电流测量状态 ---- */
#define TICKS_PER_SECOND 6250U       /* TIM6 160us -> 1s */
static void Noise_Tick(void);
static float HardIntegral_CycleCurrent(void);   /* 单循环双斜率结果 (前向声明) */
static volatile uint8_t noise_active;
static volatile uint8_t noise_done;
static uint32_t noise_ticks;
static uint32_t noise_samples;       /* 已存 1s 采样数 */
static float noise_bias_current;

/* ---- ADC 域阈值 (原始码) ---- */
static uint16_t code_upper, code_lower, code_zero, code_target;
static uint16_t code_baseline;      /* 上积起点: 断开参考、注入稳定后实测 */
static uint16_t code_down_start;    /* 下放起点: 反向开关闭合瞬间电压 (反推, 含注入台阶) */
static uint16_t down_s1;            /* 下放相第一个采样, 用于反推 */

/* 电压 -> ADS8866 原始码 */
static uint16_t VToCode(float v_adc)
{
    if (v_adc <= 0.0f)
    {
        return 0U;
    }
    if (v_adc >= ADS8866_VREF)
    {
        return 0xFFFFU;
    }
    return (uint16_t)(v_adc / ADS8866_VREF * 65536.0f);
}

/* ADS8866 原始码 -> 积分器电压 (反相映射) */
static float CodeToVInt(uint16_t code)
{
    float v_adc = (float)code * ADS8866_VREF / 65536.0f;
    return (LEVELSHIFT_V_ADC_ZERO - v_adc) / LEVELSHIFT_GAIN;
}

/* 上升过阈时刻线性插值 (prev < th <= now), 返回以秒为单位 */
static float InterpCrossUp(uint16_t prev, uint16_t now, uint16_t th, uint32_t ticks)
{
    float f = (now > prev) ? (float)(now - th) / (float)(now - prev) : 0.0f;
    if (f < 0.0f) f = 0.0f;
    if (f > 1.0f) f = 1.0f;
    return ((float)ticks - f) * T_INT;
}

/* 下降过阈时刻线性插值 (prev > th >= now) */
static float InterpCrossDown(uint16_t prev, uint16_t now, uint16_t th, uint32_t ticks)
{
    float f = (prev > now) ? (float)(th - now) / (float)(prev - now) : 0.0f;
    if (f < 0.0f) f = 0.0f;
    if (f > 1.0f) f = 1.0f;
    return ((float)ticks - f) * T_INT;
}

/* 由电平移位映射计算 ADC 域阈值码 */
static void ThresholdCodes_Init(void)
{
    code_upper  = VToCode(V_ADC_UPPER);
    code_lower  = VToCode(V_ADC_LOWER);
    code_zero   = VToCode(LEVELSHIFT_V_ADC_ZERO);
    code_target = VToCode(V_ADC_TARGET);
    code_baseline = code_zero;
}

/* ============================================================
 * 模式一: 滞回电荷平衡 (1 秒窗口)
 * ============================================================ */
void Current_Start(void)
{
    m_count = 0;
    n_count = 0;
    sample_index = 0;
    finish_flag = 0;
    sel_state = SEL_POS;        /* 默认输入正向标准电流 */
    hard_phase = HARD_IDLE;
    noise_active = 0U;
    noise_done = 0U;

    ThresholdCodes_Init();

    ADG_Enable();
    ADG_Select_Positive();
}

/* 停止测量: 停 TIM6 + 关断 ADG (空闲状态不注入参考电流) */
void Current_Stop(void)
{
    HAL_TIM_Base_Stop_IT(&htim6);
    ADG_Disable();
}

/* TIM6 中断每 160us 调用一次 (模式一/模式二/底噪模式共用的节拍) */
void Current_Process(void)
{
    uint16_t raw;

    if (noise_active)
    {
        Noise_Tick();
        return;
    }

    raw = ADS8866_ReadRaw();

    if (hard_phase == HARD_IDLE)
    {
        /* ---- 模式一: 滞回 bang-bang ---- */
        if (sample_index >= TOTAL_CYCLE)
        {
            return;
        }

        voltage_buf[sample_index] = raw;
        sample_index++;

        /* bang-bang 阈值逻辑 (反相映射, 阈值永远生效):
         * POS(+50nA 注入) -> V_o 下降 -> V_ad 上升 -> 触 code_upper 切 NEG
         * NEG(-50nA 注入) -> V_o 上升 -> V_ad 下降 -> 触 code_lower 切 POS
         * (论文式(3): I+I_+ = -C*dV/dt, I_+>0 -> dV/dt<0) */
        if (sel_state == SEL_POS)
        {
            m_count++;
            if (raw >= code_upper)
            {
                sel_state = SEL_NEG;
                ADG_Select_Negative();
            }
        }
        else
        {
            n_count++;
            if (raw <= code_lower)
            {
                sel_state = SEL_POS;
                ADG_Select_Positive();
            }
        }

        if (sample_index >= TOTAL_CYCLE)
        {
            /* 窗口完成: 中断内先算结果, 再重置立刻开新窗 (连续模式, 不停 TIM6) */
            window_current = Calculate_Current();
            finish_flag = 1;
            sample_index = 0;
            m_count = 0;
            n_count = 0;
        }
    }
    else if (hard_phase == HARD_UP)
    {
        /* ---- 模式二: 上积相 (被测电流单独积分) ----
         * 负电流(流出节点) -> V_o 上升 -> V_ad 下降: 过目标 = raw <= code_target */
        up_ticks++;
        if (raw <= code_target)
        {
            t1_s = InterpCrossDown(prev_raw, raw, code_target, up_ticks);
            hard_phase = HARD_DOWN;
            down_ticks = 0;
            ADG_Enable();
            ADG_Select_Positive();      /* +50nA, V_o 回落 (下放相) */
        }
        else if (up_ticks >= (HARD_TIMEOUT_TICKS - total_ticks))
        {
            /* 剩余预算内未到目标: 有历史循环取平均, 否则判超时 */
            hard_phase = (cycles_done > 0U) ? HARD_DONE : HARD_TIMEOUT;
            HAL_TIM_Base_Stop_IT(&htim6);
        }
        prev_raw = raw;
    }
    else if (hard_phase == HARD_DOWN)
    {
        /* ---- 模式二: 下放相 (+50nA 拉 V_o 回落, V_ad 上升过基线) ---- */
        down_ticks++;

        if (raw >= code_baseline)
        {
            /* 回落回到实测基线记 t2 (含过阈线性插值, 码域向上过基线) */
            t2_s = InterpCrossUp(prev_raw, raw, code_baseline, down_ticks);
            sum_current += HardIntegral_CycleCurrent();
            cycles_done++;
            total_ticks += up_ticks + down_ticks;
            ADG_Disable();

            if (total_ticks >= HARD_TIMEOUT_TICKS)
            {
                /* 10s 预算用尽: 结束, 结果取多循环平均 */
                hard_phase = HARD_DONE;
                HAL_TIM_Base_Stop_IT(&htim6);
            }
            else
            {
                /* 预算未用尽: 进入循环间基线测量 (断开参考后注入台阶
                 * 需稳定, 基线必须重测, 不能沿用上一循环) */
                hard_phase = HARD_BASE;
                base_ticks = 0;
                base_sum = 0U;
            }
        }
        else if (down_ticks == 1U)
        {
            down_s1 = raw;
        }
        else if (down_ticks == 2U)
        {
            /* 电荷注入补偿: 闭合瞬间注入台阶瞬时完成, 之后线性放电,
             * 用前两点反推 t=0 (切换瞬间) 的真实电压 Vc */
            int32_t est = (int32_t)down_s1 * 2 - (int32_t)raw;
            if (est < 0) est = 0;
            if (est > 65535) est = 65535;
            code_down_start = (uint16_t)est;
        }
        else if (down_ticks >= HARD_DOWN_MAX_TICKS)
        {
            /* 本循环无效: 有历史循环取平均, 否则判超时 */
            hard_phase = (cycles_done > 0U) ? HARD_DONE : HARD_TIMEOUT;
            HAL_TIM_Base_Stop_IT(&htim6);
        }
        prev_raw = raw;
    }
    else if (hard_phase == HARD_BASE)
    {
        /* ---- 模式二: 循环间基线实测 (ADG 已断开, 仅被测电流积分) ----
         * 3 tick 均值, 期间被测电流 <1nA 造成的漂移 <16uV, 可忽略 */
        base_sum += raw;
        base_ticks++;
        if (base_ticks >= HARD_BASE_TICKS)
        {
            code_baseline = (uint16_t)(base_sum / HARD_BASE_TICKS);
            code_down_start = code_target;      /* 防护默认值 */
            up_ticks = 0U;
            down_ticks = 0U;
            prev_raw = raw;
            hard_phase = HARD_UP;
        }
    }
    /* DONE/TIMEOUT: 不再采样 */
}

/* 式(6): I = C*(Vo1-Vo2)/(N*T) - (m*I_+ + n*I_-)/N
 * 实测极性: POS(+50nA) -> 电压下降, 对应论文的 n (放电, I_+ 侧);
 *           NEG(-50nA) -> 电压上升, 对应论文的 m (充电, I_- 侧) */
float Calculate_Current(void)
{
    float vo1, vo2, n_total;

    n_total = (float)(m_count + n_count);
    if (n_total == 0.0f || sample_index == 0U)
    {
        return 0.0f;
    }

    vo1 = CodeToVInt(voltage_buf[0]);
    vo2 = CodeToVInt(voltage_buf[sample_index - 1U]);

    n_total = C_INT * (vo1 - vo2) / (n_total * T_INT);
    n_total -= ((float)m_count * I_POS + (float)n_count * I_NEG)
               / (float)(m_count + n_count);

    return n_total;
}

uint8_t Current_Is_Finished(void)
{
    return finish_flag;
}

uint8_t Current_WindowFinished(void)
{
    return finish_flag;
}

void Current_ClearWindowFlag(void)
{
    finish_flag = 0U;
}

float Current_GetWindowResult(void)
{
    return window_current;
}

uint16_t Current_GetSample(uint32_t index)
{
    if (index >= sample_index)
    {
        return 0U;
    }
    return voltage_buf[index];
}

/* 1s 窗口内积分器电压峰峰值 (bang-bang 摆幅, V) */
float Current_GetSwingVoltage(void)
{
    uint16_t vmin = 0xFFFFU, vmax = 0U;
    uint32_t i;
    double dv;

    for (i = 0U; i < sample_index; i++)
    {
        if (voltage_buf[i] < vmin)
        {
            vmin = voltage_buf[i];
        }
        if (voltage_buf[i] > vmax)
        {
            vmax = voltage_buf[i];
        }
    }

    if (sample_index == 0U || vmax < vmin)
    {
        return 0.0f;
    }

    dv = (double)(vmax - vmin) * ((double)ADS8866_VREF / 65536.0)
         / (double)LEVELSHIFT_GAIN;
    return (float)dv;
}

/* 去趋势噪声: 用 ±5 点邻居线性插值抵消 bang-bang 斜坡,
 * 残差 RMS / sqrt(1.5) = 每采样白噪声 RMS (积分器域, V)
 * 说明: 直接算原始采样 std 会被锯齿波淹没, 必须先去趋势 */
#define NOISE_DETREND_K 5U

float Current_GetNoiseVoltage(void)
{
    double sum = 0.0, sumsq = 0.0, mean, var, resid_rms, sigma_code, sigma_v;
    uint32_t n = 0U, i;

    for (i = NOISE_DETREND_K; i + NOISE_DETREND_K < sample_index; i++)
    {
        double v_prev = (double)voltage_buf[i - NOISE_DETREND_K];
        double v_next = (double)voltage_buf[i + NOISE_DETREND_K];
        double resid = (double)voltage_buf[i] - (v_prev + v_next) / 2.0;
        sum += resid;
        sumsq += resid * resid;
        n++;
    }

    if (n < 2U)
    {
        return 0.0f;
    }

    mean = sum / (double)n;
    var = sumsq / (double)n - mean * mean;
    if (var < 0.0)
    {
        var = 0.0;
    }
    resid_rms = sqrt(var);

    /* var(残差) = sigma^2 * (1 + 1/2) = 1.5 sigma^2 (白噪声假设) */
    sigma_code = resid_rms / 1.224744871;
    sigma_v = sigma_code * ((double)ADS8866_VREF / 65536.0) / (double)LEVELSHIFT_GAIN;
    return (float)sigma_v;
}

/* ============================================================
 * 模式二: 双斜率硬积分 (电流 < 1nA)
 *  复位相: 拉回零位 (阻塞) -> 上积相: 被测电流积分到 2.0V 记 t1
 *  -> 下放相: 反向标准电流放电回零位记 t2 -> I = I_ref*t2/(t1+t2)
 * ============================================================ */
void HardIntegral_Start(void)
{
    uint16_t raw;
    uint32_t guard;
    uint32_t sum = 0U;
    uint32_t i;

    hard_phase = HARD_IDLE;

    /* ---- 复位相: 把积分器拉回零位 (阻塞, 最多 ~2.7ms) ---- */
    raw = ADS8866_ReadRaw();
    if (raw > code_zero)
    {
        guard = HARD_RESET_GUARD;
        ADG_Enable();
        ADG_Select_Negative();          /* 降压方向 */
        while (raw > code_zero && guard-- > 0U)
        {
            raw = ADS8866_ReadRaw();
        }
    }
    else if (raw < code_zero)
    {
        guard = HARD_RESET_GUARD;
        ADG_Enable();
        ADG_Select_Positive();          /* 升压方向 */
        while (raw < code_zero && guard-- > 0U)
        {
            raw = ADS8866_ReadRaw();
        }
    }
    ADG_Disable();                      /* 断开参考: 被测电流单独积分 */

    /* ---- 上积起点基线: 断开参考、电荷注入稳定后实测均值 8 点.
     * 开关切换有注入台阶, 基线必须实测, 不能假设为标称零位 ---- */
    for (i = 0U; i < 8U; i++)
    {
        sum += ADS8866_ReadRaw();
    }
    code_baseline = (uint16_t)(sum / 8U);

    /* ---- 上积相 (多循环状态清零) ---- */
    up_ticks = 0;
    down_ticks = 0;
    base_ticks = 0;
    total_ticks = 0;
    cycles_done = 0U;
    sum_current = 0.0f;
    t1_s = 0.0f;
    t2_s = 0.0f;
    code_down_start = code_target;      /* 默认值: 下放未反推成功时的防护 */
    prev_raw = ADS8866_ReadRaw();
    hard_phase = HARD_UP;
    HAL_TIM_Base_Start_IT(&htim6);
}

uint8_t HardIntegral_IsFinished(void)
{
    return (hard_phase == HARD_DONE) || (hard_phase == HARD_TIMEOUT);
}

uint8_t HardIntegral_IsTimeout(void)
{
    return (hard_phase == HARD_TIMEOUT);
}

/* 单循环双斜率 + 电荷注入补偿 (反相映射, 全部用实测端点原始码:
 *   上积:  I*t1 = C*(Va - Vb)/GAIN          Va=基线码, Vb=目标码 (Vb < Va)
 *   下放:  (I + I_ref)*t2 = C*(Va - Vc)/GAIN  Vc=切换瞬间码 (前两点反推)
 *   => I = I_ref*t2*(Va-Vb) / [t1*(Va-Vc) + t2*(Va-Vb)]
 * 无注入 (Vc=Vb) 时退化为 I_ref*t2/(t1+t2) */
static float HardIntegral_CycleCurrent(void)
{
    int32_t d_up, d_down;
    float denom;

    if (t1_s <= 0.0f || t2_s <= 0.0f)
    {
        return 0.0f;
    }

    d_up   = (int32_t)code_baseline - (int32_t)code_target;      /* 基线->目标 (正) */
    d_down = (int32_t)code_baseline - (int32_t)code_down_start;  /* 基线->下放起点 */

    /* 防护: 注入方向异常导致下放起点不低于基线时, 退化为近似式 */
    if (d_down <= 0)
    {
        return I_POS * t2_s / (t1_s + t2_s);
    }

    denom = t1_s * (float)d_down + t2_s * (float)d_up;
    if (denom <= 0.0f)
    {
        return 0.0f;
    }

    return I_POS * t2_s * (float)d_up / denom;
}

/* 多循环平均 (10s 预算内至少 1 个有效循环) */
float HardIntegral_GetCurrent(void)
{
    if (cycles_done == 0U)
    {
        return 0.0f;
    }
    return sum_current / (float)cycles_done;
}

/* ============================================================
 * 底噪/偏置电流测量: ADG 全断 -> 纯积分 1000s,
 * 每秒 1 点存入 voltage_buf, 最小二乘拟合 dV/dt -> I_bias = C*dV/dt
 * ============================================================ */
static void Noise_Fit(void)
{
    double sum_t = 0.0, sum_v = 0.0, sum_tt = 0.0, sum_tv = 0.0;
    double n, denom, slope_code_per_s, dv_dt;
    uint32_t i;

    for (i = 0U; i < noise_samples; i++)
    {
        double t = (double)i;               /* 采样序号 = 秒 */
        double v = (double)voltage_buf[i];  /* 原始码 */
        sum_t += t;
        sum_v += v;
        sum_tt += t * t;
        sum_tv += t * v;
    }

    n = (double)noise_samples;
    denom = n * sum_tt - sum_t * sum_t;
    slope_code_per_s = (denom > 1e-9) ? (n * sum_tv - sum_t * sum_v) / denom : 0.0;

    /* 码/s -> 积分器电压 V/s: V_o = (1.55 - V_ad)/0.33 (反相映射),
     * 故 dV_o/dt = -(dV_ad/dt)/0.33 */
    dv_dt = -slope_code_per_s * ((double)ADS8866_VREF / 65536.0)
            / (double)LEVELSHIFT_GAIN;

    /* I_bias = C * dV/dt */
    noise_bias_current = (float)((double)C_INT * dv_dt);
}

void Noise_Start(void)
{
    ADG_Disable();              /* 所有输入关闭, 无参考注入 */

    noise_active = 1U;
    noise_done = 0U;
    noise_ticks = 0U;
    noise_samples = 0U;
    noise_bias_current = 0.0f;

    HAL_TIM_Base_Start_IT(&htim6);
}

/* TIM6 中断里由 Current_Process 路由调用 */
static void Noise_Tick(void)
{
    uint16_t raw = ADS8866_ReadRaw();

    noise_ticks++;

    if ((noise_ticks % TICKS_PER_SECOND) == 0U)
    {
        if (noise_samples < TOTAL_CYCLE)    /* 缓冲复用, 上限 6250 秒 */
        {
            voltage_buf[noise_samples] = raw;
            noise_samples++;
        }
    }

    if (noise_ticks >= (uint32_t)NOISE_MEASURE_SECONDS * TICKS_PER_SECOND)
    {
        noise_active = 0U;
        HAL_TIM_Base_Stop_IT(&htim6);
        Noise_Fit();
        noise_done = 1U;
    }
}

uint8_t Noise_IsFinished(void)
{
    return noise_done;
}

float Noise_GetBiasCurrent(void)
{
    return noise_bias_current;
}

uint32_t Noise_GetElapsedSec(void)
{
    return noise_ticks / TICKS_PER_SECOND;
}

uint16_t Noise_GetSample(uint32_t index)
{
    if (index >= noise_samples)
    {
        return 0U;
    }
    return voltage_buf[index];
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
    if (htim->Instance == TIM6)
    {
        Current_Process();
    }
}
