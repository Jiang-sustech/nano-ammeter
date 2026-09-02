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
#include <stdio.h>
/* USER CODE END Includes */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
/* PB4 按键硬件故障 (恒读低), 全部按键功能关闭 (用户决定):
 * 控制一律走串口/HTML 控制台; PB4 修好后删掉此宏即可恢复 */
#define BUTTON_DISABLED
static uint8_t noise_mode_active = 0;
static uint8_t has_result = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static void Measurement_Task(void);
static void Noise_Task(void);
static void OLED_DrawScreen(const char *value, const char *unit, const char *mode);
static void FormatScaled(int64_t value, uint8_t decimals, char *buf);
static void FormatCurrentNA(float current, char *buf);
static void UART_SendResultLine(float current, uint8_t mode);
static void UART_SendResultQuery(void);
static void UART_DumpWaveform(void);
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

/* 定点十进制格式化: value / 10^decimals, 带符号, 如 (12345,3) -> "+12.345"
 * 避免浮点 printf (nano 库默认不带 _printf_float) */
static void FormatScaled(int64_t value, uint8_t decimals, char *buf)
{
    char digits[24];
    int idx = 0;
    int64_t v;
    uint8_t n = 0;
    char *p = buf;

    if (value < 0)
    {
        *p++ = '-';
        v = -value;
    }
    else
    {
        *p++ = '+';
        v = value;
    }

    do
    {
        digits[idx++] = (char)('0' + (v % 10));
        v /= 10;
        n++;
    } while (v > 0);

    /* 不足 decimals+1 位则补前导零 */
    while (n <= decimals)
    {
        digits[idx++] = '0';
        n++;
    }

    while (idx > 0)
    {
        *p++ = digits[--idx];
        if (decimals > 0 && idx == decimals)
        {
            *p++ = '.';
        }
    }
    *p = '\0';
}

/* 电流 -> nA 3 位小数文本 (单位 0.001 nA) */
static void FormatCurrentNA(float current, char *buf)
{
    double v = (double)current * 1e12;
    if (v > 9.0e18) v = 9.0e18;
    if (v < -9.0e18) v = -9.0e18;
    FormatScaled((int64_t)v, 3, buf);
}

/* 结果上报: "I=+12.345 nA MODE=1" */
static void UART_SendResultLine(float current, uint8_t mode)
{
    char v[24];
    char line[48];

    FormatCurrentNA(current, v);
    sprintf(line, "I=%s nA MODE=%u\r\n", v, mode);
    UART_SendString(line);
}

/* D 指令: 查询最近结果 */
static void UART_SendResultQuery(void)
{
    char v[24];
    char line[56];

    if (!has_result)
    {
        UART_SendString("RESULT NONE\r\n");
        return;
    }

    FormatCurrentNA(MeasurementState_GetResult(), v);
    sprintf(line, "RESULT I=%s nA MODE=%u\r\n", v,
            MeasurementState_ResultIsHard() ? 2U : 1U);
    UART_SendString(line);
}

/* B 指令: 回传当前窗口原始波形
 * 格式: "WAVE <count>\r\n" + count*2 字节小端 u16 + 2 字节校验和(样本和) */
