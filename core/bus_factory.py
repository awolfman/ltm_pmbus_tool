# core/bus_factory.py
"""
Bus factory - supports CH341 and FTDI adapters.
"""

import threading
from .ch341_i2c import CH341Bus
from .ftdi_i2c import FTDIBus, HAS_PYFTDI, find_ftdi_devices

# Offsets for bus numbering (используются в GUI)
# /dev/i2c-N: 0-99
CH341_OFFSET = 100  # CH341: 100-199
FTDI_OFFSET = 200   # FTDI: 200-299

# Глобальный кэш для хранения открытых адаптеров
_BUS_CACHE = {}
_CACHE_LOCK = threading.Lock()

def create_bus(bus_num):
    global _BUS_CACHE

    with _CACHE_LOCK:
        if CH341_OFFSET <= bus_num < FTDI_OFFSET:
            # CH341 bus
            ch341_index = bus_num - CH341_OFFSET
            if bus_num not in _BUS_CACHE:
                _BUS_CACHE[bus_num] = CH341Bus(ch341_index)
            return _BUS_CACHE[bus_num]
        elif bus_num >= FTDI_OFFSET:
            # FTDI bus
            if not HAS_PYFTDI:
                raise ImportError("pyftdi not installed. Please install: pip install pyftdi")

            ftdi_index = bus_num - FTDI_OFFSET
            if bus_num not in _BUS_CACHE:
                _BUS_CACHE[bus_num] = FTDIBus(ftdi_index)
            return _BUS_CACHE[bus_num]
        else:
            # /dev/i2c-N bus (0-99)
            # Здесь можно добавить поддержку системных шин если нужно
            raise ValueError(f"Bus number {bus_num} is reserved for system I2C buses (0-99)")

def create_bus_from_index(bus_num):
    return create_bus(bus_num)

def close_all_buses():
    """Close all open bus connections."""
    global _BUS_CACHE

    with _CACHE_LOCK:
        # Close FTDI buses
        try:
            FTDIBus.close_all()
        except:
            pass

        # Close CH341 buses
        for bus in _BUS_CACHE.values():
            try:
                if hasattr(bus, 'close'):
                    bus.close()
            except:
                pass

        _BUS_CACHE.clear()
        print("[BusFactory] All buses closed")
