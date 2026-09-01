#ifndef __SH1106_H
#define __SH1106_H

#include "main.h"
#include "spi.h"
#include <stdint.h>

#define OLED_WIDTH   128
#define OLED_HEIGHT  64

// GPIO定义
// 直接写 BSRR 寄存器 (PB12=CS, PB14=DC):
// 与 SWD 手动验证通过的时序完全一致, 绕过 HAL_GPIO_WritePin
#define OLED_CS_LOW()  (*(volatile uint32_t *)(0x48000418UL) = 0x10000000UL)  /* PB12=0 */
#define OLED_CS_HIGH() (*(volatile uint32_t *)(0x48000418UL) = 0x00001000UL)  /* PB12=1 */
#define OLED_DC_LOW()  (*(volatile uint32_t *)(0x48000418UL) = 0x40000000UL)  /* PB14=0 */
#define OLED_DC_HIGH() (*(volatile uint32_t *)(0x48000418UL) = 0x00004000UL)  /* PB14=1 */

// 基础通信
void SH1106_WriteCmd(uint8_t cmd);

void SH1106_WriteData(uint8_t data);


// 初始化
void SH1106_Init(void);


// 显存
void SH1106_SetPos(uint8_t page,uint8_t column);

void SH1106_Clear(void);

void SH1106_Update(void);


// 字符显示
void SH1106_ShowNum16(uint8_t x, uint8_t page, uint8_t num);
void OLED_ShowChar(uint8_t x, uint8_t page, char character);
void OLED_ShowString(uint8_t x, uint8_t page, const char *text);
void OLED_ShowSmallString(uint8_t x, uint8_t page, const char *text);



// 数值显示
void OLED_ShowFloat(uint8_t x, uint8_t page, float num, uint8_t decimal);

void OLED_ShowCurrent(float current);
void OLED_ShowCurrentAndMode(float current, const char *mode);


#endif