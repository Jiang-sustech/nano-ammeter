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

### console_src/ 与「内联」约定

`纳安表控制台.html` 必须保持**单文件、可双击打开**，所以下面两个模块的代码是
**内联**进 HTML 的 `<script>` 里的：

| 源文件 | 内容 | 自带测试 |
|---|---|---|
| `console_src/stats.js` | 统计量：压轨前缀、坏读计数、差分统计、相关系数、最小二乘、上升沿 | `node console_src/stats_test.js`（45 项，与 Python 实现逐位交叉验证） |
| `console_src/archive.js` | 数据存档：`captureStem` 命名、双路 CSV、底噪 CSV、元数据 JSON | `node console_src/archive_test.js`（18 项） |

**改这两块逻辑请改 `console_src/` 的源文件，再把改后的内容贴回 HTML。**
HTML 里对应的段落有醒目注释标出边界。`console_src/ref_data.json` 是 2026-09-11
的实采数据夹具，供交叉验证使用。

两个源文件末尾都有 `if (typeof module !== 'undefined' && module.exports)` 守卫 ——
Node 下可 `require`，内联进浏览器时不会因 `module` 未定义而报错。

改完**两处都要跑**：
```
node console_src/stats_test.js      # 模块自身
node console_src/archive_test.js
node 纳安表控制台_自测.js            # 内联后的整体（26 项）
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

- 模拟前端待验证：模拟部分电源未接（±5V/±6V 轨未上电），当前 ADC 读数为运放
  悬空电平，测量结果无物理意义；接电后先做 CAL_ZERO（V_adc 应为 1.55V）再验证
  锯齿波，异常时按 U3 开关关断/锡桥、C16 30pF、ADA4530 顺序排查
- ADS8866 的 AINP/AINN 在板上接反（内置 ADC 路径不受影响；修好后把
  `ads8866.c` 的 `USE_MCU_ADC` 改为 0 可切 16 位）
- PB4 按键硬件故障（恒读低），按键功能已全部禁用
- BOOT0 按键焊点不良，串口烧录需飞线 BOOT0→3V3 + 复位
- 板载 CH340 的 USB-C 线易松脱（串口掉线先查线）
