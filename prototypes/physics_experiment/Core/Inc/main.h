#ifndef __MAIN_H
#define __MAIN_H

#include "stm32l4xx_hal.h"

/* ADG1219 开关引脚 (PB1=IN, PB2=EN) */
#define ADG_IN_Pin GPIO_PIN_1
#define ADG_IN_GPIO_Port GPIOB
#define ADG_EN_Pin GPIO_PIN_2
#define ADG_EN_GPIO_Port GPIOB

void Error_Handler(void);
void MX_GPIO_Init(void);

#endif
