#include "measurement_state.h"
#include "current.h"
#include "tim.h"

/* ============================================================
 * 测量状态机 (单次测量语义, 用户确认的最终设计):
 *   IDLE -> (S 指令) -> MODE1 (1s 窗口) -> >=1nA: 出结果, 自动回 IDLE
 *                                      -> <1nA: MODE2 (10s 双斜率) -> 出结果, 自动回 IDLE
 *   一次指令 = 一次完整测量, 测完停住 (TIM6 停 + ADG 关断);
 *   无取消/无连续模式 (越简单越可靠), 测量期间忽略一切指令
 * ============================================================ */

#define DIRECT_MEASURE_THRESHOLD 1e-9f   /* 1nA: 低于则自动进入双斜率 */

static MeasurementState measurement_state;
static float measurement_result;
static uint8_t result_is_hard;

void MeasurementState_Init(void)
{
    measurement_state = MEASUREMENT_STATE_IDLE;
    measurement_result = 0.0f;
    result_is_hard = 0;
}

void MeasurementState_StartCommand(void)
{
    if (measurement_state != MEASUREMENT_STATE_IDLE)
    {
        return;
    }

    Current_Start();
    if (HAL_TIM_Base_Start_IT(&htim6) != HAL_OK)
    {
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

    switch (measurement_state)
    {
    case MEASUREMENT_STATE_IDLE:
    default:
        return MEASUREMENT_PROCESSING;

    case MEASUREMENT_STATE_MODE1:
        /* 1s 窗口结果 (中断内算好) */
        if (!Current_WindowFinished())
        {
            return MEASUREMENT_PROCESSING;
        }
        Current_ClearWindowFlag();
        current = Current_GetWindowResult();

        if (current < DIRECT_MEASURE_THRESHOLD)
        {
            /* <1nA: 自动进入双斜率硬积分 (单次 10s 多循环) */
            HAL_TIM_Base_Stop_IT(&htim6);
            HardIntegral_Start();
            measurement_state = MEASUREMENT_STATE_MODE2;
            return MEASUREMENT_MODE2_STARTED;
        }

        /* >=1nA: 单次测量完成 */
        measurement_result = current;
        result_is_hard = 0;
        MeasurementState_Finish();
        return MEASUREMENT_RESULT_READY;

    case MEASUREMENT_STATE_MODE2:
        if (!HardIntegral_IsFinished())
        {
            return MEASUREMENT_PROCESSING;
        }

        /* 超时(约 <6pA 或负电流) -> 结果记 0, 由硬件验证时观察 */
        measurement_result = HardIntegral_IsTimeout()
                             ? 0.0f : HardIntegral_GetCurrent();
        result_is_hard = 1;

        /* 单次测量完成: 不再回到模式一 */
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

uint8_t MeasurementState_ResultIsHard(void)
{
    return result_is_hard;
}
