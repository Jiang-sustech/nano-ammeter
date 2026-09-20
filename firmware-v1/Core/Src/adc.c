/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    adc.c
  * @brief   This file provides code for the configuration
  *          of the ADC instances.
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
#include "adc.h"
#include "uart.h"       /* UART_SendString: 校准失败提示 */

/* USER CODE BEGIN 0 */

/* 转换超时的退避值: 返回 0 会被 bang-bang 判决当成"已到 +4.3V"的合法轨码,
 * 也会混进底噪序列当假数据点, 故保持上一次有效码 (最坏只是判决晚一拍)。
 * 若 ADC 从未完成过一次转换, 退避值仍是初值 0 —— 那种全坏由模式一的
 * precond_timeout 打满守卫、底噪由"任一点在轨"判据报 IBIAS=RAIL 暴露 */
static uint16_t adc_last_raw;

/* USER CODE END 0 */

ADC_HandleTypeDef hadc1;

/* ADC1 init function */
void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ClockPrescaler = ADC_CLOCK_ASYNC_DIV1;
  hadc1.Init.Resolution = ADC_RESOLUTION_12B;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc1.Init.LowPowerAutoWait = DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.NbrOfConversion = 1;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;
  hadc1.Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
  hadc1.Init.DMAContinuousRequests = DISABLE;
  hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc1.Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_8;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_640CYCLES_5;
  sConfig.SingleDiff = ADC_SINGLE_ENDED;
  sConfig.OffsetNumber = ADC_OFFSET_NONE;
  sConfig.Offset = 0;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

void HAL_ADC_MspInit(ADC_HandleTypeDef* adcHandle)
{

  GPIO_InitTypeDef GPIO_InitStruct = {0};
  RCC_PeriphCLKInitTypeDef PeriphClkInit = {0};
  if(adcHandle->Instance==ADC1)
  {
  /* USER CODE BEGIN ADC1_MspInit 0 */

  /* USER CODE END ADC1_MspInit 0 */

  /** Initializes the peripherals clock
  */
    /* ADC 时钟 = SYSCLK (80MHz, 在 L431 fADC 上限 80MHz 之内, 数据手册 6.3.17).
     * 不用 PLLSAI1: 本工程 HAL 版本对 L431 的 PLLSAI1 路径有缺陷
     * (L431 PLLSAI1 无 M 分频器, 该 HAL 写出非法配置且不使能 PLL,
     *  导致 ADC 无时钟 -> 转换永不完成 -> 中断内轮询死锁) */
    PeriphClkInit.PeriphClockSelection = RCC_PERIPHCLK_ADC;
    PeriphClkInit.AdcClockSelection = RCC_ADCCLKSOURCE_SYSCLK;
    if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInit) != HAL_OK)
    {
      Error_Handler();
    }

    /* ADC1 clock enable */
    __HAL_RCC_ADC_CLK_ENABLE();

    __HAL_RCC_GPIOA_CLK_ENABLE();
    /**ADC1 GPIO Configuration
    PA3     ------> ADC1_IN8
    */
    GPIO_InitStruct.Pin = GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_ANALOG_ADC_CONTROL;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* USER CODE BEGIN ADC1_MspInit 1 */

  /* USER CODE END ADC1_MspInit 1 */
  }
}

void HAL_ADC_MspDeInit(ADC_HandleTypeDef* adcHandle)
{

  if(adcHandle->Instance==ADC1)
  {
  /* USER CODE BEGIN ADC1_MspDeInit 0 */

  /* USER CODE END ADC1_MspDeInit 0 */
    /* Peripheral clock disable */
    __HAL_RCC_ADC_CLK_DISABLE();

    /**ADC1 GPIO Configuration
    PA3     ------> ADC1_IN8
    */
    HAL_GPIO_DeInit(GPIOA, GPIO_PIN_3);

  /* USER CODE BEGIN ADC1_MspDeInit 1 */

  /* USER CODE END ADC1_MspDeInit 1 */
  }
}

