"""FTDI I2C transport using PyFtdi.

Scalar SMBus-compatible operations:
    Receive Byte
    Send Byte
    Read/Write Byte Data
    Read/Write Word Data, little-endian

Block helpers implement fixed-length I2C transfers only.
SMBus Block Read with a count byte and PEC are not implemented.

Transport failures propagate as exceptions.
No error sentinels and no automatic CLEAR_FAULTS.
"""

import logging
import threading
from contextlib import contextmanager

from .base_driver import I2CDriverBase

try:
    from pyftdi.ftdi import Ftdi
    from pyftdi.i2c import I2cController
    from pyftdi.usbtools import UsbTools

    HAS_PYFTDI = True
except ImportError:
    Ftdi = None
    I2cController = None
    UsbTools = None
    HAS_PYFTDI = False


logger = logging.getLogger(__name__)

I2C_PIDS = {
    0x6014: "232h",
    0x6010: "2232h",
    0x6011: "4232h",
}

I2C_FREQUENCY = 100_000
I2C_CLOCK_STRETCHING = False
MAX_I2C_BLOCK_LENGTH = 32


def find_ftdi_devices():
    if not HAS_PYFTDI:
        return []

    try:
        UsbTools.flush_cache()

        result = []

        for item in Ftdi.list_devices():
            if (
                isinstance(item, tuple)
                and len(item) == 2
            ):
                desc, _interfaces = item
            else:
                desc = item

            if not hasattr(desc, "vid") or not hasattr(desc, "pid"):
                logger.warning(
                    "Ignoring invalid FTDI descriptor: %r",
                    desc,
                )
                continue

            result.append(desc)

        return result

    except Exception:
        logger.exception("FTDI enumeration failed")
        return []

def _url_for(desc, devices):
    """Select interface 1 without silently choosing another device."""
    vendor = f"0x{desc.vid:04x}"
    product = f"0x{desc.pid:04x}"
    serial = getattr(desc, "sn", None)

    if serial:
        matches = [
            item for item in devices
            if item.vid == desc.vid
            and item.pid == desc.pid
            and getattr(item, "sn", None) == serial
        ]
        if len(matches) != 1:
            raise OSError(
                "Multiple FTDI devices have the same serial number. "
                "Connect only the intended adapter."
            )

        return f"ftdi://{vendor}:{product}:{serial}/1"

    matches = [
        item for item in devices
        if item.vid == desc.vid and item.pid == desc.pid
    ]
    if len(matches) != 1:
        raise OSError(
            "Cannot uniquely select an FTDI device without a serial "
            "number. Connect only one adapter of this type."
        )

    return f"ftdi://{vendor}:{product}/1"


def _validate_integer(value, minimum, maximum, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{name} must be an integer in "
            f"{minimum}..{maximum}, got {value!r}"
        )
    return value


def _validate_address(addr):
    # Exclude reserved I2C addresses.
    return _validate_integer(addr, 0x08, 0x77, "I2C address")


def _validate_byte(value, name):
    return _validate_integer(value, 0, 0xFF, name)


def _checked_response(data, length):
    if data is None or isinstance(data, int):
        raise OSError(f"Invalid I2C response: {data!r}")

    try:
        payload = bytes(data)
    except (TypeError, ValueError) as exc:
        raise OSError("Invalid I2C response type") from exc

    if len(payload) != length:
        raise OSError(
            f"Short or oversized I2C response: "
            f"expected {length} bytes, received {len(payload)}"
        )

    return payload


