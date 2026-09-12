# 纳安表 (Nano-Ammeter)

基于电荷平衡原理的微弱电流测量仪（物理实验竞赛项目）。

## 项目结构

| 目录 | 说明 |
|------|------|
| `nano_ammeter/` | **当前固件（唯一在开发的一份）**：测量状态机 + OLED 显示 + 串口协议/波形回传 + 双斜率硬积分 + 编译期标定固件 |
| `纳安表控制台.html` | 演示用网页控制台（Web Serial API，Chrome/Edge 打开即可用） |
| `纳安表控制台_自测.js` | 控制台自测：从 HTML 提取内联脚本，在 DOM/串口桩里跑 26 项测试 |
| `console_src/` | 纯函数模块源文件（统计量、数据存档），见下 |
| `tools/` | `nanoammeter_capture.py` 采集脚本 + `uart_flash.py` 串口烧录器 + SWD 调试 TCL 脚本 |

### 分工：页面只采集与显示，分析交给 MATLAB / Origin

**不在 JS 里重造数学轮子。** 相关性、最小二乘、统计量、拟合一律导出原始数据后用
MATLAB / Origin 做 —— 那些工具本来就干这个，而且比手写实现更可信。

页面的职责就三件：

1. **采集** —— 发指令、收数据、校验和、断线恢复
2. **显示** —— 波形渲染（双路叠加、坐标轴、阈值线）和数值
3. **导出** —— 把原始码值原样存成文件，供外部工具分析

因此 CSV 的设计以「MATLAB/Origin 能直接读」为第一优先：

| 文件 | 内容 | 谁用 |
|---|---|---|
| `<stem>_wave.csv` | `index,t_us,code_int,code_ext`（双路同一索引） | MATLAB `readmatrix` / Origin 拖入 |
| `<stem>_noise.csv` | `t_s,code` | 同上 |
| `<stem>_meta.json` | 结果行、端口、时间、全部通信协议行（含中文） | 给人看 |
| `<stem>.png` | 波形图 | 给人看 |

**CSV 硬约束**（中文 Windows 上 MATLAB 与 Origin 对 UTF-8 处理不一致）：
表头只用英文、数值只用十进制整数、**不得有 BOM**、无注释行、换行统一 `\n`。

### console_src/ 与「内联」约定

`纳安表控制台.html` 必须保持**单文件、可双击打开**，所以
`console_src/archive.js`（导出与命名）的代码是**内联**进 HTML 的：

| 源文件 | 内容 | 自带测试 |
|---|---|---|
| `console_src/archive.js` | `captureStem` 命名、双路 CSV、底噪 CSV、元数据 JSON | `node console_src/archive_test.js`（18 项） |

**改这块逻辑请改源文件，再把内容贴回 HTML。** HTML 里对应段落有醒目注释标边界。
源文件末尾有 `if (typeof module !== 'undefined' && module.exports)` 守卫 ——
Node 下可 `require`，内联进浏览器时不会因 `module` 未定义而报错。

改完跑：
```
node console_src/archive_test.js    # 模块自身
node 纳安表控制台_自测.js            # 内联后的整体（25 项）
```
| `中间测试/` | 历史固件归档，**只读不再开发**：见下表 |

### 中间测试/（历史归档）

| 目录 | 说明 |
|------|------|
| `OLED_SH1106/` | 合并版固件前身：测量状态机 + 屏幕显示 + 串口控制/数据回传，硬件验证通过。`nano_ammeter/` 的直接祖先，文件列表一致 |
| `physics_experiment/` | 实机验证版固件：上电自动 bang-bang，积分器闭环 + 阈值 + 电平移位 + ADC 全链路跑通，实采到干净三角波；含 ADS8866 与内置 ADC 同拍并行采集 |
| `Version1/` | 早期正式版固件（测量核心出处，旧显示驱动） |
| `Version1.zip` | `Version1/` 的压缩副本，冗余，仅供参考 |

## 硬件架构

