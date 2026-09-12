#include "current.h"
#include "adc.h"        /* Adc_ReadRaw: 控制路径 (内置 ADC1 / PA3) */
#include "ads8866.h"    /* ADS8866_ReadRaw: 观测路径 (外部 16 位 / SPI1) */
#include "adg4530.h"
#include "tim.h"
#include "cal_mode.h"
#include "cal_coef.h"   /* Cal_GetQ / Cal_GetIPos / Cal_GetINeg: 可标定的物理常数 */
#include <math.h>

/* ---- 硬件常量 (C_INT/I_POS_REF/I_NEG_REF 已上移到 current.h 供标定模块共用) ---- */
#define T_INT        160e-6f    /* 积分周期 (TIM6 160us) */
#define TOTAL_CYCLE  6250U      /* 1 秒窗口总采样数 */

/* 模式一 滞回阈值对应的原始码 (反相映射: V_ad = 1.55 - 0.33*V_o)
 * V_o=+4.3 (正轨) -> V_ad=0.131 -> code_lower
 * V_o=-4.3 (负轨) -> V_ad=2.969 -> code_upper */
#define V_ADC_UPPER  (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_LOWER)  /* 2.969 */
#define V_ADC_LOWER  (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_UPPER)  /* 0.131 */

/* ---- 模式一 状态 ---- */
static uint16_t voltage_buf[TOTAL_CYCLE];   /* 1 秒全部采样原始码, 12.5KB */
static uint32_t sample_index;
static uint32_t last_window_count;          /* 最近一个完整窗口的采样数 (单次测量完成后 B 指令取数用) */
static uint32_t last_m, last_n;             /* 最近完整窗口的 POS/NEG 周期数 (标定用) */
static uint32_t m_count;                    /* POS(升压方向) 施加周期数 */
static uint32_t n_count;                    /* NEG(降压方向) 施加周期数 */
volatile uint8_t finish_flag;   /* 兼作窗口封存锁: 完成时置位, 被取走时清零 */

/* ---- ADS8866 外部 16 位并行观测 ----
 * 与 voltage_buf 同拍: 每个 tick 先读内置 ADC 做控制判决, 再读外部 16 位存这里。
 * 外部读取含 9us 转换等待 + 1.6us SPI, 故两者存在约 11us 的固定相位偏斜
 * (相对 160us 节拍是 7%, 相对 ~18ms 三角波周期只有 0.06%, 用于对照足够)。
 * 只写不参与控制 —— 外部芯片任何异常都不会影响测量结果。 */
static uint16_t voltage_buf_ext[TOTAL_CYCLE];
static uint32_t ext_bad_read;               /* 坏读累计 (跨窗口只增不清) */


/* 同拍读两路: 返回内置(控制), 经 *ext_out 带出外部(观测)。
 * 顺序固定为先内后外 —— 控制判决必须基于本 tick 最早那一刻的电平,
 * 外部读取的 ~11us 不能挤进判决路径。
 *
 * ---- 坏读判据 (为什么不在驱动层判) ----
 * 0xFFFF 既是"DOUT 常高 / MISO 断线"的特征, **也是合法的满量程码**。
 * 只按外部值判, 输入撞轨时会把 100% 的有效读数记成坏读 —— 实测底噪那一轮
 * ERR 从 37 虚涨到 312537 (= 37 + 50s x 6250, 精确吻合), 37 个误报全是
 * 0xFFFF、0x0000 一个都没有。诊断计数一旦这样撒谎, 所有人都会去查一个
 * 不存在的硬件故障。
 *
 * 所以判据上移到能同时看到两路的地方:
 *   外部满量程 且 内置也压轨   -> 真撞轨, 不计数
 *   外部满量程 而 内置在量程中段 -> MISO 真断, 计数
 *   外部 0x0000                -> 计数 (本硬件正轨映射到码 ~1290, 模式一
 *                                 实测最低 2288, 节点根本到不了 0V, 无歧义)
 *
 * 残留误判已界定: 两路 11us 采样偏斜在斜坡上只差约 160 码, 而外部满量程
 * 对应内置码 >= 64800、判据取 61440, 裕度 3360 码 >> 160 码。真断线叠真撞轨
 * 时会漏报, 但那时读数本就无效且不影响控制路径。
 * **这个判据只影响 ERR 这个诊断计数, 永远不影响测量数值。** */
