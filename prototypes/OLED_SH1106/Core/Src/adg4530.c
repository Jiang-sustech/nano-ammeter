#include "main.h"
#include "adg4530.h"

/* ============================================================
 * ADG1219 开关控制 (数据手册真值表):
 *   EN IN | Switch A | Switch B
 *   0  X  | Off      | Off
 *   1  0  | On       | Off
 *   1  1  | Off      | On
 * EN(PB2) 高有效: 1=使能, 0=全部断开
 * IN(PB1): 0=Switch A (+5V, 实测 U3.7), 1=Switch B (-5V, 实测 U3.5)
 * 实测依据: EN=1/IN=1 时 U3.6=-5.002V (万用表)
 * ============================================================ */

void ADG_Enable(void)
{
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_2,GPIO_PIN_SET);     /* EN 高有效 */
}


void ADG_Select_Positive(void)
{
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_1,GPIO_PIN_RESET);   /* A: +5V */
}


void ADG_Select_Negative(void)
{
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_1,GPIO_PIN_SET);     /* B: -5V */
}


void ADG_Disable(void)
{
    HAL_GPIO_WritePin(GPIOB,GPIO_PIN_2,GPIO_PIN_RESET);   /* EN=0 全断 */
}
