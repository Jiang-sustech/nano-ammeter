#include "current.h"
#include "adc.h"        /* Adc_ReadRaw: 控制路径 (内置 ADC1 / PA3) */
#include "ads8866.h"    /* ADS8866_ReadRaw: 观测路径 (外部 16 位 / SPI1) */
#include "adg4530.h"
#include "tim.h"
#include "cal_mode.h"
#include "cal_coef.h"   /* Cal_GetQ / Cal_GetIPos / Cal_GetINeg: 可标定的物理常数 */
#include "uart.h"       /* UART_SendString: 手动模式 (M 指令) 的回显 */
#include "main.h"       /* DWT->CYCCNT: 实测手动模式的采样周期 */
#include <math.h>
#include <stdio.h>      /* sprintf */

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
static uint32_t last_window_count;          /* 最近一个完整窗口**存入缓冲**的点数 (抽点后, B 指令取数用) */
static volatile uint32_t last_window_ticks; /* 最近一个完整窗口的**实际拍数** (= m+n, 结果行的 T= 用它) */
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

/* 阻塞 n 微秒 (DWT 忙等, 80MHz)。中断里不能用 HAL_Delay (SysTick 被饿死) */
static void DelayUs(uint32_t us)
{
    uint32_t t0 = DWT->CYCCNT;
    while ((DWT->CYCCNT - t0) < (us * 80U)) { }
}

static uint16_t ReadBoth(uint16_t *ext_out)
{
    uint32_t c0;

#if USE_INTERNAL_ADC
    uint16_t r_int;
    c0 = DWT->CYCCNT;
    r_int = Adc_ReadRaw();
    t_int_ns = (DWT->CYCCNT - c0) * 100U / 8U;      /* 12.5ns/周期 -> ns */
    c0 = DWT->CYCCNT;
    uint16_t r_ext = ADS8866_ReadRaw();
#else
    /* 试验期: 内置路不读。
     *
     * **但 CONVST 不能紧贴节拍边界** —— 实测 (6 窗 x 6250 拍):
     *   去掉内置那 8.2us 的间隔后, 外部路的 0xFFFF 坏读从每窗 ~0 涨到
     *   平均 31 次 (186 次/6 窗, 0.5%); 而**首拍的坏读率高达 1/6 = 17%**,
     *   比平均高 33 倍 —— 每次都栽在第一次。
     * 怀疑 CONVST 边沿撞上了 TIM6 中断入口附近的活动。
     * 这里补一个预延时把它推回原来的时间位置附近, 验证这个假设。
     * (注意: 这**不是**在读内置路 —— 内置 ADC 完全不参与 ✓) */
    t_int_ns = 0U;
    DelayUs(EXT_PREDELAY_US);
    c0 = DWT->CYCCNT;
    uint16_t r_ext = ADS8866_ReadRaw();
#endif
    t_ext_ns = (DWT->CYCCNT - c0) * 100U / 8U;
    if (r_ext == 0xFFFFU) { ext_ffff_cnt++; }

#if USE_INTERNAL_ADC
    /* ---- 两路都在: 用交叉判据区分"真撞轨"与"真断线" ---- */
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
    return r_int;                   /* 控制值 = 内置路 */
#else
    /* ---- 试验期: 内置路不读, 控制值 = 外部读数 ---- */
    /* 顺序变了: 原来"先内后外"是因为控制判决必须基于本 tick **最早**那一刻;
     * 现在只剩一路, 那个理由不存在了 —— 控制判决点自然就是外部读数的时刻。
     * (代价: 判决点比原来晚 ~8us; 理论上均匀偏移、无系统误差, 待实测) */
    /* 坏读判据退化为单路版: 原靠两路交叉(见 USE_INTERNAL_ADC 注释②), 现在
     * 只能按外部值判, 0x0000/0xFFFF 都记坏读。代价是输入撞轨时会把有效读数
     * 记成坏读(ERR 虚高) —— 但**只影响 ERR 这个诊断计数, 不影响测量数值**。 */
    if ((r_ext == 0xFFFFU) || (r_ext == 0x0000U))
    {
        ext_bad_read++;
    }

    *ext_out = r_ext;
    return r_ext;                   /* 控制值 = 外部路 */
#endif
}