#define RAIL_DETECT_CODE  0xF000U       /* 内置压轨判据 (满轨实测 0xFFF0) */

static uint16_t ReadBoth(uint16_t *ext_out)
{
    uint16_t r_int = Adc_ReadRaw();
    uint16_t r_ext = ADS8866_ReadRaw();
    uint8_t  ext_full = (uint8_t)((r_ext == 0xFFFFU) || (r_ext == 0x0000U));

    if (ext_full)
    {
        /* 内置也在轨上 -> 节点真的撞轨了, 外部读数是对的, 不算坏读 */
        uint8_t int_railed = (uint8_t)((r_int >= RAIL_DETECT_CODE) ||
                                       (r_int <= (uint16_t)(0xFFFFU - RAIL_DETECT_CODE)));
        /* 0x0000 无歧义: 正轨映射到码 ~1290, 节点到不了 0V, 一定是链路故障 */
        if ((r_ext != 0x0000U) && int_railed)
        {
            /* 真撞轨: 不计数 */
        }
        else
        {
            ext_bad_read++;
        }
    }

    *ext_out = r_ext;
    return r_int;
}

/* 模式二逐 tick 波形入队 (满了就停, 不覆盖) */
static volatile float window_current;       /* 窗口结果, 由**内置 ADC** 算出 (控制路径) */
static volatile float window_current_ext;   /* 同一窗口, 由 **ADS8866** 算出 (观测路径) */

#define SEL_POS 0U
#define SEL_NEG 1U
static volatile uint8_t sel_state;



/* ---- 小电流模式 (纯积分 + 斜率) 的参数与状态 ---- */
#define SI_TICKS_PER_SEC   6250U        /* TIM6 160us -> 1s */
#define SI_DEFAULT_SEC     3U
#define SI_MAX_SEC         30U
#define SI_RAIL_CODE       22900U       /* |Δ码| 到这儿就提前收尾 (= 3.5V,
                                         * 按标称映射 1码=152.6uV; 轨在 4.3V)。
                                         * 这是安全限, 不参与精度, 标称换算无妨 */
#define SI_SETTLE_TICKS    8U           /* 断开参考后注入台阶的稳定拍数 */
#define SI_AVG_TICKS       8U           /* 起点/终点各取多少拍平均 */

static volatile uint8_t  si_active;
static volatile uint8_t  si_done;
static volatile uint8_t  si_short;      /* 撞轨提前收尾 (结果仍有效, 但积分时间短了) */
static volatile uint32_t si_ticks;
static uint32_t          si_target_ticks = SI_DEFAULT_SEC * SI_TICKS_PER_SEC;
static uint32_t          si_decim;      /* 抽点存波形的间隔 */
/* 起点/终点都存**原始码的平均**, 不存电压: 码域直接乘 q 就行, 不必过
 * LEVELSHIFT_GAIN。而 (V_ADC_ZERO - v) 那两项在相减时本来就会约掉 ——
 * 存电压等于白白把两个标称常量引进计算链 */
static float             si_start_int, si_start_ext;    /* 起点 (平均原始码) */
static float             si_cur_int,  si_cur_ext;       /* 结果 (A) */
static uint16_t          si_ring_int[SI_AVG_TICKS];
static uint16_t          si_ring_ext[SI_AVG_TICKS];
static uint8_t           si_ring_n, si_ring_i;

/* 前向声明 */
static void SmallI_Tick(uint16_t raw, uint16_t raw_ext);

/* ---- ADC 域阈值 (原始码) ---- */
static uint16_t code_upper, code_lower, code_zero;

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

/* 注: 原来这里有个 CodeToVInt() (原始码 -> 积分器电压)。现在模式一和小电流
 * 模式都在**码域**直接乘 q 算, 不再需要它 —— 那正是为了甩掉 LEVELSHIFT_GAIN
 * 这个从没测过的标称值 (它在端点差里本来就会约掉, 换算成电压是白引入一次除法) */

