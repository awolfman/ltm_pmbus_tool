"""LTM4673 GUI presentation."""

from .gui_base import (
    GuiProfile,
    VIN,
    IIN,
    PIN,
    TEMP_IC,
    VOUT,
    IOUT,
    POUT,
    TEMP1,
)

from .gui_config import (
    ConfigSection,
    COMMON_CONFIG_SECTIONS,
)

MODEL_CONFIG_SECTIONS = (
    ConfigSection(
        "Protection",
        "Fault routing",
        (
            "MFR_FAULTB0_PROPAGATE",
            "MFR_FAULTB1_PROPAGATE",
            "MFR_FAULTB0_RESPONSE",
            "MFR_FAULTB1_RESPONSE",
            "MFR_PWRGD_EN",
            "MFR_VOUT_DISCHARGE_THRESHOLD",
        ),
    ),
    ConfigSection(
        "Timing",
        "Manufacturer timing",
        (
            "MFR_POWERGOOD_ASSERTION_DELAY",
            "MFR_WATCHDOG_T_FIRST",
            "MFR_WATCHDOG_T",
            "MFR_RETRY_COUNT",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer configuration",
        (
            "MFR_CONFIG_LTM4673",
            "MFR_CONFIG_ALL_LTM4673",
            "MFR_CONFIG2_LTM4673",
            "MFR_CONFIG3_LTM4673",
            "MFR_PAGE_FF_MASK",
            "MFR_EIN_CONFIG",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer calibration",
        (
            "MFR_T_SELF_HEAT",
            "MFR_IOUT_CAL_GAIN_TAU_INV",
            "MFR_IOUT_CAL_GAIN_THETA",
            "MFR_IIN_CAL_GAIN_TC",
            "MFR_IIN_CAL_GAIN",
            "MFR_IOUT_SENSE_VOLTAGE",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional diagnostics",
        (
            "MFR_READ_IOUT",
            "MFR_DAC",
            "MFR_SPECIAL_LOT",
            "MFR_FAULT_LOG_STATUS",
            "MFR_I2C_BASE_ADDRESS",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional peaks and minima",
        (
            "MFR_IIN_PEAK",
            "MFR_IIN_MIN",
            "MFR_PIN_PEAK",
            "MFR_PIN_MIN",
            "MFR_IOUT_MIN",
            "MFR_VOUT_MIN",
            "MFR_VIN_MIN",
            "MFR_TEMPERATURE_1_MIN",
        ),
    ),
)

PROFILE = GuiProfile(
    model="LTM4673",
    global_telemetry=(
        VIN,
        IIN,
        PIN,
        TEMP_IC,
    ),
    channel_telemetry=(
        VOUT,
        IOUT,
        POUT,
        TEMP1,
    ),
    config_sections=(
        COMMON_CONFIG_SECTIONS + MODEL_CONFIG_SECTIONS
    ),
)
