#ifndef CURRENT_H
#define CURRENT_H

#include "stdint.h"

/* ============================================================
 * 电平移位电路映射 (反相求和器, 论文确认):
 *   V_o=+4.5V -> V_ad=0.065V; V_o=-4.5V -> V_ad=3.035V
 *   V_adc = LEVELSHIFT_V_ADC_ZERO - LEVELSHIFT_GAIN * V_int
 *   V_int = (LEVELSHIFT_V_ADC_ZERO - V_adc) / LEVELSHIFT_GAIN
 * 若电阻值改动, 只需改这两个宏
 * ============================================================ */
#define LEVELSHIFT_GAIN        0.33f
#define LEVELSHIFT_V_ADC_ZERO  1.55f    /* 积分器 0V 对应 ADC 电压 */

/* ============================================================
 * USE_INTERNAL_ADC —— 内置 ADC (STM32 ADC12) 是否参与
 * ============================================================
 * 2026-09-13 用户裁定: "代码应该和内置 ADC 完全无关, 不论是什么状态、
 * 什么测量模式、什么阶段"。按用户要求做法 —— **内置路的代码保留但停用**;
 * 所有活跃路径改用外部 ADS8866。测完之后再决定删除还是回滚。
 *
 *   1 -> 原状: 控制值/端点/结果都用**内置路**, 外部路只作观测 (X=)
 *   0 -> 试验: **全部用外部路**; 内置路的读取被 #if 挡掉, 代码保留可查
 *
 * **回滚就是把这一个数字改回 1**, 不需要动别处。
 *
 * 改 0 之前要知道的四件事:
 *   ① 标定常数 q / I± **一定会变** —— 换了 ADC, 端点码的尺度与偏置都不同,
 *      必须重新标定。这是预期内的, 不是故障。
 *   ② 坏读判据失去"两路交叉" —— 原来靠"外部满量程 **且** 内置也在轨"区分
 *      "真撞轨"与"MISO 真断线"; 只剩一路就分不清了 (见 ReadBoth 的注释)。
 *   ③ 外部路的转换等待是 `DWT_DelayUs` **盲等** (已实测: 见 ads8866.c 的
 *      T_CONV_US 注释)。它当观测用时时序不紧张; 一旦当控制用, 要求完全不同。
 *   ④ ~~每拍从 19us 降到 11us, 控制判决点往后挪 11us~~ —— **实测推翻了**:
 *      外部路单次实为 18.4us (不是推算的 11us, 漏了 6.36us 的 SPI+GPIO 开销),
 *      而控制判决点其实**几乎没动** —— 内置路的采样孔径也是从 tick+0 开始,
 *      与外部路的 CONVST 边沿基本同刻。实测计数逐位吻合 (差 <=1 拍) 印证了这点。
 * ============================================================ */
#define USE_INTERNAL_ADC   0

/* 试验期专用: 外部读取之前的预延时 (us)。
 * 目的是把 CONVST 边沿从"紧贴 TIM6 中断入口"推回**原来的时间位置** ——
 * 原来前面隔着一次内置读取 (实测 8.2us), 现在没有了, CONVST 就落在了
 * 中断入口附近; 实测后果是 0xFFFF 坏读从每窗 ~0 涨到 ~31 次, 而**首拍
 * 坏读率 1/6** (比平均高 33 倍)。取 8 是照原来那 8.2us 复原。
 * 设 0 可以关掉, 用来做对照实验。 */
#define EXT_PREDELAY_US    8U

/* 硬件常量 (计算与标定模块共用) */
/* 积分电容: 100pF 为标称值 (用户按丝印确认; 早期 30pF 是错的假设) */
#define C_INT            100e-12f   /* 积分电容 (F) */

/* ---- 参考电流 (实测) ----
 * 测法: 短接积分节点, 2100 DCV @10PLC 量 100MΩ 上的压降。
 *
 *   V+ = 4.99703 V (±0.004%, 2100 DCV 量程)
 *   V- = -4.99938 V
 *   R  = 99.48 MΩ  (±1%, 2100 的 100MΩ 量程 —— 这是弱环节)
 *
 * 拆开看修正量的来源:
 *   V 那部分 4.99703/5.000 = -0.059%    <- 实打实
 *   R 那部分 100/99.48      = +0.523%   <- 带 ±1%, 基本淹没在表的不确定度里
 *
 * **注: 这两个值不影响最终精度。** 读数 = q·Δc/T - (m·I₊ + n·I₋)/N,
 * 其中 I₊/I₋ 的"共同尺度"误差是纯增益(进 a)、"不对称"是纯偏移(进 b),
 * 而 a/b 是对着 2636B 灌的已知输入电流拟合出来的 —— 两者都被吸收。
 * 此处写实测值是为了**记录**仪器的真实状态, 以及进报告的不确定度预算。 */