/* 由电平移位映射计算 ADC 域阈值码 */
static void ThresholdCodes_Init(void)
{
    code_upper  = VToCode(V_ADC_UPPER);
    code_lower  = VToCode(V_ADC_LOWER);
    code_zero   = VToCode(LEVELSHIFT_V_ADC_ZERO);
}

/* ============================================================
 * 模式一: 滞回电荷平衡 (1 秒窗口)
 * ============================================================ */
/* 拉回相守卫: 每次 Adc_ReadRaw() 约 8us (ADC 时钟 80MHz, 采样 640.5 周期),
 * 50000 次约 400ms。
 *
 * 量级参考 (C=100pF, |I_ref|=50nA, 拉回速率 = 净电流/C):
 *   最坏行程 5.3V (电平移位饱和轨 -> 0V), 输入 0     -> 500V/s  -> 约 11ms
 *   输入 45nA 与参考相抵                            ->  50V/s  -> 约 106ms
 * 即 400ms 对最坏情况有约 4 倍余量。
 * 再往外就只是防挂死保险丝, 不是量程判据 (超量程由用户保证不会发生) */
#define PRECOND_GUARD  50000U

/* 拉回相未能在守卫内到位次数 (SWD 按符号读, 正常恒为 0) */
volatile uint32_t precond_timeout;

void Current_Start(void)
{
    uint16_t raw;
    uint32_t guard;

    m_count = 0;
    n_count = 0;
    sample_index = 0;
    last_window_count = 0U;
    last_m = 0U;
    last_n = 0U;
    finish_flag = 0;
    sel_state = SEL_POS;        /* 默认输入正向标准电流 */
    si_active = 0U;
    si_done = 0U;

    ThresholdCodes_Init();

    ADG_Enable();

    /* ---- 拉回相 (阻塞): 把积分器拉到接近 0V ----
     * 必须在调用方 HAL_TIM_Base_Start_IT 之前完成 —— 此刻 TIM6 未跑,
     * ISR 不参与, 所以这里可以安全地阻塞轮询 Adc_ReadRaw()
     * (Adc_ReadRaw 不可重入, 不能与 ISR 里的转换并发)。
     *
     * 为什么需要: 空闲态 ADG 断开 (Current_Stop 关断参考), 积分器被被测
     * 电流推到压轨。直接开窗的话窗口开头约 6ms (38 tick) 落在电平移位器
     * 饱和区, voltage_buf[0] 变成一个推不出真实电压的轨值, 式(6) 的
     * C*(Vo1-Vo2)/(N*T) 项随之失效 -> 结果带固定偏置
     * (实测两次逐位相同的 25.274nA, 说明它是偏置而不是噪声)。
     *
     * ---- 为什么目标是 0V 而不是某个阈值 ----
     * 1) 拉到边界是**赌符号**: 拉到 -4.3V 后若被测电流把它往更负推, 余量
     *    就是 0, 第一拍立刻撞轨 (底噪那一轮 100% 满量程就是这么来的)。
     *    拉到 0V 则两个方向各留 4.3V, 不需要预知电流符号。
     * 2) 恰好停在阈值上开窗, 第一拍立即触发切换, 那个相位退化成 1 拍
     *    (旧记录里见过"第一个 NEG 相 285 拍 / 稳态 36.5 拍"这种畸变)。
     *    停在 0V 则第一个相位是正常长度。
     * 3) 0V 是电平移位器最线性的点。
     * 代价: 两次测量的 6250 点不再逐点相位对齐。那只影响波形叠图比对,
     * 不影响电流值 —— 式(6) 用的是实测的 Vo1/Vo2。 */
    raw = Adc_ReadRaw();
    guard = PRECOND_GUARD;

    if (raw > code_zero)                    /* 典型: 压在下轨 (raw 满量程) */
    {
        sel_state = SEL_NEG;
        ADG_Select_Negative();              /* V_o 上抬 -> raw 下降 */
        while ((raw > code_zero) && (guard != 0U))
        {
            raw = Adc_ReadRaw();
            guard--;
        }
    }
    else
    {
        sel_state = SEL_POS;
        ADG_Select_Positive();              /* V_o 下压 -> raw 上升 */
        while ((raw < code_zero) && (guard != 0U))
        {
            raw = Adc_ReadRaw();
            guard--;
        }
    }

    /* 守卫超时也照常开窗 —— 不新增超量程上报路径 (输入可控), 这里只作
     * 防挂死保险丝。最坏行程 5.3V: 45nA 输入与参考相抵时净 5nA -> 50V/s
     * -> 106ms, 而 PRECOND_GUARD 约 400ms, 4 倍余量 */
    if (guard == 0U)
    {
        precond_timeout++;
    }
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
    uint16_t raw_ext;

    raw = ReadBoth(&raw_ext);       /* 内置(控制) + 外部(观测) 同拍 */

    if (si_active)
    {
        SmallI_Tick(raw, raw_ext);
        return;
    }

    /* ---- 模式一: 滞回 bang-bang ---- */
    /* 结果未被取走前不再采样, 否则下一个窗口会覆写缓冲头部 */
    if (finish_flag)
    {
        return;
    }

    voltage_buf[sample_index] = raw;
    voltage_buf_ext[sample_index] = raw_ext;
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
        /* 窗口完成: 中断内先算结果; 单次测量下主循环随后会停 TIM6,
         * 完整窗口数据保留在电压缓冲里供 B 指令回传 */
        window_current = Calculate_Current();
        window_current_ext = Calculate_Current_From(voltage_buf_ext);
        finish_flag = 1;            /* 同时封存窗口, 直到消费方取走结果 */
        last_window_count = TOTAL_CYCLE;
        last_m = m_count;
        last_n = n_count;
        sample_index = 0;
        m_count = 0;
        n_count = 0;
    }
    /* DONE/TIMEOUT: 不再采样 */
}

