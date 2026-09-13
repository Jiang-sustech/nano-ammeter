/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : 纳安表主程序 (电荷平衡微弱电流测量)
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "adc.h"
#include "spi.h"
#include "tim.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "sh1106.h"
#include "font7x10.h"
#include "uart.h"
#include "adg4530.h"
#include "ads8866.h"
#include "button.h"
#include "current.h"
#include "measurement_state.h"
#include "cal_coef.h"
#include "cal_mode.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
/* PB4 按键硬件故障 (恒读低), 全部按键功能关闭 (用户决定):
 * 控制一律走串口/HTML 控制台; PB4 修好后删掉此宏即可恢复 */
#define BUTTON_DISABLED
static uint8_t has_result = 0;
/* 1 = HSE 8MHz 晶振; 0 = MSI 内部 RC (实测 HSE 起振正常, 见 docs) */
#ifndef CLOCK_USE_HSE
#define CLOCK_USE_HSE 1
#endif

const char *clock_source_name = "?";   /* 实际用上的时钟源, READY 行报出来 */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static void Measurement_Task(void);
static void OLED_DrawScreen(const char *value, const char *unit, const char *mode);
static void FormatCurrentNA(float current, char *buf);
static void UART_SendResultLine(float cur_int, float cur_ext, uint8_t mode,
                                uint8_t timeout);
static void UART_SendResultQuery(void);
static void UART_DumpBuffer(const char *tag, uint16_t (*get)(uint32_t), uint32_t count);
static void UART_DumpWaveform(void);
static void UART_DumpExtWaveform(void);
static void UART_SendExtDiag(void);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* ============================================================
 * 显示: 数值行 + 单位 + 模式状态行 (SH1106-master 驱动, 7x10 字体)
 * ============================================================ */
static void OLED_DrawScreen(const char *value, const char *unit, const char *mode)
{
    uint8_t x;

    SH1106_Fill(0x00);
    x = (uint8_t)LCD_PutStr(0, 6, value, fnt7x10);
    x = (uint8_t)LCD_PutStr(x + 3, 6, unit, fnt7x10);
    LCD_PutStr(0, 28, mode, fnt7x10);
    SH1106_Flush();
}

/* 电流 -> nA 3 位小数文本 (单位 0.001 nA) */
static void FormatCurrentNA(float current, char *buf)
{
    double v = (double)current * 1e12;
    if (v > 9.0e18) v = 9.0e18;
    if (v < -9.0e18) v = -9.0e18;
    UART_FormatScaled((int64_t)v, 3, buf);
}

/* 结果行: 两个 ADC 的结果都报, 校准生效且非超时时再追加校准值。
 *
 *   I=<内置> X=<外部> MODE=<n> T=<ms> [TIMEOUT] [CAL=<校准后>]
 *   MODE=1 再追加 RAW m=.. n=.. INT1=.. INT2=.. EXT1=.. EXT2=..
 *
 * I= 保持与原固件兼容 (上位机的解析正则不锚定行尾); X= 是 ADS8866 的结果,
 * 物理实验表征以它为准; CAL= 由 X= 经分段校准模型算出, 报告要的是它。
 * 超时时数值无意义, 不出 CAL=。
 *
 * RAW 段是离线拟合要用的原始量: m/n = POS/NEG 周期数, INT1/INT2 与 EXT1/EXT2
 * = 窗口首尾两个端点的原始码 (内置那路 / ADS8866 那路)。INT1/EXT1 是拉回相最后
 * 一拍的采样 (V_01), 不是 voltage_buf[0] —— 式(6) 用的端点就是它。只报电流值的话这些量
 * 就丢了 —— 而 (m+n) 与 (m+n-1) 两种分母之争、以及分段系数 a/b 的拟合,
 * 都得从**同一次测量**的原始量出发, 不然只能靠反推。报告的表 4-9~4-14 也用得上。
 *
 * 仅 MODE=1 (电荷平衡窗口) 出这一段: 小电流模式不算 m/n, 用的是起点/终点
 * 两段平均, 原始量是另一套 (结果行的 T= 已经给了它的实际积分拍数)。 */
