"""
阶段 1：确认电脑能识别 2636B。

=====================================================================
这个脚本不会给任何电流。它只查链路。
=====================================================================

两条路都能走：

  A) USB（需要 NI-VISA）
     [ ] NI-VISA 已安装（ni.com 下载，NI Package Manager 安装，要管理员权限）
     [ ] 2636B 已开机，USB-B 线（打印机线那种方口）已连到电脑
     python check_connection.py

  B) LAN 网口（**不需要任何驱动**）
     [ ] 2636B 已开机，网线插在 2636B 的 LAN 口上
     [ ] 已在 2636B 前面板查到它拿到的 IP
     python check_connection.py "TCPIP0::192.168.1.50::5025::SOCKET"

关于后端，有个坑必须讲清楚：
    NI-VISA 没装的时候，pyvisa.ResourceManager() 不会报错，它会**静默回退**
    到 pyvisa-py。于是脚本"跑通了"，但 USB 上根本找不到仪器，你会以为线坏了。
    所以本脚本把两个后端分开点名体检，不混着报 OK。
"""

from __future__ import annotations

import socket
import sys
import warnings

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

import pyvisa  # noqa: E402

from instrument import Keithley2636B, Keithley2636BError  # noqa: E402

NI_VISA_URL = "https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html"


def probe_backend(label: str, backend: str) -> object | None:
    """单独探测一个 VISA 后端，成功返回 ResourceManager，失败返回 None。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rm = pyvisa.ResourceManager(backend)
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ {label:<10}: 不可用 —— {type(exc).__name__}: {exc}")
        return None
    print(f"    ✓ {label:<10}: 可用")
    return rm


def probe_lan_socket(ip: str, port: int = 5025, timeout: float = 3.0) -> str | None:
    """纯 socket 探测，完全不经过 VISA。

    这一步的价值在于**分离故障**：连不上就是网络问题（IP 错、没插好、不同网段），
    连得上但 VISA 打不开就是软件问题。不先分开，两种故障会混在一起。
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.sendall(b"*IDN?\n")
            s.settimeout(timeout)
            data = s.recv(256)
        return data.decode("ascii", "replace").strip()
    except OSError as exc:
        print(f"    ✗ 连不上 {ip}:{port} —— {exc}")
        return None