/* 式(6): I = C*(Vo1-Vo2)/(N*T) - (m*I_+ + n*I_-)/N
 * m_count = POS(+50nA) 周期数 配 I_POS, n_count = NEG(-50nA) 周期数 配 I_NEG。
 * 自检 (I=+25nA): 平衡要求 t_pos:t_neg = 1:3 -> m-n = -N/2 -> 补偿项
 * = -50nA*(m-n)/N = +25nA, 与输入相符。交叉配对会得 -25nA, 即补偿项反号、
 * 误差翻倍 —— 曾写在这里的 "POS 对应论文的 n" 就是那个错法。 */
/* 同一套式(6)计算, 但可指定用哪一路缓冲 —— 控制路径(内置)与观测路径
 * (ADS8866) 同 tick 同索引存储, 因此可以逐点互换。
 * 物理实验表征固件里最终结果以 ADS8866 为准 (16 位分辨率, 内置只有 12 位
 * 左移 4 位、等效 16 码), 内置那路作为对照。 */
float Calculate_Current_From(const uint16_t *buf)
{
    float n_total, dc;
    uint32_t m_span, n_span;

    if (sample_index == 0U)
    {
        return 0.0f;
    }

    /* 端点样本之间只有 N-1 个 160us 间隔: 计数按"每拍一次"累加, 而开关判决在
     * 采样之后才生效, 第 j 拍的计数对应的是它之前那一段 [j-1, j] —— 第 0 拍
     * 因此落在窗口外。Current_Start 保证以 SEL_NEG 开窗, 多出来的必定是 NEG。
     * (旧写法按 N 和全部 N 拍算, 结果恒为 I*0.99984 + 8.0pA) */
    m_span = m_count;
    n_span = n_count - 1U;

    /* 端点项直接在码域算。推导:
     *   Vo = (V_ADC_ZERO - c·VREF/65536)/GAIN
     *   Vo1 - Vo2 = (c2 - c1)·VREF/(65536·GAIN)     <- V_ADC_ZERO 在这里约掉了
     *   C·(Vo1 - Vo2) = [C·VREF/(65536·GAIN)]·(c2 - c1) = q·(c2 - c1)
     * 于是省掉一次除法和 V_ADC_ZERO, 而且 q 是可标定量 (cal_coef), 不再依赖
     * C / VREF / GAIN 三个从没测过的标称值 */
    dc = (float)buf[sample_index - 1U] - (float)buf[0];

    n_total = (float)(m_span + n_span);
    n_total = Cal_GetQ() * dc / (n_total * T_INT)
              - ((float)m_span * Cal_GetIPos() + (float)n_span * Cal_GetINeg()) / n_total;

    return n_total;
}

