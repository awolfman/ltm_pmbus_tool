"""Common register transport descriptors for LTM4673/4677/4678.

Tuple fields:
    name, size, format, is_paged

Shared descriptors do not imply identical bit semantics,
allowed values, defaults or side effects.
"""

BASE_REGISTER_MAP = {
    0x00: ('PAGE',                      'byte', 'BYTE', False),
    0x01: ('OPERATION',                 'byte', 'BYTE', True),
    0x02: ('ON_OFF_CONFIG',             'byte', 'BYTE', True),
    0x10: ('WRITE_PROTECT',             'byte', 'BYTE', False),
    0x19: ('CAPABILITY',                'byte', 'BYTE', False),
    0x20: ('VOUT_MODE',                 'byte', 'BYTE', True),
    0x21: ('VOUT_COMMAND',              'word', 'L16',  True),
    0x24: ('VOUT_MAX',                  'word', 'L16',  True),
    0x25: ('VOUT_MARGIN_HIGH',          'word', 'L16',  True),
    0x26: ('VOUT_MARGIN_LOW',           'word', 'L16',  True),
    0x35: ('VIN_ON',                    'word', 'L11',  False),
    0x36: ('VIN_OFF',                   'word', 'L11',  False),
    0x40: ('VOUT_OV_FAULT_LIMIT',        'word', 'L16',  True),
    0x41: ('VOUT_OV_FAULT_RESPONSE',     'byte', 'BYTE', True),
    0x42: ('VOUT_OV_WARN_LIMIT',         'word', 'L16',  True),
    0x43: ('VOUT_UV_WARN_LIMIT',         'word', 'L16',  True),
    0x44: ('VOUT_UV_FAULT_LIMIT',        'word', 'L16',  True),
    0x45: ('VOUT_UV_FAULT_RESPONSE',     'byte', 'BYTE', True),
    0x46: ('IOUT_OC_FAULT_LIMIT',        'word', 'L11',  True),
    0x47: ('IOUT_OC_FAULT_RESPONSE',     'byte', 'BYTE', True),
    0x4A: ('IOUT_OC_WARN_LIMIT',         'word', 'L11',  True),
    0x4F: ('OT_FAULT_LIMIT',             'word', 'L11',  True),
    0x50: ('OT_FAULT_RESPONSE',          'byte', 'BYTE', True),
    0x51: ('OT_WARN_LIMIT',             'word', 'L11',  True),
    0x53: ('UT_FAULT_LIMIT',             'word', 'L11',  True),
    0x54: ('UT_FAULT_RESPONSE',          'byte', 'BYTE', True),
    0x55: ('VIN_OV_FAULT_LIMIT',         'word', 'L11',  False),
    0x58: ('VIN_UV_WARN_LIMIT',          'word', 'L11',  False),
    0x60: ('TON_DELAY',                 'word', 'L11',  True),
    0x61: ('TON_RISE',                  'word', 'L11',  True),
    0x62: ('TON_MAX_FAULT_LIMIT',        'word', 'L11',  True),
    0x63: ('TON_MAX_FAULT_RESPONSE',     'byte', 'BYTE', True),
    0x64: ('TOFF_DELAY',                'word', 'L11',  True),
    0x88: ('READ_VIN',                  'word', 'L11',  False),
    0x8B: ('READ_VOUT',                 'word', 'L16',  True),
    0x8C: ('READ_IOUT',                 'word', 'L11',  True),
    0x8D: ('READ_TEMPERATURE_1',         'word', 'L11',  True),
    0x8E: ('READ_TEMPERATURE_2',         'word', 'L11',  False),
    0x96: ('READ_POUT',                 'word', 'L11',  True),
    0x98: ('PMBUS_REVISION',            'byte', 'BYTE', False),
    0xB0: ('USER_DATA_00',              'word', 'RAW',  False),
    0xB1: ('USER_DATA_01',              'word', 'RAW',  True),
    0xB2: ('USER_DATA_02',              'word', 'RAW',  False),
    0xB3: ('USER_DATA_03',              'word', 'RAW',  True),
    0xB4: ('USER_DATA_04',              'word', 'RAW',  False),
    0xBD: ('MFR_EE_UNLOCK',             'byte', 'BYTE', False),
    0xBE: ('MFR_EE_ERASE',              'byte', 'BYTE', False),
    0xBF: ('MFR_EE_DATA',               'word', 'RAW',  False),
    0xD7: ('MFR_IOUT_PEAK',             'word', 'L11',  True),
    0xDD: ('MFR_VOUT_PEAK',             'word', 'L16',  True),
    0xDE: ('MFR_VIN_PEAK',              'word', 'L11',  False),
    0xDF: ('MFR_TEMPERATURE_1_PEAK',     'word', 'L11',  True),
    0xE7: ('MFR_SPECIAL_ID',            'word', 'RAW',  False),
    0xF9: ('MFR_TEMP_1_OFFSET',         'word', 'L11',  True),
}

BASE_READ_ONLY = {
    0x19, 0x20,
    0x88, 0x8B, 0x8C, 0x8D, 0x8E, 0x96, 0x98,
    0xD7, 0xDD, 0xDE, 0xDF, 0xE7,
}

BASE_GLOBAL_CMDS = {
    cmd
    for cmd, (_, _, _, paged) in BASE_REGISTER_MAP.items()
    if not paged
}

# name, is_paged
BASE_SEND_COMMANDS = {
    0x15: ('STORE_USER_ALL', False),
    0x16: ('RESTORE_USER_ALL', False),
    0xEA: ('MFR_FAULT_LOG_STORE', False),
    0xEC: ('MFR_FAULT_LOG_CLEAR', False),
}

# These are not ordinary configuration fields.
# Access requires dedicated workflows and must not be part of
# automatic configuration polling or generic dump restoration.
BASE_SPECIAL_ACCESS = {
    0xBD, 0xBE, 0xBF,
}

BASE_NO_GENERIC_WRITE = {
    0x00, 0x19, 0x20,
    0xB0, 0xB1, 0xB2,
    0xBD, 0xBE, 0xBF,
}
