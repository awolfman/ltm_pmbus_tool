"""LTM4673 register profile, including engineering samples."""

from core.devices.registry import register_device

register_device(0x4480, "LTM4673", 4, __name__)
register_device(0x0230, "LTM4673", 4, __name__)

VOUT_EXPONENT = -13

REGISTER_OVERRIDES = {
    0x38: ("IOUT_CAL_GAIN", "word", "L11", True),
    0x4B: ("IOUT_UC_FAULT_LIMIT", "word", "L11", True),
    0x4C: ("IOUT_UC_FAULT_RESPONSE", "byte", "BYTE", True),
    0x52: ("UT_WARN_LIMIT", "word", "L11", True),
    0x56: ("VIN_OV_FAULT_RESPONSE", "byte", "BYTE", False),
    0x57: ("VIN_OV_WARN_LIMIT", "word", "L11", False),
    0x59: ("VIN_UV_FAULT_LIMIT", "word", "L11", False),
    0x5A: ("VIN_UV_FAULT_RESPONSE", "byte", "BYTE", False),
    0x5E: ("POWER_GOOD_ON", "word", "L16", True),
    0x5F: ("POWER_GOOD_OFF", "word", "L16", True),

    0x78: ("STATUS_BYTE", "byte", "BYTE", True),
    0x79: ("STATUS_WORD", "word", "RAW", True),
    0x7A: ("STATUS_VOUT", "byte", "BYTE", True),
    0x7B: ("STATUS_IOUT", "byte", "BYTE", True),
    0x7C: ("STATUS_INPUT", "byte", "BYTE", False),
    0x7D: ("STATUS_TEMPERATURE", "byte", "BYTE", True),
    0x7E: ("STATUS_CML", "byte", "BYTE", False),
    0x80: ("STATUS_MFR_SPECIFIC", "byte", "BYTE", True),

    # Shared input measurements must not be summed across pages.
    0x89: ("READ_IIN", "word", "L11", True),
    0x97: ("READ_PIN", "word", "L11", True),

    # Documentation only. Excluded from generic access below.
    0xB5: ("MFR_LTC_RESERVED_1", "word", "RAW", True),
    0xB8: ("MFR_T_SELF_HEAT", "word", "L11", True),
    0xB9: ("MFR_IOUT_CAL_GAIN_TAU_INV", "word", "L11", True),
    0xBA: ("MFR_IOUT_CAL_GAIN_THETA", "word", "L11", True),
    0xBB: ("MFR_READ_IOUT", "word", "RAW", True),
    0xBC: ("MFR_LTC_RESERVED_2", "word", "RAW", True),

    0xC0: ("MFR_EIN", "block", "RAW", False),
    0xC1: ("MFR_EIN_CONFIG", "byte", "BYTE", False),
    0xC2: ("MFR_SPECIAL_LOT", "byte", "BYTE", True),
    0xC3: ("MFR_IIN_CAL_GAIN_TC", "word", "RAW", False),
    0xC4: ("MFR_IIN_PEAK", "word", "L11", True),
    0xC5: ("MFR_IIN_MIN", "word", "L11", True),
    0xC6: ("MFR_PIN_PEAK", "word", "L11", True),
    0xC7: ("MFR_PIN_MIN", "word", "L11", True),
    0xC8: ("MFR_COMMAND_PLUS", "word", "RAW", False),
    0xC9: ("MFR_DATA_PLUS0", "word", "RAW", False),
    0xCA: ("MFR_DATA_PLUS1", "word", "RAW", False),

    0xD0: ("MFR_CONFIG_LTM4673", "word", "RAW", True),
    0xD1: ("MFR_CONFIG_ALL_LTM4673", "word", "RAW", False),
    0xD2: ("MFR_FAULTB0_PROPAGATE", "byte", "BYTE", True),
    0xD3: ("MFR_FAULTB1_PROPAGATE", "byte", "BYTE", True),
    0xD4: ("MFR_PWRGD_EN", "word", "RAW", False),
    0xD5: ("MFR_FAULTB0_RESPONSE", "byte", "BYTE", False),
    0xD6: ("MFR_FAULTB1_RESPONSE", "byte", "BYTE", False),
    0xD8: ("MFR_IOUT_MIN", "word", "L11", True),
    0xD9: ("MFR_CONFIG2_LTM4673", "byte", "BYTE", False),
    0xDA: ("MFR_CONFIG3_LTM4673", "byte", "BYTE", False),
    0xDB: ("MFR_RETRY_DELAY", "word", "L11", False),
    0xDC: ("MFR_RESTART_DELAY", "word", "L11", False),

    0xE0: ("MFR_DAC", "word", "RAW", True),
    0xE1: ("MFR_POWERGOOD_ASSERTION_DELAY", "word", "L11", False),
    0xE2: ("MFR_WATCHDOG_T_FIRST", "word", "L11", False),
    0xE3: ("MFR_WATCHDOG_T", "word", "L11", False),
    0xE4: ("MFR_PAGE_FF_MASK", "byte", "BYTE", False),
    0xE5: ("MFR_PADS", "word", "RAW", False),
    0xE6: ("MFR_I2C_BASE_ADDRESS", "byte", "BYTE", False),
    0xE8: ("MFR_IIN_CAL_GAIN", "word", "L11", False),
    0xE9: ("MFR_VOUT_DISCHARGE_THRESHOLD", "word", "L11", True),
    0xED: ("MFR_FAULT_LOG_STATUS", "byte", "BYTE", False),
    0xEE: ("MFR_FAULT_LOG", "block", "RAW", False),
    0xEF: ("MFR_COMMON", "byte", "BYTE", False),

    0xF6: ("MFR_IOUT_CAL_GAIN_TC", "word", "RAW", True),
    0xF7: ("MFR_RETRY_COUNT", "byte", "BYTE", False),
    0xF8: ("MFR_TEMP_1_GAIN", "word", "RAW", True),
    0xFA: ("MFR_IOUT_SENSE_VOLTAGE", "word", "RAW", True),
    0xFB: ("MFR_VOUT_MIN", "word", "L16", True),
    0xFC: ("MFR_VIN_MIN", "word", "L11", False),
    0xFD: ("MFR_TEMPERATURE_1_MIN", "word", "L11", True),
}

