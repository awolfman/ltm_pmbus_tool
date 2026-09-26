"""GUI descriptions independent of register transport and access rules."""

from dataclasses import dataclass
from .gui_config import ConfigSection, COMMON_CONFIG_SECTIONS

@dataclass(frozen=True)
class TelemetryField:
    key: str
    command: str
    label: str
    unit: str
    color: str


@dataclass(frozen=True)
class GuiProfile:
    model: str
    global_telemetry: tuple[TelemetryField, ...]
    channel_telemetry: tuple[TelemetryField, ...]
    config_sections: tuple[ConfigSection, ...] = (
        COMMON_CONFIG_SECTIONS
    )


VIN = TelemetryField(
    "VIN", "READ_VIN", "VIN", "V", "#2196F3"
)
IIN = TelemetryField(
    "IIN", "READ_IIN", "IIN", "A", "#FF9800"
)
PIN = TelemetryField(
    "PIN", "READ_PIN", "PIN", "W", "#E91E63"
)
TEMP_IC = TelemetryField(
    "TEMP_IC",
    "READ_TEMPERATURE_2",
    "IC Temperature",
    "C",
    "#795548",
)

VOUT = TelemetryField(
    "VOUT", "READ_VOUT", "VOUT", "V", "#4CAF50"
)
IOUT = TelemetryField(
    "IOUT", "READ_IOUT", "IOUT", "A", "#F44336"
)
POUT = TelemetryField(
    "POUT", "READ_POUT", "POUT", "W", "#9C27B0"
)
TEMP1 = TelemetryField(
    "TEMP1",
    "READ_TEMPERATURE_1",
    "Temperature",
    "C",
    "#E91E63",
)
DUTY = TelemetryField(
    "DUTY", "READ_DUTY_CYCLE", "Duty Cycle", "%", "#009688"
)
FREQ = TelemetryField(
    "FREQ", "READ_FREQUENCY", "Frequency", "kHz", "#607D8B"
)
CHANNEL_IIN = TelemetryField(
    "IIN", "MFR_READ_IIN", "Channel IIN", "A", "#FF9800"
)


def available_telemetry(device, fields):
    """Filter presentation fields using the actual device register map.

    Display location does not define PMBus PAGE scope.
    PMBusDevice remains responsible for reading the correct page.
    """
    result = []
    seen_keys = set()

    for field in fields:
        if field.key in seen_keys:
            raise ValueError(
                f"Duplicate telemetry field: {field.key}"
            )
        seen_keys.add(field.key)

        cmd = device.command_code(field.command)
        if cmd is None:
            continue

        if not device.can_read_register(cmd):
            continue

        result.append(field)

    return tuple(result)
