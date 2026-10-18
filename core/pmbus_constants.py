"""PMBus command codes and device-specific register map assembly.

Register descriptor:
    name, size, format, is_paged

Ordinary registers, send-byte commands and special protocols are
kept separate.

Shared transport descriptors do not imply identical value ranges,
bit meanings or side effects across device models.
"""

from core.devices.base import (
    BASE_REGISTER_MAP,
    BASE_READ_ONLY,
    BASE_SEND_COMMANDS,
    BASE_SPECIAL_ACCESS,
    BASE_NO_GENERIC_WRITE,
)
from core.devices.registry import (
    DEVICE_REGISTRY,
    get_device_profile,
)


class Cmd:
    PAGE = 0x00
    OPERATION = 0x01
    ON_OFF_CONFIG = 0x02
    CLEAR_FAULTS = 0x03

    PAGE_PLUS_WRITE = 0x05
    PAGE_PLUS_READ = 0x06

    WRITE_PROTECT = 0x10
    STORE_USER_ALL = 0x15
    RESTORE_USER_ALL = 0x16
    CAPABILITY = 0x19
    SMBALERT_MASK = 0x1B

    VOUT_MODE = 0x20
    VOUT_COMMAND = 0x21
    VOUT_MAX = 0x24
    VOUT_MARGIN_HIGH = 0x25
    VOUT_MARGIN_LOW = 0x26
    VOUT_TRANSITION_RATE = 0x27

    FREQUENCY_SWITCH = 0x33
    VIN_ON = 0x35
    VIN_OFF = 0x36
    IOUT_CAL_GAIN = 0x38

    VOUT_OV_FAULT_LIMIT = 0x40
    VOUT_OV_FAULT_RESPONSE = 0x41
    VOUT_OV_WARN_LIMIT = 0x42
    VOUT_UV_WARN_LIMIT = 0x43
    VOUT_UV_FAULT_LIMIT = 0x44
    VOUT_UV_FAULT_RESPONSE = 0x45

    IOUT_OC_FAULT_LIMIT = 0x46
    IOUT_OC_FAULT_RESPONSE = 0x47
    IOUT_OC_WARN_LIMIT = 0x4A
    IOUT_UC_FAULT_LIMIT = 0x4B
    IOUT_UC_FAULT_RESPONSE = 0x4C

    OT_FAULT_LIMIT = 0x4F
    OT_FAULT_RESPONSE = 0x50
    OT_WARN_LIMIT = 0x51
    UT_WARN_LIMIT = 0x52
    UT_FAULT_LIMIT = 0x53
    UT_FAULT_RESPONSE = 0x54

    VIN_OV_FAULT_LIMIT = 0x55
    VIN_OV_FAULT_RESPONSE = 0x56
    VIN_OV_WARN_LIMIT = 0x57
    VIN_UV_WARN_LIMIT = 0x58
    VIN_UV_FAULT_LIMIT = 0x59
    VIN_UV_FAULT_RESPONSE = 0x5A

    IIN_OC_WARN_LIMIT = 0x5D
    POWER_GOOD_ON = 0x5E
    POWER_GOOD_OFF = 0x5F

    TON_DELAY = 0x60
    TON_RISE = 0x61
    TON_MAX_FAULT_LIMIT = 0x62
    TON_MAX_FAULT_RESPONSE = 0x63
    TOFF_DELAY = 0x64
    TOFF_FALL = 0x65
    TOFF_MAX_WARN_LIMIT = 0x66

    STATUS_BYTE = 0x78
    STATUS_WORD = 0x79
    STATUS_VOUT = 0x7A
    STATUS_IOUT = 0x7B
    STATUS_INPUT = 0x7C
    STATUS_TEMPERATURE = 0x7D
    STATUS_CML = 0x7E
    STATUS_MFR_SPECIFIC = 0x80

    READ_VIN = 0x88
    READ_IIN = 0x89
    READ_VOUT = 0x8B
    READ_IOUT = 0x8C
    READ_TEMPERATURE_1 = 0x8D
    READ_TEMPERATURE_2 = 0x8E
    READ_DUTY_CYCLE = 0x94
    READ_FREQUENCY = 0x95
    READ_POUT = 0x96
    READ_PIN = 0x97

    PMBUS_REVISION = 0x98
    MFR_ID = 0x99
    MFR_MODEL = 0x9A
    MFR_REVISION = 0x9B
    MFR_SERIAL = 0x9E

    MFR_VOUT_MAX = 0xA5
    MFR_PIN_ACCURACY = 0xAC

    MFR_EE_UNLOCK = 0xBD
    MFR_EE_ERASE = 0xBE
    MFR_EE_DATA = 0xBF

    MFR_PADS = 0xE5
    MFR_ADDRESS = 0xE6
    MFR_SPECIAL_ID = 0xE7
    MFR_FAULT_LOG = 0xEE
    MFR_COMMON = 0xEF

    # Other MFR addresses have model-dependent meanings.
    # Resolve them using the selected register map or send_commands.


