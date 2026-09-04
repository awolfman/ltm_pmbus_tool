# core/ftdi_i2c.py
"""FTDI FT232H / FT2232H / FT4232H USB-to-I2C via MPSSE.

Drop-in replacement for smbus2.SMBus using pyftdi.
pyftdi handles repeated-start internally in read_from / exchange.

Requirements:  pip install pyftdi
"""

import threading

from .base_driver import I2CDriverBase

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
    else:
        return f'ftdi://ftdi:{name}/1'


class FTDIBus(I2CDriverBase):
    """SMBus-compatible I2C bus via FTDI MPSSE.

    Shared I2cController per physical device.
    Cached I2cPort per slave address.
    Thread-safe via per-device lock.
    """

    _shared = {}
    _global_lock = threading.Lock()

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

        print(f"[FTDI] Opening: {url}")

        ctrl = I2cController()
        ctrl.configure(url)

        # --- ДОБАВЛЯЕМ СТРОКУ ТАЙМАУТА ДЛЯ LIBUSB ---
        # Устанавливаем внутренний USB таймаут в миллисекундах (100 мс)
        if hasattr(ctrl, 'ftdi') and hasattr(ctrl.ftdi, '_usb_read_timeout'):
            ctrl.ftdi._usb_read_timeout = 100
            ctrl.ftdi._usb_write_timeout = 100
        # -----------------------------------

        self._shared[dev_index] = {
            'ctrl':  ctrl,
            'ports': {},
            'lock':  threading.Lock(),
            'desc': desc,
            'url': url,
        }

        label = getattr(desc, 'description', '') or \
                getattr(desc, 'sn', '') or f'#{dev_index}'
        print(f"[FTDI] opened {label} ({url})")

    def _port(self, addr):
        if self._closed:
            raise RuntimeError("Bus is closed")
        entry = self._get_entry()
        if addr not in entry['ports']:
            try:
                entry['ports'][addr] = entry['ctrl'].get_port(addr)
            except Exception as e:
                print(f"[FTDI] Failed to get port for 0x{addr:02X}: {e}")
                raise
        return entry['ports'][addr]

    # SMBus interface

    def read_byte(self, addr):
        """Read single byte (device detect / SMBus read byte)."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                ctrl = entry['ctrl']

                # Шаг 1. Безопасный низкоуровневый пинг шины.
                # Если на адресе никого нет, poll() мгновенно отвалится по NACK, не зависая.
                # Флаг relax=True принудительно заставляет libusb сбросить транзакцию при неудаче.
                if not ctrl.poll(addr, relax=True):
                    return 0xFF

                # Шаг 2. Если адрес физически ответил, создаем полноценный SMBus-порт
                # и читаем стандартный PMBus-регистр 0x00 (PAGE).
                # Это гарантирует, что капризный чип LTM4673 примет команду и вернет ACK.
                port = self._port(addr)

                # Читаем 1 байт из регистра 0x00 (PAGE)
                data = port.read_from(0x00, 1)

                if data and len(data) > 0:
                    return data[0]
                return 0xFF
            except Exception:
                return 0xFF

    def read_byte_data(self, addr, cmd):
        """Write register, repeated start, read 1 byte."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                data = port.read_from(cmd, 1)  # без таймаута
                if data is not None and len(data) > 0:
                    return data[0]
                return 0xFF
            except Exception:
                return 0xFF

    def read_word_data(self, addr, cmd):
        """Write register, repeated start, read 2 bytes (LE)."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                data = port.read_from(cmd, 2)  # без таймаута
                if data is not None and len(data) >= 2:
                    return data[0] | (data[1] << 8)
                return 0xFFFF
            except Exception:
                return 0xFFFF

    def read_i2c_block_data(self, addr, cmd, length):
        """Block read: write register, repeated start, read N."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                data = port.exchange(bytes([cmd]), length)
                return list(data) if data else []
            except Exception:
                return []

    def write_byte(self, addr, val):
        """SMBus send byte."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                port.write(bytes([val & 0xFF]))
            except Exception:
                pass

    def write_byte_data(self, addr, cmd, val):
        """SMBus write byte data."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                port.write_to(cmd, bytes([val & 0xFF]))
            except Exception:
                pass

    def write_word_data(self, addr, cmd, val):
        """SMBus write word data (LE)."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                port.write_to(cmd, bytes([val & 0xFF, (val >> 8) & 0xFF]))
            except Exception:
                pass

    def write_i2c_block_data(self, addr, cmd, data):
        """SMBus write block data."""
        if self._closed:
            raise RuntimeError("Bus is closed")

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                if isinstance(data, list):
                    data = bytes(data)
                port.write_to(cmd, data)
            except Exception:
                pass

    def detect(self, addr):
        """Check if device exists at address."""
        if self._closed:
            return False

        entry = self._get_entry()
        with entry['lock']:
            try:
                port = self._port(addr)
                data = port.read(1)
                return data is not None and len(data) > 0
            except Exception:
                return False

    @classmethod
    def close_all(cls):
        """Terminate all FTDI controllers."""
        with cls._global_lock:
            for entry in cls._shared.values():
                try:
                    entry['ctrl'].terminate()
                except Exception as e:
                    print(f"[FTDI] Error terminating controller: {e}")
            cls._shared.clear()
            print("[FTDI] all devices released")

    @classmethod
    def get_available_devices(cls):
        """Return list of available FTDI devices with info."""
        if not HAS_PYFTDI:
            return []
        devs = find_ftdi_devices()
        result = []
        for i, desc in enumerate(devs):
            info = {
                'index': i,
                'description': getattr(desc, 'description', ''),
                'sn': getattr(desc, 'sn', ''),
                'pid': desc.pid,
                'vid': desc.vid,
            }
            result.append(info)
        return result
