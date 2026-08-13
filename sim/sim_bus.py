"""Simulated I2C bus for testing without hardware.

Page-aware: paged registers stored as (page, cmd) keys,
global registers as plain cmd keys.
"""
import random
from core.pmbus_constants import Cmd, REGISTER_MAP, build_register_map
from core.pmbus_formats import (float_to_l11, float_to_l16,
                                 l11_to_float, l16_to_float)


class SimBus:
    """Drop-in replacement for smbus2.SMBus."""
    _shared_wr = {}

    def __init__(self, bus_num=1):
        self._bus_num = bus_num
        # Добавляем адрес 0x5C для LTM4673 (инженерный ID 0x0236)
        self._devs = {
            0x40: 0x4770,  # LTM4677
            0x42: 0x4480,  # LTM4673
            0x5C: 0x0236,  # LTM4673 engineering
        }
        self._dev_regmaps = {}
        for addr, sid in self._devs.items():
            rm, _, _, _, _ = build_register_map(sid)
            self._dev_regmaps[addr] = rm
        if bus_num not in SimBus._shared_wr:
            SimBus._shared_wr[bus_num] = {
                a: {'_page': 0} for a in self._devs
            }
        self._wr = SimBus._shared_wr[bus_num]

    def reset_bus(self):
        """Сброс шины (эмуляция)."""
        pass

    def _get_page(self, addr):
        return self._wr.get(addr, {}).get('_page', 0)

    def _regmap(self, addr):
        return self._dev_regmaps.get(addr, REGISTER_MAP)

    def _key(self, addr, cmd):
        rm = self._regmap(addr)
        is_paged = rm.get(cmd, ('', '', '', True))[3]
        if is_paged:
            return (self._get_page(addr), cmd)
        return cmd

    def _reg_info(self, addr, cmd):
        rm = self._regmap(addr)
        return rm.get(cmd, ('', 'word', '', True))

    def _cfg(self, addr, cmd, default, fmt='L16'):
        d = self._wr.get(addr, {})
        key = self._key(addr, cmd)
        if key not in d:
            return default
        raw = d[key]
        if fmt == 'L16':
            return l16_to_float(raw, -13)
        if fmt == 'L11':
            return l11_to_float(raw)
        return raw

    def read_byte(self, addr):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        return 0

    def read_byte_data(self, addr, cmd):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        d = self._wr.get(addr, {})
        key = self._key(addr, cmd)
        ri = self._reg_info(addr, cmd)
        if key in d and ri[1] == 'byte':
            return d[key] & 0xFF
        if cmd == Cmd.VOUT_MODE:
            return 0x13
        defs = {
            0x01: 0x80, 0x02: 0x1E, 0x10: 0x00, 0x19: 0xB0, 0x41: 0x80,
            0x45: 0x7F, 0x47: 0x00, 0x4C: 0x00, 0x50: 0xB8,
            0x54: 0xB8, 0x56: 0x80, 0x5A: 0x00, 0x63: 0xB8,
            0xD0: 0x1F, 0xD1: 0x09, 0xD5: 0xC0, 0xD6: 0xC0,
            0xD9: 0x00, 0xDA: 0x00,
            0xE4: 0x0F, 0xE6: 0x5C, 0xEF: 0xFC, 0xF7: 0x07,
        }
        if cmd in (Cmd.STATUS_VOUT, Cmd.STATUS_IOUT, Cmd.STATUS_INPUT,
                   Cmd.STATUS_TEMPERATURE, Cmd.STATUS_CML,
                   Cmd.STATUS_MFR_SPECIFIC, Cmd.PAGE):
            return 0
        return defs.get(cmd, 0)

    def read_word_data(self, addr, cmd):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        d = self._wr.get(addr, {})
        key = self._key(addr, cmd)
        ri = self._reg_info(addr, cmd)
        if key in d and ri[1] == 'word':
            return d[key] & 0xFFFF
        sid = self._devs[addr]
        if cmd == Cmd.MFR_SPECIAL_ID:
            return sid
        if cmd == Cmd.STATUS_WORD:
            return 0
        if cmd == Cmd.READ_VOUT:
            _v = self._cfg(addr, Cmd.VOUT_COMMAND, 1.0, 'L16')
            return float_to_l16(_v + random.gauss(0, 0.003), -13)
        if cmd == Cmd.READ_FREQUENCY:
            _f = self._cfg(addr, Cmd.FREQUENCY_SWITCH, 600, 'L11')
            return float_to_l11(_f)
        if cmd == Cmd.READ_DUTY_CYCLE:
            _v = self._cfg(addr, Cmd.VOUT_COMMAND, 1.0, 'L16')
            return float_to_l11((_v / 12.0) * 100 + random.gauss(0, 0.1))
        if cmd == Cmd.READ_POUT:
            _v = self._cfg(addr, Cmd.VOUT_COMMAND, 1.0, 'L16')
            return float_to_l11(_v * 3.0 + random.gauss(0, 0.05))
        if cmd == Cmd.READ_PIN:
            _v = self._cfg(addr, Cmd.VOUT_COMMAND, 1.0, 'L16')
            return float_to_l11(_v * 3.0 / 0.85 + random.gauss(0, 0.05))
        r = random.gauss
        m = {
            Cmd.VOUT_COMMAND:        (1.0, 'L16'),
            Cmd.VOUT_MAX:            (4.0, 'L16'),
            Cmd.VOUT_MARGIN_HIGH:    (1.05, 'L16'),
            Cmd.VOUT_MARGIN_LOW:     (0.95, 'L16'),
            Cmd.VOUT_OV_FAULT_LIMIT: (1.1, 'L16'),
            Cmd.VOUT_OV_WARN_LIMIT:  (1.07, 'L16'),
            Cmd.VOUT_UV_WARN_LIMIT:  (0.93, 'L16'),
            Cmd.VOUT_UV_FAULT_LIMIT: (0.9, 'L16'),
            Cmd.POWER_GOOD_ON:       (0.96, 'L16'),
            Cmd.POWER_GOOD_OFF:      (0.94, 'L16'),
            Cmd.READ_VIN:            (12 + r(0, .05), 'L11'),
            Cmd.READ_IIN:            (0.5 + r(0, .01), 'L11'),
            Cmd.READ_IOUT:           (3 + r(0, .05), 'L11'),
            Cmd.READ_TEMPERATURE_1:  (45 + r(0, .5), 'L11'),
            Cmd.READ_TEMPERATURE_2:  (42 + r(0, .3), 'L11'),
            Cmd.READ_FREQUENCY:      (600, 'L11'),
            Cmd.READ_DUTY_CYCLE:     (8.3 + r(0, .1), 'L11'),
            Cmd.READ_POUT:           (3.0, 'L11'),
            Cmd.READ_PIN:            (6.0, 'L11'),
            Cmd.VIN_ON:              (4.5, 'L11'),
            Cmd.VIN_OFF:             (4.4, 'L11'),
            Cmd.IOUT_OC_FAULT_LIMIT: (17, 'L11'),
            Cmd.IOUT_OC_WARN_LIMIT:  (13, 'L11'),
            Cmd.IOUT_UC_FAULT_LIMIT: (-3, 'L11'),
            Cmd.OT_FAULT_LIMIT:      (128, 'L11'),
            Cmd.OT_WARN_LIMIT:       (125, 'L11'),
            Cmd.UT_WARN_LIMIT:       (-20, 'L11'),
            Cmd.UT_FAULT_LIMIT:      (-45, 'L11'),
            Cmd.FREQUENCY_SWITCH:    (600, 'L11'),
            Cmd.TON_DELAY:           (1, 'L11'),
            Cmd.TON_RISE:            (10, 'L11'),
            Cmd.TOFF_DELAY:          (1, 'L11'),
            Cmd.TON_MAX_FAULT_LIMIT: (15, 'L11'),
            Cmd.VIN_OV_FAULT_LIMIT:  (15, 'L11'),
            Cmd.VIN_OV_WARN_LIMIT:   (14, 'L11'),
            Cmd.VIN_UV_WARN_LIMIT:   (0, 'L11'),
            Cmd.VIN_UV_FAULT_LIMIT:  (0, 'L11'),
        }
        raw_defs = {
            0xD0: 0x0088, 0xD1: 0x0F7B, 0xD4: 0x0000,
            0xE0: 0x01FF, 0xE5: 0xFFFF,
            0xE8: float_to_l11(5.0),
        }
        if cmd in m:
            v, f = m[cmd]
            return float_to_l16(v, -13) if f == 'L16' else float_to_l11(v)
        return raw_defs.get(cmd, 0)

    def read_i2c_block_data(self, addr, cmd, length):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        if cmd == Cmd.MFR_MODEL:
            n = b"LTM4677" if self._devs[addr] == 0x4770 else b"LTM4673"
            return [len(n)] + list(n) + [0] * (length - len(n) - 1)
        if cmd == Cmd.MFR_REVISION:
            return [1, ord('A')] + [0] * (length - 2)
        return [0] * length

    def write_byte_data(self, addr, cmd, val):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        d = self._wr.setdefault(addr, {'_page': 0})
        if cmd == 0x00:
            d['_page'] = val & 0xFF
        else:
            key = self._key(addr, cmd)
            d[key] = val

    def write_word_data(self, addr, cmd, val):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")
        d = self._wr.setdefault(addr, {'_page': 0})
        key = self._key(addr, cmd)
        d[key] = val

    def write_byte(self, addr, cmd):
        if addr not in self._devs:
            raise OSError(f"[sim] no device at 0x{addr:02X}")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass
