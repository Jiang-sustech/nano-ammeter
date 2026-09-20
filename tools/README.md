# tools/ —— 脚本索引

本目录是**测量与分析脚本的工作目录**，不是分层代码库。全部文件**平铺**在这一层，
因为脚本之间靠

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cal_sweep import ...
```

互相引用 —— 拆子目录要改几十个文件的 import 块，风险大于收益。**要"按功能找"就查本表。**

底层依赖：`pyvisa`（源表走 LAN）、`pyserial`、`numpy`、`matplotlib`、`scipy`（仅 `outlier_test.py`）。

---

## 一、公共模块 —— 被多个脚本 import，**改动要全仓回归**

| 脚本 | 被几个脚本依赖 | 内容 |
|---|---|---|
| `cal_sweep.py` | **9** | 串口/源表底层、测量原语、`MEASURE_TIMEOUT_S`、`ReadbackSampler`。也在 CLI 下当标定扫描用 |
| `cv_linearity.py` | **8** | 物理常数、式(6)、残差与线性度工具 |
| `report_table.py` | **7** | 式(6) 的公共实现 `i_of()` + 报告表格（pA/nA、`u_c`、`Δ/u_c`）|
| `paper_style.py` | **4** | 论文图的统一风格（配色、字体、`caption()` 定位）|
| `uncal_vs_cal.py` | **3** | 未校准 / 校准后对比（也导图）|
| `dense_report.py` | 2 | 162 点密集标定的交付报告 + 三常数最小二乘 |
| `sweep_pulse.py` | 2 | 脉冲点表、真值口径 `δ`、TSP 脉冲配置。也在 CLI 下采集 |
| `rel_accuracy.py` | 1 | 相对准确度（`paper_figs.py` 依赖它）|
| `linearity_all.py` | 1 | 全数据拟合线性度（`switching_times.py` 依赖它）|

（计数由脚本实测，不是估的。改上面任何一个都要跑一遍全仓回归。）

⚠️ `cal_sweep.py` 里的 `MEASURE_TIMEOUT_S` 必须**覆盖固件的最长窗口**。
长窗 2026-09-17 由 10 s 改为 50 s，该常量与 `board_measure`/`board_warmup` 的默认值
一并从 40 提到 60。改固件的 `CURRENT_WIN_LONG_TICKS` 时回来一起看。

---

## 二、采集（连仪器）

| 脚本 | 用途 |
|---|---|
| `sweep_dense.py` | 密集电流网格扫描，**每次测量整窗回传**并存档。`--preset lowI` / `--points` / `--signed` / `--neg-only` / `--pos-only` / `--warm-ms` |
| `sweep_broad.py` | 宽范围扫描 |
| `sweep_dc.py` | 直流扫描 |
| `sweep_pulse.py` | 脉冲实验采集（TSP 脉冲源模式）|
| `cal_sweep.py` | 标定扫描（CLI）；也是所有人的底层库 |
| `warm_test.py` | 预热必要性对照实验（always / never / first）|
| `nanoammeter_capture.py` | 单次采集 → `raw_<日期>_<值>nA.npz/.png/.log`。⚠️ 对 `firmware-v1` 要加 `--no-noise`（该固件已删底噪指令）|
| `pulse_real.py` | 真实脉冲波形重建（PC 翻转 / 仪器脉冲），含整窗原始码反推 |
| `test_smu.py` | 源表连通与功能自检 |

---

## 三、标定与线性度

| 脚本 | 用途 |
|---|---|
| `uncal_vs_cal.py` | 未校准 / 校准后两图 + 相对偏差、残差 RMS |
| `rel_accuracy.py` | 相对准确度 |
| `linearity_all.py` | 把**全部**数据一起拟合，算所有点的线性度 |
| `cal_table.py` | 逐点标定表 |
| `residual_pct_plot.py` | ±5~45 nA 残差百分比图（`--bar` 出分组柱状图）|
| `outlier_test.py` / `.m` | 独立性检验与离群值判定（Python / MATLAB 双实现互校）|
| `reproducibility.py` | 残差是"电流的确定函数"还是"每轮随机"—— 跨轮复现性 |
| `endpoint_codes.py` | 8 / 9 nA 两点的 ADC 端点码差 Δc 逐个列出 |
| `range_effect.py` | 自动量程切换的影响（跨档外推法）|
| `range_test.py` | 量程效应判定实验（同电流交替 autorange / 锁定）|
| `window_sawtooth.py` | 窗口内锯齿波形态 |
| `fit_linearity.py` / `fit_tau.py` / `make_fit_csv.py` / `fit_constants.m` | 早期拟合链（README 有引用，仍在使用）|
| `test_make_fit_csv.py` / `test_fit_constants.m` | 上面两个的回归测试 |

---

## 四、低电流段（段0，|I| < 1 nA）

| 脚本 | 用途 |
|---|---|
| `lowI_compare.py` | 三条误差链：装置 / 设定 / 2636B 回读 |
| `lowI_error_curve.py` | 误差-电流曲线（对数横轴，横轴取 \|I_std\|）|
| `seg0_budget.py` | 段0 偏置拆项（T1/T2）+ 按窗口长度的量纲排除 |
| `seg0_joint_fit.py` | 把段0 并进拟合，看常数移动与残差变化 |
| `fsw_hypothesis.py` | 「段0 偏置 = 锯齿轮翻转电荷注入」的**形状预测**检验（已证伪）|
| `raw_recalc.py` | 结果行 RAW 段代回式(6)，绕过屏幕 1 pA 的取整（固件已改 5 位小数后，仅对旧数据有用）|

---

## 五、脉冲

| 脚本 | 用途 |
|---|---|
| `pulse_probe.py` | TSP 脉冲能力探针；单因素拆分定位三条前置条件 |
| `pulse_introspect.py` | KIPulse 内省（**纯只读**，不开输出、不改状态）|
| `analyze_pulse.py` | 脉冲实验分析（方案见 `docs/脉冲电流实验方案.md`）|
| `sim_pulse_recover.py` | 脉冲恢复仿真 |

---

## 六、论文出图

| 脚本 | 出什么 |
|---|---|
| `paper_figs.py` | 低电流段图（读 `data/lowI_pos/raw.csv`）|
| `paper_lin10_fig.py` | ±10 nA 那批的图 |
| `paper_pulse_fig.py` | 脉冲四格图（A/P/K/直流对照）|
| `photoelectric_fig.py` | 光电效应相关图 |
| `console_figure.py` | **网页控制台截图**（注入真实测量数据后 Chrome headless 渲染）|

`console_figure.py` 的用法：

```bash
python tools/console_figure.py            # 深色版
python tools/console_figure.py --light    # 浅色版（白底论文用）
```

它会生成中间 HTML 并打印截图命令。**截图前不要开页面直接截** —— 空白的控制台
只有布局没有内容，放论文里说明不了任何事。

---

## 七、工具与杂项

| 脚本 | 用途 |
|---|---|
| `console_copies.py` | 列出全盘所有「console.html」副本及各自版本。**改了控制台却没生效时先跑这个** |
| `boot0_probe.py` / `uart_flash.py` | 串口 bootloader 烧录路径（独立于 ST-Link）。⚠️ 固定 115200 8E1，**不要改** |
| `test_capture.py` | `nanoammeter_capture.py` 的离线回归测试 |
| `measure_transient.py` | 量 `EN=0` 断开瞬间注入的电荷 → 寄生电容 C_p |
| `switching_times.py` | 开关切换速度与装置各时间尺度 |
| `wave_compress.py` | 整窗波形能压多少（用实测数据量，不猜）|
| `sim_node_decay.py` | 仿真 100 MΩ 输入端节点电压波形 |
| `matlab_utf8.sh` | 在 MATLAB 里跑 `.m` 的 UTF-8 包装（中文注释不乱码）|
| `run_app.sh` | 本地起服务打开控制台 |

---

## 八、几条踩过的坑（写进脚本注释里了，这里再列一遍）

- **CSV 硬约束**：表头只用英文、数值只用十进制整数、**不得有 BOM**、无注释行、换行 `\n`
  —— 中文 Windows 上 MATLAB 与 Origin 对 UTF-8 处理不一致。
- **单位坑**：`ReadbackSampler` 给的是安培，而所有 CSV 的 `true_nA` 是纳安。差 1e9，踩过多次。
- **聚合口径**：逐点均值 vs 逐次，两者差 1.3 倍，混用会得出不同结论。
- **等待上限要跟着窗口走**：跟不上就会出现"预热: 无响应"那类假故障（2026-09-16 撞过）。
- **改控制台要同步**：仓库根目录那份是源头，桌面交付目录、release、backup 各有一份副本。
  改了不生效先用 `console_copies.py` 查你打开的是哪份。