float Calculate_Current(void)
{
    return Calculate_Current_From(voltage_buf);
}

uint8_t Current_WindowFinished(void)
{
    return finish_flag;
}

/* 消费方取走结果: 清标志即解除封存 (CAL_BANG 靠这一步继续下一个窗口) */
void Current_ClearWindowFlag(void)
{
    finish_flag = 0U;
}

float Current_GetWindowResult(void)
{
    return window_current;
}

/* 同一窗口由 ADS8866 算出的结果 (未采样到时返回上一次的; 上电初值为 0) */
float Current_GetWindowResultExt(void)
{
    return window_current_ext;
}

/* 可用点数: 窗口进行中返回实时计数; 完成后 (sample_index 已归零) 返回完整窗口 6250。
 * 判界必须用这个值而不是 sample_index —— B 指令只在 IDLE 态处理, 那时
 * sample_index 恒为 0, 用它将导致回传全 0 (本工程实测踩过) */
uint32_t Current_GetSampleCount(void)
{
    return (sample_index > 0U) ? sample_index : last_window_count;
}

uint16_t Current_GetSample(uint32_t index)
{
    if (index >= Current_GetSampleCount())
    {
        return 0U;
    }
    return voltage_buf[index];
}

/* ---- ADS8866 外部 16 位并行观测 (只读) ---- */
uint16_t Current_GetExtSample(uint32_t index)
{
    if (index >= Current_GetSampleCount())
    {
        return 0U;
    }
    return voltage_buf_ext[index];
}

uint32_t Current_GetExtSampleCount(void)
{
    return Current_GetSampleCount();     /* 内外同拍, 计数一致 */
}




uint32_t Current_GetExtBadRead(void)
{
    return ext_bad_read;
}

uint32_t Current_GetMCount(void)
{
    return last_m;
}

uint32_t Current_GetNCount(void)
{
    return last_n;
}

/* 最近窗口的最小/最大码 = 下/上阈值切换点 (标定用);
 * 主循环内遍历 (与中断写入存在弱竞态, 极值采样在缓冲中停留远长于遍历耗时, 可接受) */
uint16_t Current_GetMinCode(void)
{
    uint16_t vmin = 0xFFFFU;
    uint32_t i;

    for (i = 0U; i < TOTAL_CYCLE; i++)
    {
        if (voltage_buf[i] < vmin)
        {
            vmin = voltage_buf[i];
        }
    }
    return vmin;
}

uint16_t Current_GetMaxCode(void)
{
    uint16_t vmax = 0U;
    uint32_t i;

    for (i = 0U; i < TOTAL_CYCLE; i++)
    {
        if (voltage_buf[i] > vmax)
        {
            vmax = voltage_buf[i];
        }
    }
    return vmax;
}

/* 1s 窗口内积分器电压峰峰值 (bang-bang 摆幅, V) */
float Current_GetSwingVoltage(void)
{
    uint16_t vmin = 0xFFFFU, vmax = 0U;
    uint32_t i, n;
    double dv;

    n = Current_GetSampleCount();       /* 同 GetSample: 不能用 sample_index */
    for (i = 0U; i < n; i++)
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

    if (n == 0U || vmax < vmin)
    {
        return 0.0f;
    }

    dv = (double)(vmax - vmin) * ((double)ADS8866_VREF / 65536.0)
         / (double)LEVELSHIFT_GAIN;
    return (float)dv;
}

/* ============================================================
 * 小电流模式 (1 pA ~ 1 nA): 纯积分 + 斜率
 *
 * 为什么不用模式二的双斜率: 1 pA 时上积相要 t1 = C*dV/I = 200 秒才积得
 * 起 2V, 10 秒预算连一个循环都跑不完 —— 模式二的下限约 6 pA 就是这么来的。
 * 而小电流根本不需要参考电流把它拉回来: 1 pA 积 3 秒才 30mV, 离 ±4.3V
 * 的轨远得很, 直接积、测首尾两点即可。
 *
 *   I = C * (V_start - V_end) / T  =  q * (码_start - 码_end) / T
 *
 * 符号与模式一同一约定 (与 I_POS 同号为正): I_POS 使 V_int 下降、原始码
 * 上升, 所以正电流 -> 码上升 -> V_start > V_end -> I > 0。
 *
 * 积分时间默认 3 秒: 上限由 90 pA 卡出来 (90pA*3s/100pF = 2.7V, 不撞轨),
 * 下限由 1 pA 的分辨率卡出来 (1pA*3s/100pF = 30mV = ADS8866 的 196 码)。
 * 积分中 |dV| 超过 3.5V 就提前收尾并按**实际**时间计算, 免得大电流直接撞轨。
 * ============================================================ */
