# 硬件文件

纳安表的原理图与制板文件。

## 内容

| 文件 | 说明 |
|---|---|
| [`Schematic.png`](Schematic.png) | 原理图（整页）|
| [`gerber/`](gerber/) | Gerber 制板文件（4 层板 + 3 个钻孔文件）|

## 板子概况

- **4 层板**（`G1` / `G2` 为内层）
- MCU：STM32L431CCT6
- 模拟前端：ADA4530 静电计运放（积分电容 100 pF）+ ADG1219 参考开关 + OPA735 电平移位
- 采集：内置 12-bit ADC（控制路）+ ADS8866 16-bit（观测路，SPI）
- 显示：SH1106 1.3" OLED（SPI）
- 串口：板载 CH340G（USB-C）
- 参考电流：±50 nA（由 ±5 V 经 100 MΩ 得到，**标称值**）

## Gerber 层对应

| 文件 | 层 |
|---|---|
| `Gerber_TopLayer.GTL` | 顶层铜 |
| `Gerber_InnerLayer1.G1` | 内层 1 |
| `Gerber_InnerLayer2.G2` | 内层 2 |
| `Gerber_BottomLayer.GBL` | 底层铜 |
| `Gerber_TopSilkscreenLayer.GTO` | 顶层丝印 |
| `Gerber_BottomSilkscreenLayer.GBO` | 底层丝印 |
| `Gerber_TopSolderMaskLayer.GTS` | 顶层阻焊 |
| `Gerber_BottomSolderMaskLayer.GBS` | 底层阻焊 |
| `Gerber_TopPasteMaskLayer.GTP` | 顶层钢网 |
| `Gerber_BoardOutlineLayer.GKO` | 板框 |
| `Gerber_DocumentLayer.GDL` | 文档层 |
| `Gerber_DrillDrawingLayer.GDD` | 钻孔图 |
| `Drill_PTH_Through.DRL` | 通孔（镀铜）|
| `Drill_PTH_Through_Via.DRL` | 过孔 |
| `Drill_NPTH_Through.DRL` | 非镀铜孔 |

`FlyingProbeTesting.json` 与 `PCB下单必读.txt` 是 EasyEDA 导出时附带的标准文件，
非设计内容。

> 导出工具：**EasyEDA Pro**。Gerber 注释里不含姓名、路径或邮箱等个人信息。

## 注意

- **元件值与阈值均为标称值**，绝对增益尚未标定。实际测得的参考电流与
  积分电容值见 `firmware-v1/Core/Inc/current.h` 的注释
  （`I_POS` / `I_NEG` 处记录了实测方法与结果）。
- 制板前请自行复核板框、钻孔与阻抗要求；本仓库不对制板结果负责。
- 未经许可证选定，**请勿视为已授权使用**（见仓库根 README 的「许可」一节）。
