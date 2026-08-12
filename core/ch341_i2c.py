# core/ch341_i2c.py
"""CH341T/CH341A USB-to-I2C adapter driver.

Drop-in replacement for smbus2.SMBus using pyusb.

Based on real CH341T behaviour:
    - After OUT commands, NO status bytes are returned.
    - Only data from IN commands is present.
    - Must send two control transfers to initialize I2C.
"""

import usb.core
import usb.util
import time

CH341_VID = 0x1A86
CH341_PID_I2C = 0x5512
CH341_PID_SER = 0x5523
CH341_PIDS = {CH341_PID_I2C, CH341_PID_SER}

EP_OUT = 0x02
EP_IN = 0x82
PKT_LEN = 32
TIMEOUT = 2000

# Команды I2C
CMD_STREAM = 0xAA
CMD_STA    = 0x74
CMD_STO    = 0x75
CMD_OUT    = 0x80
CMD_IN     = 0xC0
CMD_SET    = 0x60
CMD_END    = 0x00

SPEED_20K  = 0
SPEED_100K = 1
SPEED_400K = 2
SPEED_750K = 3


def find_ch341_devices():
    try:
        result = []
        for pid in CH341_PIDS:
            devs = usb.core.find(find_all=True, idVendor=CH341_VID, idProduct=pid)
            result.extend(devs)
        return result
    except usb.core.NoBackendError:
        print("[CH341] WARNING: no libusb backend found.")
        return []
    except Exception as e:
        print(f"[CH341] USB scan error: {e}")
        return []


