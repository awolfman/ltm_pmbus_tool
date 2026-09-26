"""LTM4677 GUI presentation."""

from .gui_base import (
    GuiProfile,
    VIN,
    IIN,
    TEMP_IC,
    VOUT,
    IOUT,
    POUT,
    TEMP1,
    CHANNEL_IIN,
    DUTY,
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
            "MFR_GPIO_PROPAGATE",
            "MFR_GPIO_RESPONSE",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer configuration",
        (
            "MFR_CHAN_CONFIG",
            "MFR_CONFIG_ALL",
            "MFR_PWM_MODE",
            "MFR_PWM_CONFIG",
            "MFR_ADC_CONTROL",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Manufacturer calibration",
        (
            "MFR_IIN_OFFSET",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional diagnostics",
        (
            "MFR_INFO",
            "MFR_ADC_TELEMETRY_STATUS",
            "MFR_ADDRESS",
            "MFR_RAIL_ADDRESS",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Additional peaks",
        (
            "MFR_TEMPERATURE_2_PEAK",
        ),
    ),
)

PROFILE = GuiProfile(
    model="LTM4677",
    global_telemetry=(
        VIN,
        IIN,
        TEMP_IC,
    ),
    channel_telemetry=(
        VOUT,
        IOUT,
        POUT,
        TEMP1,
        CHANNEL_IIN,
        DUTY,
    ),
    config_sections=(
        COMMON_CONFIG_SECTIONS + MODEL_CONFIG_SECTIONS
    ),
)
