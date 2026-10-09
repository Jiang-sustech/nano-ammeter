# scripts/ —— PC 端脚本索引

连接仪器、采集、分析、出图的 Python / MATLAB 脚本。**按用途分成五格**：

| 子目录 | 干什么 |
|---|---|
| [`smu/`](smu/) | **2636B 源表控制与采集**（连仪器）|
| [`plotting/`](plotting/) | **绘图**（论文插图）|
| [`analysis/`](analysis/) | 标定、拟合、线性度、误差预算 |
| [`lib/`](lib/) | **公共模块** —— 被上面三者 import，改动要全仓回归 |
| [`misc/`](misc/) | 烧录、副本排查等杂项 |

> ⚠️ **import 约定。** 每个脚本开头有一段 `sys.path` 设置，把 `scripts/` 根 + 各子目录
> （`lib`/`smu`/`plotting`/`analysis`/`misc`）都加进 import 路径，所以脚本之间仍是
> `from cal_sweep import ...` 这样的**平铺 import**。**移动文件或改文件名要全仓回归。**
>
> **一律从仓库根目录运行**（相对路径如 `data/...` 都按仓库根解析）：
> `python scripts/smu/sweep_dense.py ...`

底层依赖：`pyvisa`（源表走 LAN）、`pyserial`、`numpy`、`matplotlib`、`scipy`
（仅 `analysis/outlier_test.py`）。

---

## 一、`lib/` —— 公共模块

被多个脚本 import，**改动要全仓回归**。

| 模块 | 被几个脚本依赖 | 内容 |
|---|---|---|
| `report_table.py` | **9** | 式(6) 的公共实现 `i_of()` + 报告表格（pA/nA、`u_c`、`Δ/u_c`）|
| `cv_linearity.py` | **7** | 物理常数、式(6)、残差与线性度工具 |
| `paper_style.py` | **4** | 论文图的统一风格（配色、字体、`caption()` 定位）|
| `uncal_vs_cal.py` | **3** | 未校准 / 校准后对比（也导图）|
| `dense_report.py` | 2 | 162 点密集标定的交付报告 + 三常数最小二乘 |
| `rel_accuracy.py` | 1 | 相对准确度（`plotting/paper_figs.py` 依赖）|
| `linearity_all.py` | 1 | 全数据拟合线性度 |
| `lowI_error_curve.py` | 1 | 误差-电流曲线（对数横轴）|

（计数由脚本实测，不是估的。）

---

## 二、`smu/` —— 2636B 源表控制与采集

连 Keithley 2636B（走 LAN / pyvisa）做标定与表征，也含串口采集本装置。

| 脚本 | 用途 |
|---|---|
| `cal_sweep.py` | **底层驱动**（串口 + 源表 + 测量原语 `ReadbackSampler`、`MEASURE_TIMEOUT_S`）。也被其它目录 import |
| `sweep_dense.py` | 密集电流网格扫描，**每次测量整窗回传**并存档。`--preset lowI` / `--points` / `--signed` / `--neg-only` / `--pos-only` / `--warm-ms` |
| `sweep_broad.py` | 宽范围扫描 |
| `sweep_dc.py` | 直流扫描 |
| `sweep_iv.py` | 光电探测器 I–V 曲线（A 路源电压 / B 路测电流）|
| `sweep_pulse.py` | 脉冲实验采集（TSP 脉冲源模式）|
| `nanoammeter_capture.py` | **单次采集** → `raw_<日期>_<值>nA.npz/.png/.log`。⚠️ 对 `firmware-v1` 要加 `--no-noise` |
| `test_smu.py` | 源表连通与功能自检 |
| `warm_test.py` | 预热必要性对照实验（always / never / first）|
| `pulse_real.py` | 真实脉冲波形重建（PC 翻转 / 仪器脉冲），含整窗原始码反推 |
| `pulse_probe.py` | TSP 脉冲能力探针；单因素拆分定位三条前置条件 |
| `pulse_introspect.py` | KIPulse 内省（**纯只读**，不开输出、不改状态）|
| `measure_transient.py` | 量 `EN=0` 断开瞬间注入的电荷 → 寄生电容 C_p |
| `switching_times.py` | 开关切换速度与装置各时间尺度 |
| `range_test.py` | 量程效应判定实验（同电流交替 autorange / 锁定）|
| `test_capture.py` | `nanoammeter_capture.py` 的离线回归测试（26 项，不碰串口）|

```bash
python scripts/smu/test_smu.py                              # 先自检源表连得上
python scripts/smu/nanoammeter_capture.py COM8 --no-noise    # 单次采集 ~15 s
python scripts/smu/sweep_dense.py --preset lowI
```

> ⚠️ `cal_sweep.py` 里的 `MEASURE_TIMEOUT_S` 必须**覆盖固件的最长窗口**。长窗
> 2026-09-17 由 10 s 改为 50 s，该常量与 `board_measure`/`board_warmup` 的默认值
> 一并从 40 提到 60。改固件的 `CURRENT_WIN_LONG_TICKS` 时回来一起看。

---

## 三、`plotting/` —— 绘图

把 `data/` 里的实测数据出成论文插图。统一风格见 `lib/paper_style.py`，
正文规范见 [`image_paper/IMAGE_SPEC.md`](../image_paper/IMAGE_SPEC.md)。

