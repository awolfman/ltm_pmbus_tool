"""CH341T/CH341A USB-to-I2C adapter driver.

Drop-in replacement for smbus2.SMBus using pyusb.

Protocol reference:
    CH341DS1.PDF section 10.2 -- I2C stream commands.
    Each OUT|N command outputs N bytes, returns 1 status byte.
    Each IN|N command reads N bytes, returns N data bytes.
    Response = [status_per_OUT..., data_per_IN...] in command order.
"""

import threading

try:
    import usb.core
    import usb.util
    HAS_PYUSB = True
except ImportError:
    HAS_PYUSB = False

# ---- USB IDs ----
CH341_VID = 0x1A86
CH341_PID_I2C = 0x5512
CH341_PID_SER = 0x5523  # CH341 in serial mode (also supports I2C)
CH341_PIDS = {CH341_PID_I2C, CH341_PID_SER}

# ---- I2C stream sub-commands ----
CMD_STREAM = 0xAA
CMD_STA    = 0x74
CMD_STO    = 0x75
CMD_OUT    = 0x80   # | byte_count (1..31)
CMD_IN     = 0xC0   # | byte_count (1..31)
CMD_SET    = 0x60   # | speed
CMD_END    = 0x00

# ---- I2C clock ----
SPEED_20K  = 0
SPEED_100K = 1
SPEED_400K = 2
SPEED_750K = 3

# ---- USB endpoints ----
EP_OUT  = 0x02
EP_IN   = 0x82
PKT_LEN = 32
TIMEOUT = 2000


def find_ch341_devices():
    """Return list of CH341 USB devices (I2C or serial mode)."""
    if not HAS_PYUSB:
        return []
    try:
        result = []
        for pid in CH341_PIDS:
            devs = usb.core.find(find_all=True,
                                 idVendor=CH341_VID,
                                 idProduct=pid)
            result.extend(devs)
        return result
    except usb.core.NoBackendError:
        print("[CH341] WARNING: no libusb backend found. "
              "Install libusb-1.0.")
        return []
    except Exception as e:
        print(f"[CH341] USB scan error: {e}")
        return []