void SmallI_SetSeconds(uint8_t sec)
{
    if (sec < 1U) { sec = 1U; }
    if (sec > SI_MAX_SEC) { sec = SI_MAX_SEC; }
    si_target_ticks = (uint32_t)sec * SI_TICKS_PER_SEC;
}

uint8_t SmallI_GetSeconds(void)
{
    return (uint8_t)(si_target_ticks / SI_TICKS_PER_SEC);
}

/* 收尾: 由末尾若干拍平均出终点, 再用实际积分时间算电流 */
static void SmallI_Finish(void)
{
    float sum_i = 0.0f, sum_e = 0.0f, t_s;
    uint8_t k;

    for (k = 0U; k < si_ring_n; k++)
    {
        sum_i += (float)si_ring_int[k];     /* 码域, 见 si_start_int 的注释 */
        sum_e += (float)si_ring_ext[k];
    }
    if (si_ring_n > 0U)
    {
        sum_i /= (float)si_ring_n;
        sum_e /= (float)si_ring_n;
    }

    /* I = C·(V_start - V_end)/T = q·(码_start - 码_end)/T */
    t_s = (float)si_ticks * T_INT;
    if (t_s > 0.0f)
    {
        si_cur_int = Cal_GetQ() * (si_start_int - sum_i) / t_s;
        si_cur_ext = Cal_GetQ() * (si_start_ext - sum_e) / t_s;
    }

    window_current     = si_cur_int;
    window_current_ext = si_cur_ext;
    last_window_count  = sample_index;      /* B/X 回传用: 抽点后的点数 */
    last_m = si_ticks;                      /* 借用: 实际积分拍数 (标定/诊断) */
    last_n = 0U;

    si_active = 0U;
    si_done   = 1U;
    HAL_TIM_Base_Stop_IT(&htim6);
    ADG_Disable();
}

/* 每 tick 调用 (由 Current_Process 转发, 已带两路采样) */
static void SmallI_Tick(uint16_t raw, uint16_t raw_ext)
{
    float d;

    /* 末尾平均用的环形 (终点用最后几拍, 单点噪声太大) */
    si_ring_int[si_ring_i] = raw;
    si_ring_ext[si_ring_i] = raw_ext;
    si_ring_i = (uint8_t)((si_ring_i + 1U) % SI_AVG_TICKS);
    if (si_ring_n < SI_AVG_TICKS) { si_ring_n++; }

    si_ticks++;

    /* 抽点存波形: 整段积分过程可见 (B/X 回传), 但缓冲只有 6250 点 */
    if ((sample_index < TOTAL_CYCLE) && ((si_ticks % si_decim) == 0U))
    {
        voltage_buf[sample_index]     = raw;
        voltage_buf_ext[sample_index] = raw_ext;
        sample_index++;
    }

    d = (float)raw - si_start_int;
    if (d < 0.0f) { d = -d; }
    if (d >= (float)SI_RAIL_CODE)
    {
        si_short = 1U;                      /* 电流比预期大: 提前收尾, 用实际 T */
        SmallI_Finish();
        return;
    }

    if (si_ticks >= si_target_ticks)
    {
        SmallI_Finish();
    }
}

/* 把积分器拉到零位 (阻塞)。必须在 TIM6 未跑时调用 ——
 * Adc_ReadRaw 不可重入, 不能与 ISR 里的转换并发。
 *
 * 为什么每个长积分测量都得先复位: 空闲态 ADG 断开, 积分器被输入电流一直
 * 推着走。不复位就开始积分, 起点可能在轨上 —— 那样积分出来的 dV 是假的。
 * 按 5pA 算 1000 秒要漂 50V, 而摆幅只有 ±4.3V, 必然撞轨。 */
