# core/bus_scanner.py
"""I2C bus scanning -- /dev/i2c + CH341 + FTDI."""

import os
import sys
import random
from .bus_factory import create_bus, CH341_OFFSET, FTDI_OFFSET
from .pmbus_device import PMBusDevice

# Глобальные адреса, которые не являются отдельными устройствами
GLOBAL_ADDRESSES = {0x5A, 0x5B, 0x7C}


# ---- КЛАСС ДЛЯ ДЕМО-РЕЖИМА (с динамическими данными) ----
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
        self.vout_exp = {0: -13, 1: -13, 2: -13, 3: -13}
        self._config_cache = {}
        self._telemetry_cache = {}
        self._page = 0

    def identify(self):
        return True

    def set_page(self, page):
        self._page = page & 0xFF

    def read_global_config(self):
        return {
            'VIN_ON': {'value': 4.5, 'cmd': 0x35, 'fmt': 'L11', 'raw': 0xCA40},
            'VIN_OFF': {'value': 4.4, 'cmd': 0x36, 'fmt': 'L11', 'raw': 0xCA33},
            'VIN_OV_FAULT_LIMIT': {'value': 15.0, 'cmd': 0x55, 'fmt': 'L11', 'raw': 0xD3C0},
            'VIN_OV_WARN_LIMIT': {'value': 14.0, 'cmd': 0x57, 'fmt': 'L11', 'raw': 0xD380},
            'VIN_UV_WARN_LIMIT': {'value': 0.0, 'cmd': 0x58, 'fmt': 'L11', 'raw': 0x8000},
            'VIN_UV_FAULT_LIMIT': {'value': 0.0, 'cmd': 0x59, 'fmt': 'L11', 'raw': 0x8000},
            'OPERATION': {'value': 0x80, 'cmd': 0x01, 'fmt': 'BYTE', 'raw': 0x80},
            'ON_OFF_CONFIG': {'value': 0x1F, 'cmd': 0x02, 'fmt': 'BYTE', 'raw': 0x1F},
        }

    def read_channel_config(self, page=0):
        base = 1.0 + page * 0.1
        return {
            'VOUT_COMMAND': {'value': base, 'cmd': 0x21, 'fmt': 'L16', 'raw': int(base * 4096)},
            'VOUT_MAX': {'value': base * 1.1, 'cmd': 0x24, 'fmt': 'L16', 'raw': int(base * 1.1 * 4096)},
            'VOUT_MARGIN_HIGH': {'value': base * 1.05, 'cmd': 0x25, 'fmt': 'L16', 'raw': int(base * 1.05 * 4096)},
            'VOUT_MARGIN_LOW': {'value': base * 0.95, 'cmd': 0x26, 'fmt': 'L16', 'raw': int(base * 0.95 * 4096)},
            'VOUT_OV_FAULT_LIMIT': {'value': base * 1.1, 'cmd': 0x40, 'fmt': 'L16', 'raw': int(base * 1.1 * 4096)},
            'VOUT_OV_WARN_LIMIT': {'value': base * 1.05, 'cmd': 0x42, 'fmt': 'L16', 'raw': int(base * 1.05 * 4096)},
            'VOUT_UV_WARN_LIMIT': {'value': base * 0.95, 'cmd': 0x43, 'fmt': 'L16', 'raw': int(base * 0.95 * 4096)},
            'VOUT_UV_FAULT_LIMIT': {'value': base * 0.9, 'cmd': 0x44, 'fmt': 'L16', 'raw': int(base * 0.9 * 4096)},
            'IOUT_OC_FAULT_LIMIT': {'value': 20.0, 'cmd': 0x46, 'fmt': 'L11', 'raw': 0xDB20},
            'IOUT_OC_WARN_LIMIT': {'value': 16.0, 'cmd': 0x4A, 'fmt': 'L11', 'raw': 0xDA00},
            'IOUT_UC_FAULT_LIMIT': {'value': -2.0, 'cmd': 0x4B, 'fmt': 'L11', 'raw': 0xC400},
            'OT_FAULT_LIMIT': {'value': 128.0, 'cmd': 0x4F, 'fmt': 'L11', 'raw': 0xF200},
            'OT_WARN_LIMIT': {'value': 125.0, 'cmd': 0x51, 'fmt': 'L11', 'raw': 0xEBE8},
            'UT_WARN_LIMIT': {'value': -20.0, 'cmd': 0x52, 'fmt': 'L11', 'raw': 0xDD80},
            'UT_FAULT_LIMIT': {'value': -45.0, 'cmd': 0x53, 'fmt': 'L11', 'raw': 0xE530},
            'FREQUENCY_SWITCH': {'value': 500.0, 'cmd': 0x33, 'fmt': 'L11', 'raw': 0xFB8},
            'TON_DELAY': {'value': 1.0, 'cmd': 0x60, 'fmt': 'L11', 'raw': 0xBA00},
            'TON_RISE': {'value': 3.0, 'cmd': 0x61, 'fmt': 'L11', 'raw': 0xC300},
            'TOFF_DELAY': {'value': 1.0, 'cmd': 0x64, 'fmt': 'L11', 'raw': 0xBA00},
            'OPERATION': {'value': 0x80, 'cmd': 0x01, 'fmt': 'BYTE', 'raw': 0x80},
            'ON_OFF_CONFIG': {'value': 0x1F, 'cmd': 0x02, 'fmt': 'BYTE', 'raw': 0x1F},
            'WRITE_PROTECT': {'value': 0x00, 'cmd': 0x10, 'fmt': 'BYTE', 'raw': 0x00},
        }

    def read_global_telemetry(self):
        vin = 12.0 + random.gauss(0, 0.1)
        temp_ic = 42.0 + random.gauss(0, 0.5)
        iin = 1.5 + random.gauss(0, 0.05)
        pin = vin * iin
        return {
            'VIN': vin,
            'TEMP_IC': temp_ic,
            'IIN': iin,
            'PIN': pin,
        }

    def read_channel_telemetry(self, page=0):
        base_voltage = 1.0 + page * 0.1
        vout = base_voltage + random.gauss(0, 0.005)
        iout = 5.0 + page * 2.0 + random.gauss(0, 0.1)
        return {
            'VOUT': vout,
            'IOUT': iout,
            'POUT': vout * iout,
            'TEMP1': 40.0 + page * 5.0 + random.gauss(0, 0.5),
            'DUTY': 0.4 + page * 0.05 + random.gauss(0, 0.01),
        }

    def read_global_status(self):
        return {'STATUS_INPUT': 0x00, 'STATUS_CML': 0x00}

    def read_channel_status(self, page=0):
        return {
            'STATUS_WORD': 0x0000,
            'STATUS_VOUT': 0x00,
            'STATUS_IOUT': 0x00,
            'STATUS_TEMPERATURE': 0x00,
            'STATUS_MFR': 0x00,
        }

    def read_status(self, page=0):
        s = self.read_global_status()
        s.update(self.read_channel_status(page))
        return s

    def write_val(self, page, cmd, value, fmt):
        return True

    def clear_faults(self):
        return True

    def store_user_all(self):
        return True

    def restore_user_all(self):
        return True

    def read_full_dump(self, page=0):
        return []

    def write_register(self, page, cmd, raw, size):
        return True


