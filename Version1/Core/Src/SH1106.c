#include "SH1106.h"
#include "font16.h"
#include <stdio.h>
#include <string.h>

uint8_t OLED_GRAM[8][128];


/***********************
 SPI底层 (寄存器直写)
 与 SWD 手动验证通过的时序完全一致, 绕过 HAL_SPI_Transmit
************************/
#define SPI2_SR  (*(volatile uint32_t *)(0x40003808UL))
#define SPI2_DR  (*(volatile uint32_t *)(0x4000380CUL))

/* 强制写入手动验证通过的完整配置.
 * 依据: 本 HAL 版本 HAL_SPI_Init 不使能 SPE (SPE 由发送函数临时置位),
 * 且 NSSP=1 下反复开关 SPE 会触发 MODF 关断 SPI.
 * CR1=0xC35C: BIDIMODE+BIDIOE(TX)+SPE+SSI+SSM+MSTR+BR=011(/16)
 * CR2=0x0700: DS=7(8bit), 无 NSSP/FRXTH */
static void OLED_SPI_Config(void)
{
    *(volatile uint32_t *)(0x40003800UL) = 0x00000000UL;
    *(volatile uint32_t *)(0x40003804UL) = 0x00000700UL;
    *(volatile uint32_t *)(0x40003800UL) = 0x0000C35CUL;
}

static void OLED_SPI_TX(const uint8_t *buf, uint16_t len)
{
    uint16_t i;
    uint32_t guard;

    for (i = 0U; i < len; i++)
    {
        guard = 0xFFFFFUL;
        while ((SPI2_SR & 0x02U) == 0U && guard-- > 0U) { }  /* 等 TXE */
        SPI2_DR = buf[i];
    }

    /* 等最后一个字节移出 (BSY 清零) */
    guard = 0xFFFFFUL;
    while ((SPI2_SR & 0x40U) != 0U && guard-- > 0U) { }      /* 等 !BSY */
}

void SH1106_WriteCmd(uint8_t cmd)
{
    OLED_CS_LOW();
    OLED_DC_LOW();
    OLED_SPI_TX(&cmd, 1);
    OLED_CS_HIGH();
    HAL_Delay(1);//命令字节间留 1ms: 模块命令解码器需恢复时间
                  //(成功案例均为慢节奏: SWD 手动 ~10ms/字节, 厂家示例位翻转)
}


void SH1106_WriteData(uint8_t data)
{
    OLED_CS_LOW();
    OLED_DC_HIGH();
    OLED_SPI_TX(&data, 1);
    OLED_CS_HIGH();
}


/***********************
 初始化
 SH1106 SPI模式
************************/

void SH1106_Init(void)
{
    /* 初始化序列对照屏幕说明书 (汉昇 HS13L01) 第 16 页官方示例:
     * 关键点: 0xAD 0x8B 开启内部 DC/DC 后必须设 0x33 (VPP=9V),
     * 否则面板驱动电压不足不亮; 对比度官方用 0xCF */
    HAL_Delay(100);//上电稳定 (官方建议 100ms)

    OLED_SPI_Config();//强制写入验证通过的 SPI 配置 (SPE+BIDIOE)

    SH1106_WriteCmd(0x02);//lower column address
    SH1106_WriteCmd(0x10);//higher column address
    SH1106_WriteCmd(0x40);//display start line
    SH1106_WriteCmd(0xB0);//page address

    SH1106_WriteCmd(0x81);//对比度
    SH1106_WriteCmd(0xFF);//亮度调到最大 (官方示例 0xCF, 用户要求最大亮度)

    SH1106_WriteCmd(0xA1);//segment remap
    SH1106_WriteCmd(0xA6);//normal display

    SH1106_WriteCmd(0xA8);//multiplex ratio
    SH1106_WriteCmd(0x3F);//duty = 1/64

    SH1106_WriteCmd(0xAD);//charge pump enable
    SH1106_WriteCmd(0x8B);//内供 VCC
    HAL_Delay(20);//DC-DC 启动需时间 (SPI 突发太快, 厂家示例位翻转天然带 ms 级间隔)
    SH1106_WriteCmd(0x33);//VPP = 9V (面板 DC/DC 电压, 缺了屏幕不亮)
    HAL_Delay(20);//VPP 爬升稳定

    SH1106_WriteCmd(0xC8);//COM scan direction

    SH1106_WriteCmd(0xD3);//display offset
    SH1106_WriteCmd(0x00);

    SH1106_WriteCmd(0xD5);//osc division
    SH1106_WriteCmd(0x80);

    SH1106_WriteCmd(0xD9);//pre-charge period
    SH1106_WriteCmd(0x1F);

    SH1106_WriteCmd(0xDA);//COM pins
    SH1106_WriteCmd(0x12);

    SH1106_WriteCmd(0xDB);//VCOMH
    SH1106_WriteCmd(0x40);

    SH1106_Clear();//先清屏再开显示, 避免上电花屏

    SH1106_WriteCmd(0xAF);//display ON

    SH1106_Update();
}



