#ifndef CAL_COEF_H
#define CAL_COEF_H

#include <stdint.h>

/* ============================================================
 * 可标定常数 (物理实验表征固件专有) —— 全存 Flash 最后一页, 掉电不丢
 *
 * ---- 一、计算电流用的物理常数 ----
 *
 *   I = q·(c2 − c1)/T窗 − (m·I₊ + n·I₋)/N          T窗 = N·160us
 *
 *   q        库仑/码 = C·VREF/(65536·GAIN)   <- 三个标称值的乘积, 不必分开测
 *   I₊, I₋   两个参考电流 (A)
 *
 * q 的标称值 1.5259e-14 C/码 是由 C=100pF / VREF=3.3V / GAIN=0.33 三个**猜的**
 * 值推出来的 (标称值相乘, 没有一个测过)。直接测法 (没有拟合, 两点一除):
 *
 *   参考断开, 灌一个已知电流 I 积 T 秒, 读码差 Δc  ->  q = I·T/Δc
 *
 * 注意 C 与电平移位增益分不开, 也不**需要**分开 —— C 在公式里只出现在 q 里,
 * 别处一次都不出现。
 *
 * ---- 二、分段校准系数 ----
 *
 *   I_cal = a·I_raw + b
 *
 * 分三段, 按电流**绝对值**选段 (增益误差与符号无关, 正负共用一套):
 *   段 0: |I| < 1 nA          —— 1 pA ~ 1 nA
 *   段 1: 1 nA <= |I| < 10 nA
 *   段 2: 10 nA <= |I| <= 45 nA
 *
 * ---- 为什么走 Flash 而不是编译期宏 ----
 * 队友重新标定只要串口发一条指令, 不用装工具链、不用重编译烧录。当前 Flash
 * 里还是默认值 (a=1, b=0), **没有真实标定数据要兼容**, 所以改结构不用管迁移。
 *
 * ---- 传输定标 (MCU 端不做浮点文本解析, strtof 体积大且受 locale 影响) ----
 *   q        aC/码  (×1e18)    标称 15259
 *   I₊, I₋   pA     (×1e12)    实测 50232 / -50255
 *   a        ppm    (×1e6)
 *   b        fA     (×1e15)
 *
 * 协议见 physics_exp_test/README.md
 * ============================================================ */

#define CAL_SEG_COUNT   3

/* 上电时从 Flash 读一次; 无有效记录则全部回落到标称默认值 */
void Cal_Init(void);

/* 1 = Flash 里有有效记录 */
uint8_t Cal_IsValid(void);

/* 按电流绝对值选段 */
uint8_t Cal_SegmentOf(float current);

/* 应用分段校准; 未标定时原样返回 */
float Cal_Apply(float raw);

/* ---- 计算用物理常数 (未标定时返回标称默认值) ---- */
float Cal_GetQ(void);       /* 库仑/码 */
float Cal_GetIPos(void);    /* A */
float Cal_GetINeg(void);    /* A */

/* ---- 分段系数: "CAL NONE" / "CAL 3 a,b a,b a,b" (a 为 ppm, b 为 fA 整数) ----
 * 响应格式**不可追加字段** —— 上位机正则锚定行尾 (/^CAL 3 (\S+) (\S+) (\S+)$/),
 * 物理常数因此走下面单独一组指令 */
void Cal_Report(void);
void Cal_HandleSetLine(const char *line);   /* K<段>,<a_ppm>,<b_fA> */

/* 只清分段系数 (a=1, b=0)。物理常数是仪器的实测属性, 不跟着清 */
void Cal_Clear(void);

/* ---- 物理常数 (与 a/b 分开, 互不影响) ---- */
void Cal_ReportPhys(void);                  /* "CAL Q <q_aC>,<ip_pA>,<in_pA>" */
void Cal_HandlePhysLine(const char *line);  /* Q<q_aC>,<ip_pA>,<in_pA> */

#endif /* CAL_COEF_H */
