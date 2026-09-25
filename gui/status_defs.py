"""Status decoding and Treeview rendering.

Manufacturer definitions:
    LTM4673: supplied datasheet tables.
    LTM4678: supplied datasheet tables.
    LTM4677: manufacturer registers remain raw.

Standard status labels for LTM4677/LTM4678 use generic PMBus
meanings, without claiming that every bit is implemented.

Severity is a GUI policy. It does not directly reproduce ALERT
generation or fault-response configuration.
"""


STATUS_WORD_BITS = {
    15: "VOUT fault/warning",
    14: "IOUT/POUT fault/warning",
    13: "Input fault/warning",
    12: "Manufacturer-specific status",
    11: "Power good negated",
    10: "Fan status, not supported",
    9: "Other status, not supported",
    8: "Unknown status, not supported",
    7: "Busy",
    6: "OFF",
    5: "VOUT OV fault",
    4: "IOUT OC fault",
    3: "VIN UV fault",
    2: "Temperature fault/warning",
    1: "CML fault",
    0: "None of the above",
}

STATUS_VOUT_BITS = {
    7: "OV fault",
    6: "OV warning",
    5: "UV warning",
    4: "UV fault",
    3: "VOUT_MAX warning",
    2: "TON_MAX fault",
    1: "TOFF_MAX, not supported",
    0: "Tracking, not supported",
}

STATUS_IOUT_BITS = {
    7: "OC fault",
    6: "OC LV, not supported",
    5: "OC warning",
    4: "UC fault",
    3: "Current sharing, not supported",
    2: "Power limiting, not supported",
    1: "POUT fault, not supported",
    0: "POUT warning, not supported",
}

STATUS_INPUT_BITS = {
    7: "VIN OV fault",
    6: "VIN OV warning",
    5: "VIN UV warning",
    4: "VIN UV fault",
    3: "Insufficient VIN",
    2: "IIN OC fault, not supported",
    1: "IIN warning, not supported",
    0: "PIN warning, not supported",
}

STATUS_TEMP_BITS = {
    7: "OT fault",
    6: "OT warning",
    5: "UT warning",
    4: "UT fault",
    3: "Reserved",
    2: "Reserved",
    1: "Reserved",
    0: "Reserved",
}

STATUS_CML_BITS = {
    7: "Invalid or unsupported command",
    6: "Invalid or unsupported data",
    5: "PEC failed",
    4: "Memory fault",
    3: "Processor fault, not supported",
    2: "Reserved",
    1: "Other communication fault",
    0: "Unknown, not supported",
}


LTM4673_STANDARD_BITS = {
    "STATUS_WORD": STATUS_WORD_BITS,
    "STATUS_VOUT": STATUS_VOUT_BITS,
    "STATUS_IOUT": STATUS_IOUT_BITS,
    "STATUS_INPUT": STATUS_INPUT_BITS,
    "STATUS_TEMPERATURE": STATUS_TEMP_BITS,
    "STATUS_CML": STATUS_CML_BITS,
}

# Generic PMBus meanings. Model-specific implementation must still
# be checked against the corresponding datasheet.
PMBUS_STANDARD_BITS = {
    "STATUS_WORD": {
        **STATUS_WORD_BITS,
        10: "Fan fault/warning",
        9: "Other status",
        8: "Unknown fault",
    },
    "STATUS_VOUT": {
        **STATUS_VOUT_BITS,
        1: "TOFF_MAX warning",
        0: "VOUT tracking error",
    },
    "STATUS_IOUT": {
        **STATUS_IOUT_BITS,
        6: "OC and low voltage fault",
        3: "Current sharing fault",
        2: "Power limiting",
        1: "POUT OP fault",
        0: "POUT OP warning",
    },
    "STATUS_INPUT": {
        **STATUS_INPUT_BITS,
        2: "IIN OC fault",
        1: "IIN OC warning",
        0: "PIN OP warning",
    },
    "STATUS_TEMPERATURE": dict(STATUS_TEMP_BITS),
    "STATUS_CML": {
        **STATUS_CML_BITS,
        3: "Processor fault",
        0: "Other memory or logic fault",
    },
}

