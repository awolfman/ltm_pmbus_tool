"""FTDI FT232H / FT2232H / FT4232H USB-to-I2C via MPSSE.

Drop-in replacement for smbus2.SMBus using pyftdi.
pyftdi handles repeated-start internally in read_from / exchange.

Requirements:  pip install pyftdi
"""

import threading

try:
    from pyftdi.i2c import I2cController
    from pyftdi.ftdi import Ftdi
    HAS_PYFTDI = True
except ImportError:
    HAS_PYFTDI = False

I2C_PIDS = {0x6014: '232h', 0x6010: '2232h', 0x6011: '4232h'}


def find_ftdi_devices():
    """Return list of I2C-capable FTDI device descriptors."""
    if not HAS_PYFTDI:
        return []
    try:
        devs = Ftdi.list_devices()
        result = []
        for desc, _n_intf in devs:
            if desc.pid in I2C_PIDS:
                result.append(desc)
        return result
    except Exception as e:
        print(f"[FTDI] scan error: {e}")
        return []


def _url_for(desc):
    """Build pyftdi URL from device descriptor."""
    name = I2C_PIDS.get(desc.pid, f'0x{desc.pid:04x}')
    sn = getattr(desc, 'sn', None)
    if sn:
        return f'ftdi://ftdi:{name}:{sn}/1'
    return f'ftdi://ftdi:{name}/1'


class FtdiBus:
    """SMBus-compatible I2C bus via FTDI MPSSE.

    Shared I2cController per physical device.
    Cached I2cPort per slave address.
    Thread-safe via per-device lock.
    """
    _shared = {}
    _global_lock = threading.Lock()

    def __init__(self, dev_index=0):
        self._idx = dev_index
        with FtdiBus._global_lock:
            if dev_index not in FtdiBus._shared:
                self._open(dev_index)

    def _open(self, dev_index):
        if not HAS_PYFTDI:
            raise ImportError(
                "pyftdi required for FTDI I2C.\n"
                "  pip install pyftdi")

        devs = find_ftdi_devices()
        if dev_index >= len(devs):
            raise OSError(
                f"FTDI #{dev_index} not found "
                f"({len(devs)} available)")

        desc = devs[dev_index]
        url = _url_for(desc)

        ctrl = I2cController()
        ctrl.configure(url)

        FtdiBus._shared[dev_index] = {
            'ctrl':  ctrl,
            'ports': {},
            'lock':  threading.Lock(),
        }

        label = getattr(desc, 'description', '') or \
                getattr(desc, 'sn', '') or f'#{dev_index}'
        print(f"[FTDI] opened {label} ({url})")

    def _port(self, addr):
        entry = FtdiBus._shared[self._idx]
        if addr not in entry['ports']:
            entry['ports'][addr] = entry['ctrl'].get_port(addr)
        return entry['ports'][addr]

    # ============================================ SMBus interface

    def read_byte(self, addr):
        """Read single byte (device detect / SMBus read byte)."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            data = port.read(1)
        return data[0]

    def read_byte_data(self, addr, cmd):
        """Write register, repeated start, read 1 byte."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            data = port.read_from(cmd, 1)
        return data[0]

    def read_word_data(self, addr, cmd):
        """Write register, repeated start, read 2 bytes (LE)."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            data = port.read_from(cmd, 2)
        return data[0] | (data[1] << 8)

    def read_i2c_block_data(self, addr, cmd, length):
        """Block read: write register, repeated start, read N."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            data = port.read_from(cmd, length)
        return list(data)

    def write_byte(self, addr, val):
        """SMBus send byte."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            port.write(bytes([val & 0xFF]))

    def write_byte_data(self, addr, cmd, val):
        """SMBus write byte data."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            port.write_to(cmd, bytes([val & 0xFF]))

    def write_word_data(self, addr, cmd, val):
        """SMBus write word data (LE)."""
        entry = FtdiBus._shared[self._idx]
        with entry['lock']:
            port = self._port(addr)
            port.write_to(cmd, bytes([val & 0xFF,
                                      (val >> 8) & 0xFF]))

    # ============================================ context manager
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @classmethod
    def close_all(cls):
        """Terminate all FTDI controllers."""
        with cls._global_lock:
            for entry in cls._shared.values():
                try:
                    entry['ctrl'].terminate()
                except Exception:
                    pass
            cls._shared.clear()
            print("[FTDI] all devices released")