- **MCU**: STM32L431CCT6（MSI 48MHz → PLL 80MHz）
- **模拟前端**: ADA4530 积分器（C=30pF）+ ADG1219 参考开关（±5V / 100MΩ = ±50nA）
- **电平移位**: OPA735 反相求和器，`V_ad = 1.55 − 0.33·V_o`（V_o=±4.5V → 0.065~3.035V）
- **采集**: PA3 12-bit 内置 ADC（ADC 时钟 = SYSCLK 80MHz；ADS8866 16-bit 路径备用，宏切换）
- **显示**: SH1106 1.3" OLED（SPI2，SH1106-master 驱动）
- **串口**: 板载 CH340G（USB-C），USART1 PA9/PA10，115200 8N1

## 测量流程（单次语义）

一次指令 = 一次完整测量，测完自动回 IDLE 停住；无取消、无连续模式（越简单越可靠），
测量期间忽略一切指令。

1. **空闲**: ADG 关断（不注入参考电流），屏幕 `MODE:IDLE`，等待指令
2. **单次测量**（串口 `S`）：模式一滞回电荷平衡 1s 窗口，
   `I = C·(Vo1−Vo2)/(N·T) − (m·I₊ + n·I₋)/N`
   - ≥1nA → 显示 + 上报 → 自动回 IDLE
   - <1nA → 自动接模式二（双斜率硬积分 10s 多循环）→ 显示 + 上报 → 自动回 IDLE
3. **底噪标定**（串口 `N`，50s 纯积分，最小二乘拟合 `I_bias = C·dV/dt`）

## 串口协议（115200 8N1）

| 指令 | 功能 | 响应 |
|------|------|------|
| `S` | 单次测量（测完自动回空闲） | `START MODE1` → … → `I=+12.345 nA MODE=1`（或 MODE=2） |
| `N` | 底噪标定（50s） | `NOISE START (50s)` + 每 10s 进度 + `IBIAS=+xx.x fA` |
| `D` | 查询最近结果 | `RESULT I=+12.345 nA MODE=1`（无结果 `RESULT NONE`） |
| `B` | 回传最近完整窗口波形 | `WAVE 6250` + 12500 字节小端 u16 + 2 字节校验和 |
| 自动 | <1nA 切模式二 | `MODE2 START (I<1nA)` |

指令大小写不敏感，行结束符忽略；测量/底噪期间指令被忽略（结果出来后才接受下一次）。
按键功能已全部关闭（PB4 硬件故障），控制一律走串口/HTML 控制台。

## 构建

```
cmake --preset Debug
cmake --build build/Debug
```

工具链：STM32Toolchain（arm-none-eabi GCC + CMake + Ninja）。
烧录：ST-Link + OpenOCD（`interface/stlink-v2-1.cfg`，克隆调试器必须用此文件），
或 `tools/uart_flash.py`（BOOT0 拉高复位进系统 Bootloader）。

## 分模块标定（编译期宏，cal_mode.h）

标定固件与正常固件共用工程，通过 `cal_mode.h` 中 `CAL_MODE` 宏切换：

| 宏值 | 模式 | 行为 | 用途 |
|------|------|------|------|
| `CAL_NONE` | 正常测量 | 单次测量流程（默认） | 日常固件 |
| `CAL_ZERO` | 零位检查 | ADG 全断，采样 10s/轮，自动循环上报 `CAL ZERO: N=… MEAN=… Vadc=…V STD=… DRIFT=…uV/s IB=…fA` | 校验 1.55V 偏移、噪声水平、偏置电流 |
| `CAL_BANG` | 无输入 bang-bang | 模式一连续窗口，每秒上报 `CAL BANG: m=… n=… VPP=…V MIN=… MAX=… I=…nA` | 波峰波谷码 = 切换阈值点；配万用表 min/max 实测电压 → 传递函数两点标定 |

**标定流程建议**：
1. CAL_ZERO：V_adc 应为 1.55V（码 ≈30787），STD 反映噪声，DRIFT/IB 给偏置电流
2. CAL_BANG（RF1 无输入）：MIN/MAX 码与万用表 min/max 电压配对 → 拟合真实
   `V_adc = a + b·V_raw`，将实测值填入 `current.h` 的 `LEVELSHIFT_V_ADC_ZERO` / `LEVELSHIFT_GAIN`
