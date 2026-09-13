# 2636B 控制与标定工具链

用电脑控制 Keithley 2636B 源表，为纳安表提供已知、可重复的微弱输入电流。

---

## 一、结论先行：2636B 用 TSP，不是 SCPI

这是本项目最容易踩的坑。

2600B 系列（含 2636B）的**原生远程语言是 TSP**（Test Script Processor，内嵌 Lua）。
常规 SCPI 命令（`:SOUR:CURR`、`:SENS:FUNC:ON` 等）在 2636B 上**默认不可用**。

要用 SCPI 只有两条路，且都只为迁移 2400 的旧代码而存在：

| 路线 | 做法 | 代价 |
|---|---|---|
| 2400 仿真 | 加载 `Persona2400` / `2600B-800A.tsp` | 整机变成 2400，行为不再等价于 2636B |
| 逐条桥接 | `Initialize2400()` 后用 `Execute2400("<SCPI>")` | 每条命令包一层，且部分命令不完全兼容 |

**本项目一律使用原生 TSP。** 已核对的命令见 `instrument/keithley2636b.py` 顶部注释。

---

## 二、安装顺序

### 1. NI-VISA（必须，走 USB 的前提）

- 下载：<https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html>
- 需要 NI 账号；约 1.5 GB；用 **NI Package Manager** 安装
- **需要管理员权限**，装完建议重启
- USB 的 USBTMC 驱动由它提供。**不装它，USB 上永远看不到仪器**

> 注：NI-VISA 没有直链，下载页是 JS 单页应用，必须走官方表单。
> `winget` 官方源里也搜不到 `NI.VISA`。

### 2. Python 与 PyVISA（本机已完成）

```bash
python -m pip install pyvisa pyvisa-py
```

本机现状：Python 3.14.5（`C:\Python314`）、PyVISA 1.16.2、pyvisa-py 0.8.1 —— 均已装好。

选装（改善 pyvisa-py 的 LAN 发现能力，消除警告）：

```bash
python -m pip install psutil zeroconf
```

---

## 三、分阶段推进

严格按顺序，**上一步没验证通过就不要做下一步**。

| 阶段 | 脚本 | 目标 | 状态 |
|---|---|---|---|
| ① | `check_connection.py` | 确认电脑识别 2636B | ✅ 已就绪，**待硬件验证** |
| ② | `test_small_current.py` | 安全输出小电流并回读 | ✅ 已就绪，**待硬件验证** |
| ③ | `instrument/keithley2636b.py` 的封装函数 | 设定值/合规/开关/回读 | ✅ 已就绪 |
| ④ | `measurement/scan.py` | 自动扫描 + 存 CSV | ⬜ 未开始（等 ①② 通过） |
| ⑤ | `analysis/calibration.py` | 线性拟合、Gain/Offset、误差 | ⬜ 未开始（等 ④ 出数据） |

**④⑤ 刻意没有提前写。** 在通信没验证通过之前写自动标定代码，只会把
"仪器没连上"和"标定逻辑写错了"两种故障混在一起，极难排查。

### 阶段 ① 用法

```bash
cd tools/keithley
python check_connection.py
```

这个脚本**不会给任何电流**，只查链路。它会分别点名体检 NI-VISA 和 pyvisa-py
两个后端——因为 NI-VISA 缺失时 `pyvisa.ResourceManager()` 会**静默回退**到
pyvisa-py，让你误以为一切正常。

### 阶段 ② 用法

```bash
python test_small_current.py --dry-run   # 先看它打算做什么，不连仪器
python test_small_current.py             # 连仪器跑
```

---

## 四、安全默认值（与出厂不同，注意）

2636B 出厂时 `offmode = OUTPUT_NORMAL`，而该模式下 **`offlimitv` 默认 40 V**。
关输出后线上仍挂着 40 V 限压——对 ADA4530-1 这类高阻输入级是危险的。

本驱动默认改为：

| 设置 | 出厂 | 本驱动 | 理由 |
|---|---|---|---|
| `offmode` | `OUTPUT_NORMAL` | `OUTPUT_NORMAL` | 不用 `HIGH_Z`：手册明确警告它会磨损输出继电器，扫描时频繁开关不可取 |
| `offfunc` | 电压源 | `OUTPUT_DCAMPS` | 关输出时是"0 A 电流源" |
| `offlimitv` | **40 V** | **2 V** | 压到被测电路能承受的水平 |
| `source.limitv` | — | 2 V（`DEFAULT_COMPLIANCE_V`） | 电压合规 |

另外，`test_small_current.py` 里有一道硬天花板 `SAFETY_CEILING_A = 50e-9`：
序列里只要有点超过它，脚本**直接拒绝执行**，而不是跑一半才发现。

---

## 五、两条必须守住的原则

1. **标定用回读值，不用设定值。**
   设定 10.000 pA，实际可能是 10.013 pA。pA 级上这个差不能忽略。
   `apply_current()` 的返回值就是回读值，`measure_current()` 也是。

2. **先关输出、再改限压、最后才开输出。**
   `apply_current()` 内部已经保证这个顺序，不要绕开它自己拼命令。

---

## 六、已知风险（留待验证）

- **2636B 直接输出 pA 级电流的准确度**在最低量程上是有限度的，回读值本身
  也有不确定度。±1 pA 这个下限是否真的可用，需要用参考表实测确认，
  **不要拿数据手册的典型值当结论**。
- 若直接电流源在 pA 级不够稳，常见替代是**电压源 + 高阻电阻**（$I = V/R$）
  产生电流。这条路是否更优，等阶段 ④ 有数据后再评估。
