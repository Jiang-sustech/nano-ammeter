# 纳安表 (Nano-Ammeter)

基于电荷平衡原理的微弱电流测量仪（物理实验竞赛项目）。

## 项目结构

| 目录 | 说明 |
|------|------|
| `Version1/` | 正式版固件（STM32L431CCT6 + CubeMX/CMake 工程） |
| `SawtoothTest/` | 锯齿波独立测试工程（上电自动 bang-bang，验证参考电流通路） |

## 硬件架构

- **MCU**: STM32L431CCT6（MSI 48MHz → PLL 80MHz）
- **模拟前端**: ADA4530 积分器（C=30pF）+ ADG1219 参考开关（±5V / 100MΩ = ±50nA）
- **电平移位**: OPA735 反相求和器，`V_ad = 1.55 − 0.33·V_o`（V_o=±4.5V → 0.065~3.035V）
- **采集**: PA3 12-bit ADC（可切换 ADS8866 16-bit 外部 ADC）
- **显示**: SH1106 1.3" OLED（SPI2）

## 测量原理

1. **模式一：滞回电荷平衡**（连续 1s 窗口，±4.3V 滞回，160µs 节拍）
   `I = C·(Vo1−Vo2)/(N·T) − (m·I₊ + n·I₋)/N`
2. **模式二：双斜率硬积分**（I < 1nA 自动进入，10s 内多循环取平均）
3. **底噪标定**：ADG 全断纯积分 + 最小二乘拟合 `I_bias = C·dV/dt`

## 控制方式

- 串口指令：`S` = 启动测量，`X` = 停止测量
- 按键 PB4：短按 = 启动/刷新显示，长按 = 底噪标定
- SWD 直读：`voltage_buf`(6250 点原始采样)、`window_current`(每秒结果)

## 构建

```
cmake -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/gcc-arm-none-eabi.cmake -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

工具链：`C:/Users/40512/STM32Toolchain`（arm-none-eabi GCC 14.3.1 + CMake 4.3.1 + Ninja）

## 已知问题

- OLED 模块损坏待换新
- 板上 U3 开关区域疑似锡桥（U3.5↔U3.6）+ EN 走线断，待修复后验证锯齿波
- ADS8866 路径的 AINP/AINN 接线疑似反接（PA3 路径不受影响）
