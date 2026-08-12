# core/pmbus_device.py
"""PMBusDevice -- I2C communication with one LTM IC."""

import threading
import time
from core.bus_factory import create_bus
from core.pmbus_constants import (
    Cmd, REGISTER_MAP, READ_ONLY_CMDS, KNOWN_DEVICES,
    build_register_map,
)
from core.pmbus_formats import (l11_to_float, l16_to_float,
                                 float_to_l11, float_to_l16, decode_value)

RETRY_COUNT = 3
PMBUS_PAUSE = 0.01

SKIP_REGISTERS = {
    0xEE,  # MFR_FAULT_LOG (block)
    0x99,  # MFR_ID (block)
    0x9E,  # MFR_SERIAL (block)
    0xC0,  # MFR_EIN (block)
    0xBD, 0xBE, 0xBF,  # EEPROM
    0xE3, 0xFD,  # send byte
}

class PMBusDevice:
    def __init__(self, bus_num, address, special_id=None):
        self.bus_num = bus_num
        self.address = address
        self.name = "Unknown"
        self.revision = "?"
        self.special_id = special_id
        self.num_pages = 1
        self.vout_exp = {}
        self._regmap = REGISTER_MAP
        self._read_only = READ_ONLY_CMDS
        self._global_cmds = set()

        self._lock = threading.RLock()

        self._raw_bus_instance = None
        self._has_native_reset = False

    def _get_bus(self):
        """Возвращает существующий экземпляр шины или создает новый."""
        if self._raw_bus_instance is None:
            try:
                self._raw_bus_instance = create_bus(self.bus_num)
                self._has_native_reset = hasattr(self._raw_bus_instance, 'reset_bus')
            except Exception as e:
                print(f"[PMBusDevice] Ошибка создания шины: {e}")
        return self._raw_bus_instance

    def reset_bus(self):
        bus = self._get_bus()
        if bus and self._has_native_reset:
            with self._lock:
                try:
                    bus.reset_bus()
                    time.sleep(PMBUS_PAUSE)
                except Exception:
                    pass

    def read_byte_data(self, cmd):
        if cmd in SKIP_REGISTERS:
            return None

        bus = self._get_bus()
        if not bus:
            return None

        with self._lock:
            for attempt in range(RETRY_COUNT):
                try:
                    val = bus.read_byte_data(self.address, cmd)
                    time.sleep(PMBUS_PAUSE)

                    if cmd == 0x7E and val in (0x82, 0x02):
                        return 0x00

                    if (val == 0xFF or val is None) and attempt < RETRY_COUNT - 1:
                        if self._has_native_reset:
                            bus.reset_bus()
                        time.sleep(PMBUS_PAUSE)
                        continue

                    return val if val != 0xFF else None
                except Exception:
                    if attempt < RETRY_COUNT - 1:
                        if self._has_native_reset:
                            try: bus.reset_bus()
                            except: pass
                        time.sleep(PMBUS_PAUSE)
                        continue
                    return None
            return None

    def read_word_data(self, cmd):
        if cmd in SKIP_REGISTERS:
            return None

        bus = self._get_bus()
        if not bus:
            return None

        with self._lock:
            for attempt in range(RETRY_COUNT):
                try:
                    val = bus.read_word_data(self.address, cmd)
                    time.sleep(PMBUS_PAUSE)

                    if (val == 0xFFFF or val is None) and attempt < RETRY_COUNT - 1:
                        if self._has_native_reset:
                            bus.reset_bus()
                        time.sleep(PMBUS_PAUSE)
                        continue

                    return val if val != 0xFFFF else None
                except Exception:
                    if attempt < RETRY_COUNT - 1:
                        if self._has_native_reset:
                            try: bus.reset_bus()
                            except: pass
                        time.sleep(PMBUS_PAUSE)
                        continue
                    return None
            return None

    def write_byte_data(self, cmd, val):
        bus = self._get_bus()
        if not bus:
            return False
        with self._lock:
            try:
                bus.write_byte_data(self.address, cmd, val & 0xFF)
                time.sleep(PMBUS_PAUSE)
                return True
            except Exception:
                return False

    def write_word_data(self, cmd, val):
        bus = self._get_bus()
        if not bus:
            return False
        with self._lock:
            try:
                bus.write_word_data(self.address, cmd, val & 0xFFFF)
                time.sleep(PMBUS_PAUSE)
                return True
            except Exception:
                return False

    def send_byte(self, cmd):
        bus = self._get_bus()
        if not bus:
            return False
        with self._lock:
            try:
                bus.write_byte(self.address, cmd)
                time.sleep(PMBUS_PAUSE)
                return True
            except Exception:
                return False

    def read_i2c_block_data(self, cmd, length=32):
        if cmd in SKIP_REGISTERS:
            return None
        bus = self._get_bus()
        if not bus:
            return None
        with self._lock:
            try:
                res = bus.read_i2c_block_data(self.address, cmd, length)
                time.sleep(PMBUS_PAUSE)
                return res
            except Exception:
                return None

    def _rb(self, cmd): return self.read_byte_data(cmd)
    def _rw(self, cmd): return self.read_word_data(cmd)
    def _wb(self, cmd, val): return self.write_byte_data(cmd, val)
    def _ww(self, cmd, val): return self.write_word_data(cmd, val)
    def _send(self, cmd): return self.send_byte(cmd)
    def _rblock(self, cmd, length=32): return self.read_i2c_block_data(cmd, length)


    def set_page(self, page):
        target_page = page & 0xFF
        bus = self._get_bus()
        if not bus:
            return False

        bus.write_byte_data(self.address, 0x00, target_page)
        time.sleep(0.005)
        for _ in range(3):
            try:
                current = bus.read_byte_data(self.address, 0x00)
                if current == target_page:
                    time.sleep(0.002)
                    return True
                bus.write_byte_data(self.address, 0x00, target_page)
                time.sleep(0.005)
            except:
                time.sleep(0.002)
        return False

    def identify(self):
        self.clear_faults()
        time.sleep(0.02)

        if self.special_id is None:
            self.special_id = self._rw(Cmd.MFR_SPECIAL_ID)
            if self.special_id is None:
                return False

        regmap, ro, gl, profile_name, profile_pages = build_register_map(self.special_id)
        self._regmap = regmap
        self._read_only = ro
        self._global_cmds = gl

        if profile_name:
            self.name = profile_name
            self.num_pages = profile_pages
        else:
            if self.special_id == 0x0236 or (self.special_id & 0xFFF0) == 0x0230:
                self.name = "LTM4673"
                self.num_pages = 4
                self.revision = "Rev A"
            else:
                if not self.name or self.name == "Unknown":
                    masked = self.special_id & 0xFFF0
                    for kid, (kn, kp) in KNOWN_DEVICES.items():
                        if masked == (kid & 0xFFF0):
                            self.name = kn
                            self.num_pages = kp
                            break
                rev = self._rw(Cmd.MFR_REVISION)
                if rev is not None and rev != 0xFFFF:
                    self.revision = f"Rev 0x{self.special_id & 0xF:X}"
                else:
                    self.revision = "?"

        for p in range(self.num_pages):
            self.set_page(p)
            vm = self._rb(Cmd.VOUT_MODE)
            if vm is not None and vm != 0xFF:
                e = vm & 0x1F
                if e > 15: e -= 32
                self.vout_exp[p] = e
            else: self.vout_exp[p] = -13

        cap = self._rb(Cmd.CAPABILITY)
        self.capability = cap if cap is not None and cap != 0xFF else 0

        self.set_page(0)
        self.clear_faults()
        return self.name != "Unknown"

    def read_global_config(self):
        cfg = {}
        for cmd_code, (name, size, fmt, is_paged) in self._regmap.items():
            if is_paged or cmd_code in SKIP_REGISTERS or size == 'block':
                continue
            if 0x78 <= cmd_code <= 0x80 or 0x88 <= cmd_code <= 0x97:
                continue
            if cmd_code not in [0x35, 0x36, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x01, 0x02, 0x10]:
                continue
            r = self._rw(cmd_code) if size == 'word' else self._rb(cmd_code)
            if r is None or (size == 'word' and r == 0xFFFF) or (size == 'byte' and r == 0xFF):
                cfg[name] = {'raw': None, 'value': None, 'cmd': cmd_code, 'fmt': fmt}
            else:
                val = l11_to_float(r) if fmt == 'L11' else (l16_to_float(r, self.vout_exp.get(0, -13)) if fmt == 'L16' else r)
                cfg[name] = {'raw': r, 'value': val, 'cmd': cmd_code, 'fmt': fmt}
        return cfg

    def read_channel_config(self, page=0):
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)
            exp = self.vout_exp.get(page, -13)
            cfg = {}
            for cmd_code, (name, size, fmt, is_paged) in self._regmap.items():
                if not is_paged or cmd_code in SKIP_REGISTERS or size == 'block':
                    continue
                if 0x78 <= cmd_code <= 0x80 or 0x88 <= cmd_code <= 0x97:
                    continue

                r = self._rw(cmd_code) if size == 'word' else self._rb(cmd_code)
                if r is None or (size == 'word' and r == 0xFFFF) or (size == 'byte' and r == 0xFF):
                    cfg[name] = {'raw': None, 'value': None, 'cmd': cmd_code, 'fmt': fmt}
                else:
                    val = l11_to_float(r) if fmt == 'L11' else (l16_to_float(r, exp) if fmt == 'L16' else r)
                    cfg[name] = {'raw': r, 'value': val, 'cmd': cmd_code, 'fmt': fmt}
        return cfg

    def write_val(self, page, cmd, value, fmt):
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)
            if fmt == 'BYTE':
                return self._wb(cmd, int(value) & 0xFF)
            if fmt == 'L16':
                exp = self.vout_exp.get(page, -13)
                return self._ww(cmd, float_to_l16(value, exp))
            elif fmt == 'L11':
                return self._ww(cmd, float_to_l11(value))
        return False

    def read_global_telemetry(self):
        telem = {}
        with self._lock:
            self.set_page(0)
            time.sleep(0.01)

            global_cmds = {
                0x88: 'VIN',
                0x89: 'IIN',
                0x8E: 'TEMP_IC',
                0x97: 'PIN'
            }
            for cmd, key in global_cmds.items():
                if cmd in [0x89, 0x97]:
                    time.sleep(0.005)

                r = self._rw(cmd)
                if r is None or r == 0xFFFF:
                    telem[key] = None
                else:
                    telem[key] = l11_to_float(r)
        return telem

    def read_channel_telemetry(self, page=0):
        telem = {}
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)

            exp = self.vout_exp.get(page, -13)
            telemetry_cmds = {
                0x8B: 'VOUT',  # READ_VOUT
                0x8C: 'IOUT',  # READ_IOUT
                0x96: 'POUT',  # READ_POUT
                0x8D: 'TEMP1', # READ_TEMPERATURE_1
            }
            for cmd, name in telemetry_cmds.items():
                if name == 'POUT':
                    time.sleep(0.005)

                r = self._rw(cmd)
                if r is None or r == 0xFFFF:
                    telem[name] = None
                else:
                    if name == 'VOUT':
                        val = l16_to_float(r, exp)
                    else:
                        val = l11_to_float(r)
                    telem[name] = val
        return telem

    def read_telemetry(self, page=0):
        t = {}
        with self._lock:
            t.update(self.read_global_telemetry())
            t.update(self.read_channel_telemetry(page))
        return t

    def read_global_status(self):
        with self._lock:
            self.set_page(0)
            time.sleep(0.01)
            status_input = self._rb(Cmd.STATUS_INPUT)
            status_cml = self._rb(Cmd.STATUS_CML)
        return {
            'STATUS_INPUT': status_input,
            'STATUS_CML':   status_cml,
        }

    def read_channel_status(self, page=0):
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)
            status_word = self._rw(Cmd.STATUS_WORD)
            if status_word == 0xFFFF:
                status_word = None
            status_vout = self._rb(Cmd.STATUS_VOUT)
            status_iout = self._rb(Cmd.STATUS_IOUT)
            status_temp = self._rb(Cmd.STATUS_TEMPERATURE)
            status_mfr = self._rb(Cmd.STATUS_MFR_SPECIFIC)
        return {
            'STATUS_WORD':        status_word,
            'STATUS_VOUT':        status_vout,
            'STATUS_IOUT':        status_iout,
            'STATUS_TEMPERATURE': status_temp,
            'STATUS_MFR':         status_mfr,
        }

    def read_status(self, page=0):
        s = {}
        with self._lock:
            s.update(self.read_global_status())
            s.update(self.read_channel_status(page))
        return s

    def clear_faults(self):
        self._rb(0x7E)
        return self._send(Cmd.CLEAR_FAULTS)

    def store_user_all(self):   return self._send(Cmd.STORE_USER_ALL)
    def restore_user_all(self): return self._send(Cmd.RESTORE_USER_ALL)

    def read_full_dump(self, page=0):
        exp = self.vout_exp.get(page, -13)
        regmap = self._regmap
        ro = self._read_only
        dump = []
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)  # 10 мс паузы перед пакетным чтением дампа памяти
            for cmd_code in sorted(regmap.keys()):
                if cmd_code in SKIP_REGISTERS:
                    continue
                rname, size, fmt, is_paged = regmap[cmd_code]
                if size == 'block':
                    continue

                if cmd_code in [0x89, 0x96, 0x97]:
                    time.sleep(0.005)

                raw = self._rb(cmd_code) if size == 'byte' else self._rw(cmd_code)
                dump.append({
                    'page': page, 'cmd': cmd_code, 'name': rname,
                    'size': size, 'format': fmt, 'is_paged': is_paged,
                    'raw': raw,
                    'decoded': (decode_value(raw, fmt, exp)
                            if raw is not None else None),
                    'readonly': cmd_code in ro,
                })
        return dump

    def write_register(self, page, cmd_code, raw_value, size):
        if cmd_code in self._read_only or cmd_code in SKIP_REGISTERS:
            return False
        with self._lock:
            self.set_page(page)
            time.sleep(0.01)  # 10 мс паузы перед записью регистра
            if size == 'byte':
                return self._wb(cmd_code, int(raw_value) & 0xFF)
            elif size == 'word':
                return self._ww(cmd_code, int(raw_value) & 0xFFFF)
        return False

    def _wb(self, cmd, val):
        bus = self._get_bus()
        if not bus: return False
        try:
            bus.write_byte_data(self.address, cmd, val & 0xFF)
            time.sleep(PMBUS_PAUSE)
            return True
        except: return False

    def _ww(self, cmd, val):
        bus = self._get_bus()
        if not bus: return False
        try:
            bus.write_word_data(self.address, cmd, val & 0xFFFF)
            time.sleep(PMBUS_PAUSE)
            return True
        except: return False
