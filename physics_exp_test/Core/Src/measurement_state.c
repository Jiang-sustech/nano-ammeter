#include "measurement_state.h"
#include "current.h"
#include "tim.h"
#include <math.h>

/* ============================================================
 * 测量状态机 (单次测量语义):
 *   IDLE -> (S 指令) -> 窗口(1s) -> |I| >= 1nA: 出结果, 回 IDLE
 *                                -> |I| <  1nA: 换 10s 窗口重测一次 -> 出结果
 *   一次指令 = 一次完整测量, 测完停住 (TIM6 停 + ADG 关断);
 *   无取消/无连续模式 (越简单越可靠), 测量期间忽略一切指令
 *
 * 2026-09-13 重构: **全量程只有一套测量逻辑** (电荷平衡, 见 current.c 的
 * 测量窗口注释)。原来 |I|<1nA 会切到独立的"小电流模式"(参考断开+纯积分),
 * 那是第二套逻辑 —— 符号约定各自实现一遍, 结果就分叉了 (实测抓到一次符号反)。
 * 现在小电流端只是**把窗口从 1s 拉到 10s**, 公式一个字都不用改
 * (分母是 m+n, 与窗口长度无关)。
 *
 * 状态也少了一个: 换窗口后仍然停在 MODE1, 只是窗口长了。用一个标志位
 * (long_tried) 保证只换一次, 不会来回切。
 * ============================================================ */

#define DIRECT_MEASURE_THRESHOLD 1e-9f   /* 1nA: 低于则换 10s 窗口重测 */

static MeasurementState measurement_state;
static float measurement_result;            /* 内置 ADC 算出的结果 */
static float measurement_result_ext;        /* ADS8866 算出的结果 (表征以它为准) */
static uint32_t measurement_ticks;          /* 本次窗口的实际拍数 (结果行的 T=) */
static uint8_t result_is_longw;             /* 结果是否来自 10s 长窗口 (结果行 MODE=2) */
static uint8_t result_is_timeout;
static uint8_t long_tried;                  /* 本次测量已经换过 10s 窗口 */
static uint32_t precond_to_at_start;        /* 本次测量开始时的 precond_timeout 快照 */
static uint32_t forced_window_ticks;        /* 0 = 自动; 否则每次 S 都用这个窗口长度 */

void MeasurementState_SetForcedWindowTicks(uint32_t ticks)
{
    forced_window_ticks = ticks;
}

uint32_t MeasurementState_GetForcedWindowTicks(void)
{
    return forced_window_ticks;
}

void MeasurementState_Init(void)
{
    measurement_state = MEASUREMENT_STATE_IDLE;
    measurement_result = 0.0f;
    measurement_result_ext = 0.0f;
    measurement_ticks = 0U;
    result_is_longw = 0U;
    result_is_timeout = 0U;
    long_tried = 0U;
}

void MeasurementState_StartCommand(void)
{
    if (measurement_state != MEASUREMENT_STATE_IDLE)
    {
        return;
    }

    /* 每次测量都从短窗口起步 —— 由它决定要不要换长的。
     * 必须在 Current_Start() 之前设: Start 按窗口长度算抽点间隔。
     *
     * 但**强制窗口优先**: 上位机设过就用它, 且 long_tried 直接置 1 让
     * 后面那段自动切换不生效 (否则 <1nA 时会把 50s 窗口换成 10s)。 */
    long_tried = (forced_window_ticks != 0U) ? 1U : 0U;
    precond_to_at_start = precond_timeout;   /* 快照, 用于事后判断本次有没有超时 */
    Current_SetWindowTicks((forced_window_ticks != 0U) ? forced_window_ticks
                                                       : CURRENT_WIN_SHORT_TICKS);

    Current_Start();
    if (HAL_TIM_Base_Start_IT(&htim6) != HAL_OK)
    {
        /* 启表失败必须把参考关掉。Current_Start() 已经 ADG_Enable() 并把拉回相
         * 挂给 ISR 了 (precond_active=1), 而 TIM6 没跑起来 ISR 根本不会执行 ——
         * 参考电流会一直注入、把积分器推到轨上待着。
         * "空闲不注入参考"是最高优先级的安全约定, 不能靠"这条分支不会走到" */
        Current_Stop();
        return;
    }
    measurement_state = MEASUREMENT_STATE_MODE1;
}

/* 单次测量收尾: 停 TIM6 + 关 ADG + 回 IDLE (结果保留在内部供显示/查询) */
static void MeasurementState_Finish(void)
{
    Current_Stop();     /* 停 TIM6 + 关断参考电流 (安全最高优先级) */
    measurement_state = MEASUREMENT_STATE_IDLE;
}