def main() -> int:
    print("=" * 68)
    print("阶段 1：2636B 通信链路检查")
    print("=" * 68)

    print(f"\nPython : {sys.version.split()[0]}  ({sys.executable})")
    print(f"PyVISA : {pyvisa.__version__}")

    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    is_lan = bool(explicit) and "TCPIP" in explicit.upper()

    # --- 1. 两个后端分开点名 ---------------------------------------------
    print("\n[1] VISA 后端体检 ...")
    rm_ni = probe_backend("NI-VISA", "@ivi")
    rm_py = probe_backend("pyvisa-py", "@py")
    print("        （'@ivi' 就是 NI-VISA；'@py' 是免驱动的纯 Python 实现）")

    if rm_ni is None and not is_lan:
        print("\n    ⚠ 没装 NI-VISA。USB 路线走不通——USBTMC 驱动是 NI-VISA 带来的。")
        print(f"      下载入口：{NI_VISA_URL}")
        print("      需要 NI 账号，约 1.5 GB，用 NI Package Manager 安装，要管理员权限。")
        print("      装完**重启**，然后重新跑这个脚本。")
        if rm_py is not None:
            print("\n      ★ 或者改走 LAN 口，用 pyvisa-py 完全绕过 NI-VISA（见文件顶部说明 B）")

    # --- 2. LAN 专用：先做纯 socket 探测 ----------------------------------
    if is_lan:
        ip = explicit.split("::")[1]
        print(f"\n[2] LAN 纯 socket 预检（不经 VISA）—— 目标 {ip}:5025 ...")
        idn = probe_lan_socket(ip)
        if idn is None:
            print("\n✗ 网络层就不通，先别管 VISA。查这些：")
            print("    · 2636B 前面板显示的 IP，和这里写的是不是一致")
            print("    · 网线插的是不是 2636B 的 LAN 口（不是别的口）")
            print("    · 2636B 后面板 LAN 口旁边的指示灯亮不亮")
            print("    · 电脑和 2636B 是不是在同一网段（前三段数字要一样）")
            return 1
        print(f"    ✓ socket 通了，*IDN? 直接返回：{idn}")
        print("      —— 说明网络和仪器都没问题，接下来只是 VISA 封装的事")

    # --- 3. 枚举资源 ------------------------------------------------------
    rm = rm_ni or rm_py
    if rm is None:
        print("\n✗ 两个 VISA 后端都不可用，无法继续。")
        return 1
    used = "NI-VISA" if rm_ni is not None else "pyvisa-py"

    print(f"\n[3] 枚举 VISA 资源（使用 {used}）...")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            resources = [str(r) for r in rm.list_resources()]
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ 枚举失败：{exc}")
        return 1

    if not resources:
        print("    （空）")
    for r in resources:
        marker = "   <-- Keithley USB！" if "0x05E6" in r.upper() else ""
        print(f"    · {r}{marker}")

    keithley_usb = [r for r in resources if "0x05E6" in r.upper()]

    # --- 4. 决定要打开哪个资源 --------------------------------------------
    if explicit:
        resource = explicit
    elif keithley_usb:
        resource = keithley_usb[0]
    else:
        print("\n✗ 没有在 USB 上找到 Keithley 仪器（VID 0x05E6）。")
        if rm_ni is None:
            print("  首要原因：NI-VISA 还没装。见上面 [1]。")
        else:
            print("  NI-VISA 在，所以往硬件上查：")
            print("    · USB-B 线是否插在 2636B 后面板的 USB 口（方口，不是前面板）")
            print("    · 2636B 开机了没，屏幕亮不亮")
            print("    · 设备管理器 -> 通用串行总线设备，有没有 USBTMC 类设备")
            print("    · 换一根 USB 线试试（劣质线是常见元凶）")
        print('\n  如果你走的是 LAN：python check_connection.py "TCPIP0::<仪器IP>::5025::SOCKET"')
        return 1

    print(f"\n[4] 打开 {resource} 并查询 *IDN? ...")

    try:
        # 不指定 backend，让驱动自己挑（NI-VISA 优先，没有就回退 pyvisa-py）
        smu = Keithley2636B(resource)

        with smu:
            print(f"    ✓ *IDN? : {smu.identify()}")

            # --- 5. 只读体检：不改变输出状态，不给电流 --------------------
            print("\n[5] 只读体检（不改变输出状态，不给电流）...")
            func = smu.query_float(f"print({smu.ch}.source.func)")
            print(f"    源模式      : {func:.0f}  ({'电流源' if func else '电压源'})")
            print(f"    源电流电平  : {smu.query_float(f'print({smu.ch}.source.leveli)')!r} A")
            print(f"    电压合规    : {smu.query_float(f'print({smu.ch}.source.limitv)')!r} V")
            print(f"    输出状态    : {smu.output_state()}  (1=开, 0=关)")
            off_lim = smu.query_float(f"print({smu.ch}.source.offlimitv)")
            flag = "  <-- 出厂默认是 40 V，偏危险" if off_lim > 10 else "  (已压低)"
            print(f"    关断限压    : {off_lim!r} V{flag}")

            err = smu.read_error()
            print(f"\n    仪器错误队列：{err}")
            if not err.startswith("0"):
                print("    ⚠ 队列里有历史错误，先查清楚再上电。")

            print("\n" + "=" * 68)
            print("阶段 1 通过。链路是通的，接着跑阶段 2：")
            print("    python test_small_current.py --dry-run   # 先看它打算干什么")
            print("=" * 68)
            return 0

    except Keithley2636BError as exc:
        print(f"    ✗ {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
