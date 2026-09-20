#include "measurement_state.h"
#include "current.h"
#include "tim.h"
#include <math.h>

/* ============================================================
 * 测量状态机 (单次测量语义, 用户确认的最终设计):
 *   IDLE -> (S 指令) -> MODE1 (1s 窗口) -> >=1nA: 出结果, 自动回 IDLE
 *                                      -> <1nA: 小电流模式 -> 出结果, 自动回 IDLE
 *   一次指令 = 一次完整测量, 测完停住 (TIM6 停 + ADG 关断);
 *   无取消/无连续模式 (越简单越可靠), 测量期间忽略一切指令
 *
 * 2026-09-13: 原"模式二 双斜率硬积分"废弃, 由小电流模式取代
 * (理由见 current.h 顶部: 积不起来 + 依赖 EN=0 的 Hi-Z 暂态)。
 * 状态机的结构没动, 只是把名叫 MODE2 的那个状态改成实名 SMALLI。
 * ============================================================ */

#define DIRECT_MEASURE_THRESHOLD 1e-9f   /* 1nA: 低于则自动进入小电流模式 */

static MeasurementState measurement_state;
static float measurement_result;            /* 内置 ADC 算出的结果 */
static float measurement_result_ext;        /* ADS8866 算出的结果 (表征以它为准) */
static uint32_t measurement_ticks;          /* 小电流模式实际积分拍数 */
static uint8_t result_is_smalli;
static uint8_t result_is_timeout;

void MeasurementState_Init(void)
{
    measurement_state = MEASUREMENT_STATE_IDLE;
    measurement_result = 0.0f;
    measurement_result_ext = 0.0f;
    measurement_ticks = 0U;
    result_is_smalli = 0;
    result_is_timeout = 0;
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
        /* 顺序要紧: 先取结果, 再停表, 最后才解除封存 —— 若先解除, 到停表
         * 之间落下一个 TIM6 中断, 会往 voltage_buf[0] 追加窗口外样本 */
        current = Current_GetWindowResult();
        current_ext = Current_GetWindowResultExt();
        HAL_TIM_Base_Stop_IT(&htim6);
        Current_ClearWindowFlag();

        /* 实际积分拍数 —— 结果行的 T= 就是它 * 160us。模式一恒为整窗 6250 拍
         * (=1000ms), 但必须在这里显式赋值: 这个变量只在下面小电流分支里被写过,
         * 模式一不赋值的话 T= 会报 0ms (实测踩过, 报告里 I=C*dV/T 的 T 就错了)。
         * 转小电流模式时会被下面的 SmallI_GetActualTicks() 覆写。 */
        measurement_ticks = Current_GetSampleCount();

        /* 判据取绝对值 —— 只判单边的话会把大负电流也送进去, 白等一整轮 */
        if (fabsf(current) < DIRECT_MEASURE_THRESHOLD)
        {
            /* <1nA: 自动进入小电流模式 (纯积分 + 首尾斜率)。
             * 上面先停了表不影响它 —— SmallI_Start 自己会重启 TIM6 */
            SmallI_Start();
            measurement_state = MEASUREMENT_STATE_SMALLI;
            return MEASUREMENT_SMALLI_STARTED;
        }

        /* >=1nA: 单次测量完成 */
        measurement_result = current;
        measurement_result_ext = current_ext;
        result_is_smalli = 0;
        result_is_timeout = 0;  /* 只在小电流分支赋值, 不清会把上次的粘过来 */
        MeasurementState_Finish();
        return MEASUREMENT_RESULT_READY;

    case MEASUREMENT_STATE_SMALLI:
        if (!SmallI_IsFinished())
        {
            return MEASUREMENT_PROCESSING;
        }

        /* 小电流模式没有"超时"一说: 积分撞到 3.5V 就提前收尾并按**实际**
         * 时间计算, 结果仍然有效 (只是积分时间短了、信噪比差些)。 */
        result_is_timeout = 0U;
        measurement_result     = SmallI_GetCurrentInt();
        measurement_result_ext = SmallI_GetCurrentExt();
        measurement_ticks      = SmallI_GetActualTicks();
        result_is_smalli = 1;

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

uint32_t MeasurementState_GetTicks(void)
{
    return measurement_ticks;
}

float MeasurementState_GetResultExt(void)
{
    return measurement_result_ext;
}

uint8_t MeasurementState_ResultIsSmallI(void)
{
    return result_is_smalli;
}

uint8_t MeasurementState_ResultIsTimeout(void)
{
    return result_is_timeout;
}