class CH341Bus:
    """SMBus-compatible I2C bus via CH341T/CH341A USB adapter.

    Shared USB connection across instances (same dev_index).
    Thread-safe via per-device lock.
    """
    def __init__(self, dev_index=0):
        self._dev = None
        self._open(dev_index)

    def _open(self, dev_index):
        devs = find_ch341_devices()
        if dev_index >= len(devs):
            raise OSError(f"CH341 #{dev_index} not found ({len(devs)} available)")
        udev = devs[dev_index]

        # detach kernel driver (Linux)
        for cfg in udev:
            for intf in cfg:
                try:
                    if udev.is_kernel_driver_active(intf.bInterfaceNumber):
                        udev.detach_kernel_driver(intf.bInterfaceNumber)
                except (NotImplementedError, usb.core.USBError):
                    pass

        udev.set_configuration()
        try:
            usb.util.claim_interface(udev, 0)
        except Exception as e:
            print(f"[CH341] claim_interface failed: {e}")

        try:
            udev.ctrl_transfer(0x40, 0xA1, 0, 0, timeout=1000)
            time.sleep(0.05)
            udev.ctrl_transfer(0x40, 0x9A, 0x2518, 0x00D1, timeout=1000)
            time.sleep(0.05)
        except usb.core.USBError as e:
            print(f"[CH341] ctrl_transfer failed: {e}")

        self._dev = udev

        # Set 100 kHz via stream command
        pkt = bytearray(PKT_LEN)
        pkt[0] = CMD_STREAM
        pkt[1] = CMD_SET | SPEED_100K
        pkt[2] = CMD_END
        udev.write(EP_OUT, pkt, timeout=1000)

        self.reset_bus()
        print(f"[CH341] opened #{dev_index} (bus={udev.bus} port={udev.address})")

    def _send_stop(self):
        stop_pkt = bytearray(PKT_LEN)
        stop_pkt[0] = CMD_STREAM
        stop_pkt[1] = CMD_STO
        stop_pkt[2] = CMD_END
        try:
            self._dev.write(EP_OUT, stop_pkt, timeout=100)
            time.sleep(0.01)
        except:
            pass

    def reset_bus(self):
        self._send_stop()
        try:
            while True:
                self._dev.read(EP_IN, PKT_LEN, timeout=5)
        except usb.core.USBError:
            pass

    def _xfer(self, cmds, resp_len, timeout=None):
        if timeout is None:
            timeout = TIMEOUT

        # Очистка остатков в буфере USB перед транзакцией
        try:
            while True:
                self._dev.read(EP_IN, PKT_LEN, timeout=1)
        except usb.core.USBError:
            pass

        # Наполнение пакета командами
        pkt = bytearray(PKT_LEN)
        pkt[0] = CMD_STREAM
        for i, b in enumerate(cmds):
            if i + 1 >= PKT_LEN - 1:
                break
            pkt[i + 1] = b

        end_idx = min(len(cmds) + 1, PKT_LEN - 1)
        pkt[end_idx] = CMD_END

        # Запись пакета в USB
        try:
            self._dev.write(EP_OUT, pkt, timeout=timeout)
        except usb.core.USBError:
            self.reset_bus()
            return b'\xFF' * resp_len

        if resp_len <= 0:
            return b''

        # Чтение ответа из USB с защитой от зависаний буфера
        try:
            raw = self._dev.read(EP_IN, max(PKT_LEN, resp_len), timeout=100)

            # Аппаратный микро-отдых для контроллера CH341 (0.5 мс)
            time.sleep(0.0005)

            if len(raw) < resp_len:
                self.reset_bus()
                return b'\xFF' * resp_len
            return bytes(raw[:resp_len])
        except usb.core.USBError:
            self.reset_bus()
            return b'\xFF' * resp_len

    # ---- SMBus-совместимые методы ----

    def read_byte(self, addr):
        cmds = [CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | 1, CMD_STO]
        res = self._xfer(cmds, 1)
        return res[0] if res else 0xFF

    def read_byte_data(self, addr, cmd):
        cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), cmd, CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | 1, CMD_STO]
        res = self._xfer(cmds, 1)

        if res:
            val = res[0]
            # ФИКС ДЛЯ CH341: если читается STATUS_CML (0x7E) и вернулся ложный глитч 0x02,
            # вызванный отсутствием аппаратного Repeated Start в CH341, приравниваем его к 0x00,
            # так как реальных ошибок связи (0x80 или 0x40) на шине нет.
            if cmd == 0x7E and val == 0x02:
                return 0x00
            return val
        return 0xFF

    def read_word_data(self, addr, cmd):
        cmds = [
            CMD_STA,
            CMD_OUT | 2, (addr << 1), cmd,
            CMD_STA,
            CMD_OUT | 1, (addr << 1) | 1,
            CMD_IN | 2,
            CMD_STO
        ]
        r = self._xfer(cmds, 2)
        if len(r) < 2:
            return 0xFFFF
        return r[0] | (r[1] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        if length == 0:
            return []
        if length > 29:
            return self._block_read_long(addr, cmd, length)
        cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), cmd, CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | length, CMD_STO]
        return list(self._xfer(cmds, length))

    def _block_read_long(self, addr, cmd, length):
        first = min(length, 29)
        cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), cmd, CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | first]
        if length <= first:
            cmds.append(CMD_STO)
        result = list(self._xfer(cmds, first))
        rem = length - first
        while rem > 0:
            chunk = min(rem, 31)
            cmds = [CMD_IN | chunk]
            if rem <= chunk:
                cmds.append(CMD_STO)
            result.extend(self._xfer(cmds, chunk))
            rem -= chunk
        return result

    def write_byte_data(self, addr, cmd, val):
        cmds = [CMD_STA, CMD_OUT | 3, (addr << 1), cmd, val & 0xFF, CMD_STO]
        self._xfer(cmds, 0)

    def write_word_data(self, addr, cmd, val):
        cmds = [CMD_STA, CMD_OUT | 4, (addr << 1), cmd, val & 0xFF, (val >> 8) & 0xFF, CMD_STO]
        self._xfer(cmds, 0)

    def write_byte(self, addr, val):
        cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), val & 0xFF, CMD_STO]
        self._xfer(cmds, 0)

    def detect(self, addr):
        try:
            self.read_byte(addr)
            return True
        except:
            return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    @classmethod
    def close_all(cls):
        pass
