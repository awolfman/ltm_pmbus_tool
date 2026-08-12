"""LTM4677 -- Dual 18A/Single 36A uModule, 2 pages.
MFR registers per LTM4677 Rev B datasheet, Appendix C Table 1.
"""
from core.devices.registry import register_device

for _id in (0x4770, 0x47C0):
    register_device(_id, "LTM4677", 2, __name__)

REGISTER_OVERRIDES = {
    0xD0: ('MFR_CHAN_CONFIG',            'byte', 'BYTE', True),
    0xD1: ('MFR_CONFIG_ALL',            'byte', 'BYTE', False),
    0xD2: ('MFR_GPIO_PROPAGATE',        'word', 'RAW',  True),
    0xD4: ('MFR_PWM_MODE',              'byte', 'BYTE', True),
    0xD5: ('MFR_GPIO_RESPONSE',         'byte', 'BYTE', True),
    0xD6: ('MFR_OT_FAULT_RESPONSE',     'byte', 'BYTE', False),
    0xD8: ('MFR_ADC_CONTROL',           'byte', 'BYTE', False),
    0xDA: ('MFR_ADC_TELEMETRY_STATUS',  'byte', 'BYTE', False),
    0xDB: ('MFR_RETRY_DELAY',           'word', 'L11',  True),
    0xDC: ('MFR_RESTART_DELAY',         'word', 'L11',  True),
    0xE3: ('MFR_CLEAR_PEAKS',           'byte', 'BYTE', False),
    0xE9: ('MFR_IIN_OFFSET',            'word', 'L11',  True),
    0xEA: ('MFR_FAULT_LOG_STORE',       'byte', 'BYTE', False),
    0xEC: ('MFR_FAULT_LOG_CLEAR',       'byte', 'BYTE', False),
    0xED: ('MFR_READ_IIN',              'word', 'L11',  True),
    0xF0: ('MFR_COMPARE_USER_ALL',      'byte', 'BYTE', False),
    0xF4: ('MFR_TEMPERATURE_2_PEAK',    'word', 'L11',  False),
    0xF5: ('MFR_PWM_CONFIG',            'byte', 'BYTE', False),
    0xF6: ('MFR_IOUT_CAL_GAIN_TC',      'word', 'RAW',  True),
    0xF8: ('MFR_TEMP_1_GAIN',           'word', 'RAW',  True),
    0xF9: ('MFR_TEMP_1_OFFSET',         'word', 'L11',  True),
    0xFA: ('MFR_RAIL_ADDRESS',          'byte', 'BYTE', True),
    0xFD: ('MFR_RESET',                 'byte', 'BYTE', False),
}

READ_ONLY_EXTRA = {
    0xD6, 0xD7, 0xED, 0xF4, 0xF6,
}

GLOBAL_CMDS_EXTRA = {
    0xD1, 0xD6, 0xD8, 0xDA, 0xDE,
    0xE3, 0xEA, 0xEC, 0xF0, 0xF4, 0xF5,
}