READ_ONLY_EXTRA = {
    0x38,
    0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x80,
    0x89, 0x97,
    0xB8, 0xBB,
    0xC0, 0xC2, 0xC4, 0xC5, 0xC6, 0xC7,
    0xD8,
    0xE5, 0xED, 0xEE, 0xEF,
    0xF6, 0xFA, 0xFB, 0xFC, 0xFD,
}

SEND_COMMANDS = {
    0x03: ("CLEAR_FAULTS", True),
    0xEB: ("MFR_FAULT_LOG_RESTORE", False),
}

# Excluded from generic reads, GUI register grids and dump restoration.
SPECIAL_ACCESS_EXTRA = {
    0xB5, 0xBC,
    0xC8, 0xC9, 0xCA,
}

NO_GENERIC_WRITE_EXTRA = SPECIAL_ACCESS_EXTRA | {
    0xE6,
}

CUSTOM_FORMATS = {
    0xBB: (True, 0.0025, "A"),
    0xC3: (True, 1.0, "ppm/degC"),
    0xF6: (True, 1.0, "ppm/degC"),
    0xF8: (False, 2.0 ** -14, ""),
    0xFA: (False, 0.025 * 2.0 ** -13, "V"),
}

BLOCK_LENGTHS = {
    0xC0: 12,
    0xEE: 255,
}

WRITE_ONE_TO_CLEAR = set()

GLOBAL_CMDS_EXTRA = {
    cmd
    for cmd, (_, _, _, paged) in REGISTER_OVERRIDES.items()
    if not paged
}

def _operation_valid(raw, use_pmbus):
    """Check the documented OPERATION bit combinations."""
    if raw & 0x03:
        return False

    control = (raw >> 6) & 0x03
    margin = (raw >> 4) & 0x03
    fault = (raw >> 2) & 0x03

    if control == 0x03:
        return False

    # With PMBus on/off enabled, immediate off ignores
    # both the margin and fault fields.
    if use_pmbus and control == 0:
        return True

    # Nominal output ignores the fault field.
    if margin == 0:
        return True

    return margin in {1, 2} and fault in {1, 2}


_OPERATION_WITH_PMBUS = frozenset(
    raw
    for raw in range(256)
    if _operation_valid(raw, True)
)

_OPERATION_WITHOUT_PMBUS = frozenset(
    raw
    for raw in range(256)
    if _operation_valid(raw, False)
)

WRITE_VALUE_RULES = {
    0x01: {
        "allowed": (
            _OPERATION_WITH_PMBUS
            & _OPERATION_WITHOUT_PMBUS
        ),
        "requires_context": (
            _OPERATION_WITH_PMBUS
            ^ _OPERATION_WITHOUT_PMBUS
        ),
        "context_error": (
            "This OPERATION encoding requires checking the "
            "current ON_OFF_CONFIG. Context-dependent raw "
            "writes are not enabled."
        ),
        "invalid_error": (
            "Reserved OPERATION bit combination."
        ),
    },
    0x02: {
        # Application write policy:
        # reserved bits 7:5 = 0 and reserved bit 1 = 1.
        # Do not classify other encodings as hardware CML faults.
        "allowed": frozenset(
            raw
            for raw in range(0x20)
            if raw & 0x02
        ),
        "invalid_error": (
            "Application policy requires ON_OFF_CONFIG "
            "bits 7:5 = 0 and bit 1 = 1."
        ),
    },
}

CONTROL_OPTIONS = {
    "OPERATION": (
        (0x80, "Sequence on / Nominal"),
        (0xA8, "Margin High"),
        (0x98, "Margin Low"),
        (0x40, "Sequence off / Nominal"),
        (0x00, "Immediate off / Nominal"),
        (0x94, "Margin Low / Ignore faults+warnings"),
        (0xA4, "Margin High / Ignore faults+warnings"),
        (0x54, "Seq off / Low / Ignore faults+warnings"),
        (0x58, "Seq off / Margin Low"),
        (0x64, "Seq off / High / Ignore faults+warnings"),
        (0x68, "Seq off / Margin High"),
    ),
    "ON_OFF_CONFIG": (
        (0x1E, "CMD+CONTROL / Pin TOFF_DELAY"),
        (0x1F, "CMD+CONTROL / Pin fast off"),
        (0x16, "CONTROL only / Pin TOFF_DELAY"),
        (0x17, "CONTROL only / Pin fast off"),
        (0x1A, "CMD only / Pin ignored"),
        (0x1B, "CMD only / Pin ignored"),
        (0x12, "No start source"),
        (0x13, "No start source"),
        (0x02, "Auto startup / CMD=0 CONTROL=0"),
        (0x03, "Auto startup / CMD=0 CONTROL=0"),
        (0x06, "Auto startup / CMD=0 CONTROL=1 / Delay"),
        (0x07, "Auto startup / CMD=0 CONTROL=1 / Fast"),
        (0x0A, "Auto startup / CMD=1 CONTROL=0"),
        (0x0B, "Auto startup / CMD=1 CONTROL=0"),
        (0x0E, "Auto startup / CMD=1 CONTROL=1 / Delay"),
        (0x0F, "Auto startup / CMD=1 CONTROL=1 / Fast"),
    ),
}
