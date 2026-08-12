"""PMBusDevice -- I2C communication with one LTM IC."""

import threading
from core.bus_factory import create_bus
from core.pmbus_constants import (
    Cmd, REGISTER_MAP, READ_ONLY_CMDS, KNOWN_DEVICES,
    build_register_map,
)
from core.pmbus_formats import (l11_to_float, l16_to_float,
                                 float_to_l11, float_to_l16, decode_value)


class PMBusDevice:
    def __init__(self, bus_num, address):
        self.bus_num = bus_num
        self.address = address
        self.name = "Unknown"
        self.revision = "?"
        self.special_id = 0
        self.num_pages = 1
        self.vout_exp = {}
        self._regmap = REGISTER_MAP
        self._read_only = READ_ONLY_CMDS
        self._global_cmds = set()
        self._lock = threading.Lock()

    # ---- low-level helpers (use bus factory) ----
    def _rb(self, cmd):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    return b.read_byte_data(self.address, cmd)
            except Exception:
                return None

    def _rw(self, cmd):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    return b.read_word_data(self.address, cmd)
            except Exception:
                return None

    def _wb(self, cmd, val):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    b.write_byte_data(self.address, cmd, val)
                return True
            except Exception:
                return False

    def _ww(self, cmd, val):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    b.write_word_data(self.address, cmd, val & 0xFFFF)
                return True
            except Exception:
                return False

    def _send(self, cmd):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    b.write_byte(self.address, cmd)
                return True
            except Exception:
                return False

    def _rblock(self, cmd, length=32):
        with self._lock:
            try:
                with create_bus(self.bus_num) as b:
                    return b.read_i2c_block_data(
                        self.address, cmd, length)
            except Exception:
                return None

    def set_page(self, page):
        return self._wb(Cmd.PAGE, page & 0xFF)

    # ---- identification ----
    def identify(self):
        self.special_id = self._rw(Cmd.MFR_SPECIAL_ID)

        # build device-specific register map
        regmap, ro, gl, profile_name, profile_pages = \
            build_register_map(self.special_id)
        self._regmap = regmap
        self._read_only = ro
        self._global_cmds = gl

        md = self._rblock(Cmd.MFR_MODEL, 16)
        if md and len(md) > 1:
            try:
                cnt = md[0]
                self.name = ''.join(
                    chr(c) for c in md[1:cnt+1]
                    if 32 <= c < 127).strip()
            except Exception:
                pass

        if profile_name:
            if not self.name or self.name == "Unknown":
                self.name = profile_name
            self.num_pages = profile_pages
        elif self.special_id:
            masked = self.special_id & 0xFFF0
            for kid, (kn, kp) in KNOWN_DEVICES.items():
                if masked == (kid & 0xFFF0):
                    if not self.name or self.name == "Unknown":
                        self.name = kn
                    self.num_pages = kp
                    break

        rd = self._rblock(Cmd.MFR_REVISION, 8)
        if rd and len(rd) > 1:
            try:
                cnt = rd[0]
                self.revision = ''.join(
                    chr(c) for c in rd[1:cnt+1]
                    if 32 <= c < 127).strip()
            except Exception:
                pass
        if not self.revision or self.revision == "?":
            self.revision = (
                f"Rev 0x{self.special_id & 0xF:X}"
                if self.special_id else "?")

        for p in range(self.num_pages):
            self.set_page(p)
            vm = self._rb(Cmd.VOUT_MODE)
            if vm is not None:
                e = vm & 0x1F
                if e > 15:
                    e -= 32
                self.vout_exp[p] = e
            else:
                self.vout_exp[p] = -13
        # Read CAPABILITY
        cap = self._rb(Cmd.CAPABILITY)
        self.capability = cap if cap is not None else 0

        self.set_page(0)
        return self.name != "Unknown"

    # ---- config read ----
    def read_global_config(self):
        cfg = {}
        for name, cmd in [
            ('VIN_ON', Cmd.VIN_ON),
            ('VIN_OFF', Cmd.VIN_OFF),
            ('VIN_OV_FAULT_LIMIT', Cmd.VIN_OV_FAULT_LIMIT),
            ('VIN_OV_WARN_LIMIT', Cmd.VIN_OV_WARN_LIMIT),
            ('VIN_UV_WARN_LIMIT', Cmd.VIN_UV_WARN_LIMIT),
            ('VIN_UV_FAULT_LIMIT', Cmd.VIN_UV_FAULT_LIMIT),
        ]:
            r = self._rw(cmd)
            cfg[name] = {
                'raw': r, 'value': l11_to_float(r),
                'fmt': 'L11', 'cmd': cmd}
        for name, cmd in [
            ('OPERATION', Cmd.OPERATION),
            ('ON_OFF_CONFIG', Cmd.ON_OFF_CONFIG),
        ]:
            r = self._rb(cmd)
            cfg[name] = {'raw': r, 'value': r, 'fmt': 'BYTE', 'cmd': cmd}
        return cfg

    def read_channel_config(self, page=0):
        self.set_page(page)
        exp = self.vout_exp.get(page, -13)
        cfg = {}
        for name, cmd in [
            ('VOUT_COMMAND', Cmd.VOUT_COMMAND),
            ('VOUT_MAX', Cmd.VOUT_MAX),
            ('VOUT_MARGIN_HIGH', Cmd.VOUT_MARGIN_HIGH),
            ('VOUT_MARGIN_LOW', Cmd.VOUT_MARGIN_LOW),
            ('VOUT_OV_FAULT_LIMIT', Cmd.VOUT_OV_FAULT_LIMIT),
            ('VOUT_OV_WARN_LIMIT', Cmd.VOUT_OV_WARN_LIMIT),
            ('VOUT_UV_WARN_LIMIT', Cmd.VOUT_UV_WARN_LIMIT),
            ('VOUT_UV_FAULT_LIMIT', Cmd.VOUT_UV_FAULT_LIMIT),
        ]:
            r = self._rw(cmd)
            cfg[name] = {
                'raw': r, 'value': l16_to_float(r, exp),
                'fmt': 'L16', 'cmd': cmd}
        for name, cmd in [
            ('IOUT_OC_FAULT_LIMIT', Cmd.IOUT_OC_FAULT_LIMIT),
            ('IOUT_OC_WARN_LIMIT', Cmd.IOUT_OC_WARN_LIMIT),
            ('IOUT_UC_FAULT_LIMIT', Cmd.IOUT_UC_FAULT_LIMIT),
            ('OT_FAULT_LIMIT', Cmd.OT_FAULT_LIMIT),
            ('OT_WARN_LIMIT', Cmd.OT_WARN_LIMIT),
            ('UT_WARN_LIMIT', Cmd.UT_WARN_LIMIT),
            ('UT_FAULT_LIMIT', Cmd.UT_FAULT_LIMIT),
            ('FREQUENCY_SWITCH', Cmd.FREQUENCY_SWITCH),
            ('TON_DELAY', Cmd.TON_DELAY),
            ('TON_RISE', Cmd.TON_RISE),
            ('TOFF_DELAY', Cmd.TOFF_DELAY),
        ]:
            r = self._rw(cmd)
            cfg[name] = {
                'raw': r, 'value': l11_to_float(r),
                'fmt': 'L11', 'cmd': cmd}
        return cfg

    def read_config(self, page=0):
        cfg = {}
        cfg.update(self.read_global_config())
        cfg.update(self.read_channel_config(page))
        return cfg

    # ---- config write ----
    def write_val(self, page, cmd, value, fmt):
        self.set_page(page)
        exp = self.vout_exp.get(page, -13)
        if fmt == 'BYTE':
            return self._wb(cmd, int(value) & 0xFF)
        if fmt == 'L16':
            return self._ww(cmd, float_to_l16(value, exp))
        elif fmt == 'L11':
            return self._ww(cmd, float_to_l11(value))
        return False

    # ---- telemetry ----
    def read_global_telemetry(self):
        return {
            'VIN':     l11_to_float(self._rw(Cmd.READ_VIN)),
            'TEMP_IC': l11_to_float(self._rw(Cmd.READ_TEMPERATURE_2)),
        }

    def read_channel_telemetry(self, page=0):
        self.set_page(page)
        exp = self.vout_exp.get(page, -13)
        return {
            'VOUT':  l16_to_float(self._rw(Cmd.READ_VOUT), exp),
            'IOUT':  l11_to_float(self._rw(Cmd.READ_IOUT)),
            'POUT':  l11_to_float(self._rw(Cmd.READ_POUT)),
            'IIN':   l11_to_float(self._rw(Cmd.READ_IIN)),
            'PIN':   l11_to_float(self._rw(Cmd.READ_PIN)),
            'TEMP1': l11_to_float(self._rw(Cmd.READ_TEMPERATURE_1)),
            'DUTY':  l11_to_float(self._rw(Cmd.READ_DUTY_CYCLE)),
            'FREQ':  l11_to_float(self._rw(Cmd.READ_FREQUENCY)),
        }

    def read_telemetry(self, page=0):
        t = {}
        t.update(self.read_global_telemetry())
        t.update(self.read_channel_telemetry(page))
        return t

    # ---- status ----
    def read_global_status(self):
        return {
            'STATUS_INPUT': self._rb(Cmd.STATUS_INPUT),
            'STATUS_CML':   self._rb(Cmd.STATUS_CML),
        }

    def read_channel_status(self, page=0):
        self.set_page(page)
        return {
            'STATUS_WORD':        self._rw(Cmd.STATUS_WORD),
            'STATUS_VOUT':        self._rb(Cmd.STATUS_VOUT),
            'STATUS_IOUT':        self._rb(Cmd.STATUS_IOUT),
            'STATUS_TEMPERATURE': self._rb(Cmd.STATUS_TEMPERATURE),
            'STATUS_MFR':         self._rb(Cmd.STATUS_MFR_SPECIFIC),
        }

    def read_status(self, page=0):
        s = {}
        s.update(self.read_global_status())
        s.update(self.read_channel_status(page))
        return s

    # ---- commands ----
    def clear_faults(self):     return self._send(Cmd.CLEAR_FAULTS)
    def store_user_all(self):   return self._send(Cmd.STORE_USER_ALL)
    def restore_user_all(self): return self._send(Cmd.RESTORE_USER_ALL)

    # ---- dump ----
    def read_full_dump(self, page=0):
        self.set_page(page)
        exp = self.vout_exp.get(page, -13)
        regmap = self._regmap
        ro = self._read_only
        dump = []
        for cmd_code in sorted(regmap.keys()):
            rname, size, fmt, is_paged = regmap[cmd_code]
            if size == 'block':
                continue
            raw = (self._rb(cmd_code) if size == 'byte'
                   else self._rw(cmd_code))
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
        if cmd_code in self._read_only:
            return False
        self.set_page(page)
        if size == 'byte':
            return self._wb(cmd_code, int(raw_value) & 0xFF)
        elif size == 'word':
            return self._ww(cmd_code, int(raw_value) & 0xFFFF)
        return False
