#include "button.h"

/* ============================================================
 * 硬件连接:
 *   +3.3V ── 10kΩ ──┬── GPIO (按键引脚) ── SW1 ── GND
 *
 * 按键引脚: PB4 (物理 40 脚), 按实际接线修改
 * ============================================================ */
#define BUTTON_PORT   GPIOB
#define BUTTON_PIN    GPIO_PIN_4
#define DEBOUNCE_MS   30U
#define LONG_PRESS_MS 3000U    /* 长按阈值: 触发底噪/偏置电流测量 */

static volatile ButtonState state = BUTTON_STATE_IDLE;
static uint8_t start_request;
static uint8_t long_press_event;
static uint8_t pressing;
static uint32_t press_start_tick;
static uint8_t raw_prev;
static uint8_t stable_level;
static uint32_t raw_change_tick;

void Button_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    __HAL_RCC_GPIOB_CLK_ENABLE();

    /* 输入模式; 外部已有 10k 上拉, 片内不重复上拉
     * (若无外部上拉, 把 Pull 改为 GPIO_PULLUP) */
    GPIO_InitStruct.Pin  = BUTTON_PIN;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(BUTTON_PORT, &GPIO_InitStruct);

    state = BUTTON_STATE_IDLE;
    start_request = 0;
    raw_prev = 1U;          /* 初始视为松开 */
    stable_level = 1U;
    raw_change_tick = 0U;
}

/* 读消抖后的稳定电平: 1=松开(HIGH), 0=按下(LOW) */
static uint8_t Button_StableLevel(void)
{
    uint8_t raw = HAL_GPIO_ReadPin(BUTTON_PORT, BUTTON_PIN);
    uint32_t now = HAL_GetTick();

    /* 电平变化: 记录变化时刻, 重新开始计时 */
    if (raw != raw_prev)
    {
        raw_prev = raw;
        raw_change_tick = now;
    }

    /* 电平保持 DEBOUNCE_MS 不变才视为稳定, 抖动被滤除 */
    if ((now - raw_change_tick) >= DEBOUNCE_MS)
    {
        stable_level = raw;
    }

    return stable_level;
}

void Button_Task(void)
{
    uint8_t level = Button_StableLevel();
    uint32_t now = HAL_GetTick();

    switch (state)
    {
    case BUTTON_STATE_IDLE:
        if (level == 0U)
        {
            /* 按下: 先记录, 松开时按时长分长短按 */
            if (pressing == 0U)
            {
                pressing = 1U;
                press_start_tick = now;
            }

            if ((now - press_start_tick) >= LONG_PRESS_MS)
            {
                /* 长按: 触发底噪/偏置电流测量 */
                pressing = 0U;
                long_press_event = 1U;
                state = BUTTON_STATE_MEASURING;
            }
        }
        else
        {
            if (pressing != 0U)
            {
                /* 短按松开: 正常测量 */
                pressing = 0U;
                start_request = 1U;
                state = BUTTON_STATE_MEASURING;
            }
        }
        break;

    case BUTTON_STATE_MEASURING:
        /* 测量期间完全忽略按键 (含持续按住/连按) */
        break;

    case BUTTON_STATE_WAIT_RELEASE:
        /* 必须检测到松开才回 IDLE, 防止长按松开被当成新按下 */
        if (level == 1U)
        {
            state = BUTTON_STATE_IDLE;
        }
        break;
    }
}

uint8_t Button_StartRequested(void)
{
    if (start_request != 0U)
    {
        start_request = 0U;
        return 1U;
    }
    return 0U;
}

uint8_t Button_LongPressRequested(void)
{
    if (long_press_event != 0U)
    {
        long_press_event = 0U;
        return 1U;
    }
    return 0U;
}

void Button_MeasurementDone(void)
{
    if (state == BUTTON_STATE_MEASURING)
    {
        state = BUTTON_STATE_WAIT_RELEASE;
    }
}

ButtonState Button_GetState(void)
{
    return state;
}
