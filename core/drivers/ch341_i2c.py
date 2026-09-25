"""CH341T/CH341A USB-to-I2C transport.

USB failures and incomplete responses raise exceptions.
I2C slave ACK is not reported by this implementation.
No PEC handling is implemented.
"""

import logging
import threading
import time

import usb.core
import usb.util

from .base_driver import I2CDriverBase


logger = logging.getLogger(__name__)

CH341_VID = 0x1A86
CH341_PID_I2C = 0x5512
CH341_PID_SER = 0x5523
CH341_PIDS = {CH341_PID_I2C}

EP_OUT = 0x02
EP_IN = 0x82
PKT_LEN = 32
TIMEOUT = 2000

CMD_STREAM = 0xAA
CMD_STA = 0x74
CMD_STO = 0x75
CMD_OUT = 0x80
CMD_IN = 0xC0
CMD_SET = 0x60
CMD_END = 0x00

SPEED_20K = 0
SPEED_100K = 1
SPEED_400K = 2
SPEED_750K = 3


def ch341_location(dev):
    ports = getattr(dev, "port_numbers", None) or ()
    if ports:
        return f"{dev.bus}-{'.'.join(str(p) for p in ports)}"
    return f"{dev.bus}-{dev.address}"


def _sort_key(dev):
    ports = getattr(dev, "port_numbers", None) or ()
    return dev.bus, tuple(ports), dev.address


def find_ch341_devices():
    try:
        result = []
        for pid in CH341_PIDS:
            devices = usb.core.find(
                find_all=True,
                idVendor=CH341_VID,
                idProduct=pid,
            )
            result.extend(devices or [])
        return sorted(result, key=_sort_key)
    except usb.core.NoBackendError:
        logger.warning("No libusb backend found")
        return []
    except usb.core.USBError as exc:
        logger.warning("CH341 enumeration failed: %s", exc)
        return []