STATUS_COMMANDS = frozenset({
    Cmd.STATUS_BYTE,
    Cmd.STATUS_WORD,
    Cmd.STATUS_VOUT,
    Cmd.STATUS_IOUT,
    Cmd.STATUS_INPUT,
    Cmd.STATUS_TEMPERATURE,
    Cmd.STATUS_CML,
    Cmd.STATUS_MFR_SPECIFIC,
})

TELEMETRY_COMMANDS = frozenset({
    Cmd.READ_VIN,
    Cmd.READ_IIN,
    Cmd.READ_VOUT,
    Cmd.READ_IOUT,
    Cmd.READ_TEMPERATURE_1,
    Cmd.READ_TEMPERATURE_2,
    Cmd.READ_DUTY_CYCLE,
    Cmd.READ_FREQUENCY,
    Cmd.READ_POUT,
    Cmd.READ_PIN,
})

# Protection for generic configuration editors and dump restoration.
# This is intentionally distinct from hardware read-only access.
GENERIC_WRITE_DENY = frozenset({
    Cmd.PAGE,
    Cmd.MFR_ADDRESS,
    0xFA,
}) | STATUS_COMMANDS


def build_register_map(special_id=None):
    """Return register map, read-only set, global set, name and pages.

    Unknown devices receive only the common bootstrap map.
    That map must never authorize writes to an unidentified device.
    """
    regmap = dict(BASE_REGISTER_MAP)
    read_only = set(BASE_READ_ONLY)

    name, pages, extras = get_device_profile(special_id)

    if name is not None:
        regmap.update(extras.get("register_overrides", {}))

        for cmd in extras.get("remove_commands", set()):
            regmap.pop(cmd, None)
            read_only.discard(cmd)

        read_only.update(extras.get("read_only_extra", set()))

    missing = read_only - regmap.keys()
    if missing:
        codes = ', '.join(f'0x{cmd:02X}' for cmd in sorted(missing))
        raise ValueError(
            f'Read-only commands absent from register map: {codes}'
        )

    for cmd, info in regmap.items():
        if not isinstance(cmd, int) or not 0 <= cmd <= 0xFF:
            raise ValueError(f'Invalid command code: {cmd!r}')
        if len(info) != 4:
            raise ValueError(f'Invalid descriptor for 0x{cmd:02X}')
        if info[1] not in {'byte', 'word', 'block'}:
            raise ValueError(f'Invalid register size for 0x{cmd:02X}')

    global_cmds = {
        cmd
        for cmd, (_, _, _, paged) in regmap.items()
        if not paged
    }

    return regmap, read_only, global_cmds, name, pages

def _validated_address_registers(declarations, regmap):
    """Validate address identities without interpreting register RAW.

    None means that the profile supplies no address description.
    The returned tuples contain immutable scalar fields only.
    This does not establish address safety or authorize transitions.
    """
    if declarations is None:
        return None

    if not isinstance(declarations, tuple):
        raise ValueError(
            "Address register declarations must be a tuple"
        )

    supported_roles = {
        "individual",
        "individual_base",
        "rail",
    }
    seen = set()
    result = []

    for entry in declarations:
        if not isinstance(entry, tuple) or len(entry) != 6:
            raise ValueError(
                "Invalid address register declaration"
            )

        cmd, name, size, fmt, paged, role = entry

        if (
            isinstance(cmd, bool)
            or not isinstance(cmd, int)
            or not 0 <= cmd <= 0xFF
        ):
            raise ValueError(
                "Invalid address register command"
            )

        if cmd in seen:
            raise ValueError(
                f"Duplicate address register command 0x{cmd:02X}"
            )
        seen.add(cmd)

        if (
            not isinstance(name, str)
            or not name
            or not isinstance(size, str)
            or size not in {"byte", "word"}
            or not isinstance(fmt, str)
            or not fmt
            or not isinstance(paged, bool)
        ):
            raise ValueError(
                f"Invalid address register descriptor 0x{cmd:02X}"
            )

        if (
            not isinstance(role, str)
            or role not in supported_roles
        ):
            raise ValueError(
                f"Unsupported address register role for 0x{cmd:02X}"
            )

        descriptor = (name, size, fmt, paged)
        if regmap.get(cmd) != descriptor:
            raise ValueError(
                f"Address register descriptor mismatch for 0x{cmd:02X}"
            )

        result.append(
            (cmd, name, size, fmt, paged, role)
        )

    return tuple(result)