LTM4673_STANDARD_LEVELS = {
    "STATUS_WORD": {
        15: "warn", 14: "warn", 13: "warn", 12: "warn",
        11: "info", 10: "warn", 9: "warn", 8: "warn",
        7: "info", 6: "info", 5: "fault", 4: "fault",
        3: "fault", 2: "warn", 1: "fault", 0: "warn",
    },
    "STATUS_VOUT": {
        7: "fault", 6: "warn", 5: "warn", 4: "fault",
        3: "warn", 2: "fault", 1: "warn", 0: "warn",
    },
    "STATUS_IOUT": {
        7: "fault", 6: "warn", 5: "warn", 4: "fault",
        3: "warn", 2: "warn", 1: "warn", 0: "warn",
    },
    "STATUS_INPUT": {
        7: "fault", 6: "warn", 5: "warn", 4: "fault",
        3: "warn", 2: "warn", 1: "warn", 0: "warn",
    },
    "STATUS_TEMPERATURE": {
        7: "fault", 6: "warn", 5: "warn", 4: "fault",
        3: "warn", 2: "warn", 1: "warn", 0: "warn",
    },
    "STATUS_CML": {
        7: "fault", 6: "fault", 5: "fault", 4: "fault",
        3: "warn", 2: "warn", 1: "fault", 0: "warn",
    },
}

PMBUS_STANDARD_LEVELS = {
    name: dict(levels)
    for name, levels in LTM4673_STANDARD_LEVELS.items()
}
PMBUS_STANDARD_LEVELS["STATUS_WORD"][8] = "fault"
PMBUS_STANDARD_LEVELS["STATUS_VOUT"][0] = "fault"
PMBUS_STANDARD_LEVELS["STATUS_IOUT"].update({
    6: "fault",
    3: "fault",
    1: "fault",
})
PMBUS_STANDARD_LEVELS["STATUS_INPUT"][2] = "fault"
PMBUS_STANDARD_LEVELS["STATUS_CML"].update({
    3: "fault",
    0: "fault",
})


LTM4673_STATUS_MFR_BITS = {
    7: "VOUT discharge fault",
    6: "FAULT1 event",
    5: "FAULT0 event",
    4: "Servo target reached",
    3: "DAC connected and driving VDAC",
    2: "Previous servo operation reached DAC limit",
    1: "AUXFAULT deasserted due to VOUT/IOUT fault; global",
    0: "Watchdog fault; global",
}

LTM4673_STATUS_MFR_LEVELS = {
    7: "fault",
    6: "fault",
    5: "fault",
    4: "info",
    3: "info",
    2: "warn",
    1: "fault",
    0: "fault",
}

LTM4678_STATUS_MFR_BITS = {
    7: "Internal temperature fault limit exceeded",
    6: "Internal temperature warning limit exceeded",
    5: "Factory trim area NVM CRC fault",
    4: "PLL unlocked",
    3: "Fault log present",
    2: "VDD33 UV or OV fault",
    1: "Short-cycle event detected",
    0: "FAULT pin asserted low by external device",
}

LTM4678_STATUS_MFR_LEVELS = {
    7: "fault",
    6: "warn",
    5: "fault",
    4: "warn",
    3: "info",
    2: "fault",
    1: "warn",
    0: "fault",
}

LTM4678_MFR_COMMON_BITS = {
    7: "Module not driving ALERT low",
    6: "Not busy",
    5: "Calculations not pending",
    4: "Outputs not in transition",
    3: "NVM initialized",
    2: "Reserved",
    1: "SHARE_CLK timeout",
    0: "WP pin status",
}

LTM4678_MFR_PADS_BITS = {
    15: "VDD33 OV fault",
    14: "VDD33 UV fault",
    13: "Reserved",
    12: "Reserved",
    11: "ADC values invalid",
    10: "SYNC clocked externally while configured as driver",
    9: "Channel 1 Power Good",
    8: "Channel 0 Power Good",
    7: "Module driving RUN1 low",
    6: "Module driving RUN0 low",
    5: "RUN1 pin state",
    4: "RUN0 pin state",
    3: "Module driving FAULT1 low",
    2: "Module driving FAULT0 low",
    1: "FAULT1 pin state",
    0: "FAULT0 pin state",
}

