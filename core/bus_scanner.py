# core/bus_scanner.py
"""I2C bus scanning -- /dev/i2c + CH341 + FTDI + CP2112."""

import os
import random
import sys
import traceback

from .bus_factory import (
    CH341_OFFSET,
    CP2112_OFFSET,
    FTDI_OFFSET,
    create_bus,
)
from .drivers.cp2112_i2c import CP2112AddressNackError

from .pmbus_device import (
    PMBusDevice,
    TransportDisconnectedError,
    TransportSessionFailedError,
)


GLOBAL_ADDRESSES = {0x5A, 0x5B, 0x7C}


class SimDevice:
    """Фиктивное устройство для демо-режима с динамической телеметрией."""

    def __init__(self):
        self.address = 0x5C
        self.addr = 0x5C
        self.num_pages = 4
        self.pages = 4
        self.name = "LTM4673 (sim)"
        self.special_id = 0x0236
        self.id = 0x0236
        self.revision = "Sim Rev 1.0"
        self.capability = 0xB0
        self.vout_exp = {
            0: -13,
            1: -13,
            2: -13,
            3: -13,
        }
        self._config_cache = {}
        self._telemetry_cache = {}
        self._page = 0

    def identify(self):
        return True

    def set_page(self, page):
        self._page = page & 0xFF

    def read_global_config(self):
        return {
            "VIN_ON": {
                "value": 4.5,
                "cmd": 0x35,
                "fmt": "L11",
                "raw": 0xCA40,
            },
            "VIN_OFF": {
                "value": 4.4,
                "cmd": 0x36,
                "fmt": "L11",
                "raw": 0xCA33,
            },
            "VIN_OV_FAULT_LIMIT": {
                "value": 15.0,
                "cmd": 0x55,
                "fmt": "L11",
                "raw": 0xD3C0,
            },
            "VIN_OV_WARN_LIMIT": {
                "value": 14.0,
                "cmd": 0x57,
                "fmt": "L11",
                "raw": 0xD380,
            },
            "VIN_UV_WARN_LIMIT": {
                "value": 0.0,
                "cmd": 0x58,
                "fmt": "L11",
                "raw": 0x8000,
            },
            "VIN_UV_FAULT_LIMIT": {
                "value": 0.0,
                "cmd": 0x59,
                "fmt": "L11",
                "raw": 0x8000,
            },
            "OPERATION": {
                "value": 0x80,
                "cmd": 0x01,
                "fmt": "BYTE",
                "raw": 0x80,
            },
            "ON_OFF_CONFIG": {
                "value": 0x1F,
                "cmd": 0x02,
                "fmt": "BYTE",
                "raw": 0x1F,
            },
        }

    def read_channel_config(self, page=0):
        base = 1.0 + page * 0.1

        return {
            "VOUT_COMMAND": {
                "value": base,
                "cmd": 0x21,
                "fmt": "L16",
                "raw": int(base * 4096),
            },
            "VOUT_MAX": {
                "value": base * 1.1,
                "cmd": 0x24,
                "fmt": "L16",
                "raw": int(base * 1.1 * 4096),
            },
            "VOUT_MARGIN_HIGH": {
                "value": base * 1.05,
                "cmd": 0x25,
                "fmt": "L16",
                "raw": int(base * 1.05 * 4096),
            },
            "VOUT_MARGIN_LOW": {
                "value": base * 0.95,
                "cmd": 0x26,
                "fmt": "L16",
                "raw": int(base * 0.95 * 4096),
            },
            "VOUT_OV_FAULT_LIMIT": {
                "value": base * 1.1,
                "cmd": 0x40,
                "fmt": "L16",
                "raw": int(base * 1.1 * 4096),
            },
            "VOUT_OV_WARN_LIMIT": {
                "value": base * 1.05,
                "cmd": 0x42,
                "fmt": "L16",
                "raw": int(base * 1.05 * 4096),
            },
            "VOUT_UV_WARN_LIMIT": {
                "value": base * 0.95,
                "cmd": 0x43,
                "fmt": "L16",
                "raw": int(base * 0.95 * 4096),
            },
            "VOUT_UV_FAULT_LIMIT": {
                "value": base * 0.9,
                "cmd": 0x44,
                "fmt": "L16",
                "raw": int(base * 0.9 * 4096),
            },
            "IOUT_OC_FAULT_LIMIT": {
                "value": 20.0,
                "cmd": 0x46,
                "fmt": "L11",
                "raw": 0xDB20,
            },
            "IOUT_OC_WARN_LIMIT": {
                "value": 16.0,
                "cmd": 0x4A,
                "fmt": "L11",
                "raw": 0xDA00,
            },
            "IOUT_UC_FAULT_LIMIT": {
                "value": -2.0,
                "cmd": 0x4B,
                "fmt": "L11",
                "raw": 0xC400,
            },
            "OT_FAULT_LIMIT": {
                "value": 128.0,
                "cmd": 0x4F,
                "fmt": "L11",
                "raw": 0xF200,
            },
            "OT_WARN_LIMIT": {
                "value": 125.0,
                "cmd": 0x51,
                "fmt": "L11",
                "raw": 0xEBE8,
            },
            "UT_WARN_LIMIT": {
                "value": -20.0,
                "cmd": 0x52,
                "fmt": "L11",
                "raw": 0xDD80,
            },
            "UT_FAULT_LIMIT": {
                "value": -45.0,
                "cmd": 0x53,
                "fmt": "L11",
                "raw": 0xE530,
            },
            "FREQUENCY_SWITCH": {
                "value": 500.0,
                "cmd": 0x33,
                "fmt": "L11",
                "raw": 0xFB8,
            },
            "TON_DELAY": {
                "value": 1.0,
                "cmd": 0x60,
                "fmt": "L11",
                "raw": 0xBA00,
            },
            "TON_RISE": {
                "value": 3.0,
                "cmd": 0x61,
                "fmt": "L11",
                "raw": 0xC300,
            },
            "TOFF_DELAY": {
                "value": 1.0,
                "cmd": 0x64,
                "fmt": "L11",
                "raw": 0xBA00,
            },
            "OPERATION": {
                "value": 0x80,
                "cmd": 0x01,
                "fmt": "BYTE",
                "raw": 0x80,
            },
            "ON_OFF_CONFIG": {
                "value": 0x1F,
                "cmd": 0x02,
                "fmt": "BYTE",
                "raw": 0x1F,
            },
            "WRITE_PROTECT": {
                "value": 0x00,
                "cmd": 0x10,
                "fmt": "BYTE",
                "raw": 0x00,
            },
        }

    def read_global_telemetry(self):
        vin = 12.0 + random.gauss(0, 0.1)
        temp_ic = 42.0 + random.gauss(0, 0.5)
        iin = 1.5 + random.gauss(0, 0.05)
        pin = vin * iin

        return {
            "VIN": vin,
            "TEMP_IC": temp_ic,
            "IIN": iin,
            "PIN": pin,
        }

    def read_channel_telemetry(self, page=0):
        base_voltage = 1.0 + page * 0.1
        vout = base_voltage + random.gauss(0, 0.005)
        iout = 5.0 + page * 2.0 + random.gauss(0, 0.1)

        return {
            "VOUT": vout,
            "IOUT": iout,
            "POUT": vout * iout,
            "TEMP1": 40.0 + page * 5.0 + random.gauss(0, 0.5),
            "DUTY": 0.4 + page * 0.05 + random.gauss(0, 0.01),
        }

    def read_global_status(self):
        return {
            "STATUS_INPUT": 0x00,
            "STATUS_CML": 0x00,
        }

    def read_channel_status(self, page=0):
        return {
            "STATUS_WORD": 0x0000,
            "STATUS_VOUT": 0x00,
            "STATUS_IOUT": 0x00,
            "STATUS_TEMPERATURE": 0x00,
            "STATUS_MFR": 0x00,
        }

    def read_status(self, page=0):
        result = self.read_global_status()
        result.update(self.read_channel_status(page))
        return result

    def write_val(self, page, cmd, value, fmt):
        return True

    def clear_faults(self, page=None):
        return True

    def store_user_all(self):
        return True

    def restore_user_all(self):
        return True

    def read_full_dump(self, page=0):
        return []

    def write_register(self, page, cmd, raw, size):
        return True