static void UART_SendResultLine(float cur_int, float cur_ext, uint8_t mode,
                                uint8_t timeout)
{
    char vi[24], vx[24], vc[24];
    /* 最坏情况: 三个值各占满 24 字节 -> 2+24+6+24+8+1+5+24+2 = 96;
     * 再加 RAW 段 (最长约 60 字节) 与结尾 NUL。192 留足余量, snprintf 兜底 */
    char line[192];
    char raw[80] = "";

    FormatCurrentNA(cur_int, vi);
    FormatCurrentNA(cur_ext, vx);

    if (mode == 1U)
    {
        uint32_t npt = Current_GetSampleCount();

        if (npt > 0U)                /* 没采到样就不出, 免得报一堆 0 */
        {
            snprintf(raw, sizeof(raw),
                     " RAW m=%lu n=%lu INT1=%u INT2=%u EXT1=%u EXT2=%u",
                     (unsigned long)Current_GetMCount(),
                     (unsigned long)Current_GetNCount(),
                     (unsigned)Current_GetFirstCode(),
                     (unsigned)Current_GetSample(npt - 1U),
                     (unsigned)Current_GetFirstCodeExt(),
                     (unsigned)Current_GetExtSample(npt - 1U));
        }
    }

    if ((timeout == 0U) && Cal_IsValid())
    {
        FormatCurrentNA(Cal_Apply(cur_ext), vc);
        snprintf(line, sizeof(line),
                 "I=%s nA X=%s nA MODE=%u T=%lums CAL=%s%s\r\n",
                 vi, vx, mode,
                 (unsigned long)(MeasurementState_GetTicks() * 160U / 1000U),
                 vc, raw);
    }
    else
    {
        snprintf(line, sizeof(line),
                 "I=%s nA X=%s nA MODE=%u T=%lums%s%s\r\n",
                 vi, vx, mode,
                 (unsigned long)(MeasurementState_GetTicks() * 160U / 1000U),
                 (timeout != 0U) ? " TIMEOUT" : "", raw);
    }
    UART_SendString(line);
}

/* D 指令: 查询最近结果 */
static void UART_SendResultQuery(void)
{
    char v[24];
    char line[72];

    if (!has_result)
    {
        UART_SendString("RESULT NONE\r\n");
        return;
    }

    FormatCurrentNA(MeasurementState_GetResult(), v);
    sprintf(line, "RESULT I=%s nA MODE=%u%s\r\n", v, MeasurementState_ResultIsSmallI() ? 2U : 1U, (MeasurementState_ResultIsTimeout() != 0U) ? " TIMEOUT" : "");
    UART_SendString(line);
}

/* 波形回传公共实现 (B / X 共用)
 * 格式: "<tag> <count>\r\n" + count*2 字节小端 u16 + 2 字节校验和(样本和) */
static void UART_DumpBuffer(const char *tag,
                            uint16_t (*get)(uint32_t),
                            uint32_t count)
{
    char header[32];
    uint16_t sum = 0;
    uint32_t i;
    static uint8_t chunk[512];

    sprintf(header, "%s %lu\r\n", tag, (unsigned long)count);
    UART_SendString(header);

    for (i = 0; i < count; i++)
    {
        uint16_t s = get(i);
        chunk[(i % 256) * 2]     = (uint8_t)(s & 0xFF);
        chunk[(i % 256) * 2 + 1] = (uint8_t)(s >> 8);
        sum += s;
        if ((i % 256) == 255 || i + 1 == count)
        {
            UART_SendBinary(chunk, (uint16_t)(((i % 256) + 1) * 2));
        }
    }
    UART_SendBinary((uint8_t *)&sum, 2);
}

/* B 指令: 回传最近完整窗口的内置 ADC (控制通路) 波形 */
static void UART_DumpWaveform(void)
{
    UART_DumpBuffer("WAVE", Current_GetSample, Current_GetSampleCount());
}

/* X 指令: 回传同一窗口的 ADS8866 外部 16 位波形 (格式与 B 完全一致,
 * 只是头字为 WAVEX。两路同索引, 可直接逐点叠图对照) */
static void UART_DumpExtWaveform(void)
{
    UART_DumpBuffer("WAVEX", Current_GetExtSample, Current_GetSampleCount());
}

/* E 指令: 观测通路健康度一行 (免去 12.5KB 回传就能判断好坏)
 * ERR = 坏读数 (0x0000 转换未完成 / 0xFFFF DOUT 常高);
 * TXE/RXNE/BSY = SPI 寄存器级守卫超时次数, 正常应恒为 0;
 * PRE = 模式一拉回相守卫超时次数, 正常恒为 0 (超量程的唯一直接证词) */
