#include "current.h"
#include "main.h"
#include "adc.h"
#include "ads8866.h"

/* ============================================================
 * 锯齿波测试: 滞回 bang-bang (±4.3V 阈值, 安全最高优先级)
 * ADG1219 真值表 (数据手册):
 *   EN=1 使能; IN=0 -> A (+5V); IN=1 -> B (-5V)
 * 极性 (物理): +50nA 注入节点 -> 电压下降; -50nA -> 上升
 * ============================================================ */

#define T_INT 160e-6f
#define I_POS 50e-9f
#define I_NEG (-50e-9f)

/* ADC 域阈值码 (电平移位映射, 注意 735-1 反相: V_adc = 1.55 - 0.33*V_int)
 * V_int=+4.3 (正轨) -> V_adc=0.131 = code_lower
 * V_int=-4.3 (负轨) -> V_adc=2.969 = code_upper */
#define V_ADC_UPPER (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_LOWER) /* 2.969 */
#define V_ADC_LOWER (LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * THRESH_INT_UPPER) /* 0.131 */

static uint16_t voltage_buf[TOTAL_CYCLE];
static volatile uint32_t sample_index;
static volatile uint32_t m_count, n_count;

#if USE_ADS8866_PARALLEL
/* 同拍外部 ADC 采样 (与 voltage_buf 一一对应), 用于两条采集路径对比 */
static uint16_t voltage_buf_ext[TOTAL_CYCLE];
static volatile uint32_t ext_error_count;

/* 诊断: 记录最近 16 次坏读的窗口内下标与原始值 (SWD 读取用) */
#define EXT_ERR_LOG 16U
static volatile uint32_t ext_err_idx[EXT_ERR_LOG];
static volatile uint16_t ext_err_val[EXT_ERR_LOG];
static volatile uint32_t ext_err_total;      /* 与 ext_error_count 同步, 仅用于对齐 */
#endif

#define SEL_POS 0U
#define SEL_NEG 1U
static volatile uint8_t sel_state;

static uint16_t code_upper, code_lower;

static uint16_t VToCode(float v_adc)
{
    if (v_adc <= 0.0f) return 0U;
    if (v_adc >= ADS8866_VREF) return 0xFFFFU;
    return (uint16_t)(v_adc / ADS8866_VREF * 65536.0f);
}

/* ---- ADG 控制 (数据手册语义) ---- */
static void ADG_Enable(void)   { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_2, GPIO_PIN_SET); }   /* EN=1 */
static void ADG_Disable(void)  { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_2, GPIO_PIN_RESET); } /* EN=0 */
static void ADG_Select_Positive(void) { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_1, GPIO_PIN_RESET); } /* A:+5V */
static void ADG_Select_Negative(void) { HAL_GPIO_WritePin(GPIOB, GPIO_PIN_1, GPIO_PIN_SET); }   /* B:-5V */

/* PA3 12 位读取 -> 16 位码域 (寄存器级, 160us 中断内不能走 HAL 阻塞轮询:
 * HAL 超时依赖 HAL_GetTick, 而 SysTick 与 TIM6 同优先级会被饿死 -> 死锁) */
static uint16_t ReadRaw(void)
{
    uint32_t guard;
    uint16_t raw;

    /* 标定后 HAL 会关掉 ADEN, 这里确保 ADC 处于使能就绪态 */
    if ((ADC1->CR & ADC_CR_ADEN) == 0U)
    {
        ADC1->ISR = ADC_ISR_ADRDY;
        ADC1->CR |= ADC_CR_ADEN;
        guard = 200000U;
        while (((ADC1->ISR & ADC_ISR_ADRDY) == 0U) && (guard-- > 0U)) { }
    }

    ADC1->ISR = ADC_ISR_EOC | ADC_ISR_OVR;   /* 清标志 */
    ADC1->CR |= ADC_CR_ADSTART;

    guard = 200000U;
    while (((ADC1->ISR & ADC_ISR_EOC) == 0U) && (guard-- > 0U)) { }
    if (guard == 0U)
    {
        return 0U;
    }

    raw = (uint16_t)ADC1->DR;

    return (uint16_t)((uint32_t)raw << 4);
}

void Current_Start(void)
{
    m_count = 0;
    n_count = 0;
    sample_index = 0;
    sel_state = SEL_POS;
#if USE_ADS8866_PARALLEL
    ext_error_count = 0;
#endif

    code_upper = VToCode(V_ADC_UPPER);
    code_lower = VToCode(V_ADC_LOWER);

    ADG_Enable();
    ADG_Select_Positive();
}

/* TIM6 中断每 160us 调用 */
void Current_Process(void)
{
    uint16_t raw = ReadRaw();

    if (sample_index >= TOTAL_CYCLE)
    {
        return;
    }

    voltage_buf[sample_index] = raw;

#if USE_ADS8866_PARALLEL
    /* 同拍读外部 16 位 ADC (同一条 V_OUT 网络)。
     * 全 0 / 全 1 视作读失败并计数: 0x0000 = 转换未完成,
     * 0xFFFF = DOUT 恒高 (MISO 断线/OE 未开) */
    {
        uint16_t ext = ADS8866_ReadRaw();
        voltage_buf_ext[sample_index] = ext;
        if (ext == 0x0000U || ext == 0xFFFFU)
        {
            ext_error_count++;
            ext_err_idx[ext_err_total % EXT_ERR_LOG] = sample_index;
            ext_err_val[ext_err_total % EXT_ERR_LOG] = ext;
            ext_err_total++;
        }
    }
#endif

    sample_index++;

    /* 滞回 bang-bang (阈值永远生效):
     * POS(+50nA 注入) -> V_int 下降 -> V_adc 上升 -> 触 code_upper 切 NEG
     * NEG(-50nA 注入) -> V_int 上升 -> V_adc 下降 -> 触 code_lower 切 POS
     * (735-1 电平移位反相, 所以 V_adc 方向与 V_int 相反) */
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
        /* 窗口满: 重置计数继续采样 (连续模式) */
        sample_index = 0;
        m_count = 0;
        n_count = 0;
    }
}

uint32_t Current_GetSampleIndex(void) { return sample_index; }
uint32_t Current_GetM(void) { return m_count; }
uint32_t Current_GetN(void) { return n_count; }

uint16_t Current_GetSample(uint32_t index)
{
    if (index >= TOTAL_CYCLE)
    {
        return 0U;
    }
    return voltage_buf[index];
}

#if USE_ADS8866_PARALLEL
uint16_t Current_GetExtSample(uint32_t index)
{
    if (index >= TOTAL_CYCLE)
    {
        return 0U;
    }
    return voltage_buf_ext[index];
}

uint32_t Current_GetExtErrorCount(void) { return ext_error_count; }
#endif
