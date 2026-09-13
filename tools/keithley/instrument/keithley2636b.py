"""
Keithley 2636B（2600B 系列）TSP 驱动 —— 基于 PyVISA

===========================================================================
2636B 是 TSP 仪器，不是 SCPI 仪器
===========================================================================
2600B 系列的原生远程语言是 TSP（Test Script Processor，内嵌 Lua）。
常规 SCPI 命令（如 :SOUR:CURR、:SENS:FUNC:ON）在 2636B 上默认不可用。

要用 SCPI 只有两条路，且都只为迁移 2400 的旧代码而存在，本模块不采用：
  1) 在仪器中加载 2400 仿真脚本（Persona2400 / 2600B-800A.tsp），整机变 2400；
  2) 不进仿真，先发 Initialize2400()，再用 Execute2400("<SCPI>") 逐条桥接。

本模块一律使用原生 TSP。

===========================================================================
已按 2600B Reference Manual（Section 7: TSP command reference）核对的命令
===========================================================================
    smuX.source.func      = smuX.OUTPUT_DCAMPS | smuX.OUTPUT_DCVOLTS
    smuX.source.leveli    = <安培>      电流源电平（输出开时立即生效）
    smuX.source.limitv    = <伏特>      电压合规（限压）
    smuX.source.rangei / autorangei / lowrangei
    smuX.source.output    = smuX.OUTPUT_ON | smuX.OUTPUT_OFF
    smuX.source.offmode   = smuX.OUTPUT_NORMAL | OUTPUT_ZERO | OUTPUT_HIGH_Z
    smuX.source.offfunc   = smuX.OUTPUT_DCAMPS | smuX.OUTPUT_DCVOLTS
    smuX.source.offlimitv / offlimiti
    smuX.measure.i() / smuX.measure.v()
    smuX.measure.nplc / autorangei / rangei / autozero

===========================================================================
安全默认值（重要）
===========================================================================
offmode 出厂默认是 OUTPUT_NORMAL，而 OUTPUT_NORMAL 下的 offlimitv 默认高达
40 V。对 ADA4530-1 这类高阻输入级，关输出后线上还挂着 40 V 限压是很危险的。

本驱动默认改为：
    offmode  = OUTPUT_NORMAL
    offfunc  = OUTPUT_DCAMPS      -> 关输出时是一台 0 A 电流源
    offlimitv = 2 V               -> 且限压 2 V，而不是 40 V

不用 OUTPUT_HIGH_Z 作为默认，是因为它会断开输出继电器，手册明确警告
"不应用于频繁开关输出的测试"——扫描时会磨损继电器。
"""

from __future__ import annotations

import time
from typing import Optional

import pyvisa

__all__ = ["Keithley2636B", "Keithley2636BError"]

# Keithley / Tektronix 的 USB 厂商 ID
KEITHLEY_USB_VID = "0x05E6"


class Keithley2636BError(RuntimeError):
    """2636B 连接失败、TSP 执行出错或仪器返回错误。"""


def _num(value: float) -> str:
    """格式化成 TSP(Lua) 能解析的数值字面量。"""
    return f"{float(value):.12g}"