def build_device_metadata(special_id=None):
    """Build operation metadata for a recognized device profile."""
    name, pages, extras = get_device_profile(special_id)

    # Never expose executable commands for an unidentified device.
    send_commands = {}
    if name is not None:
        send_commands.update(BASE_SEND_COMMANDS)
        send_commands.update(extras.get('send_commands', {}))

    special_protocols = dict(
        extras.get('special_protocol_commands', {})
    )

    special_access = (
        set(BASE_SPECIAL_ACCESS)
        | set(extras.get('special_access_extra', set()))
    )

    write_one_to_clear = set(
        extras.get('write_one_to_clear', set())
    )

    no_generic_write = (
        set(BASE_NO_GENERIC_WRITE)
        | set(GENERIC_WRITE_DENY)
        | special_access
        | write_one_to_clear
        | set(extras.get('no_generic_write_extra', set()))
    )

    metadata = {
        "name": name,
        "pages": pages,
        "address_registers": None,
        "send_commands": send_commands,
        "special_protocols": special_protocols,
        "special_access": special_access,
        "no_generic_write": no_generic_write,
        "write_one_to_clear": write_one_to_clear,
        "custom_formats": dict(
            extras.get("custom_formats", {})
        ),
        "block_lengths": dict(
            extras.get("block_lengths", {})
        ),
        "block_allowed_lengths": dict(
            extras.get("block_allowed_lengths", {})
        ),
        "register_units": dict(
            extras.get("register_units", {})
        ),
        "remove_commands": set(
            extras.get("remove_commands", set())
        ),
        "vout_exponent": extras.get("vout_exponent"),
        "write_value_rules": {
            cmd: dict(rule)
            for cmd, rule in extras.get(
                "write_value_rules", {}
            ).items()
        },
        "control_options": {
            name: tuple(options)
            for name, options in extras.get(
                "control_options", {}
            ).items()
        },
    }

    if name is not None:
        regmap, _, _, _, _ = build_register_map(special_id)

        metadata["address_registers"] = (
            _validated_address_registers(
                extras.get("address_registers"),
                regmap,
            )
        )

        overlap = regmap.keys() & send_commands.keys()
        if overlap:
            codes = ', '.join(
                f'0x{cmd:02X}' for cmd in sorted(overlap)
            )
            raise ValueError(
                f'Commands declared as registers and send-byte: {codes}'
            )

        overlap = regmap.keys() & special_protocols.keys()
        if overlap:
            codes = ', '.join(
                f'0x{cmd:02X}' for cmd in sorted(overlap)
            )
            raise ValueError(
                f'Commands declared as registers and special protocols: '
                f'{codes}'
            )

    return metadata

