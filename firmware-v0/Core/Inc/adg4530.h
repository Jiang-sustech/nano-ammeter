#ifndef __adg4530_H
#define __adg4530_H


void ADG_Enable(void);

void ADG_Select_Positive(void);

void ADG_Select_Negative(void);


/* ADG1219 真值表 (数据手册):
 *   EN IN | A | B
 *   0  X  | Off Off
 *   1  0  | On  Off
 *   1  1  | Off On
 * EN(PB2) 高有效: 0=全部断开 (纯积分模式用: 参考电流不注入)
 * 实测: EN=1/IN=1 时 U3.6=-5.002V (B 导通) */
void ADG_Disable(void);


#endif