"""LTM4678 -- Dual 25A/Single 50A uModule, 2 pages.
MFR registers per LTM4678 Rev B datasheet, Table 7.
"""
from core.devices.registry import register_device

for _id in (0x4100, 0x4110, 0x4690):
    register_device(_id, "LTM4678", 2, __name__)

REGISTER_OVERRIDES = {
    0xD0: ('MFR_CHAN_CONFIG',            'byte', 'BYTE', True),
    0xD1: ('MFR_CONFIG_ALL',            'byte', 'BYTE', False),
    0xD2: ('MFR_FAULT_PROPAGATE',       'word', 'RAW',  True),
    0xD3: ('MFR_PWM_COMP',              'byte', 'BYTE', True),
    0xD4: ('MFR_PWM_MODE',              'byte', 'BYTE', True),
    0xD5: ('MFR_FAULT_RESPONSE',        'byte', 'BYTE', True),
    0xD6: ('MFR_OT_FAULT_RESPONSE',     'byte', 'BYTE', False),
    0xD8: ('MFR_ADC_CONTROL',           'byte', 'BYTE', False),
    0xDB: ('MFR_RETRY_DELAY',           'word', 'L11',  True),
    0xDC: ('MFR_RESTART_DELAY',         'word', 'L11',  True),
    0xE1: ('MFR_READ_IIN_PEAK',         'word', 'L11',  False),
    0xE3: ('MFR_CLEAR_PEAKS',           'byte', 'BYTE', False),
    0xE4: ('MFR_READ_ICHIP',            'word', 'L11',  False),
    0xE8: ('MFR_IIN_CAL_GAIN',          'word', 'L11',  False),
    0xEA: ('MFR_FAULT_LOG_STORE',       'byte', 'BYTE', False),
    0xEC: ('MFR_FAULT_LOG_CLEAR',       'byte', 'BYTE', False),
    0xED: ('MFR_FAULT_LOG_STATUS',      'byte', 'BYTE', False),
    0xF0: ('MFR_COMPARE_USER_ALL',      'byte', 'BYTE', False),
    0xF4: ('MFR_TEMPERATURE_2_PEAK',    'word', 'L11',  False),
    0xF5: ('MFR_PWM_CONFIG',            'byte', 'BYTE', False),
    0xF6: ('MFR_IOUT_CAL_GAIN_TC',      'word', 'RAW',  True),
    0xF7: ('MFR_RVIN',                  'word', 'L11',  False),
    0xF8: ('MFR_TEMP_1_GAIN',           'word', 'RAW',  True),
    0xF9: ('MFR_TEMP_1_OFFSET',         'word', 'L11',  True),
    0xFA: ('MFR_RAIL_ADDRESS',          'byte', 'BYTE', True),
    0xFD: ('MFR_RESET',                 'byte', 'BYTE', False),
}

READ_ONLY_EXTRA = {
    0xD6, 0xD7,
    0xE1, 0xE4, 0xED, 0xF4, 0xF6,
}

GLOBAL_CMDS_EXTRA = {
    0xD1, 0xD6, 0xD8, 0xDE,
    0xE1, 0xE3, 0xE4, 0xE8,
    0xEA, 0xEC, 0xF0, 0xF4, 0xF5, 0xF7,
}
