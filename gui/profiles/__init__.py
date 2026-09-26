"""Model-specific GUI presentation profiles."""

from .gui_base import (
    GuiProfile,
    TelemetryField,
    available_telemetry,
)
from .gui_registry import get_gui_profile


__all__ = [
    "GuiProfile",
    "TelemetryField",
    "available_telemetry",
    "get_gui_profile",
]
