# drivers/base_driver.py
"""Общий интерфейс и общая инфраструктура для USB-to-I2C драйверов.

Каждый подкласс обязан объявить собственные _shared и _global_lock
на уровне класса. Если этого не сделать, разные бэкенды будут
делить один и тот же словарь и мешать друг другу по dev_index.
"""

from abc import ABC, abstractmethod
import threading


class I2CDriverBase(ABC):
    """SMBus-совместимый интерфейс, общий объект на (бэкенд, dev_index)."""

    _shared = {}
    _global_lock = threading.Lock()

    def __init__(self, dev_index=0):
        self._idx = dev_index
        self._closed = False
        with self._global_lock:
            if dev_index not in self._shared:
                self._open(dev_index)

    @abstractmethod
    def _open(self, dev_index):
        """Открыть/сконфигурировать устройство, заполнить self._shared[dev_index]."""

    def _get_entry(self):
        entry = self._shared.get(self._idx)
        if entry is None:
            raise RuntimeError(f"{type(self).__name__} #{self._idx} not initialized")
        return entry

    # ---- обязательный SMBus-совместимый контракт ----

    @abstractmethod
    def read_byte(self, addr):
        ...

    @abstractmethod
    def read_byte_data(self, addr, cmd):
        ...

    @abstractmethod
    def read_word_data(self, addr, cmd):
        ...

    @abstractmethod
    def read_i2c_block_data(self, addr, cmd, length):
        ...

    @abstractmethod
    def write_byte(self, addr, val):
        ...

    @abstractmethod
    def write_byte_data(self, addr, cmd, val):
        ...

    @abstractmethod
    def write_word_data(self, addr, cmd, val):
        ...

    def detect(self, addr):
        """Реализация по умолчанию. Переопределяйте, если есть более дешёвый способ пинга."""
        try:
            self.read_byte(addr)
            return True
        except Exception:
            return False

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @classmethod
    def close_all(cls):
        with cls._global_lock:
            cls._shared.clear()