class FTDIBus(I2CDriverBase):
    """Shared PyFtdi controller with serialized transactions.

    Only interface 1 is opened.

    The driver does not retry actions itself beyond one PyFtdi
    transaction attempt. Higher-level read retries remain the
    responsibility of PMBusDevice.
    """

    _shared = {}
    _global_lock = threading.RLock()

    legacy_error_sentinels = False
    checks_i2c_ack = True

    def __init__(self, dev_index=0):
        _validate_integer(
            dev_index, 0, 1_000_000, "FTDI device index"
        )

        self._idx = dev_index
        self._closed = True
        self._entry = None

        with type(self)._global_lock:
            if dev_index not in type(self)._shared:
                self._open(dev_index)

            entry = type(self)._shared[dev_index]
            entry["users"] += 1
            self._entry = entry
            self._closed = False

    def _open(self, dev_index):
        if not HAS_PYFTDI:
            raise ImportError(
                "PyFtdi is required. Install with: "
                "python -m pip install pyftdi"
            )

        devices = find_ftdi_devices()
        if dev_index >= len(devices):
            raise OSError(
                f"FTDI #{dev_index} not found; "
                f"{len(devices)} supported device(s) available"
            )

        desc = devices[dev_index]
        url = _url_for(desc, devices)
        controller = I2cController()

        try:
            controller.configure(
                url,
                frequency=I2C_FREQUENCY,
                clockstretching=I2C_CLOCK_STRETCHING,
            )

            # One transaction attempt. Do not replay writes silently.
            controller.set_retry_count(1)

        except Exception:
            try:
                controller.terminate()
            except Exception:
                logger.exception(
                    "Failed to release FTDI after initialization error"
                )
            raise

        type(self)._shared[dev_index] = {
            "ctrl": controller,
            "ports": {},
            "lock": threading.RLock(),
            "desc": desc,
            "url": url,
            "users": 0,
            "closed": False,
        }

        logger.info(
            "FTDI opened: %s, frequency=%d Hz, clockstretching=%s",
            url,
            I2C_FREQUENCY,
            I2C_CLOCK_STRETCHING,
        )

    def _get_entry(self):
        entry = self._entry
        if (
            self._closed
            or entry is None
            or entry["closed"]
            or type(self)._shared.get(self._idx) is not entry
        ):
            raise RuntimeError("FTDI bus is closed")
        return entry

    @contextmanager
    def _transaction(self):
        # Acquire the transaction lock before allowing close_all
        # to remove this entry.
        with type(self)._global_lock:
            entry = self._get_entry()
            entry["lock"].acquire()

        try:
            if self._closed or entry["closed"]:
                raise RuntimeError("FTDI bus is closed")
            yield entry
        finally:
            entry["lock"].release()

    @staticmethod
    def _port_locked(entry, addr):
        port = entry["ports"].get(addr)
        if port is None:
            port = entry["ctrl"].get_port(addr)
            entry["ports"][addr] = port
        return port

    def read_byte(self, addr):
        """SMBus Receive Byte, without writing a command first.

        Do not use this operation for PMBus device identification.
        """
        _validate_address(addr)

        with self._transaction() as entry:
            port = self._port_locked(entry, addr)
            data = port.read(1, relax=True)
            return _checked_response(data, 1)[0]

    def _read_fixed(self, addr, cmd, length):
        _validate_address(addr)
        _validate_byte(cmd, "Command")
        _validate_integer(
            length, 1, MAX_I2C_BLOCK_LENGTH, "Read length"
        )

        with self._transaction() as entry:
            port = self._port_locked(entry, addr)

            # Command write, repeated START, data read, STOP.
            # ACK/NACK generation is handled by PyFtdi.
            data = port.exchange(
                bytes([cmd]),
                length,
                relax=True,
            )
            return _checked_response(data, length)

    def read_byte_data(self, addr, cmd):
        return self._read_fixed(addr, cmd, 1)[0]

    def read_word_data(self, addr, cmd):
        data = self._read_fixed(addr, cmd, 2)
        return data[0] | (data[1] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        """Fixed-length I2C read, not SMBus Block Read."""
        return list(self._read_fixed(addr, cmd, length))

    def _write_payload(self, addr, payload):
        _validate_address(addr)

        with self._transaction() as entry:
            port = self._port_locked(entry, addr)
            port.write(payload, relax=True)

        return True

    def write_byte(self, addr, val):
        """SMBus Send Byte."""
        _validate_byte(val, "Command")
        return self._write_payload(addr, bytes([val]))

    def write_byte_data(self, addr, cmd, val):
        _validate_byte(cmd, "Command")
        _validate_byte(val, "Byte value")
        return self._write_payload(
            addr, bytes([cmd, val])
        )

    def write_word_data(self, addr, cmd, val):
        _validate_byte(cmd, "Command")
        _validate_integer(val, 0, 0xFFFF, "Word value")
        return self._write_payload(
            addr,
            bytes([cmd, val & 0xFF, val >> 8]),
        )

    def write_i2c_block_data(self, addr, cmd, data):
        """Fixed-length I2C write without an SMBus count byte."""
        _validate_byte(cmd, "Command")

        if isinstance(data, (int, str)):
            raise ValueError("Block data must be a byte sequence")

        try:
            items = list(data)
        except TypeError as exc:
            raise ValueError(
                "Block data must be a byte sequence"
            ) from exc

        _validate_integer(
            len(items), 1, MAX_I2C_BLOCK_LENGTH, "Block length"
        )
        for value in items:
            _validate_byte(value, "Block byte")

        return self._write_payload(
            addr, bytes([cmd]) + bytes(items)
        )

    def detect(self, addr):
        """Probe address ACK only.

        An address probe is not PMBus identification and may affect
        the status of some slaves. Do not use it in the PMBus scan.
        """
        _validate_address(addr)

        with self._transaction() as entry:
            return bool(
                entry["ctrl"].poll(addr, relax=True)
            )

    @staticmethod
    def _terminate_entry(entry):
        entry["closed"] = True
        entry["ports"].clear()
        try:
            entry["ctrl"].terminate()
        except Exception:
            logger.exception(
                "Failed to terminate FTDI controller %s",
                entry["url"],
            )

    def close(self):
        cls = type(self)

        with cls._global_lock:
            if self._closed:
                return

            entry = self._entry
            if entry is None:
                self._closed = True
                return

            with entry["lock"]:
                self._closed = True
                self._entry = None

                if entry["closed"]:
                    return

                entry["users"] -= 1
                if entry["users"] == 0:
                    if cls._shared.get(self._idx) is entry:
                        del cls._shared[self._idx]
                    cls._terminate_entry(entry)

    @classmethod
    def close_all(cls):
        """Close controllers and invalidate existing bus objects."""
        with cls._global_lock:
            entries = list(cls._shared.values())
            cls._shared.clear()

            for entry in entries:
                with entry["lock"]:
                    cls._terminate_entry(entry)

    @classmethod
    def get_available_devices(cls):
        result = []
        for index, desc in enumerate(find_ftdi_devices()):
            result.append({
                "index": index,
                "description": getattr(
                    desc, "description", ""
                ),
                "sn": getattr(desc, "sn", ""),
                "pid": desc.pid,
                "vid": desc.vid,
            })
        return result
