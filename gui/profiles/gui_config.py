"""Configuration layout descriptions and register placement.

Register maps remain the authority for command availability,
PAGE scope, transport format and write permissions.
"""

from dataclasses import dataclass

from core.pmbus_constants import (
    STATUS_COMMANDS,
    TELEMETRY_COMMANDS,
)


TAB_TITLES = (
    "Output",
    "Protection",
    "Timing",
    "Advanced",
)


@dataclass(frozen=True)
class ConfigSection:
    tab: str
    title: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class ConfigRow:
    cmd: int
    name: str
    size: str
    fmt: str
    paged: bool
    writable: bool

    def as_register_tuple(self):
        """Compatibility with the current RegisterGroup."""
        return (
            self.cmd,
            self.name,
            self.size,
            self.fmt,
            self.paged,
            not self.writable,
        )


@dataclass(frozen=True)
class ConfigGroup:
    tab: str
    title: str
    rows: tuple[ConfigRow, ...]


COMMON_CONFIG_SECTIONS = (
    ConfigSection(
        "Output",
        "Control",
        (
            "OPERATION",
            "ON_OFF_CONFIG",
        ),
    ),
    ConfigSection(
        "Output",
        "Output voltage",
        (
            "VOUT_COMMAND",
            "VOUT_MAX",
            "VOUT_MARGIN_HIGH",
            "VOUT_MARGIN_LOW",
            "MFR_VOUT_MAX",
        ),
    ),
    ConfigSection(
        "Output",
        "Input and switching",
        (
            "VIN_ON",
            "VIN_OFF",
            "FREQUENCY_SWITCH",
        ),
    ),
    ConfigSection(
        "Protection",
        "Output voltage protection",
        (
            "VOUT_OV_FAULT_LIMIT",
            "VOUT_OV_WARN_LIMIT",
            "VOUT_UV_WARN_LIMIT",
            "VOUT_UV_FAULT_LIMIT",
            "VOUT_OV_FAULT_RESPONSE",
            "VOUT_UV_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Protection",
        "Output current protection",
        (
            "IOUT_OC_FAULT_LIMIT",
            "IOUT_OC_WARN_LIMIT",
            "IOUT_UC_FAULT_LIMIT",
            "IOUT_OC_FAULT_RESPONSE",
            "IOUT_UC_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Protection",
        "Temperature protection",
        (
            "OT_FAULT_LIMIT",
            "OT_WARN_LIMIT",
            "UT_WARN_LIMIT",
            "UT_FAULT_LIMIT",
            "OT_FAULT_RESPONSE",
            "UT_FAULT_RESPONSE",
            "MFR_OT_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Protection",
        "Input protection",
        (
            "VIN_OV_FAULT_LIMIT",
            "VIN_OV_WARN_LIMIT",
            "VIN_UV_WARN_LIMIT",
            "VIN_UV_FAULT_LIMIT",
            "IIN_OC_WARN_LIMIT",
            "VIN_OV_FAULT_RESPONSE",
            "VIN_UV_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Protection",
        "Power Good",
        (
            "POWER_GOOD_ON",
            "POWER_GOOD_OFF",
        ),
    ),
    ConfigSection(
        "Timing",
        "Startup",
        (
            "TON_DELAY",
            "TON_RISE",
            "TON_MAX_FAULT_LIMIT",
            "TON_MAX_FAULT_RESPONSE",
        ),
    ),
    ConfigSection(
        "Timing",
        "Shutdown",
        (
            "TOFF_DELAY",
            "TOFF_FALL",
            "TOFF_MAX_WARN_LIMIT",
        ),
    ),
    ConfigSection(
        "Timing",
        "Transitions and retry",
        (
            "VOUT_TRANSITION_RATE",
            "MFR_RETRY_DELAY",
            "MFR_RESTART_DELAY",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Write protection",
        (
            "WRITE_PROTECT",
        ),
    ),
    ConfigSection(
        "Advanced",
        "User data",
        (
            "USER_DATA_00",
            "USER_DATA_01",
            "USER_DATA_02",
            "USER_DATA_03",
            "USER_DATA_04",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Calibration",
        (
            "IOUT_CAL_GAIN",
            "MFR_IOUT_CAL_GAIN_TC",
            "MFR_TEMP_1_GAIN",
            "MFR_TEMP_1_OFFSET",
        ),
    ),
    ConfigSection(
        "Advanced",
        "Recorded peaks",
        (
            "MFR_IOUT_PEAK",
            "MFR_VOUT_PEAK",
            "MFR_VIN_PEAK",
            "MFR_TEMPERATURE_1_PEAK",
        ),
    ),
)


# Already displayed outside the configuration editor.
NON_CONFIG_COMMANDS = (
    STATUS_COMMANDS
    | TELEMETRY_COMMANDS
    | frozenset({
        0x00,  # PAGE
        0x19,  # CAPABILITY
        0x20,  # VOUT_MODE
        0x98,  # PMBUS_REVISION
        0xE5,  # MFR_PADS
        0xE7,  # MFR_SPECIAL_ID
        0xEF,  # MFR_COMMON
    })
)


def config_candidates(device, paged):
    """Return eligible scalar registers for one PAGE scope."""
    metadata = getattr(device, "_metadata", {})
    special_access = set(metadata.get("special_access", ()))

    profile_telemetry = set()
    gui_profile = getattr(device, "_config_gui_profile", None)

    if gui_profile is not None:
        for field in (
            gui_profile.global_telemetry
            + gui_profile.channel_telemetry
        ):
            cmd = device.command_code(field.command)
            if cmd is not None:
                profile_telemetry.add(cmd)

    rows = []

    for cmd, descriptor in sorted(device._regmap.items()):
        name, size, fmt, is_paged = descriptor

        if bool(is_paged) != bool(paged):
            continue
        if size not in {"byte", "word"}:
            continue
        if cmd in NON_CONFIG_COMMANDS:
            continue
        if cmd in profile_telemetry:
            continue
        if cmd in special_access:
            continue
        if not device.can_read_register(cmd, size):
            continue

        rows.append(
            ConfigRow(
                cmd=cmd,
                name=name,
                size=size,
                fmt=fmt,
                paged=bool(is_paged),
                writable=device.can_write_register(cmd, size),
            )
        )

    return tuple(rows)


def build_config_layout(device, profile, paged):
    """Build sections without reading or writing hardware."""
    # Resolve telemetry exclusions locally without changing the device.
    telemetry_commands = {
        cmd
        for field in (
            profile.global_telemetry
            + profile.channel_telemetry
        )
        if (cmd := device.command_code(field.command)) is not None
    }

    candidates = tuple(
        row
        for row in config_candidates(device, paged)
        if row.cmd not in telemetry_commands
    )

    sections = profile.config_sections
    placements = {}

    for section in sections:
        if section.tab not in TAB_TITLES:
            raise ValueError(
                f"Unknown Config tab: {section.tab}"
            )

        for name in section.commands:
            if name in placements:
                raise ValueError(
                    f"Duplicate Config placement for {name}"
                )
            placements[name] = (section.tab, section.title)

    buckets = {}
    for row in candidates:
        location = placements.get(
            row.name,
            ("Advanced", "Other"),
        )
        buckets.setdefault(location, []).append(row)

    result = []
    emitted = set()

    for tab in TAB_TITLES:
        for section in sections:
            location = (section.tab, section.title)

            if section.tab != tab or location in emitted:
                continue

            emitted.add(location)
            rows = buckets.get(location)
            if not rows:
                continue

            # Preserve the order declared in the GUI section.
            order = {
                name: index
                for index, name in enumerate(section.commands)
            }
            rows = sorted(
                rows,
                key=lambda row: order.get(
                    row.name, len(order)
                ),
            )

            result.append(
                ConfigGroup(
                    tab=tab,
                    title=section.title,
                    rows=tuple(rows),
                )
            )

        if tab == "Advanced":
            location = ("Advanced", "Other")
            if location not in emitted and location in buckets:
                result.append(
                    ConfigGroup(
                        tab="Advanced",
                        title="Other",
                        rows=tuple(buckets[location]),
                    )
                )

    return tuple(result)
