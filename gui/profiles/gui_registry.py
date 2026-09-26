"""Select GUI presentation using the existing device registry."""

from core.devices.registry import get_device_profile

from .gui_base import (
    GuiProfile,
    VIN,
    IIN,
    TEMP_IC,
    VOUT,
    IOUT,
    POUT,
    TEMP1,
)
from .ltm4673_gui import PROFILE as LTM4673_PROFILE
from .ltm4677_gui import PROFILE as LTM4677_PROFILE
from .ltm4678_gui import PROFILE as LTM4678_PROFILE


GUI_PROFILES = {
    profile.model: profile
    for profile in (
        LTM4673_PROFILE,
        LTM4677_PROFILE,
        LTM4678_PROFILE,
    )
}


FALLBACK_PROFILE = GuiProfile(
    model="Generic",
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
    ),
)


def get_gui_profile(device):
    special_id = getattr(device, "special_id", None)

    if (
        isinstance(special_id, int)
        and not isinstance(special_id, bool)
    ):
        model, _, _ = get_device_profile(special_id)
        if model in GUI_PROFILES:
            return GUI_PROFILES[model]

    model = getattr(device, "name", "")
    return GUI_PROFILES.get(model, FALLBACK_PROFILE)