void Current_PullToZero(void)
{
    uint16_t raw = Adc_ReadRaw();
    uint32_t guard;

    ThresholdCodes_Init();

    if (raw > code_zero)
    {
        guard = PULLZERO_GUARD;
        ADG_Enable();
        ADG_Select_Negative();
        while ((raw > code_zero) && (guard != 0U)) { raw = Adc_ReadRaw(); guard--; }
    }
    else if (raw < code_zero)
    {
        guard = PULLZERO_GUARD;
        ADG_Enable();
        ADG_Select_Positive();
        while ((raw < code_zero) && (guard != 0U)) { raw = Adc_ReadRaw(); guard--; }
    }
    ADG_Disable();                          /* 断开参考: 只让被测电流积分 */
}

void SmallI_Start(void)
{
    uint16_t raw = 0U, ext = 0U;
    uint32_t i;
    float sum_i = 0.0f, sum_e = 0.0f;

    /* ---- 复位相 (阻塞): 拉到零位, 让两个方向的电流都有满摆幅可用 ---- */
    Current_PullToZero();

    /* ---- 稳定等待 + 起点 (阻塞) ----
     * 断开瞬间有电荷注入台阶, 必须先等它过去再取起点; 否则那一步会被
     * 当成被测电流积出来的电压。 */
    for (i = 0U; i < SI_SETTLE_TICKS; i++) { (void)ReadBoth(&ext); }
    for (i = 0U; i < SI_SETTLE_TICKS; i++)
    {
        raw = ReadBoth(&ext);
        sum_i += (float)raw;                /* 码域, 见 si_start_int 的注释 */
        sum_e += (float)ext;
    }
    si_start_int = sum_i / (float)SI_SETTLE_TICKS;
    si_start_ext = sum_e / (float)SI_SETTLE_TICKS;

    /* ---- 开积分 ---- */
    si_ticks  = 0U;
    si_ring_n = 0U;
    si_ring_i = 0U;
    si_done   = 0U;
    si_short  = 0U;
    si_decim  = (si_target_ticks > TOTAL_CYCLE) ? (si_target_ticks / TOTAL_CYCLE) : 1U;
    sample_index = 0U;
    window_current = 0.0f;
    window_current_ext = 0.0f;

    si_active = 1U;
    HAL_TIM_Base_Start_IT(&htim6);
}

uint8_t SmallI_IsFinished(void) { return si_done; }
uint8_t SmallI_IsShort(void)    { return si_short; }
float   SmallI_GetCurrentInt(void) { return si_cur_int; }
float   SmallI_GetCurrentExt(void) { return si_cur_ext; }
uint32_t SmallI_GetActualTicks(void) { return last_m; }   /* 供结果行报实际积分时间 */

/* ============================================================
 * 模式二: 双斜率硬积分 (电流 < 1nA)
 *  复位相: 拉回零位 (阻塞) -> 上积相: 被测电流积分到 2.0V 记 t1
 *  -> 下放相: 反向标准电流放电回零位记 t2 -> I = I_ref*t2/(t1+t2)
 * ============================================================ */




/* 多循环平均 (10s 预算内至少 1 个有效循环) */

/* ============================================================
 * 底噪/偏置电流测量: ADG 全断 -> 纯积分 1000s,
 * 每秒 1 点存入 voltage_buf, 最小二乘拟合 dV/dt -> I_bias = C*dV/dt
 * ============================================================ */


/* TIM6 中断里由 Current_Process 路由调用 */



/* 结果是否因压轨而不可信 (调用方应据此拒绝上报数值) */





void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
    if (htim->Instance == TIM6)
    {
#if CAL_MODE == CAL_ZERO
        CalMode_Tick();     /* 零位检查: 自己的逐拍累加逻辑 (只读内置 ADC) */
#else
        /* 正常固件与 CAL_BANG 都要走模式一采样 —— CAL_BANG 就是"无输入的
         * 连续模式一窗口", 靠 Current_Process 填电压缓冲、置 finish_flag。
         * (原来的写法是 CAL_NONE 之外全走 CalMode_Tick, 而 CalMode_Tick 里
         *  只有 CAL_ZERO 的代码, 于是 CAL_BANG 永远收不到样本、一行都不出。) */
        Current_Process();
#endif
    }
}
