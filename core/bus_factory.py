# core/bus_factory.py
"""
Bus factory - supports CH341 and FTDI adapters.
"""

import threading
from .ch341_i2c import CH341Bus
#from .ftdi_i2c import FTDIBus   # пока закомментировано

# Offsets for bus numbering (используются в GUI)
CH341_OFFSET = 0
FTDI_OFFSET = 100

# Глобальный кэш для хранения открытых адаптеров
_BUS_CACHE = {}
_CACHE_LOCK = threading.Lock()

def create_bus(bus_num):
    global _BUS_CACHE

    with _CACHE_LOCK:
        if bus_num < FTDI_OFFSET:
            if bus_num not in _BUS_CACHE:
                # Физически инициализируем USB-адаптер только ОДИН раз
                _BUS_CACHE[bus_num] = CH341Bus(bus_num)
            return _BUS_CACHE[bus_num]
        else:
            # TODO: реализовать FTDI
            raise NotImplementedError("FTDI support not yet implemented")

def create_bus_from_index(bus_num):
    return create_bus(bus_num)
