"""PMBus constants -- builds effective register map per device.

Uses base map + device-specific overlays from core.devices.
"""
from core.devices.base import BASE_REGISTER_MAP, BASE_READ_ONLY, BASE_GLOBAL_CMDS
from core.devices.registry import get_device_profile, DEVICE_REGISTRY


class Cmd:
    """Standard PMBus + common MFR command codes."""
    PAGE                   = 0x00
    OPERATION              = 0x01
    ON_OFF_CONFIG          = 0x02
    CLEAR_FAULTS           = 0x03
    WRITE_PROTECT          = 0x10
    STORE_USER_ALL         = 0x15
    RESTORE_USER_ALL       = 0x16
    CAPABILITY             = 0x19
    VOUT_MODE              = 0x20
    VOUT_COMMAND           = 0x21
    VOUT_MAX               = 0x24
    VOUT_MARGIN_HIGH       = 0x25
    VOUT_MARGIN_LOW        = 0x26
    VOUT_TRANSITION_RATE   = 0x27
    FREQUENCY_SWITCH       = 0x33
    VIN_ON                 = 0x35
    VIN_OFF                = 0x36
    IOUT_CAL_GAIN          = 0x38
    VOUT_OV_FAULT_LIMIT    = 0x40
    VOUT_OV_FAULT_RESPONSE = 0x41
    VOUT_OV_WARN_LIMIT     = 0x42
    VOUT_UV_WARN_LIMIT     = 0x43
    VOUT_UV_FAULT_LIMIT    = 0x44
    VOUT_UV_FAULT_RESPONSE = 0x45
    IOUT_OC_FAULT_LIMIT    = 0x46
    IOUT_OC_FAULT_RESPONSE = 0x47
    IOUT_OC_WARN_LIMIT     = 0x4A
    IOUT_UC_FAULT_LIMIT    = 0x4B
    IOUT_UC_FAULT_RESPONSE = 0x4C
    OT_FAULT_LIMIT         = 0x4F
    OT_FAULT_RESPONSE      = 0x50
    OT_WARN_LIMIT          = 0x51
    UT_WARN_LIMIT          = 0x52
    UT_FAULT_LIMIT         = 0x53
    UT_FAULT_RESPONSE      = 0x54
    VIN_OV_FAULT_LIMIT     = 0x55
    VIN_OV_FAULT_RESPONSE  = 0x56
    VIN_OV_WARN_LIMIT      = 0x57
    VIN_UV_WARN_LIMIT      = 0x58
    VIN_UV_FAULT_LIMIT     = 0x59
    VIN_UV_FAULT_RESPONSE  = 0x5A
    POWER_GOOD_ON          = 0x5E
    POWER_GOOD_OFF         = 0x5F
    TON_DELAY              = 0x60
    TON_RISE               = 0x61
    TON_MAX_FAULT_LIMIT    = 0x62
    TON_MAX_FAULT_RESPONSE = 0x63
    TOFF_DELAY             = 0x64
    TOFF_FALL              = 0x65
    TOFF_MAX_WARN_LIMIT    = 0x66
    STATUS_BYTE            = 0x78
    STATUS_WORD            = 0x79
    STATUS_VOUT            = 0x7A
    STATUS_IOUT            = 0x7B
    STATUS_INPUT           = 0x7C
    STATUS_TEMPERATURE     = 0x7D
    STATUS_CML             = 0x7E
    STATUS_MFR_SPECIFIC    = 0x80
    READ_VIN               = 0x88
    READ_IIN               = 0x89
    READ_VOUT              = 0x8B
    READ_IOUT              = 0x8C
    READ_TEMPERATURE_1     = 0x8D
    READ_TEMPERATURE_2     = 0x8E
    READ_DUTY_CYCLE        = 0x94
    READ_FREQUENCY         = 0x95
    READ_POUT              = 0x96
    READ_PIN               = 0x97
    PMBUS_REVISION         = 0x98
    MFR_ID                 = 0x99
    MFR_MODEL              = 0x9A
    MFR_REVISION           = 0x9B
    MFR_SPECIAL_ID         = 0xE7
    MFR_COMMON             = 0xEF


def build_register_map(special_id=None):
    """Build complete register map for a specific device.

    Returns (register_map, read_only_set, global_cmds_set, name, pages).
    """
    regmap = dict(BASE_REGISTER_MAP)
    ro = set(BASE_READ_ONLY)
    gl = set(BASE_GLOBAL_CMDS)
    name, pages, extras = get_device_profile(special_id)
    if extras:
        regmap.update(extras.get('register_overrides', {}))
        ro |= extras.get('read_only_extra', set())
        gl |= extras.get('global_cmds_extra', set())
    return regmap, ro, gl, name, pages


# backward compat: default map uses LTM4673
REGISTER_MAP, READ_ONLY_CMDS, GLOBAL_CMDS, _, _ = build_register_map(0x4480)

KNOWN_DEVICES = {}
for _id, info in DEVICE_REGISTRY.items():
    KNOWN_DEVICES[_id] = (info['name'], info['num_pages'])
# Devices without their own module yet
for _id, _tup in [
    (0x4780, ("LTM4675", 2)), (0x4700, ("LTM4676", 2)),
    (0x4710, ("LTM4676A", 2)), (0x4680, ("LTM4671", 1)),
]:
    if _id not in KNOWN_DEVICES:
        KNOWN_DEVICES[_id] = _tup