# Compatibility export only. Rendering uses the selected model.
STATUS_MFR_BITS = LTM4673_STATUS_MFR_BITS

COLORS = {
    "fault": "#D32F2F",
    "warn": "#9A6700",
    "ok": "#228B22",
    "info": "#1565C0",
    "unknown": "#666666",
    "error": "#C62828",
}

INDICATOR_COLORS = {
    "fault": "#FF4444",
    "warn": "#FFD700",
    "ok": "#00FF00",
    "info": "#64B5F6",
    "unknown": "#BDBDBD",
    "error": "#FF8A80",
}

ASEL_STATES = {
    0b00: "Low",
    0b01: "Reserved encoding",
    0b10: "Floating",
    0b11: "High",
}


def _model_matches(device, name, identifiers):
    model = getattr(device, "name", "")
    if model == name or model.startswith(name + " "):
        return True

    special_id = getattr(device, "special_id", None)
    return (
        isinstance(special_id, int)
        and not isinstance(special_id, bool)
        and (special_id & 0xFFF0) in identifiers
    )


def is_ltm4673(device):
    return _model_matches(device, "LTM4673", {0x0230, 0x4480})


def is_ltm4677(device):
    return _model_matches(device, "LTM4677", {0x47B0})


def is_ltm4678(device):
    return _model_matches(device, "LTM4678", {0x4100})


def configure_status_tree(tree, global_view=False):
    for tag, color in COLORS.items():
        tree.tag_configure(tag, foreground=color)

    tree.heading("#0", text="Register / Bit / State", anchor="w")
    tree.heading("val", text="State")
    tree.heading("hex", text="Hex")

    tree.column(
        "#0",
        width=340 if global_view else 260,
        minwidth=160,
        stretch=True,
    )
    tree.column("val", width=65, minwidth=55, anchor="center")
    tree.column("hex", width=75, minwidth=65, anchor="center")


def reset_status_tree(tree):
    """Clear rows while preserving expanded nodes."""
    expanded = set()

    def visit(parent):
        for item in tree.get_children(parent):
            if tree.item(item, "open"):
                expanded.add(item)
            visit(item)

    visit("")
    for item in tree.get_children(""):
        tree.delete(item)

    return expanded


def _insert(tree, expanded, iid, parent, text, values, tag):
    tree.insert(
        parent,
        "end",
        iid=iid,
        text=text,
        values=values,
        tags=(tag,),
        open=iid in expanded,
    )


def _valid_raw(raw, width):
    return (
        isinstance(raw, int)
        and not isinstance(raw, bool)
        and 0 <= raw < (1 << width)
    )


def _combined_level(levels):
    levels = list(levels)
    for level in ("fault", "error", "warn", "unknown", "info"):
        if level in levels:
            return level
    return "ok"


def _level_text(level):
    return {
        "fault": "FAULT",
        "warn": "WARN",
        "info": "INFO",
        "unknown": "RAW",
        "error": "ERR",
        "ok": "OK",
    }[level]


def _error_row(tree, expanded, name):
    _insert(
        tree, expanded, name, "",
        name, ("ERR", "---"), "error",
    )
    return "error"


def render_bit_register(
    tree,
    expanded,
    name,
    raw,
    width,
    labels=None,
    active_levels=None,
):
    if not _valid_raw(raw, width):
        return _error_row(tree, expanded, name)

    labels = labels or {}
    active_levels = active_levels or {}
    rows = []

    for bit in range(width - 1, -1, -1):
        value = (raw >> bit) & 1
        label = labels.get(bit)

        if label is None:
            label = "Meaning not verified for this model"
            level = "unknown"
        else:
            level = (
                active_levels.get(bit, "warn")
                if value else "ok"
            )

        rows.append((bit, value, label, level))

    level = _combined_level(row[3] for row in rows)
    digits = width // 4

    _insert(
        tree, expanded, name, "",
        name,
        (_level_text(level), f"0x{raw:0{digits}X}"),
        level,
    )

    for bit, value, label, bit_level in rows:
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {label}",
            (value, ""),
            bit_level,
        )

    return level