def is_sim():
    return "--sim" in sys.argv or "--demo" in sys.argv


def find_buses():
    if is_sim():
        return [1]

    buses = []

    # Linux /dev/i2c-N, 0..99
    for index in range(20):
        if os.path.exists(f"/dev/i2c-{index}"):
            buses.append(index)

    # CH341 USB, 100..199
    try:
        from .drivers.ch341_i2c import (
            CH341_PID_I2C,
            find_ch341_devices,
        )

        ch341_devices = find_ch341_devices()

        for index, device in enumerate(ch341_devices):
            if getattr(device, "idProduct", None) != CH341_PID_I2C:
                continue

            bus_num = CH341_OFFSET + index

            if bus_num not in buses:
                buses.append(bus_num)

    except ImportError:
        pass
    except Exception as exc:
        print(f"[scan] CH341 error: {exc}")

    # FTDI USB, 200..299
    try:
        from .drivers.ftdi_i2c import find_ftdi_devices

        ftdi_devices = find_ftdi_devices()

        for index in range(len(ftdi_devices)):
            bus_num = FTDI_OFFSET + index

            if bus_num not in buses:
                buses.append(bus_num)

    except ImportError:
        pass
    except Exception as exc:
        print(f"[scan] FTDI error: {exc}")

    # CP2112 USB, 300..399
    try:
        from .drivers.cp2112_i2c import find_cp2112_devices

        cp2112_devices = find_cp2112_devices()

        for index in range(len(cp2112_devices)):
            bus_num = CP2112_OFFSET + index

            if bus_num not in buses:
                buses.append(bus_num)

    except ImportError:
        pass
    except Exception as exc:
        print(f"[scan] CP2112 error: {exc}")

    return sorted(buses)