static void UART_SendExtDiag(void)
{
    char line[128];

    sprintf(line, "EXT N=%lu ERR=%lu TXE=%lu RXNE=%lu BSY=%lu PRE=%lu\r\n",
            (unsigned long)Current_GetSampleCount(),
            (unsigned long)Current_GetExtBadRead(),
            (unsigned long)ads_spi_txe_timeout,
            (unsigned long)ads_spi_rxne_timeout,
            (unsigned long)ads_spi_bsy_timeout,
            (unsigned long)precond_timeout);
    UART_SendString(line);
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_ADC1_Init();
  MX_TIM6_Init();
  MX_USART1_UART_Init();
  MX_SPI2_Init();
  /* USER CODE BEGIN 2 */
  ADG_Disable();                 /* 空闲: 关断参考电流 (安全最高优先级) */
  SH1106_Init();
  Adc_Init();                    /* 控制通路: 内置 ADC1 (PA3) 校准 + 使能 */
  ADS8866_Init();                /* 观测通路: SPI1 + CONVST, 寄存器级, 自包含 */
#ifndef BUTTON_DISABLED
  Button_Init();
#endif
  UART_Init_RX();
  MeasurementState_Init();
  Cal_Init();                 /* 从 Flash 读校准系数 */

#if CAL_MODE != CAL_NONE
  /* ---- 标定固件: 专属主流程 (正常固件 CAL_MODE=CAL_NONE 不编译) ---- */
  OLED_DrawScreen("+0.000", "nA", (CAL_MODE == CAL_ZERO) ? "CAL ZERO" : "CAL BANG");
  UART_SendString((CAL_MODE == CAL_ZERO)
                  ? "CAL ZERO START (10s cycles)\r\n"
                  : "CAL BANG START (1s windows)\r\n");
  CalMode_Start();
  while (1)
  {
    CalMode_Task();
  }
#else
  {
    char buf[80];
    sprintf(buf, "NANO-AMMETER READY (%s)\r\n", clock_source_name);
    UART_SendString(buf);
  }
  OLED_DrawScreen("+0.000", "nA", "MODE:IDLE");
#endif
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
#ifndef BUTTON_DISABLED
    /* 按键任务: 消抖 + 状态机 (非阻塞) */
    Button_Task();
#endif

    /* 测量期间忽略一切指令 (单次语义: 测完自动回 IDLE 后才接受新指令) */
    if (MeasurementState_GetState() == MEASUREMENT_STATE_IDLE)
    {
      /* 串口指令: S=单次测量   D=查询结果
       *           B=内置 ADC 波形  X=ADS8866 波形  E=观测通路自检
       * 参数化指令 (整行):
       *   C                    查询分段系数 a/b
       *   K<段>,<a_ppm>,<b_fA> 写入分段系数        Z 清除分段系数
       *   Q                    查询物理常数 q/I+/I-
       *   Q<q_aC>,<i+_pA>,<i-_pA>  写入物理常数
       *   T<秒>                小电流模式积分时长 */
      /* 物理常数与分段系数分成两组指令: 前者是仪器的实测属性, 后者是拟合出来的
       * 修正; 混在一起会让人以为 Z 会把标定好的 q 一起清掉 */
      /* 整行接收: 参数化指令 (K/C/Z) 需要整行; 单字符指令走同一个缓冲,
       * 长度 1 的行就是简单指令 */
      char line[UART_LINE_MAX];
      char cmd = 0x00;

      if (UART_GetLine(line, sizeof(line)))
      {
        cmd = line[0];
        if (cmd == 'K') { Cal_HandleSetLine(line); cmd = 0x00; }
        else if (cmd == 'C') { Cal_Report(); cmd = 0x00; }
        else if (cmd == 'Z') { Cal_Clear(); cmd = 0x00; }
        else if (cmd == 'Q')
        {
          /* 裸 Q = 查询, 带参数 = 写入 */
          if (line[1] == '\0') { Cal_ReportPhys(); }
          else                 { Cal_HandlePhysLine(line); }
          cmd = 0x00;
        }
        else if (cmd == 'M')
        {
          /* M0/M1/M2: 手动阻塞连采 (量 tau / q 开环标定), 结果用 B / X 回传 */
          Current_ManualLine(line);
          cmd = 0x00;
        }
      }
      switch (cmd)
      {
      case 'S':
        MeasurementState_StartCommand();
        OLED_DrawScreen("+0.000", "nA", "MODE1 RUN");
        UART_SendString("START MODE1\r\n");
        break;
      case 'D':
        UART_SendResultQuery();
        break;
      case 'B':
        UART_DumpWaveform();
        break;
      case 'X':
        UART_DumpExtWaveform();
        break;
      case 'E':
        UART_SendExtDiag();
        break;
      default:
        break;
      }

      /* 短按: 启动单次测量 */
#ifndef BUTTON_DISABLED
      if (Button_StartRequested())
      {
        MeasurementState_StartCommand();
        OLED_DrawScreen("+0.000", "nA", "MODE1 RUN");
        UART_SendString("START MODE1\r\n");
        Button_MeasurementDone();
      }
#endif
    }


    /* 测量任务: 非阻塞推进 (TIM6 中断采样, 主循环查状态) */
    Measurement_Task();
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  * 内部 MSI 48MHz -> PLL (M=6) -> SYSCLK 80MHz;
  * PLLSAI1 供 ADC (adc.c 里配置, 依赖 MSI 48MHz 输入)
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK)
  {
    Error_Handler();
  }

  /** 时钟源: HSE 8MHz 晶振 -> PLL -> SYSCLK 80MHz
  * 2026-09-12 实测本板 HSE 起振正常 (RCC_CR.HSERDY 置位), 比 MSI 准得多。
  *   8MHz / M(1) = 8MHz 输入;  x N(20) = 160MHz VCO;  / R(2) = 80MHz
  *
  * HSE 起不来就退回 MSI (48/6 x20 /2 = 80MHz), **不要直接 Error_Handler** ——
  * 那样整机卡死, 连串口都没了没法诊断。用哪个源会在 READY 行报出来。
  */
