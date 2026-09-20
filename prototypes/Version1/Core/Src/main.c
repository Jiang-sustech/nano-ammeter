/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "adc.h"
#include "spi.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "adg4530.h"
#include "ads8866.h"
#include "button.h"
#include "current.h"
#include "measurement_state.h"
#include "SH1106.h"
#include "uart.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* 开机自动流程: 先跑 50s 底噪标定, 完成后自动开始正常测量.
 * 比赛演示用 1000s; 本次为 50s 临时底噪测试 */
#define NOISE_FIRST_ON_BOOT

/* PB4 按键已修复 (电平正常), 恢复按键功能:
 * 短按 = 空闲时启动测量 / 测量中刷新显示; 长按 = 底噪测量 */
/* 底噪测量: 开机自动流程启用中; 按键长按路径关闭 (PB4 间歇接触会误触发) */
#define NOISE_DISABLED
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static uint8_t noise_mode_active = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static void Measurement_Task(void);
static void Noise_Task(void);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

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
  ADG_Disable();               /* 空闲状态: 关断参考电流 (gpio.c 初始电平也已改低) */
  SH1106_Init();
  if (ADS8866_Init() != HAL_OK)
  {
    Error_Handler();
  }
  Button_Init();
  UART_Init_RX();
  OLED_ShowCurrentAndMode(0.0f, "MODE:IDLE");
  MeasurementState_Init();
#ifdef NOISE_FIRST_ON_BOOT
  Noise_Start();
  noise_mode_active = 1;
  OLED_ShowCurrentAndMode(0.0f, "MODE:NOISE");
#endif
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */

  while (1)
  {
    /* 按键任务: 消抖 + 状态机 (非阻塞) */
    Button_Task();

    /* 底噪模式运行期间忽略按键 (开机自动标定时防误触发) */
    if (!noise_mode_active)
    {
#ifndef NOISE_DISABLED
      /* 长按(>3s): 底噪/偏置电流测量 (ADG 全断, 纯积分 1000s) */
      if (Button_LongPressRequested())
      {
        MeasurementState_StopCommand();  /* 先退出正常测量 */
        Noise_Start();
        noise_mode_active = 1;
        OLED_ShowCurrentAndMode(0.0f, "MODE:NOISE");
        Button_MeasurementDone();        /* 解锁按键状态机 */
      }
#endif

      /* 串口指令: 'S'=启动测量  'X'=停止测量 */
      char cmd = UART_GetCommand();
      if (cmd == 'S')
      {
        MeasurementState_StartCommand();
        OLED_ShowCurrentAndMode(0.0f, "MODE1 RUN");
      }
      else if (cmd == 'X')
      {
        MeasurementState_StopCommand();
        OLED_ShowCurrentAndMode(MeasurementState_GetResult(), "MODE:IDLE");
      }

      /* 短按: 空闲时启动测量; 测量中则刷新显示上一秒结果 */
      if (Button_StartRequested())
      {
        if (MeasurementState_GetState() == MEASUREMENT_STATE_IDLE)
        {
          MeasurementState_StartCommand();
          OLED_ShowCurrentAndMode(0.0f, "MODE1 RUN");
        }
        else
        {
          OLED_ShowCurrentAndMode(MeasurementState_GetResult(),
                                  MeasurementState_ResultIsHard()
                                  ? "MODE:HARD" : "MODE:MEASURE");
        }
        Button_MeasurementDone();        /* 解锁按键, 允许下一次按键 */
      }
    }

    /* 底噪测量任务: 1000s 积分结束后拟合斜率 -> I_bias
     * 注意: 必须始终调用 (开机底噪流程完成后靠它清除 noise_mode_active),
     * NOISE_DISABLED 只管住按键长按触发 */
    Noise_Task();

    /* 测量任务: 非阻塞推进 (TIM6 中断驱动采样, 主循环只查状态) */
    Measurement_Task();
  }
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */

/**
  * @brief System Clock Configuration
  * @retval None
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
  * 注意: 用内部 MSI 48MHz 代替外部 HSE (板上 8MHz 晶振不起振时也能跑)
  * MSI 48 / PLLM 6 = 8MHz, 与原 HSE 配置的 PLL 输入完全一致,
  * SYSCLK 仍为精确 80MHz, 串口波特率/160us 采样周期不受影响 */
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
 * 流程: 连续模式一(每秒 1 个 1s 窗口结果) -> 若 <1nA 自动双斜率
 *       (10s 内多循环取平均) -> 出结果后自动回到连续模式一
 * 显示策略: 模式一结果每秒 UART 输出(供调试助手记录), 屏幕由
 *           按键/指令刷新; 模式二开始与最终结果主动上屏 */
static void Measurement_Task(void)
{
  MeasurementProcessResult process_result = MeasurementState_Process();

  if (process_result == MEASUREMENT_MODE2_STARTED)
  {
    OLED_ShowCurrentAndMode(0.0f, "MODE2 RUN");
    UART_SendString("MODE2 START (I<1nA)\r\n");
  }
  else if (process_result == MEASUREMENT_RESULT_READY)
  {
    float current = MeasurementState_GetResult();
    char current_text[48];

    /* 电流(科学计数法) + 去趋势噪声 RMS + 1s 窗口摆幅
     * 注意: Vn/Vpp 遍历 1s 缓冲, 连续模式下与中断写入存在弱竞态,
     *       统计量对个别撕裂采样不敏感, 可接受 */
    snprintf(current_text, sizeof(current_text),
             "I=%.3e A, Vn=%.1f uV, Vpp=%.2f V\r\n",
             (double)current,
             (double)(Current_GetNoiseVoltage() * 1e6f),
             (double)Current_GetSwingVoltage());
    printf("%s", current_text);
    UART_SendString(current_text);

    if (MeasurementState_ResultIsHard())
    {
      /* 模式二最终结果: 主动显示 (给出电流数值) */
      OLED_ShowCurrentAndMode(current, "MODE:HARD");
    }
  }
}

/* 底噪测量任务: 1000s 积分结束后, 输出拟合的偏置电流 */
static void Noise_Task(void)
{
  float ibias;

  if (!noise_mode_active)
  {
    return;
  }

  if (Noise_IsFinished())
  {
    char current_text[48];

    ibias = Noise_GetBiasCurrent();

    /* 偏置电流可能是 fA 量级, 用科学计数法 */
    snprintf(current_text, sizeof(current_text), "Ib=%.4e A\r\n", (double)ibias);
    printf("%s", current_text);
    UART_SendString(current_text);
    OLED_ShowCurrentAndMode(ibias, "MODE:NOISE");

    noise_mode_active = 0;
    Button_MeasurementDone();

#ifdef NOISE_FIRST_ON_BOOT
    /* 开机标定流程: 底噪完成后自动开始正常测量
     * (临时关闭: 保留底噪数据在 voltage_buf 供 dump 绘图) */
    /* MeasurementState_StartCommand();
    OLED_ShowCurrentAndMode(0.0f, "MODE1 RUN"); */
#endif
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