3. 参考电流/电容联合值由摆幅斜率给出：`I_ref/C = dV_raw/dt`（R9 单独测准后可解出 C，
   填入 `current.h` 的 `C_INT` / `I_POS` / `I_NEG`）

注意：固定电流斜坡不可用（50nA/30pF = 1.67V/ms，约 3ms 即撞轨），故用锯齿波
波峰波谷作为天然阈值停靠点完成两点标定。

## 重要工程记录（踩过的坑）

1. **ADC 时钟必须用 SYSCLK**（80MHz，L431 fADC 上限内）。本工程 HAL 版本对 L431 的
   PLLSAI1 配置路径有缺陷（L431 PLLSAI1 无 M 分频器，HAL 写出非法配置且不使能），
   会导致 ADC 无时钟、转换永不完成。
2. **中断内 ADC 轮询不能依赖 HAL_GetTick**：TIM6 中断与 SysTick 同优先级时 tick 饿死，
   HAL 超时轮询变死循环。`ads8866.c` 已改为寄存器级轮询 + 计数保护。
3. **L4 校准函数会关掉 ADC**（ADEN=0），寄存器级读取前需重新使能。
4. **电平移位反相映射**（论文确认）：`V_ad = 1.55 − 0.33·V_o`，
   滞回阈值码: code_lower=0.131V↔V_o=+4.3V, code_upper=2.969V↔V_o=−4.3V。

## 已知问题

> **分三类标注验证状态。未验证的条目不要当成事实引用。**

### A. 需上板验证（尚未定论）

- **ADS8866 坏读分类**：`ads8866.c` 把 `0xFFFF` 一律判为「DOUT 常高 / MISO 断线」。
  但 `0xFFFF` 同时是**合法满量程码** —— 2026-09-11 实采数据里 37 个坏读**全部落在
  开头 0..36 的饱和前缀内**，斜坡中段一个都没有，说明它们是被误判的真实撞轨。
  修法：把分类从驱动层上移到 `current.c` 的 `ReadBoth()`（那里同时看得到两路，
  可交叉判定 —— 外部满量程**且**内置也在轨 = 真撞轨；外部 0xFFFF 而内置在中段
  = 真断线）。**改了要上板验证。**
- **模式二只覆盖一个极性**：上积相等 `raw <= code_target`（原始码下降 = V_int 上升
  = 与 I_POS **反号**的电流），正的小电流永远不触发 → 10 s → `HARD_TIMEOUT`。
- **拉回相未上板验证**：`Current_Start()` 的拉回相是为解决「窗口开头端点撞轨」
  （实测开头 38 个样本压在满量程）而加，有效性尚未在硬件上确认。

### B. 已确认的设计约束（不是故障）

- **量程**受参考电流约束：`|I_in| < I_ref`（参考电流是唯一能把积分器拉回的力，
  超过就单向跑飞钉轨）。
- **全部用标称值计算**：`C_INT` / `I_POS` / `I_NEG` / `LEVELSHIFT_*` 均为标称值，
  **绝对增益未标定**。标定流程见上一节，拟合结果直接填回 `current.h`（无独立
  拟合头文件）。
- **内置 ADC 有效分辨率 16 码**：12 位左移 4 位归一到 16 位域（`adc.c` 的
  `raw << 4`），实测所有码值都是 16 的倍数；满量程在 16 位域是 **65520**
  （= `4095 << 4`），不是 65535。ADS8866 是真 16 位，满量程 65535。

### C. 待复核（来源不可靠，勿引用）

以下条目来自早期记录，**证据不足或已被反驳**，保留只为提醒「需要重新确认」：

- PB4 按键是否真的恒读低 —— 代码里 `BUTTON_DISABLED` 的依据只是当时的判断
- 板上 8 MHz HSE 晶振是否起振 —— 固件实际在用 MSI，但那不能证明 HSE 坏
- BOOT0 按键焊点、CH340 的 USB-C 接触

**复核 HSE 的现成办法**（不改固件）：
```
mww 0x40021000 0x00010000     # RCC_CR: 置 HSEON (bit16)
mdw 0x40021000                # 读回: bit17(HSERDY)=1 即起振成功
```
复位即恢复，不影响 Flash 内容。