def compare_address_register_raw(metadata, target_records, baseline_records):
    """Compare declared address RAW values without device I/O.

    metadata must come from build_device_metadata.
    This checks equality and completeness, not address semantics,
    effective addresses, bus safety or transition feasibility.
    """
    report = {
        "status": "unresolved",
        "comparison_complete": False,
        "address_registers_unchanged": None,
        "entries": [],
        "errors": [],
        "execution_ready": False,
        "write_sequence": None,
    }

    def reject(message):
        report["errors"].append(message)
        return report

    if not isinstance(metadata, dict):
        return reject("Invalid device metadata")

    declarations = metadata.get("address_registers")
    if declarations is None:
        return reject("address_policy_unavailable")

    # An empty description does not establish address independence.
    if not isinstance(declarations, tuple) or not declarations:
        return reject("address_register_description_empty_or_invalid")

    pages = metadata.get("pages")
    if (
        isinstance(pages, bool)
        or not isinstance(pages, int)
        or pages < 1
    ):
        return reject("Invalid profile page count")

    expected = {}
    commands = set()
    names = set()

    for declaration in declarations:
        if (
            not isinstance(declaration, tuple)
            or len(declaration) != 6
        ):
            return reject("Invalid address register declaration")

        cmd, name, size, fmt, paged, role = declaration

        if (
            isinstance(cmd, bool)
            or not isinstance(cmd, int)
            or not 0 <= cmd <= 0xFF
            or not isinstance(name, str)
            or not name
            or not isinstance(size, str)
            or size not in {"byte", "word"}
            or not isinstance(fmt, str)
            or not fmt
            or not isinstance(paged, bool)
            or not isinstance(role, str)
            or role not in {"individual", "individual_base", "rail"}
        ):
            return reject("Invalid address register declaration")

        if cmd in commands or name in names:
            return reject("Duplicate address register declaration")

        commands.add(cmd)
        names.add(name)

        for page in range(pages) if paged else (None,):
            expected[(page, cmd)] = declaration

    def collect(records, raw_field, source):
        if not isinstance(records, (list, tuple)):
            raise ValueError(f"{source}: expected a list or tuple")

        collected = {}

        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"{source}: invalid record")

            cmd = record.get("cmd")
            name = record.get("name")

            valid_cmd = (
                isinstance(cmd, int)
                and not isinstance(cmd, bool)
                and 0 <= cmd <= 0xFF
            )
            if not valid_cmd:
                raise ValueError(f"{source}: invalid command code")

            is_address_name = (
                isinstance(name, str) and name in names
            )
            if cmd not in commands and not is_address_name:
                continue

            if "page" not in record:
                raise ValueError(f"{source}: missing address page")

            page = record["page"]
            if page is not None and (
                isinstance(page, bool)
                or not isinstance(page, int)
                or not 0 <= page < pages
            ):
                raise ValueError(f"{source}: invalid address page")

            identity = (page, cmd)
            declaration = expected.get(identity)
            if declaration is None:
                raise ValueError(
                    f"{source}: unexpected address identity {identity!r}"
                )

            _, expected_name, size, fmt, paged, _ = declaration
            if name != expected_name or record.get("size") != size:
                raise ValueError(
                    f"{source}: address descriptor mismatch {identity!r}"
                )

            if "format" in record and record["format"] != fmt:
                raise ValueError(
                    f"{source}: address format mismatch {identity!r}"
                )

            if "is_paged" in record and record["is_paged"] is not paged:
                raise ValueError(
                    f"{source}: address scope mismatch {identity!r}"
                )

            if identity in collected:
                raise ValueError(
                    f"{source}: duplicate address record {identity!r}"
                )

            raw = record.get(raw_field)
            maximum = 0xFF if size == "byte" else 0xFFFF
            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= maximum
            ):
                raise ValueError(
                    f"{source}: invalid {raw_field} for {identity!r}"
                )

            collected[identity] = raw

        missing = [
            identity
            for identity in expected
            if identity not in collected
        ]
        if missing:
            raise ValueError(
                f"{source}: missing address records {missing!r}"
            )

        return collected

    try:
        target = collect(target_records, "raw", "target")
        baseline = collect(
            baseline_records, "current_raw", "baseline"
        )
    except ValueError as exc:
        return reject(str(exc))

    for identity, declaration in expected.items():
        page, cmd = identity
        _, name, size, _, _, role = declaration
        current_raw = baseline[identity]
        target_raw = target[identity]

        report["entries"].append({
            "page": page,
            "cmd": cmd,
            "name": name,
            "size": size,
            "role": role,
            "current_raw": current_raw,
            "target_raw": target_raw,
            "matches_target": current_raw == target_raw,
        })

    unchanged = all(
        entry["matches_target"]
        for entry in report["entries"]
    )
    report["comparison_complete"] = True
    report["address_registers_unchanged"] = unchanged
    report["status"] = "unchanged" if unchanged else "changed"
    return report

# Backward compatibility for modules that still import these names.
# PMBusDevice must not use this LTM4673 map as its initial map.
REGISTER_MAP, READ_ONLY_CMDS, GLOBAL_CMDS, _, _ = (
    build_register_map(0x4480)
)

# Only devices with an actual registered profile.
KNOWN_DEVICES = {
    special_id: (info['name'], info['num_pages'])
    for special_id, info in DEVICE_REGISTRY.items()
}