MeasurementProcessResult MeasurementState_Process(void)
{
    float current;
    float current_ext;
    float judge;

    switch (measurement_state)
    {
    case MEASUREMENT_STATE_IDLE:
    default:
        return MEASUREMENT_PROCESSING;

    case MEASUREMENT_STATE_MODE1:
        /* 窗口结果 (中断内算好) */
        if (!Current_WindowFinished())
        {
            return MEASUREMENT_PROCESSING;
        }
        /* 顺序要紧: 先取结果, 再停表, 最后才解除封存 —— 若先解除, 到停表
         * 之间落下一个 TIM6 中断, 会往电压缓冲头部追加窗口外样本 */
        current = Current_GetWindowResult();
        current_ext = Current_GetWindowResultExt();
        HAL_TIM_Base_Stop_IT(&htim6);
        Current_ClearWindowFlag();

        /* 结果行的 T= 用**实际拍数**, 不是 Current_GetSampleCount() ——
         * 后者是抽点后存入缓冲的点数, 10s 窗口下只有 6250, 拿它算 T= 会少报
         * 10 倍 (这正是"注释写拍、实际是点数"那类老坑的新形态) */
        measurement_ticks = Current_GetWindowTicks();

        /* 判据用**外部路 (X=)**, 不用内置路 (I=) —— 2026-09-13 用户裁定:
         * "代码应该和内置 ADC 完全无关"。理由: 拿内置路判的话, 1nA 边界上两路
         * 分歧(内置 1.01 / 外部 0.99)会让**同一个电流落到不同的窗口长度**,
         * 数据前后不一致。而 README 与 main.c 都写明"表征以外部路为准"。
         * 外部路无效(NaN/0)时才退回内置, 免得判据整个失效。 */
        judge = (isfinite(current_ext) && (current_ext != 0.0f)) ? current_ext : current;

        if ((fabsf(judge) < DIRECT_MEASURE_THRESHOLD) && (long_tried == 0U))
        {
            /* <1nA: 换 10s 窗口重测一次。
             * **同一条测量逻辑**, 只是窗口更长 —— 公式不用改。 */
            long_tried = 1U;
            Current_SetWindowTicks(CURRENT_WIN_LONG_TICKS);
            Current_Start();
            if (HAL_TIM_Base_Start_IT(&htim6) != HAL_OK)
            {
                Current_Stop();
                measurement_state = MEASUREMENT_STATE_IDLE;
                return MEASUREMENT_PROCESSING;
            }
            measurement_state = MEASUREMENT_STATE_MODE1;   /* 还是 MODE1, 窗口变长 */
            return MEASUREMENT_LONGW_STARTED;
        }

        /* 出结果 */
        measurement_result = current;
        measurement_result_ext = current_ext;
        /* MODE 由**实际窗口长度**决定, 不用 long_tried —— 后者在强制窗口时被
         * 提前置 1, 会把强制 1s 也报成 MODE=2。真正的窗口长度看结果行的 T=。 */
        result_is_longw = (measurement_ticks > CURRENT_WIN_SHORT_TICKS) ? 1U : 0U;
        /* 拉回相守卫超时 -> 开窗那一拍可能还没到位, 读数不可信, **必须上报**。
         * 判据是"本次测量期间 precond_timeout 有没有涨" —— 不能用它的绝对值,
         * 那是个跨测量只增的累计计数, 看过一次之后就永远非 0。
         * 上报到结果行的 TIMEOUT 后缀 (采集脚本已能解析), 并让 OLED 显示出来。 */
        result_is_timeout = (precond_timeout != precond_to_at_start) ? 1U : 0U;

        /* ---- 首拍在轨 -> 这一窗同样不可信, 一并报成 TIMEOUT ----
         * 2026-09-13 实测: 外部路的 0xFFFF 坏读**聚在窗口开头** ——
         * 平均坏读率 0.085% (32/37500), 而首拍坏读率高达 1/6 = 17%,
         * 相差 200 倍。所以专门查首拍, 不靠平均率去碰运气。
         * (拉回相守卫超时与首拍在轨是两个不同原因, 但含义一样: 读数不可信,
         *  所以复用同一个 TIMEOUT 标记; 结果行与 OLED 都已能显示。) */
        if ((Current_GetFirstCode() >= 0xFF00U) || (Current_GetFirstCode() <= 0x00FFU))
        {
            result_is_timeout = 1U;
        }
        MeasurementState_Finish();
        return MEASUREMENT_RESULT_READY;
    }
}

MeasurementState MeasurementState_GetState(void)
{
    return measurement_state;
}

float MeasurementState_GetResult(void)
{
    return measurement_result;
}

uint32_t MeasurementState_GetTicks(void)
{
    return measurement_ticks;
}

float MeasurementState_GetResultExt(void)
{
    return measurement_result_ext;
}

uint8_t MeasurementState_ResultIsLongW(void)
{
    return result_is_longw;
}

uint8_t MeasurementState_ResultIsTimeout(void)
{
    return result_is_timeout;
}
