"""GUI demo using actual device register profiles.

No USB or system I2C access is permitted.
All-channel output control is simulated in memory.
Other configuration writes and NVM actions remain disabled.
Values are synthetic, not hardware configuration defaults.
"""

import random
import time

from core.pmbus_constants import (
    Cmd,
    build_register_map,
    build_device_metadata,
)
from core.pmbus_device import PMBusDevice
from core.pmbus_formats import encode_value


class DemoDevice(PMBusDevice):
    """Profile-aware demo with limited in-memory output control."""

    is_demo = True
    demo_output_control_enabled = True

    def __init__(self, address, special_id):
        super().__init__(
            bus_num=1,
            address=address,
            special_id=special_id,
        )

        regmap, _, _, name, pages = build_register_map(special_id)
        metadata = build_device_metadata(special_id)

        if name is None:
            raise ValueError(
                f"Unknown demo profile: 0x{special_id:04X}"
            )

        self._demo_id = special_id
        self._demo_page = 0
        self._demo_values = {}
        self._demo_rng = random.Random()
        self._demo_last_update = None
        self._demo_signals = {}

        exponent = metadata["vout_exponent"]
        if exponent is None:
            raise ValueError("Demo profile has no VOUT exponent")

        # Synthetic values only. Not defaults for actual hardware.
        engineering_values = {
            "READ_VIN": 12.0,
            "READ_IIN": 1.0,
            "READ_PIN": 12.0,
            "READ_VOUT": 1.0,
            "READ_IOUT": 2.0,
            "READ_POUT": 2.0,
            "READ_TEMPERATURE_1": 40.0,
            "READ_TEMPERATURE_2": 42.0,
            "READ_DUTY_CYCLE": 10.0,
            "READ_FREQUENCY": 500.0,
            "MFR_READ_IIN": 0.5,
            "VOUT_COMMAND": 1.0,
            "VOUT_MAX": 1.2,
            "VOUT_MARGIN_HIGH": 1.05,
            "VOUT_MARGIN_LOW": 0.95,
            "VIN_ON": 4.5,
            "VIN_OFF": 4.0,
            "FREQUENCY_SWITCH": 500.0,
            "TON_DELAY": 1.0,
            "TON_RISE": 3.0,
            "TOFF_DELAY": 1.0,
        }

        for cmd, descriptor in regmap.items():
            reg_name, size, fmt, paged = descriptor
            if size not in {"byte", "word"}:
                continue

            raw = 0

            if reg_name in engineering_values:
                value = engineering_values[reg_name]
                if fmt == "L16":
                    raw = encode_value(value, fmt, exponent)
                elif fmt == "L11":
                    raw = encode_value(value, fmt)

            if cmd == Cmd.OPERATION:
                raw = 0x80
            elif cmd == Cmd.ON_OFF_CONFIG:
                raw = 0x1F
            elif name == "LTM4673" and reg_name == "MFR_PAGE_FF_MASK":
                raw = 0x0F
            elif cmd == Cmd.MFR_SPECIAL_ID:
                raw = special_id
            elif cmd == Cmd.CAPABILITY:
                raw = 0xB0
            elif cmd == Cmd.VOUT_MODE:
                raw = exponent & 0x1F
            elif cmd == Cmd.PMBUS_REVISION:
                raw = 0x22
            elif cmd == Cmd.MFR_COMMON:
                raw = 0xFC
            elif cmd == Cmd.MFR_PADS:
                raw = 0x0333 if name == "LTM4678" else 0
            elif cmd == Cmd.STATUS_MFR_SPECIFIC:
                raw = 0x18 if name == "LTM4673" else 0

            for page in range(pages) if paged else (None,):
                self._demo_values[(page, cmd)] = raw

        if not self.identify():
            raise RuntimeError(
                self.last_error or "Demo identification failed"
            )

        if self.name == "LTM4677":
            for page in range(self.num_pages):
                self._demo_apply_operation(
                    page,
                    self._demo_values[(page, Cmd.OPERATION)],
                )

    def _demo_walk(self, key, nominal, span, step):
        """Bounded random walk around a synthetic nominal value."""
        previous = self._demo_signals.get(key, nominal)

        value = (
            previous
            + self._demo_rng.gauss(0.0, step)
            + 0.08 * (nominal - previous)
        )
        value = max(nominal - span, min(nominal + span, value))

        self._demo_signals[key] = value
        return value

    def _demo_set_measurement(self, name, value, page=0):
        """Update a supported demo measurement in raw register form."""
        cmd = self.command_code(name)
        if cmd is None:
            return

        _, size, fmt, paged = self._regmap[cmd]
        if size not in {"byte", "word"}:
            return

        if fmt == "L16":
            exponent = self.vout_exp.get(page)
            if exponent is None:
                return
            raw = encode_value(value, fmt, exponent)
        elif fmt == "L11":
            raw = encode_value(value, fmt)
        else:
            return

        maximum = 0xFF if size == "byte" else 0xFFFF
        if not isinstance(raw, int) or not 0 <= raw <= maximum:
            raise ValueError(
                f"Invalid synthetic value for {name}: {raw!r}"
            )

        storage_page = page if paged else None
        self._demo_values[(storage_page, cmd)] = raw

    def _update_demo_telemetry(self):
        """Update a shared snapshot no more often than every 0.5 s."""
        now = time.monotonic()

        if (
            self._demo_last_update is not None
            and now - self._demo_last_update < 0.5
        ):
            return

        self._demo_last_update = now

        vin = self._demo_walk(
            "VIN", nominal=12.0, span=0.25, step=0.04
        )
        iin = self._demo_walk(
            "IIN", nominal=1.0, span=0.20, step=0.025
        )
        temp_ic = self._demo_walk(
            "TEMP_IC", nominal=42.0, span=3.0, step=0.20
        )

        self._demo_set_measurement("READ_VIN", vin)
        self._demo_set_measurement(
            "READ_TEMPERATURE_2", temp_ic
        )

        # LTM4673 input readings are paged in the register map.
        # Populate each page with the same shared input measurement.
        # For non-paged registers the same storage slot is updated.
        for page in range(self.num_pages):
            self._demo_set_measurement(
                "READ_IIN", iin, page
            )
            self._demo_set_measurement(
                "READ_PIN", vin * iin, page
            )

        for page in range(self.num_pages):
            nominal_vout = 1.0 + page * 0.1
            nominal_iout = 2.0 + page * 0.5

            vout = self._demo_walk(
                (page, "VOUT"),
                nominal=nominal_vout,
                span=0.015,
                step=0.002,
            )
            iout = self._demo_walk(
                (page, "IOUT"),
                nominal=nominal_iout,
                span=0.30,
                step=0.04,
            )
            temperature = self._demo_walk(
                (page, "TEMP1"),
                nominal=40.0 + page * 2.0,
                span=3.0,
                step=0.20,
            )
            frequency = self._demo_walk(
                (page, "FREQ"),
                nominal=500.0,
                span=5.0,
                step=0.8,
            )

            operation = self._demo_values.get(
                (page, Cmd.OPERATION), 0x80
            )
            output_off = (operation & 0xC0) in {0x00, 0x40}

            if output_off:
                vout = 0.0
                iout = 0.0

            # Synthetic buck-like duty estimate, not a hardware model.
            duty = 100.0 * vout / vin

            self._demo_set_measurement(
                "READ_VOUT", vout, page
            )
            self._demo_set_measurement(
                "READ_IOUT", iout, page
            )
            self._demo_set_measurement(
                "READ_POUT", vout * iout, page
            )
            self._demo_set_measurement(
                "READ_TEMPERATURE_1", temperature, page
            )
            self._demo_set_measurement(
                "READ_DUTY_CYCLE", duty, page
            )
            self._demo_set_measurement(
                "READ_FREQUENCY", frequency, page
            )
            self._demo_set_measurement(
                "MFR_READ_IIN",
                0.0 if output_off else iin / self.num_pages,
                page,
            )

    def read_global_telemetry(self):
        with self._lock:
            self._update_demo_telemetry()
            return super().read_global_telemetry()

    def read_channel_telemetry(self, page=0):
        with self._lock:
            self._update_demo_telemetry()
            return super().read_channel_telemetry(page)

    def _get_bus(self):
        raise RuntimeError(
            "DemoDevice must never access a hardware bus"
        )

    def identify(self):
        result = super().identify()
        if result:
            self.revision = (
                f"DEMO / synthetic values / "
                f"ID revision 0x{self._demo_id & 0x0F:X}"
            )
        return result

    def _read_transport(
        self, cmd, size, attempts=1, quiet=False
    ):
        if size not in {"byte", "word"}:
            raise ValueError("Scalar size must be byte or word")

        if cmd == Cmd.PAGE:
            return self._demo_page

        descriptor = self._regmap.get(cmd)
        if descriptor is None or descriptor[1] != size:
            self._error(
                f"read_{size}",
                cmd,
                "Demo register unavailable",
                quiet=quiet,
            )
            return None

        if (
            self.name == "LTM4673"
            and self._demo_page == 0xFF
            and descriptor[3]
        ):
            self._error(
                f"read_{size}",
                cmd,
                "Paged reads with PAGE 0xFF are invalid on LTM4673",
                quiet=quiet,
            )
            return None

        if descriptor[3]:
            page = (
                0 if self._demo_page == 0xFF
                else self._demo_page
            )
        else:
            page = None

        return self._demo_values.get((page, cmd))

    def _write_transport(self, cmd, value=None, size="byte"):
        """Simulate service PAGE writes and all-channel OPERATION."""
        with self._lock:
            supported = self.name in {
                "LTM4673", "LTM4677", "LTM4678"
            }

            if cmd == Cmd.PAGE and size == "byte":
                broadcast = (
                    supported
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value == 0xFF
                )

                if not self._valid_page(value) and not broadcast:
                    return False

                self._demo_page = value
                return True

            if (
                supported
                and self.demo_output_control_enabled
                and cmd == Cmd.OPERATION
                and size == "byte"
                and self._demo_page == 0xFF
            ):
                self.validate_register_raw(cmd, value, size)

                if self.name == "LTM4673":
                    mask = self._demo_values[(None, 0xE4)]
                    pages = [
                        page
                        for page in range(self.num_pages)
                        if mask & (1 << page)
                    ]
                else:
                    pages = list(range(self.num_pages))

                # Validate every affected channel before mutation.
                for page in pages:
                    config = self._demo_values[
                        (page, Cmd.ON_OFF_CONFIG)
                    ]
                    self._validate_all_operation_pair(config, value)

                for page in pages:
                    self._demo_values[
                        (page, Cmd.OPERATION)
                    ] = value
                    self._demo_apply_operation(page, value)

                self._demo_last_update = None
                self._update_demo_telemetry()
                return True

            self._error(
                f"write_{size}",
                cmd,
                "Demo supports all-channel output control only",
            )
            return False

    def can_write_register(self, cmd, size=None):
        return (
            self.name in {"LTM4673", "LTM4677", "LTM4678"}
            and self.demo_output_control_enabled
            and cmd == Cmd.OPERATION
            and super().can_write_register(cmd, size)
        )

    def _demo_apply_operation(self, page, operation):
        """Update synthetic output status without transition timing."""
        off = (operation & 0xC0) in {0x00, 0x40}

        status_key = (page, Cmd.STATUS_WORD)
        status = self._demo_values.get(status_key, 0)

        if off:
            status |= 0x0040 | 0x0800
        else:
            status &= ~(0x0040 | 0x0800)

        self._demo_values[status_key] = status

        # Preserve the established LTM4677 pads simulation.
        # Do not copy its bit layout to other models.
        if self.name != "LTM4677":
            return

        pads_key = (None, Cmd.MFR_PADS)
        pads = self._demo_values.get(pads_key, 0)
        pads |= 0x0030
        power_good = 1 << (8 + page)

        if off:
            pads &= ~power_good
        else:
            pads |= power_good

        self._demo_values[pads_key] = pads

    @property
    def generic_write_blocked(self):
        return frozenset(self._regmap)

    def send_byte(self, cmd):
        self._error(
            "send_byte",
            cmd,
            "Read-only demo; actions are disabled",
        )
        return False

    def read_block_data(self, cmd):
        self._error(
            "read_block",
            cmd,
            "Block data is not simulated",
        )
        return None

    def reset_bus(self):
        return False


def create_demo_devices():
    """Return three independent demo devices without opening hardware."""
    return [
        DemoDevice(address=0x40, special_id=0x0236),
        DemoDevice(address=0x41, special_id=0x47B8),
        DemoDevice(address=0x4F, special_id=0x4101),
    ]