/* 读一次"控制值" —— 按 USE_INTERNAL_ADC 决定用哪一路。
 * 给那些**自己直接调 Adc_ReadRaw()** 的场合用 (PullToZero / CAL_ZERO 等),
 * 这样切宏时它们会跟着切, 不必逐处改 —— 少一处漏改就少一个"一半内置一半外部"
 * 的诡异状态。 */
/* ---- 时序实测 (2026-09-13 加) ----
 * 之前 8.2us / 10.6us 都是按**配置推算**的, 从没实测过。而它直接决定一件事:
 * ADS8866 的转换时间是不是 <= 驱动里 `DWT_DelayUs(9)` 那个**盲等** ——
 * 如果不够, SPI 读回来的是**上一次的样本**, 一个隐性的滞后一拍, 而且没人知道。
 * ext_ffff_cnt: 外部路返回 0xFFFF 的次数 —— 它既是"转换未完成"的特征,
 * 也是合法满量程码, 分开数才知道到底是哪种。 */
volatile uint32_t t_int_ns;         /* 内置路单次读取耗时 (ns) */
volatile uint32_t t_ext_ns;         /* 外部路单次读取耗时 (ns) */
volatile uint32_t ext_ffff_cnt;     /* 外部路返回 0xFFFF 的累计次数 */

uint16_t Current_ReadControl(void)
{
#if USE_INTERNAL_ADC
    return Adc_ReadRaw();
#else
    return ADS8866_ReadRaw();
#endif
}

static volatile float window_current;       /* 窗口结果, 由**内置 ADC** 算出 (控制路径) */
static volatile float window_current_ext;   /* 同一窗口, 由 **ADS8866** 算出 (观测路径) */

#define SEL_POS 0U
#define SEL_NEG 1U
static volatile uint8_t sel_state;



/* ---- 测量窗口 (2026-09-13 统一为一条路) ----
 *
 * 全量程只有**一套**测量逻辑: 参考在 ±I_ref 之间 bang-bang, 窗口结束时用
 *
 *     I = q·Δc/(N·T)  -  (m·I₊ + n·I₋)/N
 *
 * 算结果 —— 这个式子本来就与窗口长度无关 (分母是 m+n, 不是硬编码的拍数),
 * 所以"拉长窗口"只是改一个变量, 不需要第二套计算。
 *
 * 窗口长度按电流自适应:
 *     |I| >= 1 nA  ->   1 秒   (WINDOW_SHORT_TICKS)
 *     |I| <  1 nA  ->  10 秒   (WINDOW_LONG_TICKS)   <- 小电流端靠时间换信噪比
 *
 * **原来的"小电流模式"(参考断开 + 纯积分) 已删除。** 它是第二套逻辑, 符号
 * 约定各自实现了一遍, 结果就分叉了 —— 2026-09-13 实测抓到一次符号反了
 * (码域与电压域差一层反相)。一条路就不存在分叉。
 *
 * 波形缓冲不能跟着窗口涨: 10s = 62500 拍, 两路要 250KB, 而 L431 只有 64KB。
 * 所以按 window_decim 抽点存, 只供 B/X 回传。**结果计算不用这个缓冲** ——
 * 端点码单独记 (v_last_*), 与抽点无关。 */
#define WINDOW_SHORT_TICKS  TOTAL_CYCLE         /* 6250 拍 = 1 s */
#define WINDOW_LONG_TICKS   (TOTAL_CYCLE * 10U) /* 62500 拍 = 10 s */

