"""LTM4678 GUI presentation."""

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
    FREQ,
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
            "MFR_FAULT_PROPAGATE",
            "MFR_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer configuration",
        (
            "MFR_CHAN_CONFIG",
            "MFR_CONFIG_ALL",
            "MFR_PWM_COMP",
            "MFR_PWM_MODE",
            "MFR_PWM_CONFIG",
            "MFR_ADC_CONTROL",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer calibration",
        (
            "MFR_IOUT_CAL_GAIN",
            "MFR_IIN_CAL_GAIN",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional diagnostics",
        (
            "MFR_PIN_ACCURACY",
            "MFR_READ_ICHIP",
            "MFR_RVIN",
            "MFR_ADDRESS",
            "MFR_RAIL_ADDRESS",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional peaks",
        (
            "MFR_READ_IIN_PEAK",
            "MFR_TEMPERATURE_2_PEAK",
        ),
    ),
)

PROFILE = GuiProfile(
    model="LTM4678",
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
        FREQ,
    ),
    config_sections=(
        COMMON_CONFIG_SECTIONS + MODEL_CONFIG_SECTIONS
    ),
)