| 脚本 | 出什么 |
|---|---|
| `paper_figs.py` | 低电流段图（读 `data/lowI_pos/raw.csv`）|
| `paper_lin10_fig.py` | ±10 nA 那批的图 |
| `paper_pulse_fig.py` | 脉冲四格图（A/P/K/直流对照）|
| `photoelectric_fig.py` | 光电效应相关图 |
| `photo_data_fig.py` | 光电数据图 |
| `dark_current_fig.py` | 暗电流图（数据来自 `smu/sweep_iv.py`）|
| `residual_pct_plot.py` | ±5~45 nA 残差百分比图（`--bar` 出分组柱状图）|
| `console_figure.py` | **网页控制台截图**（注入真实测量数据后 Chrome headless 渲染）|

```bash
python scripts/plotting/paper_figs.py
python scripts/plotting/console_figure.py            # 深色版
python scripts/plotting/console_figure.py --light    # 浅色版（白底论文用）
```

> ⚠️ `console_figure.py` 截图前**不要开页面直接截** —— 空白的控制台只有布局没有内容。

---

## 四、`analysis/` —— 标定与分析

标定常数拟合、线性度、误差预算、脉冲分析。

| 脚本 | 用途 |
|---|---|
| `fit_constants.m` / `make_fit_csv.py` | 三常数（`M`/`I₊`/`I₋`）最小二乘拟合 + 汇总 CSV |
| `fit_linearity.py` / `fit_tau.py` | 早期拟合链 |
| `cal_table.py` | 逐点标定表 |
| `detect_limit.py` | 检测下限 |
| `lowI_compare.py` | 三条误差链：装置 / 设定 / 2636B 回读 |
| `outlier_test.py` / `.m` | 独立性检验与离群值判定（Python / MATLAB 双实现互校）|
| `reproducibility.py` | 残差是"电流的确定函数"还是"每轮随机"—— 跨轮复现性 |
| `seg0_budget.py` / `seg0_joint_fit.py` | 低电流段（段0）偏置拆项、并进拟合 |
| `fsw_hypothesis.py` | 「段0 偏置 = 锯齿轮翻转电荷注入」形状预测检验（已证伪）|
| `endpoint_codes.py` | 8 / 9 nA 两点的 ADC 端点码差 Δc |
| `range_effect.py` | 自动量程切换的影响（跨档外推法）|
| `window_sawtooth.py` | 窗口内锯齿波形态 |
| `raw_recalc.py` | 结果行 RAW 段代回式(6)，绕过屏幕取整 |
| `analyze_pulse.py` | 脉冲实验分析（方案见 `docs/脉冲电流实验方案.md`）|
| `sim_pulse_recover.py` | 脉冲恢复仿真 |
| `sim_node_decay.py` | 仿真 100 MΩ 输入端节点电压波形 |
| `wave_compress.py` | 整窗波形能压多少（用实测数据量，不猜）|
| `test_make_fit_csv.py` / `test_fit_constants.m` | 上面拟合链的回归测试 |
| `matlab_utf8.sh` | 在 MATLAB 里跑 `.m`（中文注释不乱码）|

```bash
# 对每个已知电流采一次 → 汇总 → 拟合
python scripts/smu/nanoammeter_capture.py COM7 --no-noise --true=25.0
python scripts/analysis/make_fit_csv.py -d . -o fit_data.csv
bash  scripts/analysis/matlab_utf8.sh "cd('scripts/analysis'); fit_constants('../../fit_data.csv')"
```

> 拟合的物理推导见 [`docs/校准方案推导.md`](../docs/校准方案推导.md)，
> 面向初学者的操作步骤见 [`docs/拟合操作指南.md`](../docs/拟合操作指南.md)。

---

## 五、`misc/` —— 杂项

| 脚本 | 用途 |
|---|---|
| `uart_flash.py` / `boot0_probe.py` | 串口 bootloader 烧录路径（独立于 ST-Link）。⚠️ 固定 115200 8E1，**不要改** |
| `console_copies.py` | 列出各控制台副本及版本，判断你打开的是哪一份 |
| `run_app.sh` | 用 SWD 把 PC 灌进 app 的 Reset_Handler（绕开"复位进 ROM bootloader"）|

`run_app.sh` / `boot0_probe.py` 里的 openocd 默认为 PATH 上的 `openocd`，
自定义安装用环境变量 `OPENOCD` / `OPENOCD_SCRIPTS` / `OPENOCD_CWD` 覆盖。

---

## 六、几条踩过的坑

- **CSV 硬约束**：表头只用英文、数值只用十进制整数、**不得有 BOM**、无注释行、换行 `\n`
  —— 中文 Windows 上 MATLAB 与 Origin 对 UTF-8 处理不一致。
- **单位坑**：`ReadbackSampler` 给的是安培，而所有 CSV 的 `true_nA` 是纳安。差 1e9，踩过多次。
- **聚合口径**：逐点均值 vs 逐次，两者差 1.3 倍，混用会得出不同结论。
- **等待上限要跟着窗口走**：跟不上就会出现"预热: 无响应"那类假故障（2026-09-16 撞过）。
- **改控制台要同步**：仓库里那份在 `pc-console/console.html` 是源头；桌面交付目录、release、
  backup 各有一份副本。改了不生效先用 `misc/console_copies.py` 查你打开的是哪份。
