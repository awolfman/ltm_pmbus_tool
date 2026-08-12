"""LTM4673 -- Quad 8A uModule, 4 pages.
MFR registers per LTM4673 Rev A datasheet.
"""
from core.devices.registry import register_device

for _id in (0x4480, 0x4730, 0x47A0, 0x0236):
    register_device(_id, "LTM4673", 4, __name__)

REGISTER_OVERRIDES = {
    0xB5: ('MFR_LTC_RESERVED_1',        'word', 'RAW',  True),
    0xB8: ('MFR_T_SELF_HEAT',           'word', 'L11',  True),
    0xB9: ('MFR_IOUT_CAL_GAIN_TAU_INV', 'word', 'L11',  True),
    0xBA: ('MFR_IOUT_CAL_GAIN_THETA',   'word', 'L11',  True),
    0xBB: ('MFR_READ_IOUT',             'word', 'RAW',  True),
    0xBC: ('MFR_LTC_RESERVED_2',        'word', 'RAW',  True),
    0xC0: ('MFR_EIN',                   'word', 'RAW',  True),
    0xC1: ('MFR_EIN_CONFIG',            'byte', 'BYTE', False),
    0xC2: ('MFR_SPECIAL_LOT',           'byte', 'BYTE', True),
    0xC3: ('MFR_IIN_CAL_GAIN_TC',       'word', 'RAW',  False),
    0xC4: ('MFR_IIN_PEAK',              'word', 'L11',  True),
    0xC5: ('MFR_IIN_MIN',               'word', 'L11',  True),
    0xC6: ('MFR_PIN_PEAK',              'word', 'L11',  True),
    0xC7: ('MFR_PIN_MIN',               'word', 'L11',  True),
    0xC8: ('MFR_COMMAND_PLUS',          'word', 'RAW',  False),
    0xC9: ('MFR_DATA_PLUS0',            'word', 'RAW',  False),
    0xCA: ('MFR_DATA_PLUS1',            'word', 'RAW',  False),
    0xD0: ('MFR_CONFIG_LTM4673',        'word', 'RAW',  True),
    0xD1: ('MFR_CONFIG_ALL',            'word', 'RAW',  False),
    0xD2: ('MFR_FAULTB0_PROPAGATE',     'byte', 'BYTE', True),
    0xD3: ('MFR_FAULTB1_PROPAGATE',     'byte', 'BYTE', True),
    0xD4: ('MFR_PWRGD_EN',              'word', 'RAW',  False),
    0xD5: ('MFR_FAULTB0_RESPONSE',      'byte', 'BYTE', False),
    0xD6: ('MFR_FAULTB1_RESPONSE',      'byte', 'BYTE', False),
    0xD8: ('MFR_IOUT_MIN',              'word', 'L11',  True),
    0xD9: ('MFR_CONFIG2_LTM4673',       'byte', 'BYTE', False),
    0xDA: ('MFR_CONFIG3_LTM4673',       'byte', 'BYTE', False),
    0xDB: ('MFR_RETRY_DELAY',           'word', 'L11',  False),
    0xDC: ('MFR_RESTART_DELAY',         'word', 'L11',  False),
    0xE0: ('MFR_DAC',                   'word', 'RAW',  True),
    0xE1: ('MFR_POWERGOOD_DELAY',       'word', 'L11',  False),
    0xE2: ('MFR_WATCHDOG_T_FIRST',      'word', 'L11',  False),
    0xE3: ('MFR_WATCHDOG_T',            'word', 'L11',  False),
    0xE4: ('MFR_PAGE_FF_MASK',          'byte', 'BYTE', False),
    0xE8: ('MFR_IIN_CAL_GAIN',          'word', 'L11',  False),
    0xE9: ('MFR_VOUT_DISCHARGE_THR',    'word', 'L11',  True),
    0xED: ('MFR_FAULT_LOG_STATUS',      'byte', 'BYTE', False),
    0xF6: ('MFR_IOUT_CAL_GAIN_TC',      'word', 'RAW',  True),
    0xF7: ('MFR_RETRY_COUNT',           'byte', 'BYTE', False),
    0xF8: ('MFR_TEMP_1_GAIN',           'word', 'RAW',  True),
    0xF9: ('MFR_TEMP_1_OFFSET',         'word', 'L11',  True),
    0xFA: ('MFR_IOUT_SENSE_VOLTAGE',    'word', 'RAW',  True),
    0xFB: ('MFR_VOUT_MIN',              'word', 'L16',  True),
    0xFC: ('MFR_VIN_MIN',               'word', 'L11',  False),
    0xEA: ('MFR_FAULT_LOG_STORE',      'byte', 'BYTE', False),
    0xEB: ('MFR_FAULT_LOG_RESTORE',     'byte', 'BYTE', False),
    0xEC: ('MFR_FAULT_LOG_CLEAR',       'byte', 'BYTE', False),
    0xEE: ('MFR_FAULT_LOG',             'word', 'RAW',  False),
    0xF0: ('MFR_COMPARE_USER_ALL',      'byte', 'BYTE', False),
    0xFD: ('MFR_TEMP_1_MIN',            'word', 'L11',  True),
}

READ_ONLY_EXTRA = {
    0xB8, 0xBB, 0xC2, 0xC4, 0xC5, 0xC6, 0xC7,
    0xD8, 0xED,
    0xF6, 0xFA, 0xFB, 0xFC, 0xFD,
}

GLOBAL_CMDS_EXTRA = {
    0xC1, 0xC3, 0xC8, 0xC9, 0xCA,
    0xD1, 0xD4, 0xD5, 0xD6, 0xD9, 0xDA,
    0xDB, 0xDC, 0xDE, 0xE1, 0xE2, 0xE3, 0xE4, 0xE8, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xF0, 0xF7, 0xFC,
}