def render_standard_status(
    tree, expanded, device, name, raw, width=8
):
    if is_ltm4673(device):
        labels = LTM4673_STANDARD_BITS.get(name)
        levels = LTM4673_STANDARD_LEVELS.get(name)
    elif is_ltm4677(device) or is_ltm4678(device):
        labels = PMBUS_STANDARD_BITS.get(name)
        levels = PMBUS_STANDARD_LEVELS.get(name)
    else:
        labels = None
        levels = None

    return render_bit_register(
        tree, expanded, name, raw, width, labels, levels
    )


def render_mfr_status(tree, expanded, device, raw):
    if is_ltm4673(device):
        labels = LTM4673_STATUS_MFR_BITS
        levels = LTM4673_STATUS_MFR_LEVELS
    elif is_ltm4678(device):
        labels = LTM4678_STATUS_MFR_BITS
        levels = LTM4678_STATUS_MFR_LEVELS
    else:
        labels = None
        levels = None

    return render_bit_register(
        tree,
        expanded,
        "STATUS_MFR_SPECIFIC",
        raw,
        8,
        labels,
        levels,
    )


def _render_state_rows(
    tree, expanded, name, raw, width, rows, state="STATE"
):
    """Render state bits without treating information as a fault.

    rows contain bit number, description and display level.
    Normal informational states do not raise the overall indicator.
    """
    level = _combined_level(
        row_level
        for _, _, row_level in rows
        if row_level != "info"
    )

    root_level = "ok" if level == "ok" else level
    root_state = state if level == "ok" else _level_text(level)

    _insert(
        tree, expanded, name, "",
        name,
        (root_state, f"0x{raw:0{width // 4}X}"),
        root_level,
    )

    for bit, description, bit_level in rows:
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {description}",
            ((raw >> bit) & 1, ""),
            bit_level,
        )

    return level


def render_mfr_common(tree, expanded, device, raw):
    name = "MFR_COMMON"

    if not _valid_raw(raw, 8):
        return _error_row(tree, expanded, name)

    if is_ltm4678(device):
        rows = [
            (
                7,
                "Module not driving ALERT low"
                if raw & 0x80 else "Module driving ALERT low",
                "info" if raw & 0x80 else "warn",
            ),
            (
                6,
                "Not busy" if raw & 0x40 else "Busy",
                "info",
            ),
            (
                5,
                "Calculations not pending"
                if raw & 0x20 else "Calculations pending",
                "info",
            ),
            (
                4,
                "Outputs not in transition"
                if raw & 0x10 else "Outputs in transition",
                "info",
            ),
            (
                3,
                "NVM initialized"
                if raw & 0x08 else "NVM not initialized",
                "info" if raw & 0x08 else "warn",
            ),
            (2, "Reserved; raw value only", "info"),
            (
                1,
                "SHARE_CLK timeout"
                if raw & 0x02 else "No SHARE_CLK timeout",
                "warn" if raw & 0x02 else "info",
            ),
            (
                0,
                "WP pin high" if raw & 0x01 else "WP pin low",
                "info",
            ),
        ]

        if not (raw & 0x40):
            state = "BUSY"
        elif not (raw & 0x20):
            state = "PENDING"
        elif not (raw & 0x10):
            state = "TRANS"
        else:
            state = "READY"

        return _render_state_rows(
            tree, expanded, name, raw, 8, rows, state
        )

    if not is_ltm4673(device):
        return render_bit_register(
            tree, expanded, name, raw, 8
        )

    rows = [
        (
            7,
            "ALERT inactive, high"
            if raw & 0x80 else "ALERT asserted, low",
            "info" if raw & 0x80 else "warn",
        ),
        (
            6,
            "Ready for PMBus commands"
            if raw & 0x40 else "Busy",
            "info",
        ),
    ]

    for bit in range(5, 1, -1):
        rows.append((
            bit,
            "Reserved; expected 1",
            "info" if raw & (1 << bit) else "warn",
        ))

    rows.extend([
        (
            1,
            "SHARECLK held low"
            if raw & 0x02 else "SHARECLK active",
            "info",
        ),
        (
            0,
            "WP pin high" if raw & 0x01 else "WP pin low",
            "info",
        ),
    ])

    return _render_state_rows(
        tree,
        expanded,
        name,
        raw,
        8,
        rows,
        "READY" if raw & 0x40 else "BUSY",
    )