#if CLOCK_USE_HSE == 2
  /* 诊断模式: SYSCLK 直接取 HSE, **不过 PLL**。
   * 好处: flash 等待周期只需 0~1 个, 无论晶振多少 MHz 都不会因超频而崩,
   *       也就不会掩盖问题。
   * 串口波特率随之变成 115200 x F / 80 (F 单位 MHz) —— 扫波特率即可
   * 反推晶振实际频率。
   */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    clock_source_name = "HSE 直连失败";
    Error_Handler();
  }
  clock_source_name = "HSE direct (DIAG)";
#elif CLOCK_USE_HSE
  /* 试 PLLM=2 (VCO 输入 4MHz, 规格下限内) 而不是 M=1 —— 排除 M=1 是否被硬件拒绝 */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 2;
  RCC_OscInitStruct.PLL.PLLN = 40;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV7;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;

  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_MSI;
    RCC_OscInitStruct.MSIState = RCC_MSI_ON;
    RCC_OscInitStruct.MSIClockRange = RCC_MSIRANGE_11;   /* 48 MHz */
    RCC_OscInitStruct.MSICalibrationValue = RCC_MSICALIBRATION_DEFAULT;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_MSI;
    RCC_OscInitStruct.PLL.PLLM = 6;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
    {
      Error_Handler();              /* 连 MSI 都不行: 真没救了 */
    }
    clock_source_name = "MSI 48MHz (HSE 起振失败, 已退回)";
  }
  else
  {
    clock_source_name = "HSE 8MHz";
  }
#else
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_MSI;
  RCC_OscInitStruct.MSIState = RCC_MSI_ON;
  RCC_OscInitStruct.MSIClockRange = RCC_MSIRANGE_11;
  RCC_OscInitStruct.MSICalibrationValue = RCC_MSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_MSI;
  RCC_OscInitStruct.PLL.PLLM = 6;
  RCC_OscInitStruct.PLL.PLLN = 20;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV7;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }
  clock_source_name = "MSI 48MHz";
#endif

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
#if CLOCK_USE_HSE == 2
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSE;
#else
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
#endif
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

#if CLOCK_USE_HSE == 2
  /* HSE 直连: F<=16MHz 0 WS, <=32MHz 1 WS —— 取 1 稳妥 */
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1) != HAL_OK)
#else
  /* PLL 到 80MHz (VOS1) 需 4 WS */
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
#endif
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* 测量任务: 主循环每轮调用, 非阻塞推进
 * 模式一: 每秒 1 个窗口结果, 自动上报串口并刷新屏幕;
 *         <1nA 自动切小电流模式 -> 出结果后自动回空闲
 * 注: 结果行的 MODE= 仍是 1/2 —— 2 表示"来自 <1nA 那条支路"。
 *     小数电流模式取代了旧的双斜率, 但字段编码不动, 免得破坏上位机解析。 */
static void Measurement_Task(void)
{
  MeasurementProcessResult process_result = MeasurementState_Process();

  if (process_result == MEASUREMENT_SMALLI_STARTED)
  {
    OLED_DrawScreen("+0.000", "nA", "SMALL-I RUN");
    UART_SendString("SMALLI START (I<1nA)\r\n");
  }
  else if (process_result == MEASUREMENT_RESULT_READY)
  {
    float current = MeasurementState_GetResult();
    uint8_t smi = MeasurementState_ResultIsSmallI();
    uint8_t timeout = MeasurementState_ResultIsTimeout();
    char v[24];

    has_result = 1;
    FormatCurrentNA(current, v);
    UART_SendResultLine(current, MeasurementState_GetResultExt(),
                        smi ? 2U : 1U, timeout);
    OLED_DrawScreen(v, "nA", timeout ? "SMALL-I TMO" : (smi ? "MODE:SMALLI" : "MODE:MEASURE"));
  }
}

/* 底噪测量任务: 50s 积分结束后输出拟合偏置电流 (fA 量级) */
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
