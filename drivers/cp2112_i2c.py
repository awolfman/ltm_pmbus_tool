# drivers/cp2112_i2c.py
"""Silicon Labs CP2112 USB-to-I2C/SMBus bridge driver.

Drop-in replacement for smbus2.SMBus using hidapi.

Device ordering is pinned to the HID device path returned by hidapi
(it encodes physical USB location), so dev_index stays stable across
rescans and across independent processes.
"""

import threading
import time

import hid

from .base_driver import I2CDriverBase

VID = 0x10C4
PID = 0xEA90


def find_cp2112_devices():
    """Список HID-дескрипторов CP2112, отсортированный по физическому path."""
    try:
        devs = hid.enumerate(VID, PID)
        return sorted(devs, key=lambda d: d.get('path', b''))
    except Exception as e:
        print(f"[CP2112] scan error: {e}")
        return []


class CP2112Bus(I2CDriverBase):
    """SMBus-compatible I2C bus via Silicon Labs CP2112 USB-HID bridge."""

    _shared = {}
    _global_lock = threading.Lock()

    def _open(self, dev_index):
        devs = find_cp2112_devices()
        if dev_index >= len(devs):
            raise OSError(f"CP2112 #{dev_index} not found ({len(devs)} available)")
        desc = devs[dev_index]

        device = hid.device()
        try:
            device.open_path(desc['path'])
        except OSError as e:
            sn = desc.get('serial_number') or '?'
            raise OSError(
                f"CP2112 #{dev_index} (sn={sn}) busy or unavailable: {e}"
            ) from e

        self._shared[dev_index] = {
            'device': device,
            'lock': threading.Lock(),
            'desc': desc,
        }

        self._configure_smbus(device)
        sn = desc.get('serial_number') or '?'
        print(f"[CP2112] opened #{dev_index} (sn={sn})")

    @staticmethod
    def _configure_smbus(device):
        """Конфигурация чипа: 400 кГц, таймауты и аппаратный Clock Stretching"""
        config = [0x06] + [0] * 13
        config[1] = 0x00
        config[2] = 0x06
        config[3] = 0x1A
        config[4] = 0x80
        config[5] = 0x02
        config[6] = 0x00
        config[7] = 0xC8
        config[8] = 0x00
        config[9] = 0x00
        config[10] = 0x00
        config[11] = 0x03
        device.send_feature_report(config)
        time.sleep(0.05)

    @staticmethod
    def _wait_for_transfer(device):
        for _ in range(100):
            device.write([0x15])
            status_report = device.read(64)
            if status_report and status_report[0] == 0x16:
                status = status_report[1]
                if status == 0x02:
                    return True
                elif status == 0x01:
                    time.sleep(0.001)
                    continue
                else:
                    return False
        return False

    @staticmethod
    def _fetch_response(device):
        device.write([0x12, 0x01])
        response = device.read(64)
        if response and response[0] == 0x13:
            length = response[2]
            if length > 0:
                return response[3:3 + length]
        return None

    def read_byte(self, addr):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            payload = [0x10, addr_8bit, 0x00, 0x01]
            device.write(payload)
            if self._wait_for_transfer(device):
                res = self._fetch_response(device)
                if res:
                    return res[0]
            return 0xFF

    def read_byte_data(self, addr, cmd):
        res = self._read_block(addr, cmd, 1)
        return res[0] if res else 0xFF

    def read_word_data(self, addr, cmd):
        res = self._read_block(addr, cmd, 2)
        if res and len(res) == 2:
            return res[0] | (res[1] << 8)
        return 0xFFFF

    def read_i2c_block_data(self, addr, cmd, length):
        res = self._read_block(addr, cmd, length)
        return list(res) if res else []

    def _read_block(self, addr, cmd, num_bytes):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            high_len = (num_bytes >> 8) & 0xFF
            low_len = num_bytes & 0xFF
            payload = [0x15, addr_8bit, high_len, low_len, 1, cmd]
            device.write(payload)
            if self._wait_for_transfer(device):
                return self._fetch_response(device)
            return None

    def write_byte(self, addr, val):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            payload = [0x14, addr_8bit, 1, val & 0xFF]
            device.write(payload)
            return self._wait_for_transfer(device)

    def write_byte_data(self, addr, cmd, val):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            payload = [0x14, addr_8bit, 2, cmd, val & 0xFF]
            device.write(payload)
            return self._wait_for_transfer(device)

    def write_word_data(self, addr, cmd, val):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            low_byte = val & 0xFF
            high_byte = (val >> 8) & 0xFF
            payload = [0x14, addr_8bit, 3, cmd, low_byte, high_byte]
            device.write(payload)
            return self._wait_for_transfer(device)

    def write_i2c_block_data(self, addr, cmd, data):
        entry = self._get_entry()
        with entry['lock']:
            device = entry['device']
            addr_8bit = addr << 1
            length = len(data) + 1
            if length > 61:
                raise ValueError("Размер блока данных превышает максимальный пакет CP2112")
            payload = [0x14, addr_8bit, length, cmd] + list(data)
            device.write(payload)
            return self._wait_for_transfer(device)

    def close(self):
        super().close()
        entry = self._shared.pop(self._idx, None)
        if entry:
            try:
                entry['device'].close()
            except Exception:
                pass

    @classmethod
    def close_all(cls):
        with cls._global_lock:
            for entry in cls._shared.values():
                try:
                    entry['device'].close()
                except Exception as e:
                    print(f"[CP2112] Error closing device: {e}")
            cls._shared.clear()
            print("[CP2112] all devices released")