#define I_POS            50.232e-9f
#define I_NEG           (-50.255e-9f)

/* 模式一 滞回阈值 (积分器域, 在 ±4.5V 线性区内) */
#define THRESH_INT_UPPER    4.3f
#define THRESH_INT_LOWER   (-4.3f)

/* 双斜率硬积分已废弃 (2026-09-13): 1pA 时上积相要 200 秒才积得起 2V, 覆盖不到
 * pA 量程; 而且它靠 `EN=0`(Hi-Z) 做"零参考", 那个浮空节点的寄生电容会注入
 * 一个指数衰减的暂态电荷 (实测 C_p ~5pF -> Q ~24pC)。小电流模式取而代之。
 * HARD_* 那组宏已删 —— 全工程再无引用。 */

/* Current_PullToZero 的守卫: 每次 Adc_ReadRaw() 约 8us (ADC 时钟 80MHz,
 * 采样 640.5 周期)。最坏行程 5.3V —— 从电平移位饱和轨 (±5.3V) 拉到 0V,
 * 净电流 50nA -> 500V/s -> 10.6ms -> 约 1300 次读。
 * 取 10000 (约 80ms) = 7.5 倍余量; 即使输入与参考相抵到净 12nA 也够。
 * 这是防挂死保险丝, 不是量程判据 —— 超时即按当前状态继续, 不阻断测量。 */
#define PULLZERO_GUARD       10000U

/* ---- 模式一: 滞回电荷平衡 (连续循环开窗, 每秒一个结果) ---- */
/* 启动: 重置窗口计数 + 开 ADG, 然后**阻塞**执行拉回相 —— 把积分器从
 * 任意初始状态(典型是空闲期被被测电流推到的压轨)拉到接近 0V, 再开窗。
 * 目标是 0V 而不是某个阈值: 拉到边界是赌电流符号, 赌错那侧余量为 0、
 * 第一拍就撞轨; 0V 则两个方向各留 4.3V, 且第一个相位是正常长度。
 * 最坏阻塞约 106ms(45nA 输入与参考相抵), PRECOND_GUARD 约 400ms。
 * 必须在 HAL_TIM_Base_Start_IT 之前调用: 拉回期间 TIM6 未跑, 无 ISR 竞争 */
void Current_Start(void);

/* 拉回相未能在守卫内到位次数, 正常恒为 0 (仅诊断, 不参与控制) */
extern volatile uint32_t precond_timeout;
void Current_Stop(void);               /* 停止: 停 TIM6 + 关 ADG (空闲不注入参考) */
void Current_Process(void);            /* TIM6 中断每 160us 调用一次 */
/* 读一次控制值 —— 按 USE_INTERNAL_ADC 决定用内置还是外部路。
 * 凡是需要"和测量用同一条路"的地方都该用它, 不要直接调 Adc_ReadRaw()。 */
uint16_t Current_ReadControl(void);
/* 式(6) —— **全量程唯一的结果计算函数**。
 * 只看窗口的首尾两个码, 分母是 m+n (计数), 所以对 1s 和 10s 窗口同样成立。
 * 端点码由 Current_GetFirstCode()* 与 Current_GetLastCode()* 给。
 * 内部读全局 m_count/n_count —— 只能在窗口封存后调用。 */
float Calculate_Current_From(uint16_t c_first, uint16_t c_last);
uint16_t Current_GetFirstCode(void);     /* 窗口起点 V_01 (内置路) */
uint16_t Current_GetFirstCodeExt(void);  /* 窗口起点 V_01 (外部路) */
uint16_t Current_GetLastCode(void);      /* 窗口最后一拍 (内置路) */
uint16_t Current_GetLastCodeExt(void);   /* 窗口最后一拍 (外部路) */
/* 窗口长度 (拍)。短 6250 拍 = 1s, 长 62500 拍 = 10s。
 * Current_SetWindowTicks() 必须在 Current_Start() **之前**调用 (Start 按它算抽点)。*/