static volatile uint32_t window_ticks = WINDOW_SHORT_TICKS;  /* 本次窗口长度 (拍) */
static uint32_t          window_decim;                       /* 抽点存波形的间隔 */
static volatile uint16_t v_last_int, v_last_ext;             /* 窗口最后一拍的码 */

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
/* ============================================================
 * 拉回相 (现在在 ISR 里做)
 *
 * ---- 为什么搬进 ISR ----
 * 拉到零的"最后一拍"要直接当窗口的起点 V_01 用。只有在 ISR 里采, 它才和
 * 后面的采样点落在**同一套节拍网格**上。
 *
 * 若在主循环里拉 (旧做法), V_01 到采样点 0 的间隔是
 *       160µs + (读一次 ADC 的耗时) + (启动 TIM6 的代码延时)
 * 比整一拍多出后面两项 —— 那一截是实打实的参考注入, 而它不属于任何一拍,
 * 折算约 0.5~1 pA 的恒定误差 (还依赖编译结果, 说不清来源)。
 *
 * ---- 搬进来之后节拍是这样对齐的 ----
 *       节拍 0..p        拉回相 (不计数、不进缓冲)
 *       节拍 p           V_01 -> v01_int / v01_ext
 *       节拍 p+1..p+N    窗口 -> voltage_buf[0..N-1], 每拍计一次数
 *
 * 窗口两端 (V_01 与最后一个窗口采样) 都是 ISR 采样, 偏移完全相同;
 * 而计数覆盖的正好是它们之间的 N 个间隔。于是:
 *
 *       n_total = m_count + n_count = N        <- 不用减一
 *       窗口时长 = N × 160µs = 1.000 s 整       <- 不用再凑
 *
 * 论文的 (m+n) 形式因此**精确成立**, 那个"多一拍"的纠缠彻底消失。
 * (旧设计窗口是 N-1 个间隔 = 0.99984 s, 论文形式差 0.99984 与 8.04 pA)
 * ============================================================ */

/* 拉回相守卫 (拍数): 一拍 160µs, 2500 拍 = 400ms。
 * 最坏行程 5.3V (电平移位饱和轨 -> 0V): 净 50nA -> 500V/s -> 10.6ms;
 * 输入 45nA 与参考相抵时净 5nA -> 50V/s -> 106ms。400ms 有 3.8 倍余量。
 * 这是防挂死保险丝, 不是量程判据 —— 超时也照常开窗 */
#define PRECOND_TICKS  2500U

static volatile uint8_t  precond_active;    /* 1 = ISR 正在拉回, 尚未开窗 */
static uint8_t           precond_ready;     /* 0 = 还没记下初始符号 */
static uint8_t           precond_pos;       /* 1 = 上一拍 raw > code_zero */
static uint32_t          precond_ticks;

static uint16_t          v01_int, v01_ext;  /* 拉回最后一次采样 = 窗口起点 */

/* 拉回相未能在守卫内到位次数 (SWD 按符号读, 正常恒为 0) */
volatile uint32_t precond_timeout;

/* 收官: 本拍采样作为 V_01, 从下一拍起开窗 */
static void Precond_Finish(uint16_t raw, uint16_t raw_ext)
{
    v01_int = raw;
    v01_ext = raw_ext;
    precond_active = 0U;
    sample_index = 0U;                      /* 窗口从 voltage_buf[0] 开始 */
}

/* 拉回相每拍 (由 Current_Process 转发, 已带两路采样) */
static void Precond_Tick(uint16_t raw, uint16_t raw_ext)
{
    uint8_t pos = (uint8_t)((int32_t)raw > (int32_t)code_zero);

    precond_ticks++;

    /* 极性的设置与"是否收官"无关: 它决定的是**下一段** [tick_k, tick_k+1]
     * 用哪个参考, 也就是下一拍 ISR 要记的那一拍。收官那一拍也必须设 ——
     * 否则开窗第一段的极性会停在上上拍, 记错。 */
    if (pos)
    {
        sel_state = SEL_NEG;                /* raw 偏高 -> V_o 上抬 -> raw 下降 */
        ADG_Select_Negative();
    }
    else
    {
        sel_state = SEL_POS;                /* raw 偏低 -> V_o 下压 -> raw 上升 */
        ADG_Select_Positive();
    }

    if ((precond_ready != 0U) && (pos != precond_pos))
    {
        Precond_Finish(raw, raw_ext);       /* 与上一拍异号: 已越过零点 */
        return;
    }

    if (precond_ticks >= PRECOND_TICKS)
    {
        precond_timeout++;                  /* 防挂死保险丝: 超时也照常开窗 */
        Precond_Finish(raw, raw_ext);
        return;
    }

    /* 首拍没有"上一拍"可比, 只记符号 —— 否则会把起始点自己判成"已越过" */
    precond_ready = 1U;
    precond_pos   = pos;
}