# ---- ОСНОВНЫЕ ФУНКЦИИ ----
def is_sim():
    return '--sim' in sys.argv or '--demo' in sys.argv


def find_buses():
    if is_sim():
        return [1]

    buses = []

    # ---- Linux /dev/i2c-N ----
    for i in range(20):
        if os.path.exists(f"/dev/i2c-{i}"):
            buses.append(i)

    # ---- CH341 USB ----
    try:
        from .ch341_i2c import find_ch341_devices
        for idx in range(len(find_ch341_devices())):
            buses.append(CH341_OFFSET + idx)
    except ImportError:
        pass
    except Exception as e:
        print(f"[scan] CH341 error: {e}")

    # ---- FTDI USB (FT232H / FT2232H / FT4232H) ----
    try:
        from .ftdi_i2c import find_ftdi_devices
        for idx in range(len(find_ftdi_devices())):
            buses.append(FTDI_OFFSET + idx)
    except ImportError:
        pass
    except Exception as e:
        print(f"[scan] FTDI error: {e}")

    return buses


def bus_label(bus_num):
    if is_sim():
        return "Simulated I2C #0"
    if bus_num >= FTDI_OFFSET:
        idx = bus_num - FTDI_OFFSET
        try:
            from .ftdi_i2c import find_ftdi_devices, I2C_PIDS
            devs = find_ftdi_devices()
            if idx < len(devs):
                d = devs[idx]
                chip = I2C_PIDS.get(d.pid, '?')
                sn = getattr(d, 'sn', '') or ''
                return f"FTDI FT{chip.upper()} {sn}".strip()
        except Exception:
            pass
        return f"FTDI #{idx}"
    if bus_num >= CH341_OFFSET:
        return f"CH341 #{bus_num - CH341_OFFSET}"
    return f"/dev/i2c-{bus_num}"


def scan_bus(bus_num):
    # ---- ДЕМО-РЕЖИМ ----
    if is_sim():
        print("[scan_bus] DEMO mode: returning simulated device")
        return [SimDevice()]

    # ---- РЕАЛЬНЫЙ РЕЖИМ ----
    devices = []
    bl = bus_label(bus_num)
    print(f"[scan_bus] scanning {bl} ...")

    try:
        with create_bus(bus_num) as bus:
            for addr in range(0x08, 0x78):
                # Пропускаем глобальные адреса
                if addr in GLOBAL_ADDRESSES:
                    continue

                try:
                    bus.read_byte(addr)
                    dev = PMBusDevice(bus_num, addr)
                    if dev.identify():
                        devices.append(dev)
                        print(f"[scan_bus]  + {dev.name} "
                              f"@ 0x{addr:02X}  "
                              f"pages={dev.num_pages}  "
                              f"id=0x{dev.special_id:04X}")
                except Exception:
                    continue
    except Exception as e:
        print(f"[scan] ошибка при сканировании шины {bus_num}: {e}")
        return []

    print(f"[scan_bus] found {len(devices)} device(s)")
    return devices


if __name__ == "__main__":
    import sys
    bus_num = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(f"Scanning bus {bus_num}...")
    found = scan_bus(bus_num)
    if found:
        print("\nFound devices:")
        for dev in found:
            print(f"  Address: 0x{dev.address:02X}")
            print(f"    Name: {dev.name}")
            print(f"    ID: 0x{dev.special_id:04X}")
            print(f"    Pages: {dev.num_pages}")
            print(f"    Revision: {dev.revision}")
            print()
    else:
        print("No devices found.")