/* USER CODE BEGIN 1 */

/* ============================================================
 * 采集路径 A: 单片机内置 ADC1 (PA3 / ADC1_IN8) —— 控制路径
 *
 * PA3 与 ADS8866 的 AINP 在 PCB 上接同一节点 V_OUT (网表确认),
 * 但这里是 12 位直采, 左移 4 位归一到 16 位码域,
 * 以便与阈值宏 / 电压换算公式 (65536 满量程) 全部兼容。
 *
 * 分工 (重要):
 *   Adc_ReadRaw()      = 内部 12 位, 供 bang-bang 判决与双斜率计时使用
 *   ADS8866_ReadRaw()  = 外部 16 位, 仅作并行观测, **不参与任何控制**
 * 两者挂在同一节点上, 数值可直接互相对照。
 *
 * 为什么不用 HAL_ADC_GetValue / HAL_ADC_PollForConversion:
 * TIM6 中断优先级 0 高于 SysTick 的 15 (tim.c:75 / stm32l4xx_hal_conf.h:185),
 * 中断执行期间 SysTick 无法抢占 -> HAL_GetTick 停走 ->
 * 中断内任何依赖 tick 的超时轮询都会变成死循环 (本工程实测踩过)。
 * 故全部寄存器级轮询 + 循环计数守卫, 不依赖 tick。
 * ============================================================ */

void Adc_Init(void)
{
    uint32_t guard;

    /* 12 位校准 (需 VDDA 已稳定供电)。
     * 校准失败只提示不阻塞启动: 基准未接好时先让程序跑起来 */
    if (HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED) != HAL_OK)
    {
        UART_SendString("WARN: ADC calibration failed (VDDA?)\r\n");
    }

    /* 校准结束后 HAL 会把 ADC 关掉 (ADEN=0), 这里重新使能并等就绪,
     * 后续 Adc_ReadRaw 直接触发转换, 不再反复开关 ADC */
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    guard = 200000U;
    while (((ADC1->ISR & ADC_ISR_ADRDY) == 0U) && (guard != 0U))
    {
        guard--;
    }
    if (guard == 0U)
    {
        UART_SendString("WARN: ADC enable timeout\r\n");
    }
}

uint16_t Adc_ReadRaw(void)
{
    uint32_t guard;
    uint16_t raw;

    /* 防御: ADC 意外被关时重新使能 (正常路径 ADEN 常开) */
    if ((ADC1->CR & ADC_CR_ADEN) == 0U)
    {
        ADC1->ISR = ADC_ISR_ADRDY;
        ADC1->CR |= ADC_CR_ADEN;
        guard = 200000U;
        while (((ADC1->ISR & ADC_ISR_ADRDY) == 0U) && (guard != 0U))
        {
            guard--;
        }
    }

    /* 清 EOC/OVR, 软件触发单次转换 */
    ADC1->ISR = ADC_ISR_EOC | ADC_ISR_OVR;
    ADC1->CR |= ADC_CR_ADSTART;

    /* 守卫写法必须是 (cond) && (guard != 0U) + 循环体内 guard--。
     * 不能写 guard-- > 0U: 那样最后一次判断会多减一次 -> 下溢成
     * 0xFFFFFFFF -> 后面 guard == 0U 的超时判断永远不成立,
     * 守卫形同虚设, 转换一旦不完成就是死循环 */
    guard = 200000U;
    while (((ADC1->ISR & ADC_ISR_EOC) == 0U) && (guard != 0U))
    {
        guard--;
    }
    if (guard == 0U)
    {
        /* 转换超时: 保持上一次有效码, 绝不卡死也绝不伪造轨值 (见上) */
        return adc_last_raw;
    }

    raw = (uint16_t)ADC1->DR;   /* 读 DR 自动清 EOC */
    adc_last_raw = (uint16_t)((uint32_t)raw << 4);
    return adc_last_raw;
}

/* USER CODE END 1 */