class CH341Bus:
    """SMBus-compatible I2C bus via CH341T/CH341A USB adapter.

    Shared USB connection across instances (same dev_index).
    Thread-safe via per-device lock.
    """
    _shared = {}
    _global_lock = threading.Lock()

    def __init__(self, dev_index=0):
        self._idx = dev_index
        with CH341Bus._global_lock:
            if dev_index not in CH341Bus._shared:
                self._open(dev_index)

    def _open(self, dev_index):
        if not HAS_PYUSB:
            raise ImportError(
                "pyusb required for CH341.\n"
                "  pip install pyusb")
        devs = find_ch341_devices()
        if dev_index >= len(devs):
            raise OSError(
                f"CH341 #{dev_index} not found "
                f"({len(devs)} available)")
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

        CH341Bus._shared[dev_index] = {
            'dev':  udev,
            'lock': threading.Lock(),
        }

        # set 100 kHz
        pkt = bytearray(PKT_LEN)
        pkt[0] = CMD_STREAM
        pkt[1] = CMD_SET | SPEED_100K
        pkt[2] = CMD_END
        udev.write(EP_OUT, pkt, timeout=TIMEOUT)

        label = f"bus={udev.bus} port={udev.address}"
        print(f"[CH341] opened #{dev_index} ({label})")

    # ---- low-level transfer ----

    def _xfer(self, cmds, resp_len):
        """Send command stream, return resp_len bytes."""
        entry = CH341Bus._shared[self._idx]
        dev  = entry['dev']
        lock = entry['lock']

        with lock:
            pkt = bytearray(PKT_LEN)
            pkt[0] = CMD_STREAM
            for i, b in enumerate(cmds):
                if i + 1 >= PKT_LEN - 1:
                    break
                pkt[i + 1] = b
            pkt[min(len(cmds) + 1, PKT_LEN - 1)] = CMD_END

            dev.write(EP_OUT, pkt, timeout=TIMEOUT)

            if resp_len <= 0:
                return b''
            raw = dev.read(EP_IN,
                           max(PKT_LEN, resp_len),
                           timeout=TIMEOUT)
            return bytes(raw[:resp_len])

    # ---- SMBus-compatible methods ----

    def read_byte(self, addr):
        """SMBus read byte / device detect."""
        cmds = [CMD_STA,
                CMD_OUT | 1, (addr << 1) | 1,
                CMD_IN  | 1,
                CMD_STO]
        # resp: 1 status (OUT|1) + 1 data (IN|1) = 2
        resp = self._xfer(cmds, 2)
        if resp[0] & 0x80:
            raise OSError(f"[CH341] NACK 0x{addr:02X}")
        return resp[1]

    def read_byte_data(self, addr, cmd):
        """Write register, repeated start, read 1 byte."""
        cmds = [CMD_STA,
                CMD_OUT | 2, (addr << 1), cmd,
                CMD_STA,
                CMD_OUT | 1, (addr << 1) | 1,
                CMD_IN  | 1,
                CMD_STO]
        # 1 status (OUT|2) + 1 status (OUT|1) + 1 data = 3
        resp = self._xfer(cmds, 3)
        if (resp[0] | resp[1]) & 0x80:
            raise OSError(
                f"[CH341] NACK rd 0x{cmd:02X}@0x{addr:02X}")
        return resp[2]

    def read_word_data(self, addr, cmd):
        """Write register, repeated start, read 2 bytes (LE)."""
        cmds = [CMD_STA,
                CMD_OUT | 2, (addr << 1), cmd,
                CMD_STA,
                CMD_OUT | 1, (addr << 1) | 1,
                CMD_IN  | 2,
                CMD_STO]
        resp = self._xfer(cmds, 4)
        if (resp[0] | resp[1]) & 0x80:
            raise OSError(
                f"[CH341] NACK rd 0x{cmd:02X}@0x{addr:02X}")
        return resp[2] | (resp[3] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        """Block read: write register, repeated start, read N."""
        if length > 29:
            return self._block_read_long(addr, cmd, length)
        cmds = [CMD_STA,
                CMD_OUT | 2, (addr << 1), cmd,
                CMD_STA,
                CMD_OUT | 1, (addr << 1) | 1,
                CMD_IN  | length,
                CMD_STO]
        resp = self._xfer(cmds, 2 + length)
        if (resp[0] | resp[1]) & 0x80:
            raise OSError(
                f"[CH341] NACK blk 0x{cmd:02X}@0x{addr:02X}")
        return list(resp[2:2 + length])

    def _block_read_long(self, addr, cmd, length):
        """Block reads > 29 bytes: split into chunks."""
        first = min(length, 29)
        cmds = [CMD_STA,
                CMD_OUT | 2, (addr << 1), cmd,
                CMD_STA,
                CMD_OUT | 1, (addr << 1) | 1,
                CMD_IN  | first]
        if length <= first:
            cmds.append(CMD_STO)
        resp = self._xfer(cmds, 2 + first)
        if (resp[0] | resp[1]) & 0x80:
            raise OSError("NACK")
        result = list(resp[2:2 + first])

        rem = length - first
        while rem > 0:
            chunk = min(rem, 31)
            cmds = [CMD_IN | chunk]
            if rem <= chunk:
                cmds.append(CMD_STO)
            resp = self._xfer(cmds, chunk)
            result.extend(resp[:chunk])
            rem -= chunk
        return result

    def write_byte(self, addr, val):
        """SMBus send byte."""
        cmds = [CMD_STA,
                CMD_OUT | 2, (addr << 1), val & 0xFF,
                CMD_STO]
        resp = self._xfer(cmds, 1)
        if resp[0] & 0x80:
            raise OSError(
                f"[CH341] NACK wr 0x{addr:02X}")

    def write_byte_data(self, addr, cmd, val):
        """SMBus write byte data."""
        cmds = [CMD_STA,
                CMD_OUT | 3, (addr << 1), cmd, val & 0xFF,
                CMD_STO]
        resp = self._xfer(cmds, 1)
        if resp[0] & 0x80:
            raise OSError(
                f"[CH341] NACK wr 0x{cmd:02X}=0x{val:02X}"
                f"@0x{addr:02X}")

    def write_word_data(self, addr, cmd, val):
        """SMBus write word data (LE)."""
        cmds = [CMD_STA,
                CMD_OUT | 4, (addr << 1), cmd,
                val & 0xFF, (val >> 8) & 0xFF,
                CMD_STO]
        resp = self._xfer(cmds, 1)
        if resp[0] & 0x80:
            raise OSError(
                f"[CH341] NACK wr 0x{cmd:02X}=0x{val:04X}"
                f"@0x{addr:02X}")

    # ---- context manager ----
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @classmethod
    def close_all(cls):
        """Release all USB connections."""
        with cls._global_lock:
            for entry in cls._shared.values():
                try:
                    usb.util.dispose_resources(entry['dev'])
                except Exception:
                    pass
            cls._shared.clear()
            print("[CH341] all devices released")