def render_mfr_pads(tree, expanded, device, raw):
    name = "MFR_PADS"

    if not _valid_raw(raw, 16):
        return _error_row(tree, expanded, name)

    if is_ltm4678(device):
        rows = []

        for bit in range(15, -1, -1):
            value = (raw >> bit) & 1
            label = LTM4678_MFR_PADS_BITS[bit]

            if bit in (15, 14):
                level = "fault" if value else "ok"
            elif bit in (11, 10):
                level = "warn" if value else "ok"
            else:
                level = "info"

            if bit in (13, 12):
                label = "Reserved; raw value only"
            elif bit in (5, 4, 1, 0):
                label += " high" if value else " low"

            rows.append((bit, label, level))

        return _render_state_rows(
            tree, expanded, name, raw, 16, rows
        )

    if not is_ltm4673(device):
        return render_bit_register(
            tree, expanded, name, raw, 16
        )

    asel1 = (raw >> 8) & 0x03
    asel0 = (raw >> 6) & 0x03

    unusual = (
        bool(raw & 0x0C00)
        or asel1 == 0b01
        or asel0 == 0b01
    )
    level = "warn" if unusual else "ok"

    _insert(
        tree, expanded, name, "",
        name,
        ("CHECK" if unusual else "STATE", f"0x{raw:04X}"),
        "warn" if unusual else "info",
    )

    for bit, pin in (
        (15, "PWRGD"),
        (14, "ALERT"),
        (13, "FAULT0"),
        (12, "FAULT1"),
    ):
        value = (raw >> bit) & 1
        text = (
            f"{pin}: this IC is not driving low"
            if value
            else f"{pin}: this IC is driving low"
        )
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {text}", (value, ""), "info",
        )

    for bit in (11, 10):
        value = (raw >> bit) & 1
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  Reserved; expected 0",
            (value, ""),
            "warn" if value else "info",
        )

    for high, low, pin in (
        (9, 8, "ASEL1"),
        (7, 6, "ASEL0"),
    ):
        value = (raw >> low) & 0x03
        field_id = f"{name}.{pin}"

        _insert(
            tree, expanded, field_id, name,
            f"b{high}:{low}  {pin}: {ASEL_STATES[value]}",
            (f"{value:02b}", ""),
            "warn" if value == 0b01 else "info",
        )

        for bit in (high, low):
            _insert(
                tree, expanded, f"{name}.b{bit}", field_id,
                f"b{bit}  {pin} encoding",
                ((raw >> bit) & 1, ""),
                "info",
            )

    for bit, pin in (
        (5, "CONTROL1"),
        (4, "CONTROL0"),
        (3, "FAULT0"),
        (2, "FAULT1"),
        (1, "CONTROL2"),
        (0, "CONTROL3"),
    ):
        value = (raw >> bit) & 1
        state = "high" if value else "low"

        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {pin} input {state}",
            (value, ""),
            "info",
        )

    return level


def set_status_indicator(widget, levels):
    """Missing renderer results must never produce a false OK."""
    levels = [
        level if level in COLORS else "unknown"
        for level in levels
    ]

    if not levels:
        text, level = "UNKNOWN", "unknown"
    elif "error" in levels:
        if "fault" in levels:
            text = "FAULT / READ ERROR"
        elif "warn" in levels:
            text = "WARNING / READ ERROR"
        else:
            text = "READ ERROR"
        level = "error"
    elif "fault" in levels:
        text, level = "FAULT", "fault"
    elif "warn" in levels:
        text, level = "WARNING", "warn"
    elif "unknown" in levels:
        text, level = "CHECK RAW", "unknown"
    elif "info" in levels:
        text, level = "INFO", "info"
    else:
        text, level = "OK", "ok"

    widget.configure(
        text=text,
        fg=INDICATOR_COLORS[level],
    )