void Current_Start(void)
{
    m_count = 0;
    n_count = 0;
    sample_index = 0;
    last_window_count = 0U;
    last_m = 0U;
    last_n = 0U;
    finish_flag = 0;

    /* 抽点间隔由窗口长度决定 —— 缓冲只有 TOTAL_CYCLE 点, 窗口 10 秒就得抽 10 倍。
     * window_ticks 本身**不在这里重置** —— 它由调用方 (MeasurementState) 决定,
     * 换窗口重测时要保留。只在首次进 MODE1 时被设成短窗口。 */
    window_decim = (window_ticks > TOTAL_CYCLE) ? (window_ticks / TOTAL_CYCLE) : 1U;
    v_last_int = 0U;
    v_last_ext = 0U;

    ThresholdCodes_Init();

    /* 拉回相交给 ISR 做 (理由见上面的长注释)。这里只摆状态 —— 不阻塞,
     * 主循环立刻返回, 调用方接着 HAL_TIM_Base_Start_IT */
    precond_active = 1U;
    precond_ready  = 0U;
    precond_pos    = 0U;
    precond_ticks  = 0U;

    ADG_Enable();
    sel_state = SEL_POS;        /* 先给个确定极性; ISR 第一拍就按实际偏差改 */
    ADG_Select_Positive();
}

/* 停止测量: 停 TIM6 + 关断 ADG (空闲状态不注入参考电流) */
void Current_Stop(void)
{
    HAL_TIM_Base_Stop_IT(&htim6);
    ADG_Disable();
    /* 拉回相的挂起标志也要清 —— 否则启表失败时 (见 MeasurementState_StartCommand)
     * 它悬在 1, 下次 Current_Process 会先跑拉回相而不是开窗 */
    precond_active = 0U;
}

