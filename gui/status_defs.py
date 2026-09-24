"""Status decoding and Treeview rendering.

Manufacturer-specific decoding below is verified for LTM4673.
Other models retain raw manufacturer bit displays.
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

# Compatibility export. New rendering code selects by device model.
STATUS_MFR_BITS = LTM4673_STATUS_MFR_BITS

# These levels are GUI policy, not datasheet ALERT/OFF flags.
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

LTM4673_STANDARD_BITS = {
    "STATUS_WORD": STATUS_WORD_BITS,
    "STATUS_VOUT": STATUS_VOUT_BITS,
    "STATUS_IOUT": STATUS_IOUT_BITS,
    "STATUS_INPUT": STATUS_INPUT_BITS,
    "STATUS_TEMPERATURE": STATUS_TEMP_BITS,
    "STATUS_CML": STATUS_CML_BITS,
}

LTM4673_STANDARD_LEVELS = {
    "STATUS_WORD": {
        15: "warn",
        14: "warn",
        13: "warn",
        12: "warn",
        11: "info",
        10: "warn",
        9: "warn",
        8: "warn",
        7: "info",
        6: "info",
        5: "fault",
        4: "fault",
        3: "fault",
        2: "warn",
        1: "fault",
        0: "warn",
    },
    "STATUS_VOUT": {
        7: "fault",
        6: "warn",
        5: "warn",
        4: "fault",
        3: "warn",
        2: "fault",
        1: "warn",
        0: "warn",
    },
    "STATUS_IOUT": {
        7: "fault",
        6: "warn",
        5: "warn",
        4: "fault",
        3: "warn",
        2: "warn",
        1: "warn",
        0: "warn",
    },
    "STATUS_INPUT": {
        7: "fault",
        6: "warn",
        5: "warn",
        4: "fault",
        3: "warn",
        2: "warn",
        1: "warn",
        0: "warn",
    },
    "STATUS_TEMPERATURE": {
        7: "fault",
        6: "warn",
        5: "warn",
        4: "fault",
        3: "warn",
        2: "warn",
        1: "warn",
        0: "warn",
    },
    "STATUS_CML": {
        7: "fault",
        6: "fault",
        5: "fault",
        4: "fault",
        3: "warn",
        2: "warn",
        1: "fault",
        0: "warn",
    },
}

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


def is_ltm4673(device):
    name = getattr(device, "name", "")
    if name == "LTM4673" or name.startswith("LTM4673 "):
        return True

    sid = getattr(device, "special_id", None)
    return (
        isinstance(sid, int)
        and (sid & 0xFFF0) in {0x0230, 0x4480}
    )


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
    """Clear rows while preserving expanded nodes by stable IDs."""
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
    decoded = bool(labels)

    rows = []
    for bit in range(width - 1, -1, -1):
        value = (raw >> bit) & 1
        label = labels.get(bit, "Meaning not verified for this model")

        if decoded:
            level = active_levels.get(bit, "warn") if value else "ok"
        else:
            level = "unknown"

        rows.append((bit, value, label, level))

    level = _combined_level([row[3] for row in rows])
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
            f"b{bit}  {label}", (value, ""), bit_level,
        )

    return level


def render_standard_status(
    tree, expanded, device, name, raw, width=8
):
    if is_ltm4673(device):
        labels = LTM4673_STANDARD_BITS.get(name)
        levels = LTM4673_STANDARD_LEVELS.get(name)
    else:
        labels = None
        levels = None

    return render_bit_register(
        tree, expanded, name, raw, width, labels, levels
    )


def render_mfr_status(tree, expanded, device, raw):
    labels = None
    levels = None

    if is_ltm4673(device):
        labels = LTM4673_STATUS_MFR_BITS
        levels = LTM4673_STATUS_MFR_LEVELS

    return render_bit_register(
        tree,
        expanded,
        "STATUS_MFR_SPECIFIC",
        raw,
        8,
        labels,
        levels,
    )


def render_mfr_common(tree, expanded, device, raw):
    name = "MFR_COMMON"

    if not is_ltm4673(device):
        return render_bit_register(
            tree, expanded, name, raw, 8
        )

    if not _valid_raw(raw, 8):
        return _error_row(tree, expanded, name)

    alert_inactive = bool(raw & 0x80)
    ready = bool(raw & 0x40)
    reserved_valid = (raw & 0x3C) == 0x3C

    level = (
        "warn"
        if not alert_inactive or not ready or not reserved_valid
        else "info"
    )

    if not reserved_valid:
        state = "CHECK"
    elif not ready:
        state = "BUSY"
    elif not alert_inactive:
        state = "ALERT"
    else:
        state = "READY"

    _insert(
        tree, expanded, name, "",
        name, (state, f"0x{raw:02X}"), level,
    )

    rows = [
        (
            7,
            "ALERT inactive, high" if alert_inactive
            else "ALERT asserted, low",
            "info" if alert_inactive else "warn",
        ),
        (
            6,
            "Ready for PMBus commands" if ready
            else "Busy; other commands may be NACKed",
            "info" if ready else "warn",
        ),
    ]

    for bit in range(5, 1, -1):
        value = (raw >> bit) & 1
        rows.append((
            bit,
            "Reserved; expected 1",
            "info" if value else "warn",
        ))

    rows.extend([
        (
            1,
            "SHARECLK held low" if raw & 0x02
            else "SHARECLK active",
            "info",
        ),
        (
            0,
            "WP pin high" if raw & 0x01 else "WP pin low",
            "info",
        ),
    ])

    for bit, text, bit_level in rows:
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {text}",
            ((raw >> bit) & 1, ""),
            bit_level,
        )

    return level


def render_mfr_pads(tree, expanded, device, raw):
    name = "MFR_PADS"

    if not is_ltm4673(device):
        return render_bit_register(
            tree, expanded, name, raw, 16
        )

    if not _valid_raw(raw, 16):
        return _error_row(tree, expanded, name)

    asel1 = (raw >> 8) & 0x03
    asel0 = (raw >> 6) & 0x03

    unusual = (
        bool(raw & 0x0C00)
        or asel1 == 0b01
        or asel0 == 0b01
    )
    level = "warn" if unusual else "info"

    _insert(
        tree, expanded, name, "",
        name,
        ("CHECK" if unusual else "STATE", f"0x{raw:04X}"),
        level,
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
        field_level = "warn" if value == 0b01 else "info"

        _insert(
            tree, expanded, field_id, name,
            f"b{high}:{low}  {pin}: {ASEL_STATES[value]}",
            (f"{value:02b}", ""),
            field_level,
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
        text = "high" if value else "low"
        _insert(
            tree, expanded, f"{name}.b{bit}", name,
            f"b{bit}  {pin} input {text}",
            (value, ""),
            "info",
        )

    return level


def set_status_indicator(widget, levels):
    """Do not show OK when any required value is unavailable."""
    levels = list(levels)

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