def bus_label(bus_num):
    if is_sim():
        return "Simulated I2C #0"

    # CH341 buses, 100..199
    if CH341_OFFSET <= bus_num < FTDI_OFFSET:
        index = bus_num - CH341_OFFSET

        try:
            from .drivers.ch341_i2c import (
                ch341_location,
                find_ch341_devices,
            )

            devices = find_ch341_devices()

            if index < len(devices):
                device = devices[index]
                location = ch341_location(device)
                return f"CH341 #{index} port {location}"

        except Exception:
            pass

        return f"CH341 #{index}"

    # FTDI buses, 200..299
    if FTDI_OFFSET <= bus_num < CP2112_OFFSET:
        index = bus_num - FTDI_OFFSET

        try:
            from .drivers.ftdi_i2c import (
                I2C_PIDS,
                find_ftdi_devices,
            )

            devices = find_ftdi_devices()

            if index < len(devices):
                device = devices[index]
                chip = I2C_PIDS.get(device.pid, "?")
                serial = getattr(device, "sn", "") or ""
                return f"FTDI FT{chip.upper()} {serial}".strip()

        except Exception:
            pass

        return f"FTDI #{index}"

    # CP2112 buses, 300..399
    if bus_num >= CP2112_OFFSET:
        index = bus_num - CP2112_OFFSET

        try:
            from .drivers.cp2112_i2c import find_cp2112_devices

            devices = find_cp2112_devices()

            if index < len(devices):
                device = devices[index]
                serial = device.get("serial_number") or ""
                return f"CP2112 #{index} {serial}".strip()

        except Exception:
            pass

        return f"CP2112 #{index}"

    return f"/dev/i2c-{bus_num}"


def scan_bus(bus_num, *, cml_diagnostic=False):
    if is_sim():
        from core.demo_device import create_demo_devices

        return create_demo_devices()

    devices = []
    diagnostic_address = 0x4F
    status_cml_command = 0x7E

    print(f"[scan_bus] scanning {bus_label(bus_num)} ...")
    print(
        f"[scan_bus] implementation: "
        f"{scan_bus.__code__.co_filename}"
    )

    bus = create_bus(bus_num)

    previous_cml_state = None

    def check_cml(stage, force=False):
        nonlocal previous_cml_state

        if not cml_diagnostic:
            return True

        try:
            raw = bus.read_byte_data(
                diagnostic_address,
                status_cml_command,
            )

            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= 0xFF
            ):
                raise OSError(
                    f"Invalid STATUS_CML response: {raw!r}"
                )

            state = ("value", raw)
            message = f"STATUS_CML=0x{raw:02X}"

        except CP2112AddressNackError:
            state = ("absent",)
            message = "STATUS_CML target did not acknowledge"

        except Exception as exc:
            state = (
                "error",
                type(exc).__name__,
                str(exc),
            )
            message = (
                f"STATUS_CML read failed: "
                f"{type(exc).__name__}: {exc}"
            )

            if force or state != previous_cml_state:
                print(
                    f"[CML diagnostic] "
                    f"target=0x{diagnostic_address:02X}, "
                    f"{stage}: {message}"
                )

            previous_cml_state = state

            if isinstance(exc, TransportDisconnectedError):
                raise

            raise TransportSessionFailedError(
                "Scan stopped during STATUS_CML diagnostic "
                f"at 0x{diagnostic_address:02X}. "
                f"Stage: {stage}. Error: {exc}"
            ) from exc

        if force or state != previous_cml_state:
            print(
                f"[CML diagnostic] "
                f"target=0x{diagnostic_address:02X}, "
                f"{stage}: {message}"
            )

        previous_cml_state = state
        return True

    check_cml(
        "after transport open, before address scan",
        force=True,
    )

    print(
        f"[scan_bus] PMBusDevice: "
        f"{PMBusDevice.__module__}"
    )

    for address in range(0x08, 0x78):
        if address in GLOBAL_ADDRESSES:
            continue

        device = PMBusDevice(bus_num, address)

        # Propagate errors without further diagnostic I/O.
        identified = device.identify()

        if device.transport_failed():
            raise TransportSessionFailedError(
                "Transport session failed during "
                f"identification at 0x{address:02X}"
            )

        check_cml(
            f"after identification attempt at "
            f"0x{address:02X}",
            force=(address == diagnostic_address),
        )

        if identified:
            devices.append(device)

            print(
                f"[scan_bus] + {device.name} "
                f"@ 0x{address:02X}, "
                f"pages={device.num_pages}, "
                f"id=0x{device.special_id:04X}"
            )

    check_cml(
        "scan completed, before GUI tab creation",
        force=True,
    )

    print(f"[scan_bus] found {len(devices)} device(s)")
    return devices

if __name__ == "__main__":
    bus_num = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    print(f"Scanning bus {bus_num}...")

    found = scan_bus(bus_num)

    if found:
        print("\nFound devices:")

        for device in found:
            print(f"  Address: 0x{device.address:02X}")
            print(f"    Name: {device.name}")
            print(f"    ID: 0x{device.special_id:04X}")
            print(f"    Pages: {device.num_pages}")
            print(f"    Revision: {device.revision}")
            print()
    else:
        print("No devices found.")