#define CURRENT_WIN_SHORT_TICKS   6250U     /* 1 s  —— |I| >= 1 nA */
#define CURRENT_WIN_LONG_TICKS    62500U    /* 10 s —— |I| <  1 nA, 靠时间换信噪比 */
void Current_SetWindowTicks(uint32_t ticks);
uint32_t Current_GetWindowTicks(void);      /* 最近窗口的**实际拍数** (结果行 T= 用它) */
float Current_GetWindowResultExt(void); /* 同一窗口由 ADS8866 算出的结果 */
uint8_t Current_WindowFinished(void);  /* 窗口完成脉冲 (每秒一次, 消费式) */
void Current_ClearWindowFlag(void);
float Current_GetWindowResult(void);   /* 上一窗口电流结果 (中断内算好, 无竞态) */
uint16_t Current_GetSample(uint32_t index); /* 调试: 读取存储的原始码 */
uint32_t Current_GetSampleCount(void);      /* 当前窗口已采样数 (B 指令回传用) */

/* ---- ADS8866 外部 16 位 (USE_INTERNAL_ADC=0 时它就是控制与结果唯一来源) ----
 * 与 voltage_buf 同拍。两路都读时, 外部读取**在内置之后** (内置实测 8.2us),
 * 即外部采样时刻比内置晚约 8.2us —— 这个相位偏斜是固定且已知的。
 * 数值域相同 (0~65535), 可直接与 Current_GetSample() 逐点对照。 */
uint16_t Current_GetExtSample(uint32_t index);   /* 外部 16 位原始码 */
uint32_t Current_GetExtSampleCount(void);        /* 与 GetSampleCount 同口径 */
uint32_t Current_GetExtBadRead(void);            /* 外部 ADC 坏读计数 (0x0000/0xFFFF) */

/* ---- 时序实测 (2026-09-13 加, 由 E 指令回显) ----
 * 8.2us / 10.6us 原来都是按**配置推算**的, 从没实测。而它直接决定一件事:
 * ADS8866 的转换时间是不是真的 <= 驱动里 `DWT_DelayUs(9)` 那个**盲等** ——
 * 不够的话 SPI 读回来的是**上一次的样本**, 一个隐性的滞后一拍。 */
extern volatile uint32_t t_int_ns;      /* 内置路单次读取耗时 (ns); USE_INTERNAL_ADC=0 时为 0 */
extern volatile uint32_t t_ext_ns;      /* 外部路单次读取耗时 (ns) */
extern volatile uint32_t ext_ffff_cnt;  /* 外部路返回 0xFFFF 的累计次数 */
/* 坏读落在窗口的哪儿 —— 分辨"开窗瞬间的突变"与"均匀散布" (见 current.c 的注释) */
extern volatile uint32_t ext_ffff_early;    /* 落在窗口前 10 拍内的 */
extern volatile uint32_t ext_ffff_late;     /* 其余 */
uint32_t Current_GetMCount(void);           /* 最近完整窗口 POS 周期数 (标定用) */
uint32_t Current_GetNCount(void);           /* 最近完整窗口 NEG 周期数 (标定用) */
uint16_t Current_GetMinCode(void);          /* 最近窗口最小码 = 下阈值切换点 (标定用) */
uint16_t Current_GetMaxCode(void);          /* 最近窗口最大码 = 上阈值切换点 (标定用) */
float Current_GetSwingVoltage(void);   /* 1s 窗口内积分器电压峰峰值 (V) */

/* ---- 积分器复位 (阻塞) ----
 * 把积分器拉到零位。**必须在 TIM6 未跑时调用** —— Adc_ReadRaw 不可重入,
 * 不能与 ISR 里的转换并发。
 *
 * 注: 测量窗口不用这个 —— 它的拉回相在 ISR 里 (Precond_Tick), 因为要拿
 * "拉回的最后一拍"当窗口起点 V_01, 必须在同一套节拍网格上。这个阻塞版是
 * 给 CAL_ZERO 与手动模式 (M) 用的。 */
void Current_PullToZero(void);

/* ---- 手动模式 (串口 M 指令): 阻塞连采, 不进 TIM6 网格 ----
 * 用途一 ****量 tau****:   M0 = 拉零后断开参考(EN=0), 录下寄生电容经 100MΩ
 *   泄放注入的指数衰减暂态。tau = R*C_p, 注入电荷 Q = C_p*5V。
 * 用途二 ****q 开环标定****: M1/M2 = 拉零后固定 +5V / -5V 参考, 录线性斜坡;
 *   q = I± * T / Δc。固定极性全程不进 EN=0, 避开了上面的暂态。
 *
 * 采样写进模式一那两个缓冲, 完成后用现成的 B / X 指令回传 (6250 点)。
 * 阻塞约 130ms; **只能在 IDLE 调用** (TIM6 必须停 —— Adc_ReadRaw 不可重入)。*/
void Current_ManualRun(uint8_t sub);        /* 0=断开 / 1=+5V / 2=-5V / 3=高阻<->+5V交替 */
void Current_ManualLine(const char *line);  /* M 指令整行解析 + 回显 */



#endif
