"""LTM4677 Rev. B: dual 18A, two pages."""

from core.devices.registry import register_device

register_device(0x47B0, "LTM4677", 2, __name__)

VOUT_EXPONENT = -12

REGISTER_OVERRIDES = {
    0x27: ('VOUT_TRANSITION_RATE',      'word', 'L11',  True),
    0x33: ('FREQUENCY_SWITCH',          'word', 'L11',  False),
    0x38: ('IOUT_CAL_GAIN',             'word', 'L11',  True),
    0x56: ('VIN_OV_FAULT_RESPONSE',     'byte', 'BYTE', True),
    0x5D: ('IIN_OC_WARN_LIMIT',         'word', 'L11',  False),
    0x65: ('TOFF_FALL',                 'word', 'L11',  True),
    0x66: ('TOFF_MAX_WARN_LIMIT',       'word', 'L11',  True),

    0x78: ('STATUS_BYTE',               'byte', 'BYTE', True),
    0x79: ('STATUS_WORD',               'word', 'RAW',  True),
    0x7A: ('STATUS_VOUT',               'byte', 'BYTE', True),
    0x7B: ('STATUS_IOUT',               'byte', 'BYTE', True),
    0x7C: ('STATUS_INPUT',              'byte', 'BYTE', False),
    0x7D: ('STATUS_TEMPERATURE',        'byte', 'BYTE', True),
    0x7E: ('STATUS_CML',                'byte', 'BYTE', False),
    0x80: ('STATUS_MFR_SPECIFIC',       'byte', 'BYTE', True),

    0x89: ('READ_IIN',                  'word', 'L11',  False),
    0x94: ('READ_DUTY_CYCLE',           'word', 'L11',  True),
    0x99: ('MFR_ID',                    'block', 'ASC', False),
    0x9A: ('MFR_MODEL',                 'block', 'ASC', False),
    0x9E: ('MFR_SERIAL',                'block', 'RAW', False),
    0xA5: ('MFR_VOUT_MAX',              'word', 'L16',  True),
    0xB6: ('MFR_INFO',                  'word', 'RAW',  False),

    0xD0: ('MFR_CHAN_CONFIG',           'byte', 'BYTE', True),
    0xD1: ('MFR_CONFIG_ALL',            'byte', 'BYTE', False),
    0xD2: ('MFR_GPIO_PROPAGATE',        'word', 'RAW',  True),
    0xD4: ('MFR_PWM_MODE',              'byte', 'BYTE', True),
    0xD5: ('MFR_GPIO_RESPONSE',         'byte', 'BYTE', True),
    0xD6: ('MFR_OT_FAULT_RESPONSE',     'byte', 'BYTE', False),
    0xD8: ('MFR_ADC_CONTROL',           'byte', 'BYTE', False),
    0xDA: ('MFR_ADC_TELEMETRY_STATUS',  'byte', 'BYTE', False),
    0xDB: ('MFR_RETRY_DELAY',           'word', 'L11',  True),
    0xDC: ('MFR_RESTART_DELAY',         'word', 'L11',  True),

    0xE5: ('MFR_PADS',                  'word', 'RAW',  False),
    0xE6: ('MFR_ADDRESS',               'byte', 'BYTE', False),
    0xE9: ('MFR_IIN_OFFSET',            'word', 'L11',  True),
    0xED: ('MFR_READ_IIN',              'word', 'L11',  True),
    0xEE: ('MFR_FAULT_LOG',             'block', 'RAW', False),
    0xEF: ('MFR_COMMON',                'byte', 'BYTE', False),

    0xF4: ('MFR_TEMPERATURE_2_PEAK',    'word', 'L11',  False),
    0xF5: ('MFR_PWM_CONFIG',            'byte', 'BYTE', False),
    0xF6: ('MFR_IOUT_CAL_GAIN_TC',      'word', 'RAW',  True),
    0xF8: ('MFR_TEMP_1_GAIN',           'word', 'RAW',  True),
    0xFA: ('MFR_RAIL_ADDRESS',          'byte', 'BYTE', True),
}

# STATUS registers are hardware-writable with clear semantics.
# They are not read-only at the protocol level.
READ_ONLY_EXTRA = {
    0x38,
    0x89, 0x94, 0x99, 0x9A, 0x9E, 0xA5, 0xB6,
    0xD6,
    0xE5, 0xED, 0xEE, 0xEF,
    0xF4,
}

WRITE_ONE_TO_CLEAR = {
    0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x80,
    0xDA,
}

SEND_COMMANDS = {
    0x03: ('CLEAR_FAULTS', False),
    0xE3: ('MFR_CLEAR_PEAKS', False),
    0xF0: ('MFR_COMPARE_USER_ALL', False),
    0xFD: ('MFR_RESET', False),
}

# Not plain register reads. Dedicated protocol handlers are required.
SPECIAL_PROTOCOL_COMMANDS = {
    0x05: ('PAGE_PLUS_WRITE', 'block_write', False),
    0x06: ('PAGE_PLUS_READ', 'block_process_call', False),
    0x1B: ('SMBALERT_MASK', 'word_write_block_process_read', True),
}

SPECIAL_ACCESS_EXTRA = set()

NO_GENERIC_WRITE_EXTRA = WRITE_ONE_TO_CLEAR | {
    0xE6, 0xFA,
}

CUSTOM_FORMATS = {
    0xF6: (True, 1.0, 'ppm/degC'),
    0xF8: (True, 2.0 ** -14, ''),
}

BLOCK_LENGTHS = {
    0x99: 3,
    0x9A: 8,
}

BLOCK_ALLOWED_LENGTHS = {
    0x9E: tuple(range(10)),
    0xEE: (0, 147),
}

GLOBAL_CMDS_EXTRA = {
    cmd
    for cmd, (_, _, _, paged) in REGISTER_OVERRIDES.items()
    if not paged
}