class Keithley2636B:
    """Keithley 2636B 单通道封装（默认 smua，双通道机型可指定 smub）。

    典型用法::

        with Keithley2636B() as smu:
            print(smu.identify())
            smu.apply_current(1e-9, compliance_v=2.0, settle_s=1.0)
            print(smu.measure_current())
    """

    DEFAULT_COMPLIANCE_V = 2.0      # 电压合规上限（V）
    DEFAULT_OFF_LIMIT_V = 2.0       # 关输出时的限压（V），出厂默认是 40
    DEFAULT_NPLC = 1.0              # 积分周期，1 PLC @ 50 Hz = 20 ms

    # ---------------------------------------------------------------- 连接

    def __init__(
        self,
        resource: Optional[str] = None,
        channel: str = "a",
        timeout_ms: int = 15000,
        backend: Optional[str] = None,
    ) -> None:
        """
        :param resource:  VISA 资源串，如 'USB0::0x05E6::0x2636::...::INSTR'。
                          省略时自动在 USB 上搜第一台 Keithley。
        :param channel:   'a' 或 'b'，对应 smua / smub。
        :param timeout_ms: VISA 超时。低电流 + 高 NPLC 时测量较慢，留足余量。
        :param backend:   None = 用系统 VISA（NI-VISA）；
                          '@py' = 用 pyvisa-py（无需 NI-VISA，走 LAN socket 时有用）。
        """
        if channel.lower() not in ("a", "b"):
            raise ValueError(f"channel 必须是 'a' 或 'b'，收到 {channel!r}")

        self.ch = f"smu{channel.lower()}"
        self._inst = None
        self._resource = resource
        self.timeout_ms = timeout_ms
        self.backend = backend

    def connect(self) -> "Keithley2636B":
        """打开 VISA 会话。"""
        if self._inst is not None:
            return self

        try:
            if self.backend:
                rm = pyvisa.ResourceManager(self.backend)
            else:
                try:
                    rm = pyvisa.ResourceManager()        # 优先 NI-VISA
                except Exception:
                    rm = pyvisa.ResourceManager("@py")   # 没装 NI-VISA 时自动回退
        except Exception as exc:  # noqa: BLE001
            raise Keithley2636BError(
                "无法初始化 VISA 资源管理器。\n"
                "  · 走 USB：必须先装 NI-VISA，USBTMC 驱动由它提供；\n"
                "  · 走 LAN：不需要任何驱动，pyvisa-py 就够。\n"
                f"底层报错：{exc}"
            ) from exc

        self._rm = rm
        resource = self._resource or self._autodetect(rm)

        try:
            inst = rm.open_resource(resource)
        except Exception as exc:  # noqa: BLE001
            raise Keithley2636BError(f"打不开 VISA 资源 {resource!r}：{exc}") from exc

        inst.timeout = self.timeout_ms
        inst.write_termination = "\n"
        inst.read_termination = "\n"
        self._inst = inst
        self._resource = resource

        # 连上就立刻验明正身，避免后面拿着一个哑会话瞎跑
        idn = self.identify()
        if "2636" not in idn.upper():
            raise Keithley2636BError(
                f"连上了 {resource!r}，但 *IDN? 返回 {idn!r}，不像是 2636B。"
            )
        return self

    @staticmethod
    def _autodetect(rm: "pyvisa.ResourceManager") -> str:
        """在 USB 上搜第一台 Keithley。找不到就报错并列出所有可见资源。"""
        found = list(rm.list_resources(f"USB0::{KEITHLEY_USB_VID}::?*::INSTR"))
        if found:
            return found[0]
        allres = [str(r) for r in rm.list_resources()]
        raise Keithley2636BError(
            "没有在 USB 上找到 Keithley 仪器。\n"
            f"当前 VISA 能看到的所有资源：{allres or '（空）'}\n"
            "排查顺序：NI-VISA 是否装好 -> USB-B 线是否插上 -> "
            "设备管理器里有没有 USBTMC 设备 -> 2636B 开机了没。"
        )

    def close(self) -> None:
        """安全关断并释放会话。会先把输出关掉。"""
        if self._inst is None:
            return
        try:
            self.output_off()
        except Exception:  # noqa: BLE001
            pass  # 已经断线的情况下关不掉也无所谓，别把 close 搞崩
        try:
            self._inst.close()
        finally:
            self._inst = None

    def __enter__(self) -> "Keithley2636B":
        return self.connect()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------ 底层收发

    @property
    def inst(self):
        if self._inst is None:
            raise Keithley2636BError("尚未连接。请先调用 connect() 或使用 with 语句。")
        return self._inst

    def write(self, tsp: str) -> None:
        """下发一条不取返回值的 TSP 语句。"""
        self.inst.write(tsp)

    def query(self, tsp: str) -> str:
        """下发 TSP 并取回一行返回（需自带 print()）。"""
        return self.inst.query(tsp).strip()

    def query_float(self, tsp: str) -> float:
        """同 query，但把返回值转成 float，顺便带上原始文本便于报错。"""
        raw = self.query(tsp)
        try:
            return float(raw)
        except ValueError as exc:
            raise Keithley2636BError(f"期望数值，仪器返回了 {raw!r}（命令：{tsp}）") from exc

    # -------------------------------------------------------------- 身份/复位

    def identify(self) -> str:
        """*IDN? —— IEEE-488.2 通用命令，TSP 仪器同样支持。"""
        return self.query("*IDN?")

    def reset(self) -> None:
        """*RST 恢复出厂设置，然后立刻重新施加本驱动的安全默认值。

        注意：*RST 会把 offlimitv 打回 40 V，所以必须紧接着 set_safe_off_state()。
        """
        self.write("*RST")
        self.write("*CLS")
        time.sleep(0.2)
        self.set_safe_off_state()

    def initialize(self, nplc: Optional[float] = None) -> "Keithley2636B":
        """按微电流用途初始化：电流源、开输出前先把限压和 off 状态设成安全值。"""
        self.set_safe_off_state()
        self.set_current_source_mode()
        self.set_nplc(self.DEFAULT_NPLC if nplc is None else nplc)
        self.set_measure_autorange(True)
        self.set_source_autorange(True)
        return self

    # ------------------------------------------------------------ 源模式配置

    def set_current_source_mode(self) -> None:
        """把通道配成直流电流源。"""
        self.write(f"{self.ch}.source.func = {self.ch}.OUTPUT_DCAMPS")

    def set_voltage_source_mode(self) -> None:
        """把通道配成直流电压源（本阶段用不到，留着做对称性测试）。"""
        self.write(f"{self.ch}.source.func = {self.ch}.OUTPUT_DCVOLTS")

    def set_compliance(self, volts: float) -> None:
        """设置电压合规（限压）。电流源模式下这是最重要的一道保护。"""
        self.write(f"{self.ch}.source.limitv = {_num(volts)}")

    def set_current(self, amps: float) -> None:
        """设置电流源电平。输出已开时立即生效。

        注意：这里设的是"设定值"。实际输出值必须用 measure_current() 回读，
        不能拿设定值当真实输入电流。
        """
        self.write(f"{self.ch}.source.leveli = {_num(amps)}")

    # -------------------------------------------------------------- 输出控制

    def output_on(self) -> None:
        self.write(f"{self.ch}.source.output = {self.ch}.OUTPUT_ON")

    def output_off(self) -> None:
        self.write(f"{self.ch}.source.output = {self.ch}.OUTPUT_OFF")

    def output_state(self) -> int:
        return int(self.query_float(f"print({self.ch}.source.output)"))

    def set_safe_off_state(self, off_limit_v: Optional[float] = None) -> None:
        """配置"关输出"时的行为，把出厂 40 V 的限压压下来。

        设成 OUTPUT_NORMAL + offfunc=DCAMPS 的效果是：关输出时仪器是一台
        0 A 电流源，且电压被 offlimitv 钳住。比断继电器（HIGH_Z）更耐久，
        比出厂默认（40 V 限压）对被测电路安全得多。
        """
        limit = self.DEFAULT_OFF_LIMIT_V if off_limit_v is None else off_limit_v
        self.write(f"{self.ch}.source.offmode = {self.ch}.OUTPUT_NORMAL")
        self.write(f"{self.ch}.source.offfunc = {self.ch}.OUTPUT_DCAMPS")
        self.write(f"{self.ch}.source.offlimitv = {_num(limit)}")

    # ------------------------------------------------------------ 量程/积分

    def set_nplc(self, nplc: float) -> None:
        """设置测量积分周期（PLC）。微电流测量应适当加长以压低噪声。"""
        if not 0.01 <= nplc <= 10:
            raise ValueError(f"NPLC 应在 0.01~10 之间，收到 {nplc}")
        self.write(f"{self.ch}.measure.nplc = {_num(nplc)}")

    def set_source_autorange(self, on: bool = True) -> None:
        flag = f"{self.ch}.AUTORANGE_ON" if on else f"{self.ch}.AUTORANGE_OFF"
        self.write(f"{self.ch}.source.autorangei = {flag}")

    def set_measure_autorange(self, on: bool = True) -> None:
        flag = f"{self.ch}.AUTORANGE_ON" if on else f"{self.ch}.AUTORANGE_OFF"
        self.write(f"{self.ch}.measure.autorangei = {flag}")

    def source_range(self) -> float:
        """回读当前源电流量程（A）。"""
        return self.query_float(f"print({self.ch}.source.rangei)")

    def measure_range(self) -> float:
        """回读当前测量电流量程（A）。"""
        return self.query_float(f"print({self.ch}.measure.rangei)")

    # ---------------------------------------------------------------- 测量

    def measure_current(self) -> float:
        """触发一次电流测量并返回安培值。

        TSP 的 measure.i() 在被 print() 时才会把结果推进输出队列，
        这正是我们从电脑端取回数值的方式。
        """
        return self.query_float(f"print({self.ch}.measure.i())")

    def measure_voltage(self) -> float:
        """触发一次电压测量并返回伏特值。"""
        return self.query_float(f"print({self.ch}.measure.v())")

    def measure_iv(self) -> tuple:
        """一次返回 (电流, 电压)。两次独立触发，不是严格同时。"""
        return self.measure_current(), self.measure_voltage()

    # ------------------------------------------------------- 安全上电序列

    def apply_current(
        self,
        amps: float,
        compliance_v: Optional[float] = None,
        settle_s: float = 1.0,
        output_on: bool = True,
        ramp_steps: int = 0,
    ) -> float:
        """安全地把输出设到指定电流，并回读实际值。

        严格按顺序执行，杜绝"从 0 直接跳到大电流"：

            关输出 -> 设限压 -> 设量程/积分 -> 设电流电平
            -> (可选斜坡) -> 开输出 -> 等稳定 -> 回读实际电流

        :param ramp_steps: >1 时先把输出打开，再从当前电平线性过渡到目标电平。
                           阶跃本身对电流源不是问题，但斜坡能让被测积分器
                           更平稳地跟上，也便于观察是否有异常。
        :returns: 稳定后回读到的实际电流（A）。**这就是应该记录的值。**
        """
        compliance = self.DEFAULT_COMPLIANCE_V if compliance_v is None else compliance_v
        if compliance <= 0:
            raise ValueError("compliance_v 必须为正数")

        # 1) 先确保输出是关的，任何状态变更都在无输出下进行
        self.output_off()
        # 2) 限压先于电平设置——顺序反了的话，中间瞬间可能顶到旧限压
        self.set_compliance(compliance)
        self.set_current_source_mode()

        if ramp_steps and ramp_steps > 1:
            return self._apply_with_ramp(amps, ramp_steps, settle_s)

        # 3) 设电平，再开输出
        self.set_current(amps)
        if output_on:
            self.output_on()
        if settle_s > 0:
            time.sleep(settle_s)

        return self.measure_current()

    def _apply_with_ramp(self, amps: float, steps: int, settle_s: float) -> float:
        """从当前电平线性过渡到目标电平。"""
        start = self.measure_current() if self.output_state() else 0.0
        self.set_current(start)
        self.output_on()
        for k in range(1, steps + 1):
            self.set_current(start + (amps - start) * k / steps)
            time.sleep(0.05)
        if settle_s > 0:
            time.sleep(settle_s)
        return self.measure_current()

    # ------------------------------------------------------------ 诊断

    def read_error(self) -> str:
        """取一条仪器错误队列里的信息。返回 '0,"No error"' 表示无错。"""
        return self.query("print(errorqueue.next())")
