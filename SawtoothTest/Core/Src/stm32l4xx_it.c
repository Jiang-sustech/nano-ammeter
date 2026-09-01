#include "main.h"
#include "stm32l4xx_it.h"
#include "tim.h"
#include "current.h"

void SysTick_Handler(void)
{
  HAL_IncTick();
}

void TIM6_DAC_IRQHandler(void)
{
  HAL_TIM_IRQHandler(&htim6);
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance == TIM6)
  {
    Current_Process();
  }
}
