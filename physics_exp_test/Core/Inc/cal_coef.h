#ifndef CAL_COEF_H
#define CAL_COEF_H

#include <stdint.h>

/* ============================================================
 * 分段校准系数 (物理实验表征固件专有)
 *
 *   I_cal = a·I_raw + b
 *
 * 分三段, 按电流**绝对值**选段 (增益误差与符号无关, 正负共用一套):
 *   段 0: |I| < 1 nA          —— 1 pA ~ 1 nA
 *   段 1: 1 nA <= |I| < 10 nA
 *   段 2: 10 nA <= |I| <= 45 nA
 *
 * 系数存在 Flash 最后一页, 掉电不丢。写入用定标整数传输:
 *   a: ppm (×1e6)      b: fA (×1e15)
 * MCU 端不做浮点文本解析 (strtof 体积大且受 locale 影响)。
 *
 * 协议见 physics_exp_test/README.md
 * ============================================================ */

#define CAL_SEG_COUNT   3

/* 上电时从 Flash 读一次; 无有效记录则校准无效 (a=1, b=0) */
void Cal_Init(void);

/* 1 = Flash 里有有效系数 */
uint8_t Cal_IsValid(void);

/* 按电流绝对值选段 */
uint8_t Cal_SegmentOf(float current);

/* 应用校准; 校准无效时原样返回 */
float Cal_Apply(float raw);

/* 发一行 "CAL NONE" 或 "CAL 3 a,b a,b a,b" (a 为 ppm, b 为 fA 整数) */
void Cal_Report(void);

/* 解析并写入 "K<seg>,<a_ppm>,<b_fA>"; 成功回 "CAL SET ...", 失败回 "CAL ERR ..." */
void Cal_HandleSetLine(const char *line);

/* 清除校准并回 "CAL CLEAR" */
void Cal_Clear(void);

#endif /* CAL_COEF_H */
