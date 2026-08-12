"""I2C backend -- smbus2, smbus, or simulation."""
import sys

_backend = None
_backend_name = None


def get_smbus_class():
    global _backend, _backend_name
    if _backend is not None:
        return _backend
    if 'smbus2' in sys.modules:
        _backend = sys.modules['smbus2'].SMBus
        _backend_name = 'smbus2 (sim)'
        return _backend
    try:
        import smbus2
        _backend = smbus2.SMBus
        _backend_name = 'smbus2'
        return _backend
    except ImportError:
        pass
    try:
        import smbus
        _backend_name = 'smbus'
        _backend = _wrap_smbus(smbus.SMBus)
        return _backend
    except ImportError:
        pass
    raise ImportError("No smbus2 or smbus. Install one.")


def backend_name():
    if _backend is None:
        get_smbus_class()
    return _backend_name


class _SMBusWrapper:
    def __init__(self, real_cls, bus_num):
        self._bus = real_cls(bus_num)
    def __enter__(self):
        return self._bus
    def __exit__(self, *a):
        try: self._bus.close()
        except Exception: pass
    def __getattr__(self, name):
        return getattr(self._bus, name)


def _wrap_smbus(real_cls):
    class W(_SMBusWrapper):
        def __init__(self, bus_num):
            super().__init__(real_cls, bus_num)
    return W
