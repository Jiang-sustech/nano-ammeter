#include "measurement_state.h"
#include "current.h"
#include "tim.h"

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

void MeasurementState_StopCommand(void)
{
    Current_Stop();     /* 停 TIM6 + 关 ADG */
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
        /* 连续模式: 每秒一个窗口, 结果由中断内算好 */
        if (!Current_WindowFinished())
        {
            return MEASUREMENT_PROCESSING;
        }
        Current_ClearWindowFlag();
        current = Current_GetWindowResult();

        if (current < DIRECT_MEASURE_THRESHOLD)
        {
            /* 电流小于 1nA: 自动进入双斜率硬积分 (10s 多循环) */
            HAL_TIM_Base_Stop_IT(&htim6);
            HardIntegral_Start();       /* 阻塞复位 + 启动第一个循环 */
            measurement_state = MEASUREMENT_STATE_MODE2;
            return MEASUREMENT_MODE2_STARTED;
        }

        measurement_result = current;
        result_is_hard = 0;
        return MEASUREMENT_RESULT_READY;    /* 模式一继续运行 */

    case MEASUREMENT_STATE_MODE2:
        if (!HardIntegral_IsFinished())
        {
            return MEASUREMENT_PROCESSING;
        }

        /* 超时(约 <6pA 或负电流) -> 结果记 0, 由硬件验证时观察 */
        measurement_result = HardIntegral_IsTimeout()
                             ? 0.0f : HardIntegral_GetCurrent();
        result_is_hard = 1;

        /* 模式二结束: 自动回到连续模式一 */
        Current_Start();
        if (HAL_TIM_Base_Start_IT(&htim6) != HAL_OK)
        {
            measurement_state = MEASUREMENT_STATE_IDLE;
        }
        else
        {
            measurement_state = MEASUREMENT_STATE_MODE1;
        }
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
