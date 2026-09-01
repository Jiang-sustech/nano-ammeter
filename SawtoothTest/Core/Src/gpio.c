#include "main.h"

void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();

  /* ADG 引脚: PB1(IN), PB2(EN)
   * EN 高有效: 上电拉低 = 开关全断 */
  HAL_GPIO_WritePin(GPIOB, ADG_IN_Pin|ADG_EN_Pin, GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = ADG_IN_Pin|ADG_EN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}
