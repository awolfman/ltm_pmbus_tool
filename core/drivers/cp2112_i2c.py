# drivers/cp2112_i2c.py
"""Silicon Labs CP2112 USB-to-I2C bridge using hidapi.

Provides scalar access and fixed-length I2C block transfers.
HID paths are sorted for enumeration; indices may change when the
connected device set changes.

A failed transaction blocks further I/O on the shared handle.
Closing and reopening a handle alone does not prove bridge recovery.
"""

import threading
import time

import hid
from .base_driver import I2CDriverBase
from contextlib import contextmanager

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

class CP2112AddressNackError(OSError):
    """A complete status report confirmed an address NACK timeout."""

class CP2112Bus(I2CDriverBase):
    """SMBus-compatible I2C bus via Silicon Labs CP2112 USB-HID bridge."""

    legacy_error_sentinels = False
    _shared = {}
    _global_lock = threading.Lock()

    def __init__(self, dev_index=0):
        if (
            isinstance(dev_index, bool)
            or not isinstance(dev_index, int)
            or dev_index < 0
        ):
            raise ValueError(
                f"Invalid CP2112 device index: {dev_index!r}"
            )

        self._idx = dev_index
        self._closed = False

        with self._global_lock:
            if dev_index not in self._shared:
                self._open(dev_index)

            entry = self._shared[dev_index]
            with entry["lock"]:
                if entry["closed"]:
                    raise OSError("CP2112 shared handle is closed")

                entry["users"] += 1
                self._entry = entry

    def is_failed(self):
        return self.session_failed()

    def _get_entry(self):
        entry = self._entry
        if self._closed or entry["closed"]:
            raise OSError("CP2112 handle is closed")
        return entry

    def session_failed(self):
        """Report an unusable session without opening it or doing I/O."""
        entry = self._entry

        with entry["lock"]:
            return bool(
                self._closed
                or entry["closed"]
                or entry["failed"]
            )

    @contextmanager
    def _transaction(self):
        entry = self._get_entry()

        with entry["lock"]:
            # Recheck after acquiring the lock. A concurrent close
            # may have completed while this operation was waiting.
            if self._closed or entry["closed"]:
                raise OSError("CP2112 handle is closed")

            if entry["failed"]:
                raise OSError(
                    "CP2112 session is blocked after a failed "
                    "transaction. Further I/O is disabled."
                )

            try:
                yield entry["device"]

            except CP2112AddressNackError:
                # A complete terminal status report was consumed.
                # No read data was received and no force-send
                # request was issued. Keep the session usable.
                raise

            except BaseException:
                # Preserve the original exception, including errno.
                # The HID response stream may be unsynchronized.
                entry["failed"] = True
                raise

    def _open(self, dev_index):
        if (
            isinstance(dev_index, bool)
            or not isinstance(dev_index, int)
            or dev_index < 0
        ):
            raise ValueError(
                f"Invalid CP2112 device index: {dev_index!r}"
            )

        devs = find_cp2112_devices()
        if dev_index >= len(devs):
            raise OSError(
                f"CP2112 #{dev_index} not found "
                f"({len(devs)} available)"
            )

        desc = devs[dev_index]
        device = hid.device()

        try:
            device.open_path(desc["path"])
            self._configure_smbus(device)

        except Exception:
            # Close the handle without replacing the original error.
            # Preserve exception identity, type and errno.
            try:
                device.close()
            except Exception:
                pass
            raise

        self._shared[dev_index] = {
            "device": device,
            "lock": threading.Lock(),
            "desc": desc,
            "users": 0,
            "closed": False,
            "failed": False,
        }

        sn = desc.get("serial_number") or "?"
        print(f"[CP2112] opened #{dev_index} (sn={sn})")

    @staticmethod
    def _configure_smbus(device):
        """Configure volatile SMBus settings and verify readback.

        AN495 section 5.6. Multi-byte fields are big-endian.
        This does not program PROM or target-device registers.
        """
        clock_hz = 100_000
        write_timeout_ms = 200
        read_timeout_ms = 200
        retry_count = 1

        config = (
            bytes([0x06])
            + clock_hz.to_bytes(4, "big")
            + bytes([
                0x02,  # Bridge slave address, not target address.
                0x00,  # Auto Send Read disabled.
            ])
            + write_timeout_ms.to_bytes(2, "big")
            + read_timeout_ms.to_bytes(2, "big")
            + bytes([
                0x00,  # SCL Low Timeout disabled.
            ])
            + retry_count.to_bytes(2, "big")
        )

        sent = device.send_feature_report(config)
        if (
            isinstance(sent, bool)
            or not isinstance(sent, int)
            or sent != len(config)
        ):
            raise OSError(
                "CP2112 SMBus configuration report failed: "
                f"expected {len(config)} bytes, got {sent!r}"
            )

        response = device.get_feature_report(0x06, len(config))

        if not isinstance(response, (list, tuple, bytes, bytearray)):
            raise OSError(
                "CP2112 returned an invalid SMBus configuration report"
            )

        try:
            actual = bytes(response)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OSError(
                "CP2112 returned invalid configuration bytes"
            ) from exc

        if len(actual) != len(config):
            raise OSError(
                "CP2112 SMBus configuration length mismatch: "
                f"expected {len(config)}, got {len(actual)}"
            )

        if actual != config:
            raise OSError(
                "CP2112 SMBus configuration readback mismatch: "
                f"expected {config.hex(' ')}, "
                f"got {actual.hex(' ')}"
            )

    @staticmethod
    def _wait_for_transfer(device, timeout=1.0):
        """Wait for successful completion using AN495 status reports.

        HID reads have a finite timeout. A transfer failure is raised,
        not returned as a successful scalar value or sentinel.
        """
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "CP2112 transfer status deadline expired"
                )

            request = bytes([0x15, 0x01])
            sent = device.write(request)

            if (
                isinstance(sent, bool)
                or not isinstance(sent, int)
                or sent != len(request)
            ):
                raise OSError(
                    "CP2112 status request failed: "
                    f"expected {len(request)} bytes, got {sent!r}"
                )

            # Recalculate after the HID write.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "CP2112 transfer status deadline expired"
                )

            timeout_ms = max(1, int(remaining * 1000))
            report = device.read(64, timeout_ms)

            if not report:
                raise TimeoutError(
                    "CP2112 transfer status response timed out"
                )

            if not isinstance(
                report, (list, tuple, bytes, bytearray)
            ):
                raise OSError(
                    "CP2112 invalid transfer status response"
                )

            try:
                report = bytes(report)
            except (TypeError, ValueError, OverflowError) as exc:
                raise OSError(
                    "CP2112 invalid transfer status bytes"
                ) from exc

            # AN495 table defines two 16-bit fields at offsets 3 and 5.
            if (
                not 7 <= len(report) <= 64
                or report[0] != 0x16
            ):
                raise OSError(
                    "CP2112 malformed transfer status response"
                )

            status = report[1]
            detail = report[2]
            retries = int.from_bytes(report[3:5], "big")
            received = int.from_bytes(report[5:7], "big")

            if status == 0x02 and detail == 0x05:
                return True

            if (
                status == 0x03
                and detail == 0x00
                and received == 0
            ):
                raise CP2112AddressNackError(
                    "CP2112 address NACK timeout: "
                    f"retries={retries}"
                )

            if status == 0x01:
                if detail not in {0x00, 0x01, 0x02, 0x03}:
                    raise OSError(
                        "CP2112 invalid busy status: "
                        f"0x{detail:02X}"
                    )
                time.sleep(0.001)
                continue

            # Idle is not evidence that this transaction succeeded.
            raise OSError(
                "CP2112 transfer did not complete successfully: "
                f"status=0x{status:02X}, "
                f"detail=0x{detail:02X}, "
                f"retries={retries}, received={received}"
            )

    @staticmethod
    def _fetch_response(device, length):
        """Fetch one data report after successful transfer status."""
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or not 1 <= length <= 61
        ):
            raise ValueError(
                "CP2112 single-report read length must be 1..61"
            )

        request = bytes([
            0x12,
            (length >> 8) & 0xFF,
            length & 0xFF,
        ])
        sent = device.write(request)

        if (
            isinstance(sent, bool)
            or not isinstance(sent, int)
            or sent != len(request)
        ):
            raise OSError(
                "CP2112 force-send request failed"
            )

        response = device.read(64, 1000)
        if not response:
            raise TimeoutError(
                "CP2112 data response timed out"
            )

        if not isinstance(
            response, (list, tuple, bytes, bytearray)
        ):
            raise OSError("CP2112 invalid data response")

        try:
            response = bytes(response)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OSError(
                "CP2112 invalid data response bytes"
            ) from exc

        if (
            not 3 <= len(response) <= 64
            or response[0] != 0x13
        ):
            raise OSError("CP2112 malformed data response")

        status = response[1]
        count = response[2]

        # Completion was already checked through report 0x16.
        # Reading completion status can return the bridge to idle.
        if status not in {0x00, 0x02}:
            raise OSError(
                "CP2112 unexpected data response status: "
                f"0x{status:02X}"
            )

        if count != length or len(response) < 3 + count:
            raise OSError(
                "CP2112 data length mismatch: "
                f"requested {length}, reported {count}, "
                f"payload available {len(response) - 3}"
            )

        return list(response[3:3 + count])

    def read_byte(self, addr):
        if (
            isinstance(addr, bool)
            or not isinstance(addr, int)
            or not 0x01 <= addr <= 0x7B
        ):
            raise ValueError("Invalid CP2112 slave address")

        with self._transaction() as device:
            payload = bytes([
                0x10, addr << 1, 0x00, 0x01,
            ])
            sent = device.write(payload)

            if (
                isinstance(sent, bool)
                or not isinstance(sent, int)
                or sent != len(payload)
            ):
                raise OSError(
                    "CP2112 read request failed"
                )

            self._wait_for_transfer(device)
            data = self._fetch_response(device, 1)
            return data[0]

    def read_byte_data(self, addr, cmd):
        data = self._read_block(addr, cmd, 1)
        return data[0]

    def read_word_data(self, addr, cmd):
        data = self._read_block(addr, cmd, 2)
        return data[0] | (data[1] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        return self._read_block(addr, cmd, length)

    def _read_block(self, addr, cmd, num_bytes):
        if (
            isinstance(addr, bool)
            or not isinstance(addr, int)
            or not 0x01 <= addr <= 0x7B
        ):
            raise ValueError("Invalid CP2112 slave address")

        if (
            isinstance(cmd, bool)
            or not isinstance(cmd, int)
            or not 0 <= cmd <= 0xFF
        ):
            raise ValueError("Invalid register command")

        if (
            isinstance(num_bytes, bool)
            or not isinstance(num_bytes, int)
            or not 1 <= num_bytes <= 61
        ):
            raise ValueError(
                "CP2112 single-report read length must be 1..61"
            )

        payload = bytes([
            0x11,
            addr << 1,
            (num_bytes >> 8) & 0xFF,
            num_bytes & 0xFF,
            0x01,
            cmd,
        ]) + bytes(15)

        with self._transaction() as device:
            sent = device.write(payload)

            if (
                isinstance(sent, bool)
                or not isinstance(sent, int)
                or sent != len(payload)
            ):
                raise OSError(
                    "CP2112 write-read request failed"
                )

            self._wait_for_transfer(device)
            return self._fetch_response(device, num_bytes)

    @staticmethod
    def _validate_byte(value, name):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 0xFF
        ):
            raise ValueError(f"{name} must be an integer byte")
        return value

    def _write_data(self, addr, data):
        if (
            isinstance(addr, bool)
            or not isinstance(addr, int)
            or not 0x01 <= addr <= 0x7B
        ):
            raise ValueError("Invalid CP2112 slave address")

        if not 1 <= len(data) <= 61:
            raise ValueError(
                "CP2112 write payload must contain 1..61 bytes"
            )

        for value in data:
            self._validate_byte(value, "Data")

        # AN495 report 0x14 has a 61-byte data field.
        # Length identifies the valid bytes; padding is not sent
        # to the target device.
        payload = (
            bytes([0x14, addr << 1, len(data)])
            + bytes(data)
            + bytes(61 - len(data))
        )

        with self._transaction() as device:
            sent = device.write(payload)

            if (
                isinstance(sent, bool)
                or not isinstance(sent, int)
                or sent != len(payload)
            ):
                raise OSError(
                    "CP2112 data write request failed: "
                    f"expected {len(payload)} bytes, got {sent!r}"
                )

            self._wait_for_transfer(device)
            return True

    def write_byte(self, addr, val):
        self._validate_byte(val, "Value")
        return self._write_data(addr, [val])

    def write_byte_data(self, addr, cmd, val):
        self._validate_byte(cmd, "Command")
        self._validate_byte(val, "Value")
        return self._write_data(addr, [cmd, val])

    def write_word_data(self, addr, cmd, val):
        self._validate_byte(cmd, "Command")

        if (
            isinstance(val, bool)
            or not isinstance(val, int)
            or not 0 <= val <= 0xFFFF
        ):
            raise ValueError("Value must be an integer word")

        # PMBus word data is little-endian, unlike the
        # multi-byte control fields of CP2112 HID reports.
        return self._write_data(
            addr,
            [cmd, val & 0xFF, (val >> 8) & 0xFF],
        )

    def write_i2c_block_data(self, addr, cmd, data):
        self._validate_byte(cmd, "Command")

        if not isinstance(data, (list, tuple, bytes, bytearray)):
            raise ValueError(
                "Block data must be a byte sequence"
            )

        if len(data) > 60:
            raise ValueError(
                "CP2112 block data must contain at most 60 bytes"
            )

        return self._write_data(addr, [cmd] + list(data))

    def close(self):
        with self._global_lock:
            entry = self._entry

            with entry["lock"]:
                if self._closed:
                    return

                self._closed = True

                if entry["closed"]:
                    return

                entry["users"] -= 1
                if entry["users"] > 0:
                    return

                entry["closed"] = True
                try:
                    entry["device"].close()
                finally:
                    if self._shared.get(self._idx) is entry:
                        self._shared.pop(self._idx, None)

    @classmethod
    def close_all(cls):
        with cls._global_lock:
            for entry in tuple(cls._shared.values()):
                with entry["lock"]:
                    if entry["closed"]:
                        continue

                    entry["closed"] = True
                    try:
                        entry["device"].close()
                    except Exception as exc:
                        print(
                            f"[CP2112] Error closing device: {exc}"
                        )

            cls._shared.clear()