/* TIM6 中断每 160us 调用一次 (模式一/模式二/底噪模式共用的节拍) */
void Current_Process(void)
{
    uint16_t raw;
    uint16_t raw_ext;

    raw = ReadBoth(&raw_ext);       /* 内置(控制) + 外部(观测) 同拍 */

    /* ---- 拉回相 (把积分器拉到接近 0V, 见 Precond_Tick 的注释) ----
     * 这一相不计数、不进缓冲; 它的最后一拍就是窗口起点 V_01 */
    if (precond_active)
    {
        Precond_Tick(raw, raw_ext);
        return;
    }

    /* ---- 测量窗口: 滞回 bang-bang ---- */
    /* 结果未被取走前不再采样, 否则下一个窗口会覆写缓冲头部 */
    if (finish_flag)
    {
        return;
    }

    /* 抽点存波形 —— 只供 B/X 回传, **结果计算不看它** (端点码走 v_last_*)。
     * 10s 窗口抽 10 倍, 所以缓冲始终覆盖整段且不超过 TOTAL_CYCLE 点。 */
    if ((window_decim <= 1U) || (((m_count + n_count) % window_decim) == 0U))
    {
        if (sample_index < TOTAL_CYCLE)
        {
            voltage_buf[sample_index] = raw;
            voltage_buf_ext[sample_index] = raw_ext;
            sample_index++;
        }
    }
    v_last_int = raw;               /* 每拍都更新 —— 窗口真正的最后一拍 */
    v_last_ext = raw_ext;

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

    if ((m_count + n_count) >= window_ticks)
    {
        /* 窗口完成 (长度由 window_ticks 决定, 1s 或 10s)。中断内先算结果;
         * 单次测量下主循环随后会停 TIM6, 抽点后的波形留在缓冲里供 B/X 回传 */
        window_current     = Calculate_Current_From(v01_int, v_last_int);
        window_current_ext = Calculate_Current_From(v01_ext, v_last_ext);
        finish_flag = 1;            /* 同时封存窗口, 直到消费方取走结果 */
        last_window_count = sample_index;
        last_window_ticks = m_count + n_count;
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
float Calculate_Current_From(uint16_t c_first, uint16_t c_last)
{
    float n_total, dc;

    n_total = (float)(m_count + n_count);
    if (n_total <= 0.0f)
    {
        return 0.0f;
    }

    /* ---- 为什么分母是 (m+n), 不用减一 ----
     * 窗口两端都是 ISR 采样: c_first 是拉回相最后一拍, c_last 是窗口最后一拍。
     * 而计数覆盖的正好是它们之间的那些间隔 —— ISR 的采样和参考切换落在同一套
     * 节拍网格上, 一一对应, 没有"多出来的一拍"需要判断归属。
     * (旧设计窗口两端是窗口内的采样点, 只有 N-1 个间隔而计数有 N 拍, 才要减一,
     *  而且减在哪个计数器上还依赖开窗极性 —— 那个纠缠随这次重构一起消失了)
     *
     * 分母是 m+n 而**不是任何硬编码的拍数** —— 所以这个式子对 1s 和 10s 窗口
     * 同样成立, 换窗口长度不需要换公式。这是"全量程一套逻辑"的支点。 */
    dc = (float)c_last - (float)c_first;

    /* 端点项直接在码域算。推导:
     *   Vo = (V_ADC_ZERO - c·VREF/65536)/GAIN
     *   Vo1 - Vo2 = (c2 - c1)·VREF/(65536·GAIN)     <- V_ADC_ZERO 在这里约掉了
     *   C·(Vo1 - Vo2) = [C·VREF/(65536·GAIN)]·(c2 - c1) = q·(c2 - c1)
     * 于是省掉一次除法和 V_ADC_ZERO, 而且 q 是可标定量 (cal_coef), 不再依赖
     * C / VREF / GAIN 三个从没测过的标称值。
     *
     * **注意码域与电压域差一层反相** (V_adc = 1.55 - 0.33*V_o, 积分器电压下降
     * 时码值反而上升), 所以是 (c_last - c_first) 而不是反过来 —— 2026-09-13
     * 在小电流模式那边正是漏了这一层, 实测抓到符号反了。 */
    return Cal_GetQ() * dc / (n_total * T_INT)
           - ((float)m_count * Cal_GetIPos() + (float)n_count * Cal_GetINeg()) / n_total;
}

/* 窗口起点 (拉回相最后一拍的采样码), 供结果行的 RAW 段上报 */
uint16_t Current_GetFirstCode(void)    { return v01_int; }
uint16_t Current_GetFirstCodeExt(void) { return v01_ext; }

/* 窗口最后一拍 —— 每拍都更新, 与抽点存缓冲无关 (10s 窗口下缓冲是抽点过的,
 * 拿 buf[末尾] 当终点会错) */
uint16_t Current_GetLastCode(void)    { return v_last_int; }
uint16_t Current_GetLastCodeExt(void) { return v_last_ext; }

/* 窗口长度 (拍)。短 6250 拍 = 1s, 长 62500 拍 = 10s。
 * 换长度必须在 Current_Start() **之前**调用 —— Current_Start 会按它算抽点间隔。 */
void Current_SetWindowTicks(uint32_t ticks)
{
    window_ticks = (ticks < 1U) ? 1U : ticks;
}

/* 最近一个窗口的**实际拍数** —— 结果行的 T= 用它。
 * (不能用 Current_GetSampleCount(): 那是抽点后的**存储点数**, 10s 窗口下只有
 *  6250, 拿它算 T= 会少报 10 倍) */
uint32_t Current_GetWindowTicks(void)
{
    return (last_window_ticks > 0U) ? last_window_ticks : window_ticks;
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
    uint32_t i, n;

    /* 用 Current_GetSampleCount() 而不是 TOTAL_CYCLE —— 缓冲未必填满:
     * 长窗口下是**抽点**存的(6250 点覆盖 62500 拍), M3 手动模式只填 400 点,
     * 其余格子是**上一个窗口的残留**。按 TOTAL_CYCLE 扫会返回过期极值。
     * 注: 长窗口下这个极值只是抽点子集上的**估计** —— 抽点间隔 10 拍, 而
     * dV/拍 = I/C*T = 50nA/100pF*160us = 0.08V, 最多偏离真切换点 9 拍 ~0.7V
     * (摆幅 8.6V 的 8%)。 */
    n = Current_GetSampleCount();
    for (i = 0U; i < n; i++)
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
    uint32_t i, n;

    /* 同 GetMinCode: 必须用 Current_GetSampleCount() (缓冲未必填满, 见那里的注释) */
    n = Current_GetSampleCount();
    for (i = 0U; i < n; i++)
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


/* 把积分器拉到零位 (阻塞)。必须在 TIM6 未跑时调用 ——
 * Adc_ReadRaw 不可重入, 不能与 ISR 里的转换并发。
 *
 * 注: 模式一不用这个 —— 它的拉回相在 ISR 里 (Precond_Tick), 因为要拿"拉回的
 * 最后一拍"当窗口起点 V_01, 必须在同一套节拍网格上。这个阻塞版是给那些
 * **不需要 V_01** 的场合用的: 小电流模式、CAL_ZERO、底噪 —— 它们都是"拉回后
 * 断开参考做纯积分", 起点自己另测。
 *
 * 为什么每个长积分测量都得先复位: 空闲态 ADG 断开, 积分器被输入电流一直
 * 推着走。不复位就开始积分, 起点可能在轨上 —— 那样积分出来的 dV 是假的。
 * 按 5pA 算 1000 秒要漂 50V, 而摆幅只有 ±4.3V, 必然撞轨。 */
void Current_PullToZero(void)
{
    /* 用 Current_ReadControl() 而不是直接调 Adc_ReadRaw() —— 这样切
     * USE_INTERNAL_ADC 时这里会跟着切, 不会出现"测量走外部、复位走内置"
     * 那种一半一半的状态。
     * 注: 守卫的时间预算随之变化 —— PULLZERO_GUARD=10000 次, 内置每次约 8us
     * (80ms 上限), 外部每次约 11us (110ms 上限), 都远大于最坏行程 10.6ms。 */
    uint16_t raw = Current_ReadControl();
    uint32_t guard;

    ThresholdCodes_Init();

    if (raw > code_zero)
    {
        guard = PULLZERO_GUARD;
        ADG_Enable();
        ADG_Select_Negative();
        while ((raw > code_zero) && (guard != 0U)) { raw = Current_ReadControl(); guard--; }
    }
    else if (raw < code_zero)
    {
        guard = PULLZERO_GUARD;
        ADG_Enable();
        ADG_Select_Positive();
        while ((raw < code_zero) && (guard != 0U)) { raw = Current_ReadControl(); guard--; }
    }
    ADG_Disable();                          /* 断开参考: 只让被测电流积分 */
}

/* ============================================================
 * 手动模式 (串口 M 指令) —— 阻塞式连续采样, 不走 TIM6 网格
 *
 *   M0  拉零 -> 断开参考(EN=0) -> 连采 -> 录指数衰减 => 量 tau = R*C_p
 *   M1  拉零 -> 固定 +5V 参考  -> 连采 -> 录线性斜坡 => q 的开环标定
 *   M2  拉零 -> 固定 -5V 参考  -> 连采 -> 同上, 另一极性
 *
 * 为什么值得单独做一个模式:
 *
 * (1) 采样率。TIM6 网格是 160us/点, 而 tau 预计在 ms 量级 —— 在 160us
 *     网格上只能采到 6 点/tau, 太粗。这里在主循环里阻塞连采, 每点约 19us
 *     (内置 ADC 8.2us + ADS8866 约 11us), 快 8 倍; 6250 点覆盖约 119ms,
 *     够看清 tau 到 10ms 量级, 也够判"tau < 一点"这个下界。
 *
 * (2) q 的开环标定**必须固定参考极性**。原设计想拿"参考断开"当 0A 参考,
 *     但 EN=0 是 Hi-Z 不是 0V: 那个浮空节点的寄生电容 C_p 会经 100MΩ 泄放,
 *     向积分器注入一个指数衰减的暂态电荷 Q = C_p*5V (C_p=10pF 时约 3277 码,
 *     占 50nA 积 8ms 信号的 12.5%)。固定极性则全程不进 EN=0, 完全避开。
 *
 * 采样直接写进 voltage_buf / voltage_buf_ext, 因此 **B / X 指令零成本回传**,
 * 不需要写任何新的回传代码。上位机拿到波形后:
 *   M0    -> 拟合"台阶 + 指数 + 线性漂移" -> tau 与注入电荷 Q
 *   M1/M2 -> 拟合直线 -> 斜率即 I*q/T, 于是 q = I * T / Δc
 *
 * 整段阻塞约 130ms, 只在 IDLE 由主循环调用 —— 那里 TIM6 必停, 满足
 * Adc_ReadRaw 不可重入的约束 (见 Current_PullToZero 的注释)。
 * ============================================================ */
#define MANUAL_POINTS   TOTAL_CYCLE     /* 6250 点; 受 voltage_buf 尺寸限制 */

/* 采样周期 (us)。back-to-back 跑 ReadBoth 的天然节拍约 23.6us (内置 8.2 +
 * ADS8866 约 15), 余下的用 DWT 补齐到 MANUAL_TICK_US。
 * 为什么要放慢: 快采时 ADS8866 的 SPI 跑得频繁, 实测噪声从模式一的 ~48 码
 * 涨到 ~830 码 —— 怀疑是 SPI 耦合进模拟前端。放慢到 50us 可验证这一点。 */
#define MANUAL_TICK_US      50U
#define MANUAL_CYCLES_PER_US 80U        /* 80 MHz 主频 */

/* M3 (高阻 <-> +5V 交替): 每多少点切换一次, 以及总点数。
 * 总点数受**积分器摆幅**限制: +5V 经 100MΩ 注 50nA, 在 100pF 上是 0.5V/ms,
 * 可用摆幅约 8.6V -> 接通的累计时间不能超约 17ms。50% 占空比下
 * 总时长 <= 34ms, 取 400 点 (20ms, 其中接通 10ms = 5V) 留一倍余量。 */
#define MANUAL_ALT_EVERY    50U
#define MANUAL_M3_POINTS    400U

static uint8_t  manual_sub;                     /* 最近一次子模式 (0/1/2/3) */
static uint32_t manual_tick_ns;                 /* 实测每点周期 (ns), 供上位机建时间轴 */
static uint32_t manual_npts;                    /* 本次实际采了多少点 */
static uint16_t manual_first_int, manual_first_ext;   /* 第一点 = 断开/接通那一刻 */

void Current_ManualRun(uint8_t sub)
{
    uint32_t i, t0, cycles, npts;
    uint16_t ext = 0U;

    /* IDLE 下 TIM6 本就没跑, 这里防御性再停一次并确保参考断开 */
    Current_Stop();

    /* ---- 复位: 拉零。Current_PullToZero() 末尾自带 ADG_Disable(),
     *      所以 M0 要观测的"断开瞬间"就是它返回的那一刻。 ---- */
    Current_PullToZero();

    /* ---- 按子模式设参考状态 ----
     * 从 PullToZero 返回到第一次采样之间要**尽量短且固定**: M0 观测的正是
     * 那一刻注入的暂态, 间隔越长看到的衰减越靠后, 拟合出的幅度就越小。 */
    if (sub == 1U)      { ADG_Enable(); ADG_Select_Positive(); }
    else if (sub == 2U) { ADG_Enable(); ADG_Select_Negative(); }
    /* sub == 0: 保持断开, 不碰 ADG */

    npts = (sub == 3U) ? MANUAL_M3_POINTS : MANUAL_POINTS;

    /* ---- 连续采样 (阻塞, 周期由 DWT 补齐到 MANUAL_TICK_US) ---- */
    sample_index = 0U;
    t0 = DWT->CYCCNT;
    for (i = 0U; i < npts; i++)
    {
        uint32_t t_slot = DWT->CYCCNT;
        uint16_t raw;

        /* M3: 每 MANUAL_ALT_EVERY 点在"高阻"与"+5V 接通"之间切换。
         * 从高阻起步, 保证第一次切换是"接通"(电压正常上升), 第二次才是
         * 我们要看的"断开 -> 注入台阶"。 */
        if ((sub == 3U) && ((i % MANUAL_ALT_EVERY) == 0U))
        {
            if ((((i / MANUAL_ALT_EVERY) & 1U) == 0U))
            {
                ADG_Disable();                          /* 高阻 */
            }
            else
            {
                ADG_Enable();
                ADG_Select_Positive();                  /* +5V */
            }
        }

        raw = ReadBoth(&ext);
        voltage_buf[i]     = raw;
        voltage_buf_ext[i] = ext;

        /* 补齐到 MANUAL_TICK_US —— DWT 忙等, 中断里不能用 HAL_Delay */
        while ((DWT->CYCCNT - t_slot) < (MANUAL_TICK_US * MANUAL_CYCLES_PER_US))
        {
        }
    }
    cycles = DWT->CYCCNT - t0;

    manual_first_int = voltage_buf[0];
    manual_first_ext = voltage_buf_ext[0];
    manual_sub       = sub;
    manual_npts      = npts;
    /* 每点周期 ns = 周期数 * 12.5ns / 点数, 写成整数式 = cycles*100/(N*8) */
    manual_tick_ns = (uint32_t)(((uint64_t)cycles * 100ULL) /
                                ((uint64_t)npts * 8ULL));

    ADG_Disable();                  /* 采完回到"空闲不注入参考" */

    /* ---- 交给 B / X 回传 ----
     * sample_index 归零, 于是 Current_GetSampleCount() 走 last_window_count
     * 那条分支 (IDLE 下 sample_index 恒为 0, 直接读它会回传全 0)。 */
    sample_index      = 0U;
    last_window_count = npts;
    last_m = 0U;
    last_n = 0U;
}

/* M 指令处理 (整行)。只在 IDLE 由 main.c 调用 —— 那里的闸门已保证。 */
void Current_ManualLine(const char *line)
{
    char d = line[1];
    char out[96];

    if ((d != '0') && (d != '1') && (d != '2') && (d != '3'))
    {
        UART_SendString("MAN ERR 格式: M0(断开) / M1(+5V) / M2(-5V) / M3(高阻<->+5V交替)\r\n");
        return;
    }

    Current_ManualRun((uint8_t)(d - '0'));

    sprintf(out, "MAN DONE sub=%u N=%lu tick_ns=%lu INT1=%u EXT1=%u\r\n",
            (unsigned)manual_sub,
            (unsigned long)manual_npts,
            (unsigned long)manual_tick_ns,
            (unsigned)manual_first_int,
            (unsigned)manual_first_ext);
    UART_SendString(out);
}

/* 2026-09-13 清理: 这里原先挂着三段"死注释" —— "模式二 双斜率硬积分"、
 * "底噪/偏置电流测量"、"结果是否因压轨而不可信" —— 对应的实体代码早就删了,
 * 只剩标题悬在空中, 容易让人以为还有实现。一并清除。
 * 双斜率废弃的理由见 current.h 顶部。 */

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