/***********************
 设置页地址
************************/

void SH1106_SetPos(uint8_t page,uint8_t column)
{
    if (page >= 8 || column >= OLED_WIDTH) {
        return;
    }

    column += 2;

    SH1106_WriteCmd(0xB0+page);

    SH1106_WriteCmd(0x10+(column>>4));

    SH1106_WriteCmd(column&0x0F);
}



/***********************
 清屏缓存
************************/

void SH1106_Clear(void)
{
    memset(OLED_GRAM,0,sizeof(OLED_GRAM));
}



/***********************
 刷新屏幕
************************/

void SH1106_Update(void)
{
    uint8_t page;
    uint8_t col;

    for(page=0;page<8;page++)
    {
        SH1106_SetPos(page, 0);
        /* 逐字节独立 CS 周期 (与 SWD 手动验证/厂家示例一致):
         * 该模块未验证过 CS 持续拉低的流式写入, 列指针跨 CS 周期保持已验证 */
        for (col = 0U; col < OLED_WIDTH; col++)
        {
            OLED_CS_LOW();
            OLED_DC_HIGH();
            OLED_SPI_TX(&OLED_GRAM[page][col], 1);
            OLED_CS_HIGH();
        }
    }
}


/***********************
字符显示部分
************************/
void SH1106_ShowNum16(uint8_t x, uint8_t page, uint8_t num)
{
    if (num > 10 || x > OLED_WIDTH - 16 || page > 6) {
        return;
    }

    memcpy(&OLED_GRAM[page][x], Num16[num], 16);
    memcpy(&OLED_GRAM[page + 1][x], &Num16[num][16], 16);

}


/***********************
浮点显示
************************/

void OLED_ShowFloat(uint8_t x, uint8_t page, float num, uint8_t decimal)
{
    char buf[20];
    char fmt[10];

    if (decimal > 6) {
        decimal = 6;
    }

    snprintf(fmt, sizeof(fmt), "%%.%uf", decimal);
    snprintf(buf, sizeof(buf), fmt, (double)num);

    OLED_ShowString(x, page, buf);
}


/***********************
微电流专用显示
************************/
void OLED_ShowCurrent(float current)
{
    OLED_ShowCurrentAndMode(current, "");
}

void OLED_ShowChar(uint8_t x, uint8_t page, char character)
{
    const uint8_t *glyph;

    if (character >= '0' && character <= '9') {
        glyph = Num16[(uint8_t)(character - '0')];
    } else if (character == '.') {
        glyph = Num16[10];
    } else {
        return;
    }

    if (x <= OLED_WIDTH - 16 && page <= 6) {
        memcpy(&OLED_GRAM[page][x], glyph, 16);
        memcpy(&OLED_GRAM[page + 1][x], &glyph[16], 16);
    }
}

void OLED_ShowString(uint8_t x, uint8_t page, const char *text)
{
    while (text != NULL && *text != '\0' && x <= OLED_WIDTH - 16) {
        OLED_ShowChar(x, page, *text++);
        x += 16;
    }
}

void OLED_ShowSmallString(uint8_t x, uint8_t page, const char *text)
{
    uint8_t column;

    if (page >= 8) {
        return;
    }

    while (text != NULL && *text != '\0' && x < OLED_WIDTH - 5) {
        if ((uint8_t)*text >= 32 && (uint8_t)*text <= 127) {
            for (column = 0; column < 5; column++) {
                OLED_GRAM[page][x + column] = Font5x7[(uint8_t)*text - 32][column];
            }
        }
        x += 6;
        text++;
    }
}

void OLED_ShowCurrentAndMode(float current, const char *mode)
{
    char value[20];

    if (current < 0.0f) {
        current = 0.0f;
    }

    snprintf(value, sizeof(value), "%.3f", (double)(current * 1000000000.0f));
    SH1106_Clear();
    OLED_ShowString(0, 0, value);
    OLED_ShowSmallString(102, 1, "nA");
    OLED_ShowSmallString(0, 3, mode == NULL ? "" : mode);
    SH1106_Update();
}