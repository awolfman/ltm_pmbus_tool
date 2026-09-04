# drivers/ch341_i2c.py
"""CH341T/CH341A USB-to-I2C adapter driver.

Drop-in replacement for smbus2.SMBus using pyusb.

Based on real CH341T behaviour:
    - After OUT commands, NO status bytes are returned.
    - Only data from IN commands is present.
    - Must send two control transfers to initialize I2C.

Device ordering is pinned to physical USB topology (bus + port path),
not to libusb enumeration order, so that two identical CH341 adapters
keep a stable dev_index across rescans and across independent processes.
"""

import threading
import time

import usb.core
import usb.util

from .base_driver import I2CDriverBase

CH341_VID = 0x1A86
CH341_PID_I2C = 0x5512
CH341_PID_SER = 0x5523
CH341_PIDS = {CH341_PID_I2C}

EP_OUT = 0x02
EP_IN = 0x82
PKT_LEN = 32
TIMEOUT = 2000

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


def ch341_location(dev):
    """Стабильный физический идентификатор: 'bus-port.port.port'."""
    port_numbers = getattr(dev, 'port_numbers', None) or ()
    if port_numbers:
        return f"{dev.bus}-{'.'.join(str(p) for p in port_numbers)}"
    # На некоторых платформах port_numbers недоступен (устройство прямо в root hub)
    return f"{dev.bus}-{dev.address}"


def _sort_key(dev):
    port_numbers = getattr(dev, 'port_numbers', None) or ()
    return (dev.bus, tuple(port_numbers), dev.address)


def find_ch341_devices():
    """Список CH341, отсортированный по физическому USB-порту.

    Порядок libusb (dev.address, порядок enumeration) может отличаться
    между вызовами и между процессами. Сортировка по (bus, port_numbers)
    гарантирует, что один и тот же физический адаптер получает один и
    тот же dev_index, пока остаётся в том же порту.
    """
    try:
        result = []
        for pid in CH341_PIDS:
            devs = usb.core.find(find_all=True, idVendor=CH341_VID, idProduct=pid)
            result.extend(devs)
        result.sort(key=_sort_key)
        return result
    except usb.core.NoBackendError:
        print("[CH341] WARNING: no libusb backend found.")
        return []
    except Exception as e:
        print(f"[CH341] USB scan error: {e}")
        return []


class CH341Bus(I2CDriverBase):
    """SMBus-compatible I2C bus via CH341T/CH341A USB adapter.

    Shared USB connection across instances (same dev_index).
    Thread-safe via per-device lock.
    dev_index назначается по физическому порту (см. find_ch341_devices),
    поэтому остаётся стабильным между пересканированиями и между
    независимыми процессами, пока адаптеры не переставляют в другие порты.
    """

    _shared = {}
    _global_lock = threading.Lock()

    def _open(self, dev_index):
        devs = find_ch341_devices()
        if dev_index >= len(devs):
            raise OSError(f"CH341 #{dev_index} not found ({len(devs)} available)")
        udev = devs[dev_index]
        loc = ch341_location(udev)

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
        except usb.core.USBError as e:
            # Интерфейс уже захвачен другим процессом -- это не warning,
            # а сигнал, что делить один физический донгл между двумя
            # программами нельзя. Раньше эта ошибка молча проглатывалась.
            raise OSError(
                f"CH341 at {loc} is busy (claimed by another process?): {e}"
            ) from e

        try:
            udev.ctrl_transfer(0x40, 0xA1, 0, 0, timeout=1000)
            time.sleep(0.05)
            udev.ctrl_transfer(0x40, 0x9A, 0x2518, 0x00D1, timeout=1000)
            time.sleep(0.05)
        except usb.core.USBError as e:
            print(f"[CH341] ctrl_transfer failed: {e}")

        self._shared[dev_index] = {
            'dev': udev,
            'lock': threading.Lock(),
            'location': loc,
        }

        pkt = bytearray(PKT_LEN)
        pkt[0] = CMD_STREAM
        pkt[1] = CMD_SET | SPEED_100K
        pkt[2] = CMD_END
        udev.write(EP_OUT, pkt, timeout=1000)

        self.reset_bus()
        print(f"[CH341] opened #{dev_index} at {loc} (bus={udev.bus} addr={udev.address})")

    def _send_stop(self, udev):
        stop_pkt = bytearray(PKT_LEN)
        stop_pkt[0] = CMD_STREAM
        stop_pkt[1] = CMD_STO
        stop_pkt[2] = CMD_END
        try:
            udev.write(EP_OUT, stop_pkt, timeout=100)
            time.sleep(0.01)
        except usb.core.USBError:
            pass

    def reset_bus(self):
        udev = self._get_entry()['dev']
        self._send_stop(udev)
        try:
            while True:
                udev.read(EP_IN, PKT_LEN, timeout=5)
        except usb.core.USBError:
            pass

    def _xfer(self, cmds, resp_len, timeout=None):
        if timeout is None:
            timeout = TIMEOUT
        udev = self._get_entry()['dev']

        try:
            while True:
                udev.read(EP_IN, PKT_LEN, timeout=1)
        except usb.core.USBError:
            pass

        pkt = bytearray(PKT_LEN)
        pkt[0] = CMD_STREAM
        for i, b in enumerate(cmds):
            if i + 1 >= PKT_LEN - 1:
                break
            pkt[i + 1] = b

        end_idx = min(len(cmds) + 1, PKT_LEN - 1)
        pkt[end_idx] = CMD_END

        try:
            udev.write(EP_OUT, pkt, timeout=timeout)
        except usb.core.USBError:
            self.reset_bus()
            return b'\xFF' * resp_len

        if resp_len <= 0:
            return b''

        try:
            raw = udev.read(EP_IN, max(PKT_LEN, resp_len), timeout=100)
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
        entry = self._get_entry()
        with entry['lock']:
            cmds = [CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | 1, CMD_STO]
            res = self._xfer(cmds, 1)
            return res[0] if res else 0xFF

    def read_byte_data(self, addr, cmd):
        entry = self._get_entry()
        with entry['lock']:
            cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), cmd, CMD_STA, CMD_OUT | 1, (addr << 1) | 1, CMD_IN | 1, CMD_STO]
            res = self._xfer(cmds, 1)
            if res:
                val = res[0]
                if cmd == 0x7E and val == 0x02:
                    return 0x00
                return val
            return 0xFF

    def read_word_data(self, addr, cmd):
        entry = self._get_entry()
        with entry['lock']:
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
        entry = self._get_entry()
        with entry['lock']:
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
        entry = self._get_entry()
        with entry['lock']:
            cmds = [CMD_STA, CMD_OUT | 3, (addr << 1), cmd, val & 0xFF, CMD_STO]
            self._xfer(cmds, 0)

    def write_word_data(self, addr, cmd, val):
        entry = self._get_entry()
        with entry['lock']:
            cmds = [CMD_STA, CMD_OUT | 4, (addr << 1), cmd, val & 0xFF, (val >> 8) & 0xFF, CMD_STO]
            self._xfer(cmds, 0)

    def write_byte(self, addr, val):
        entry = self._get_entry()
        with entry['lock']:
            cmds = [CMD_STA, CMD_OUT | 2, (addr << 1), val & 0xFF, CMD_STO]
            self._xfer(cmds, 0)
