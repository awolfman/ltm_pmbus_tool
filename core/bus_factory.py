# core/bus_factory.py
"""Cached factory for USB-to-I2C adapters."""

import logging
import threading

from .drivers import CH341Bus, FTDIBus, HAS_PYFTDI, CP2112Bus


logger = logging.getLogger(__name__)

CH341_OFFSET = 100
FTDI_OFFSET = 200
CP2112_OFFSET = 300

_BUS_CACHE = {}
_CACHE_LOCK = threading.Lock()


def create_bus(bus_num):
    if (
        isinstance(bus_num, bool)
        or not isinstance(bus_num, int)
        or bus_num < 0
    ):
        raise ValueError(f"Invalid bus number: {bus_num!r}")

    with _CACHE_LOCK:
        cached = _BUS_CACHE.get(bus_num)
        if cached is not None and not getattr(
            cached, "_closed", False
        ):
            return cached

        if CH341_OFFSET <= bus_num < FTDI_OFFSET:
            bus = CH341Bus(bus_num - CH341_OFFSET)

        elif FTDI_OFFSET <= bus_num < CP2112_OFFSET:
            if not HAS_PYFTDI:
                raise ImportError(
                    "pyftdi is required for FTDI adapters"
                )
            bus = FTDIBus(bus_num - FTDI_OFFSET)

        elif CP2112_OFFSET <= bus_num < 400:
            bus = CP2112Bus(bus_num - CP2112_OFFSET)

        elif 0 <= bus_num < CH341_OFFSET:
            raise ValueError(
                "System I2C buses are not implemented "
                "in this factory"
            )

        else:
            raise ValueError(f"Unsupported bus number: {bus_num}")

        _BUS_CACHE[bus_num] = bus
        logger.debug("Opened bus %s", bus_num)
        return bus


def create_bus_from_index(bus_num):
    return create_bus(bus_num)


def close_all_buses():
    with _CACHE_LOCK:
        for driver_class in (CH341Bus, FTDIBus, CP2112Bus):
            try:
                driver_class.close_all()
            except Exception:
                logger.warning(
                    "Cannot close %s",
                    driver_class.__name__,
                    exc_info=True,
                )

        for bus in _BUS_CACHE.values():
            if hasattr(bus, "_closed"):
                bus._closed = True

        _BUS_CACHE.clear()