class CH341Bus(I2CDriverBase):
    _shared = {}
    _global_lock = threading.Lock()

    legacy_error_sentinels = False
    checks_i2c_ack = False

    def _get_entry(self):
        if self._closed:
            raise RuntimeError("CH341 bus is closed")
        return super()._get_entry()

    @staticmethod
    def _validate(value, maximum, label):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= maximum
        ):
            raise ValueError(f"Invalid {label}: {value!r}")

    def _open(self, dev_index):
        devices = find_ch341_devices()
        if not 0 <= dev_index < len(devices):
            raise OSError(f"CH341 #{dev_index} not found")

        dev = devices[dev_index]
        location = ch341_location(dev)
        detached = []
        claimed = False

        try:
            for cfg in dev:
                for intf in cfg:
                    number = intf.bInterfaceNumber
                    try:
                        if dev.is_kernel_driver_active(number):
                            dev.detach_kernel_driver(number)
                            detached.append(number)
                    except NotImplementedError:
                        pass

            dev.set_configuration()
            usb.util.claim_interface(dev, 0)
            claimed = True

            dev.ctrl_transfer(0x40, 0xA1, 0, 0, timeout=1000)
            time.sleep(0.05)
            dev.ctrl_transfer(
                0x40, 0x9A, 0x2518, 0x00D1, timeout=1000
            )
            time.sleep(0.05)

            self._shared[dev_index] = {
                "dev": dev,
                "lock": threading.RLock(),
                "location": location,
                "detached": detached,
            }

            self._write_packet(
                dev, [CMD_SET | SPEED_100K], timeout=1000
            )
            self._drain(dev)

        except Exception:
            self._shared.pop(dev_index, None)
            if claimed:
                try:
                    usb.util.release_interface(dev, 0)
                except Exception:
                    pass
            for number in detached:
                try:
                    dev.attach_kernel_driver(number)
                except Exception:
                    pass
            usb.util.dispose_resources(dev)
            raise

        print(f"[CH341] opened #{dev_index} at {location}")

    @staticmethod
    def _write_packet(dev, commands, timeout=TIMEOUT):
        if len(commands) > PKT_LEN - 2:
            raise ValueError("CH341 command packet is too long")

        packet = bytearray(PKT_LEN)
        packet[0] = CMD_STREAM
        packet[1:1 + len(commands)] = bytes(commands)
        packet[1 + len(commands)] = CMD_END

        written = dev.write(EP_OUT, packet, timeout=timeout)
        if written != len(packet):
            raise OSError(
                f"Short USB write: {written}/{len(packet)}"
            )

    @staticmethod
    def _drain(dev):
        # Only an endpoint timeout means that the queue is empty.
        for _ in range(16):
            try:
                dev.read(EP_IN, PKT_LEN, timeout=2)
            except usb.core.USBTimeoutError:
                return
        raise OSError("CH341 input queue did not become empty")

    def reset_bus(self):
        entry = self._get_entry()
        with entry["lock"]:
            self._write_packet(
                entry["dev"], [CMD_STO], timeout=100
            )
            time.sleep(0.01)
            self._drain(entry["dev"])

    def _recover(self, dev):
        try:
            self._write_packet(dev, [CMD_STO], timeout=100)
            self._drain(dev)
        except Exception:
            logger.debug("CH341 recovery failed", exc_info=True)

    def _xfer(self, commands, resp_len, timeout=TIMEOUT):
        entry = self._get_entry()
        dev = entry["dev"]

        if not 0 <= resp_len <= PKT_LEN:
            raise ValueError("Invalid CH341 response length")

        with entry["lock"]:
            try:
                self._drain(dev)
                self._write_packet(dev, commands, timeout)

                if resp_len == 0:
                    return b""

                raw = bytes(
                    dev.read(EP_IN, PKT_LEN, timeout=timeout)
                )
                if len(raw) != resp_len:
                    raise OSError(
                        f"Unexpected USB response length: "
                        f"{len(raw)}, expected {resp_len}"
                    )

                time.sleep(0.0005)
                return raw

            except Exception:
                self._recover(dev)
                raise

    def read_byte(self, addr):
        self._validate(addr, 0x7F, "address")
        commands = [
            CMD_STA,
            CMD_OUT | 1, (addr << 1) | 1,
            CMD_IN,
            CMD_STO,
        ]
        return self._xfer(commands, 1)[0]

    def _read_fixed(self, addr, cmd, length):
        self._validate(addr, 0x7F, "address")
        self._validate(cmd, 0xFF, "command")
        self._validate(length, 255, "length")

        if length == 0:
            return b""

        entry = self._get_entry()
        with entry["lock"]:
            result = bytearray()
            remaining = length
            first = True

            while remaining:
                count = min(remaining, 31)
                final = count == remaining
                commands = []

                if first:
                    commands.extend([
                        CMD_STA,
                        CMD_OUT | 2, addr << 1, cmd,
                        CMD_STA,
                        CMD_OUT | 1, (addr << 1) | 1,
                    ])

                if final:
                    if count > 1:
                        commands.append(CMD_IN | (count - 1))
                    commands.extend([CMD_IN, CMD_STO])
                else:
                    # All intermediate bytes are acknowledged.
                    commands.append(CMD_IN | count)

                result.extend(self._xfer(commands, count))
                remaining -= count
                first = False

            return bytes(result)

    def read_byte_data(self, addr, cmd):
        return self._read_fixed(addr, cmd, 1)[0]

    def read_word_data(self, addr, cmd):
        data = self._read_fixed(addr, cmd, 2)
        return data[0] | (data[1] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        """Fixed-length I2C read, not SMBus Block Read."""
        return list(self._read_fixed(addr, cmd, length))

    def _block_read_long(self, addr, cmd, length):
        return self.read_i2c_block_data(addr, cmd, length)

    def _write_data(self, addr, payload):
        self._validate(addr, 0x7F, "address")
        for value in payload:
            self._validate(value, 0xFF, "payload byte")

        commands = [
            CMD_STA,
            CMD_OUT | (len(payload) + 1),
            addr << 1,
            *payload,
            CMD_STO,
        ]
        self._xfer(commands, 0)
        # USB completion does not prove slave ACK.

    def write_byte(self, addr, val):
        self._write_data(addr, [val])

    def write_byte_data(self, addr, cmd, val):
        self._write_data(addr, [cmd, val])

    def write_word_data(self, addr, cmd, val):
        self._validate(val, 0xFFFF, "word")
        self._write_data(
            addr, [cmd, val & 0xFF, (val >> 8) & 0xFF]
        )

    def detect(self, addr):
        raise NotImplementedError(
            "Reliable address-only ACK detection is not implemented"
        )

    @classmethod
    def _release_entry(cls, entry):
        with entry["lock"]:
            dev = entry["dev"]
            try:
                cls._write_packet(dev, [CMD_STO], timeout=100)
            except Exception:
                pass
            try:
                usb.util.release_interface(dev, 0)
            except Exception:
                pass
            for number in entry.get("detached", []):
                try:
                    dev.attach_kernel_driver(number)
                except Exception:
                    pass
            usb.util.dispose_resources(dev)

    def close(self):
        with self._global_lock:
            entry = self._shared.pop(self._idx, None)
            self._closed = True
            if entry is not None:
                self._release_entry(entry)

    @classmethod
    def close_all(cls):
        with cls._global_lock:
            entries = list(cls._shared.values())
            cls._shared.clear()
            for entry in entries:
                try:
                    cls._release_entry(entry)
                except Exception:
                    logger.warning(
                        "CH341 release failed", exc_info=True
                    )