static void UART_DumpWaveform(void)
{
    char header[32];
    uint32_t count = Current_GetSampleCount();
    uint16_t sum = 0;
    uint32_t i;
    static uint8_t chunk[512];

    sprintf(header, "WAVE %lu\r\n", (unsigned long)count);
    UART_SendString(header);

    for (i = 0; i < count; i++)
    {
        uint16_t s = Current_GetSample(i);
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
  ADS8866_Init();                /* PA3 12 位路径, 校准失败不阻塞 */
#ifndef BUTTON_DISABLED
  Button_Init();
#endif
  UART_Init_RX();
  MeasurementState_Init();

  OLED_DrawScreen("+0.000", "nA", "MODE:IDLE");
  UART_SendString("NANO-AMMETER READY\r\n");
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

    /* 测量/底噪期间忽略一切指令 (单次语义: 测完自动回 IDLE 后才接受新指令) */
    if (!noise_mode_active
        && MeasurementState_GetState() == MEASUREMENT_STATE_IDLE)
    {
      /* 长按(>3s): 底噪/偏置电流测量 (ADG 全断, 纯积分) */
#ifndef BUTTON_DISABLED
      if (Button_LongPressRequested())
      {
        Noise_Start();
        noise_mode_active = 1;
        OLED_DrawScreen("+0.000", "nA", "MODE:NOISE");
        UART_SendString("NOISE START (50s)\r\n");
        Button_MeasurementDone();
      }
#endif

      /* 串口指令: S=单次测量 N=底噪 D=查询结果 B=回传波形 */
      char cmd = UART_GetCommand();
      switch (cmd)
      {
      case 'S':
        MeasurementState_StartCommand();
        OLED_DrawScreen("+0.000", "nA", "MODE1 RUN");
        UART_SendString("START MODE1\r\n");
        break;
      case 'N':
        Noise_Start();
        noise_mode_active = 1;
        OLED_DrawScreen("+0.000", "nA", "MODE:NOISE");
        UART_SendString("NOISE START (50s)\r\n");
        break;
      case 'D':
        UART_SendResultQuery();
        break;
      case 'B':
        UART_DumpWaveform();
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

    /* 底噪任务: 50s 积分结束后拟合斜率 -> I_bias */
    Noise_Task();

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

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_MSI;
  RCC_OscInitStruct.MSIState = RCC_MSI_ON;
  RCC_OscInitStruct.MSIClockRange = RCC_MSIRANGE_11;   /* 48 MHz */
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

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* 测量任务: 主循环每轮调用, 非阻塞推进
 * 模式一: 每秒 1 个窗口结果, 自动上报串口并刷新屏幕;
 *         <1nA 自动切模式二 (10s 多循环) -> 出结果后自动回模式一 */
static void Measurement_Task(void)
{
  MeasurementProcessResult process_result = MeasurementState_Process();

  if (process_result == MEASUREMENT_MODE2_STARTED)
  {
    OLED_DrawScreen("+0.000", "nA", "MODE2 RUN");
    UART_SendString("MODE2 START (I<1nA)\r\n");
  }
  else if (process_result == MEASUREMENT_RESULT_READY)
  {
    float current = MeasurementState_GetResult();
    uint8_t hard = MeasurementState_ResultIsHard();
    char v[24];

    has_result = 1;
    FormatCurrentNA(current, v);
    UART_SendResultLine(current, hard ? 2U : 1U);
    OLED_DrawScreen(v, "nA", hard ? "MODE:HARD" : "MODE:MEASURE");
  }
}

/* 底噪测量任务: 50s 积分结束后输出拟合偏置电流 (fA 量级) */
static void Noise_Task(void)
{
  float ibias;
  static uint32_t last_reported_sec = 0;

  if (!noise_mode_active)
  {
    last_reported_sec = 0;
    return;
  }

  /* 每 10s 报一次进度, 演示时可见 */
  uint32_t elapsed = Noise_GetElapsedSec();
  if (elapsed != last_reported_sec && (elapsed % 10U) == 0U)
  {
    char line[32];
    sprintf(line, "NOISE %lu/50s\r\n", (unsigned long)elapsed);
    UART_SendString(line);
    last_reported_sec = elapsed;
  }

  if (Noise_IsFinished())
  {
    char fb[24];
    char line[48];

    ibias = Noise_GetBiasCurrent();
    FormatScaled((int64_t)((double)ibias * 1e15), 1, fb);   /* fA 1 位小数 */

    sprintf(line, "IBIAS=%s fA\r\n", fb);
    UART_SendString(line);
    OLED_DrawScreen(fb, "fA", "MODE:NOISE");

    has_result = 1;
    noise_mode_active = 0;
    Button_MeasurementDone();
  }
}
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
