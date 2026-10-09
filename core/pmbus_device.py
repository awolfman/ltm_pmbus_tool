"""Profile-aware PMBus access.

Generic access excludes reserved and special-access commands.
Send Byte actions are explicit and never restored from dumps.
"""

import logging
import math
import threading
import time

from core.bus_factory import create_bus
from core.pmbus_constants import (
    Cmd,
    STATUS_COMMANDS,
    TELEMETRY_COMMANDS,
    build_register_map,
    build_device_metadata,
)
from core.pmbus_formats import decode_value, encode_value
from core.drivers import CP2112AddressNackError


logger = logging.getLogger(__name__)

RETRY_COUNT = 3
PMBUS_PAUSE = 0.01
PAGE_PAUSE = 0.005
READY_TIMEOUT = 1.0
NVM_TIMEOUT = 10.0

BOOTSTRAP_READ_COMMANDS = frozenset({
    Cmd.PAGE,
    Cmd.MFR_SPECIAL_ID,
    Cmd.CAPABILITY,
    Cmd.VOUT_MODE,
})

EXPLICIT_ACTION_NAMES = frozenset({
    "CLEAR_FAULTS",
    "STORE_USER_ALL",
    "RESTORE_USER_ALL",
    "MFR_CLEAR_PEAKS",
    "MFR_COMPARE_USER_ALL",
    "MFR_RESET",
    "MFR_FAULT_LOG_STORE",
    "MFR_FAULT_LOG_RESTORE",
    "MFR_FAULT_LOG_CLEAR",
})

NVM_ACTION_NAMES = EXPLICIT_ACTION_NAMES - {
    "CLEAR_FAULTS",
    "MFR_CLEAR_PEAKS",
}

class TransportDisconnectedError(RuntimeError):
    """Fatal transport loss handled by legacy disconnect handlers."""

class TransportSessionFailedError(TransportDisconnectedError):
    """Transport session is unusable; USB removal is not established."""


def _is_device_disconnected(exc):
    current = exc
    visited = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))

        if getattr(current, "errno", None) == 19:
            return True

        text = str(current).lower()
        if (
            "no such device" in text
            or "device has been disconnected" in text
            or "device disconnected" in text
            or (
                "usb device" in text
                and "disconnected" in text
            )
        ):
            return True

        cause = current.__cause__
        context = current.__context__

        if cause is not None:
            current = cause
        else:
            current = context

    return False

class PMBusDevice:
    _device_locks = {}
    _device_locks_guard = threading.Lock()

    def __init__(self, bus_num, address, special_id=None):
        if (
            isinstance(address, bool)
            or not isinstance(address, int)
            or not 0x08 <= address <= 0x77
        ):
            raise ValueError("Expected a 7-bit address in 0x08..0x77")

        self.bus_num = bus_num
        self.address = address
        self.addr = address
        self._expected_id = special_id
        self.special_id = special_id
        self._raw_bus_instance = None

        with self._device_locks_guard:
            self._lock = self._device_locks.setdefault(
                (bus_num, address), threading.RLock()
            )

        self.last_error = None
        self.last_errors = {}
        self._reset_profile()

    def _reset_profile(self):
        self.name = "Unknown"
        self.revision = "?"
        self.num_pages = 1
        self.capability = None
        self.pmbus_revision = None
        self.vout_exp = {}
        self.vout_mode = {}
        self._identified = False
        self._identifying = False
        self._page = None

        (
            self._regmap,
            self._read_only,
            self._global_cmds,
            _,
            _,
        ) = build_register_map(None)

        self._metadata = build_device_metadata(None)
        self._generic_write_blocked = set(self._regmap)

    @property
    def pages(self):
        return self.num_pages

    @property
    def id(self):
        return self.special_id

    @property
    def identified(self):
        return self._identified

    @property
    def generic_write_blocked(self):
        return frozenset(self._generic_write_blocked)

    def _error(self, operation, cmd, error, quiet=False):
        message = (
            f"{operation}: bus={self.bus_num}, "
            f"addr=0x{self.address:02X}, "
            f"page={self._page}, cmd=0x{cmd:02X}: {error}"
        )
        self.last_error = message
        self.last_errors[(operation, self._page, cmd)] = message
        if quiet:
            logger.debug(message)
        else:
            logger.warning(message)

    def _get_bus(self):
        if self._raw_bus_instance is None:
            self._raw_bus_instance = create_bus(self.bus_num)
        return self._raw_bus_instance

    def transport_failed(self):
        """Return whether the active transport session is unusable."""
        bus = self._raw_bus_instance

        if bus is None:
            return False

        checker = getattr(bus, "session_failed", None)

        if not callable(checker):
            return False

        return bool(checker())

    def _raise_if_transport_lost(self, exc, operation, cmd):
        """Propagate fatal transport errors before any retry."""
        if isinstance(exc, TransportDisconnectedError):
            self._page = None
            raise exc

        if _is_device_disconnected(exc):
            self._page = None
            raise TransportDisconnectedError(
                "USB-I2C adapter disconnected during "
                f"{operation} at command 0x{cmd:02X}"
            ) from exc

        if self.transport_failed():
            self._page = None
            raise TransportSessionFailedError(
                "Transport session is unusable during "
                f"{operation} at command 0x{cmd:02X}. "
                f"Original error: {exc}. "
                "Further operations require an explicit reconnect."
            ) from exc

    def reset_bus(self):
        with self._lock:
            self._page = None
            try:
                reset = getattr(self._get_bus(), "reset_bus", None)
                if not callable(reset):
                    return False
                reset()
                return True
            except Exception as exc:
                self._error("reset_bus", Cmd.PAGE, exc)
                return False

    def supports(self, cmd, size=None):
        info = self._regmap.get(cmd)
        return info is not None and (
            size is None or info[1] == size
        )

    def command_code(self, name):
        for cmd, info in self._regmap.items():
            if info[0] == name:
                return cmd
        return None

    def can_read_register(self, cmd, size=None):
        info = self._regmap.get(cmd)
        if info is None or info[1] not in {"byte", "word"}:
            return False
        if size is not None and info[1] != size:
            return False
        if cmd in self._metadata["special_access"]:
            return False
        if self._identified or self._identifying:
            return True
        return cmd in BOOTSTRAP_READ_COMMANDS

    def _can_read_register(self, cmd, size):
        return self.can_read_register(cmd, size)

    def apply_dump_write_plan(self, records):
        """Apply a validated dump plan with baseline and readback.

        No rollback and no NVM store are performed.
        The input records are not modified.
        """
        report = {
            "ok": False,
            "verified": [],
            "attempted": [],
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Dump preflight"

            try:
                validated = self.validate_dump_write_records(
                    records
                )

                if not validated:
                    raise ValueError(
                        "No selected dump writes"
                    )

                prepared = []

                # Read every baseline before the first write.
                for record in validated:
                    page = record["page"]
                    cmd = record["cmd"]
                    active = (
                        f"Record page {page}, "
                        f"command 0x{cmd:02X}"
                    )

                    current = self.read_register(page, cmd)

                    if current is None:
                        raise OSError(
                            f"Cannot read baseline for "
                            f"page {page}, command 0x{cmd:02X}"
                        )

                    item = dict(record)
                    item["current_raw"] = current
                    prepared.append(item)

                for item in prepared:
                    page = item["page"]
                    cmd = item["cmd"]
                    size = item["size"]
                    target = item["raw"]
                    active = (
                        f"Record page {page}, "
                        f"command 0x{cmd:02X}"
                    )

                    # Recheck immediately before this write.
                    current = self.read_register(page, cmd)

                    if current is None:
                        raise OSError(
                            f"Cannot recheck baseline for "
                            f"page {page}, command 0x{cmd:02X}"
                        )

                    if current != item["current_raw"]:
                        raise ValueError(
                            "Baseline changed before write: "
                            f"page {page}, command 0x{cmd:02X}, "
                            f"expected 0x{item['current_raw']:04X}, "
                            f"received 0x{current:04X}"
                        )

                    # A failed transaction may still have reached
                    # the device. Record the attempt first.
                    report["attempted"].append(dict(item))

                    if not self.write_register(
                        page,
                        cmd,
                        target,
                        size,
                    ):
                        raise OSError(
                            self.last_error
                            or "Register write failed"
                        )

                    actual = self.read_register(page, cmd)

                    if actual is None:
                        raise OSError(
                            "Write attempted, but readback failed"
                        )

                    if actual != target:
                        raise OSError(
                            "Readback mismatch: "
                            f"page {page}, command 0x{cmd:02X}, "
                            f"expected 0x{target:04X}, "
                            f"received 0x{actual:04X}"
                        )

                    verified = dict(item)
                    verified["actual_raw"] = actual
                    report["verified"].append(verified)

                report["ok"] = True
                return report

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"
                return report

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"
                return report

    def apply_vout_write_plan(self, changes):
        """Apply a VOUT_COMMAND-only plan with readback.

        Returns a partial execution report on transport failure.
        No automatic rollback or NVM store is performed.
        """
        plan = [dict(change) for change in changes]

        if not plan:
            raise ValueError("The write plan is empty")

        if getattr(self, "is_demo", False):
            raise ValueError("Demo writes are disabled")

        for change in plan:
            cmd = change.get("cmd")
            info = self._regmap.get(cmd)

            if info is None or info[0] != "VOUT_COMMAND":
                raise ValueError(
                    "This stage permits VOUT_COMMAND only. "
                    "Remove other edits from the plan."
                )

        report = {
            "ok": False,
            "verified": [],
            "attempted": [],
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Preflight"

            try:
                # Validate the entire plan and all baselines before
                # the first configuration write.
                checked = self.check_write_plan(plan)

                if not checked["ok"]:
                    failures = []
                    for item in checked["results"]:
                        if item["status"] != "match":
                            failures.append(
                                f"CH{item['page']} "
                                f"{item['name']}: {item['status']}"
                            )

                    report["error"] = (
                        "Preflight failed. No configuration "
                        "writes were attempted.\n"
                        + "\n".join(failures)
                    )
                    return report

                for change in plan:
                    page = change["page"]
                    cmd = change["cmd"]
                    active = (
                        f"CH{page} {change['name']} "
                        f"0x{cmd:02X}"
                    )

                    # Recheck just before this particular write.
                    current = self.read_register(page, cmd)

                    if current is None:
                        raise OSError(
                            "Cannot read the current value"
                        )

                    if current != change["previous_raw"]:
                        raise ValueError(
                            "Baseline changed before write: "
                            f"expected 0x{change['previous_raw']:04X}, "
                            f"received 0x{current:04X}"
                        )

                    if not self.can_write_register(
                        cmd, change["size"]
                    ):
                        raise ValueError("Write is blocked")

                    # A failed transaction may still have reached
                    # the device. Record the attempt beforehand.
                    report["attempted"].append(dict(change))

                    if not self.write_register(
                        page,
                        cmd,
                        change["new_raw"],
                        change["size"],
                    ):
                        raise OSError(
                            self.last_error
                            or "Register write failed"
                        )

                    actual = self.read_register(page, cmd)

                    if actual is None:
                        raise OSError(
                            "Write attempted, but readback failed"
                        )

                    if actual != change["new_raw"]:
                        raise OSError(
                            "Readback mismatch: "
                            f"expected 0x{change['new_raw']:04X}, "
                            f"received 0x{actual:04X}"
                        )

                    verified = dict(change)
                    verified["actual_raw"] = actual
                    report["verified"].append(verified)

                report["ok"] = True
                return report

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"
                return report

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"
                return report

    def validate_dump_metadata(self, metadata, records):
        """Check imported dump compatibility without device I/O.

        Records must already have passed validate_dump_write_records.
        Metadata is a compatibility declaration, not authentication.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            if getattr(self, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            if not isinstance(metadata, dict):
                raise ValueError("CSV metadata must be a dictionary")

            if metadata.get("device") != self.name:
                raise ValueError(
                    "CSV device model is missing or does not match"
                )

            sid_text = metadata.get("special_id")
            if (
                not isinstance(sid_text, str)
                or not sid_text.lower().startswith("0x")
            ):
                raise ValueError(
                    "CSV Special ID must be hexadecimal with 0x prefix"
                )

            try:
                sid = int(sid_text, 16)
            except ValueError as exc:
                raise ValueError("Invalid CSV Special ID") from exc

            if not 0 <= sid <= 0xFFFF or sid != self.special_id:
                raise ValueError(
                    "CSV Special ID does not match the device"
                )

            # A single exponent in the current CSV format must
            # match every selected L16 target page.
            l16_pages = {
                record["page"]
                for record in records
                if self._regmap[record["cmd"]][2] == "L16"
            }

            if l16_pages:
                exponent_text = metadata.get("vout_mode_exp")
                if not isinstance(exponent_text, str):
                    raise ValueError(
                        "CSV VOUT exponent is missing"
                    )

                try:
                    exponent = int(exponent_text, 10)
                except ValueError as exc:
                    raise ValueError(
                        "Invalid CSV VOUT exponent"
                    ) from exc

                if not -16 <= exponent <= 15:
                    raise ValueError(
                        "CSV VOUT exponent is outside the signed "
                        "five-bit range"
                    )

                for page in sorted(l16_pages):
                    actual = self.vout_exp.get(page)
                    if actual is None or actual != exponent:
                        raise ValueError(
                            f"CSV VOUT exponent does not match "
                            f"target page {page}"
                        )

            return True

    def validate_dump_write_records(self, records, default_page=0):
        """Validate selected dump writes without device I/O.

        Return independent record dictionaries in the original order.
        This does not validate board safety, baselines or write order.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            if getattr(self, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            if not self._valid_page(default_page):
                raise ValueError("Invalid default page")

            validated = []
            seen = set()

            for index, record in enumerate(records, start=1):
                prefix = f"Record {index}"

                if not isinstance(record, dict):
                    raise ValueError(
                        f"{prefix}: expected a record dictionary"
                    )

                cmd = record.get("cmd")
                if (
                    isinstance(cmd, bool)
                    or not isinstance(cmd, int)
                    or not 0 <= cmd <= 0xFF
                ):
                    raise ValueError(
                        f"{prefix}: invalid command code"
                    )

                info = self._regmap.get(cmd)
                if info is None:
                    raise ValueError(
                        f"{prefix}: unknown command 0x{cmd:02X}"
                    )

                name, size, fmt, paged = info
                prefix = f"{prefix} / {name}"

                if size not in {"byte", "word"}:
                    raise ValueError(
                        f"{prefix}: scalar access required"
                    )

                if record.get("size") != size:
                    raise ValueError(
                        f"{prefix}: size mismatch"
                    )

                # Older in-memory callers may omit descriptive
                # fields. If present, they must match the profile.
                if "name" in record and record["name"] != name:
                    raise ValueError(
                        f"{prefix}: name mismatch"
                    )

                if "format" in record and record["format"] != fmt:
                    raise ValueError(
                        f"{prefix}: format mismatch"
                    )

                if "is_paged" in record:
                    scope = record["is_paged"]
                    if (
                        not isinstance(scope, bool)
                        or scope != bool(paged)
                    ):
                        raise ValueError(
                            f"{prefix}: scope mismatch"
                        )

                if record.get("readonly") is not False:
                    raise ValueError(
                        f"{prefix}: not selected as writable"
                    )

                if not self.can_write_register(cmd, size):
                    raise ValueError(
                        f"{prefix}: write blocked by device profile"
                    )

                page = record.get("page", default_page)
                if not self._valid_page(page):
                    raise ValueError(
                        f"{prefix}: invalid page {page!r}"
                    )

                raw = record.get("raw")
                maximum = 0xFF if size == "byte" else 0xFFFF
                if (
                    isinstance(raw, bool)
                    or not isinstance(raw, int)
                    or not 0 <= raw <= maximum
                ):
                    raise ValueError(
                        f"{prefix}: invalid raw value"
                    )

                try:
                    self.validate_register_raw(
                        cmd, raw, size
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"{prefix}: {exc}"
                    ) from exc

                identity = (
                    page if paged else None,
                    cmd,
                )
                if identity in seen:
                    raise ValueError(
                        f"{prefix}: duplicate target register"
                    )
                seen.add(identity)

                prepared = dict(record)
                prepared.update(
                    page=page,
                    name=name,
                    format=fmt,
                    is_paged=bool(paged),
                )
                validated.append(prepared)

            if not validated:
                raise ValueError("No selected dump writes")

            return validated

    def check_write_plan(self, changes):
        """Validate a scalar write plan and check device baselines.

        Performs reads only. Does not modify the supplied plan.
        A successful result is a point-in-time check, not a lock
        against subsequent changes by other software or hardware.
        """
        manual_only = {
            "OPERATION",
            "ON_OFF_CONFIG",
            "WRITE_PROTECT",
        }

        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            validated = []
            seen = set()

            # Validate the entire plan before any device I/O.
            for change in changes:
                cmd = change.get("cmd")
                if (
                    isinstance(cmd, bool)
                    or not isinstance(cmd, int)
                    or not 0 <= cmd <= 0xFF
                ):
                    raise ValueError("Invalid command code")

                info = self._regmap.get(cmd)
                if info is None:
                    raise ValueError(
                        f"Unknown command 0x{cmd:02X}"
                    )

                name, size, fmt, paged = info

                if name in manual_only:
                    raise ValueError(
                        f"{name}: individual write required"
                    )

                if size not in {"byte", "word"}:
                    raise ValueError(
                        f"{name}: scalar access required"
                    )

                if (
                    change.get("name") != name
                    or change.get("size") != size
                    or change.get("format") != fmt
                ):
                    raise ValueError(
                        f"{name}: plan does not match device profile"
                    )

                plan_paged = change.get("is_paged")
                if (
                    not isinstance(plan_paged, bool)
                    or plan_paged != bool(paged)
                ):
                    raise ValueError(
                        f"{name}: register scope mismatch"
                    )

                page = change.get("page")
                if paged:
                    if not self._valid_page(page):
                        raise ValueError(
                            f"{name}: invalid page {page!r}"
                        )
                elif page is not None:
                    raise ValueError(
                        f"{name}: global register must use page=None"
                    )

                identity = (page, cmd)
                if identity in seen:
                    raise ValueError(
                        f"{name}: duplicate plan entry"
                    )
                seen.add(identity)

                if not self.can_read_register(cmd, size):
                    raise ValueError(
                        f"{name}: read unavailable"
                    )

                if not self.can_write_register(cmd, size):
                    raise ValueError(
                        f"{name}: write blocked"
                    )

                maximum = 0xFF if size == "byte" else 0xFFFF

                for field in ("previous_raw", "new_raw"):
                    raw = change.get(field)
                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= maximum
                    ):
                        raise ValueError(
                            f"{name}: invalid {field}"
                        )

                # Copy the fields used during the hardware check.
                validated.append({
                    "page": page,
                    "cmd": cmd,
                    "name": name,
                    "size": size,
                    "previous_raw": change["previous_raw"],
                    "new_raw": change["new_raw"],
                })

            results = []

            for entry in validated:
                page = entry["page"]
                actual = self.read_register(
                    page if page is not None else 0,
                    entry["cmd"],
                )

                result = dict(entry)
                result["actual_raw"] = actual

                if actual is None:
                    result["status"] = "read_error"
                elif actual != entry["previous_raw"]:
                    result["status"] = "conflict"
                else:
                    result["status"] = "match"

                results.append(result)

                # Stop on the first ordinary read failure.
                # TransportDisconnectedError propagates immediately.
                if actual is None:
                    break

            return {
                "ok": (
                    len(results) == len(validated)
                    and all(
                        item["status"] == "match"
                        for item in results
                    )
                ),
                "total": len(validated),
                "checked": len(results),
                "results": results,
            }

    def can_write_register(self, cmd, size=None):
        if not self._identified:
            return False
        info = self._regmap.get(cmd)
        if info is None or info[1] not in {"byte", "word"}:
            return False
        if size is not None and info[1] != size:
            return False
        return cmd not in self._generic_write_blocked

    def _read_transport(
        self, cmd, size, attempts=RETRY_COUNT, quiet=False
    ):
        if size not in {"byte", "word"}:
            raise ValueError("Scalar size must be byte or word")

        method = (
            "read_byte_data" if size == "byte"
            else "read_word_data"
        )
        maximum = 0xFF if size == "byte" else 0xFFFF
        failure = None

        for attempt in range(attempts):
            try:
                bus = self._get_bus()
                value = getattr(bus, method)(
                    self.address, cmd
                )
                time.sleep(PMBUS_PAUSE)

                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= maximum
                ):
                    raise OSError(
                        f"Invalid {size} response: {value!r}"
                    )

                if (
                    getattr(
                        bus,
                        "legacy_error_sentinels",
                        True,
                    )
                    and value == maximum
                ):
                    raise OSError(
                        f"Ambiguous legacy response 0x{maximum:X}"
                    )

                return value

            except CP2112AddressNackError as exc:
                # The CP2112 returned a complete terminal status
                # report. The adapter session remains usable, but
                # this target address did not respond.
                failure = exc
                self._error(
                    f"read_{size}",
                    cmd,
                    exc,
                    quiet=quiet,
                )
                return None

            except Exception as exc:
                self._raise_if_transport_lost(
                    exc, method, cmd
                )

                failure = exc
                if attempt + 1 < attempts:
                    time.sleep(PMBUS_PAUSE)

        self._error(
            f"read_{size}",
            cmd,
            failure,
            quiet=quiet,
        )
        return None

    def read_byte_data(self, cmd, quiet=False):
        with self._lock:
            if not self.can_read_register(cmd, "byte"):
                self._error(
                    "read_byte", cmd,
                    "Register unavailable for generic byte access",
                    quiet=quiet,
                )
                return None
            return self._read_transport(cmd, "byte", quiet=quiet)

    def read_word_data(self, cmd, quiet=False):
        with self._lock:
            if not self.can_read_register(cmd, "word"):
                self._error(
                    "read_word", cmd,
                    "Register unavailable for generic word access",
                    quiet=quiet,
                )
                return None
            return self._read_transport(cmd, "word", quiet=quiet)

    def read_block_data(self, cmd):
        with self._lock:
            if (
                not self._identified
                or not self.supports(cmd, "block")
                or cmd in self._metadata["special_access"]
            ):
                self._error("read_block", cmd, "Block access unavailable")
                return None

            try:
                reader = getattr(
                    self._get_bus(), "read_block_data", None
                )
                if not callable(reader):
                    raise NotImplementedError(
                        "Driver has no SMBus Block Read implementation"
                    )

                payload = reader(self.address, cmd)
                if payload is None or isinstance(payload, int):
                    raise OSError("Invalid block response")
                data = bytes(payload)

                if len(data) > 255:
                    raise OSError("Block exceeds profile maximum")

                expected = self._metadata["block_lengths"].get(cmd)
                if expected is not None and len(data) != expected:
                    raise OSError(
                        f"Expected {expected} bytes, got {len(data)}"
                    )

                allowed = self._metadata[
                    "block_allowed_lengths"
                ].get(cmd)
                if allowed is not None and len(data) not in allowed:
                    raise OSError(
                        f"Unexpected block length {len(data)}"
                    )

                return list(data)

            except Exception as exc:
                self._raise_if_transport_lost(
                    exc, "block read", cmd
                )

                self._error("read_block", cmd, exc)
                return None

    def read_i2c_block_data(self, cmd, length=32):
        # Compatibility name only. Never substitute fixed-length I2C
        # transactions for a genuine SMBus Block Read.
        return self.read_block_data(cmd)

    def _write_transport(self, cmd, value=None, size="byte"):
        try:
            bus = self._get_bus()
            if size == "send":
                result = bus.write_byte(self.address, cmd)
            elif size == "byte":
                result = bus.write_byte_data(
                    self.address, cmd, value
                )
            elif size == "word":
                result = bus.write_word_data(
                    self.address, cmd, value
                )
            else:
                raise ValueError("Unsupported write type")

            if result is False:
                raise OSError("Driver reported write failure")

            time.sleep(PMBUS_PAUSE)
            return True

        except Exception as exc:
            self._raise_if_transport_lost(
                exc, f"write_{size}", cmd
            )

            self._error(f"write_{size}", cmd, exc)
            return False

    def _wait_ready(self, timeout=READY_TIMEOUT):
        if not (self._identified or self._identifying):
            return False

        if not self.supports(Cmd.MFR_COMMON, "byte"):
            self._error(
                "wait_ready", Cmd.MFR_COMMON,
                "Profile has no readiness register",
            )
            return False

        mask = 0x40 if self.name == "LTM4673" else 0x60
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            value = self._read_transport(
                Cmd.MFR_COMMON,
                "byte",
                attempts=1,
                quiet=True,
            )

            if value is not None and value != 0xFF:
                if value & mask == mask:
                    return True
            time.sleep(PMBUS_PAUSE)

        self._error(
            "wait_ready", Cmd.MFR_COMMON,
            f"No unambiguous ready response within {timeout:g} seconds",
        )
        return False

    def _valid_page(self, page):
        return (
            isinstance(page, int)
            and not isinstance(page, bool)
            and 0 <= page < self.num_pages
        )

    def set_page(self, page):
        with self._lock:
            if not (self._identified or self._identifying):
                self._error("set_page", Cmd.PAGE, "Unknown device")
                return False
            if not self._valid_page(page):
                self._error(
                    "set_page", Cmd.PAGE, f"Invalid page {page!r}"
                )
                return False

            self._page = None
            if not self._wait_ready():
                return False

            for _ in range(RETRY_COUNT):
                if not self._write_transport(Cmd.PAGE, page, "byte"):
                    return False
                time.sleep(PAGE_PAUSE)

                current = self._read_transport(
                    Cmd.PAGE, "byte", attempts=1, quiet=True
                )
                if current == page:
                    self._page = page
                    return True

            self._error(
                "set_page", Cmd.PAGE,
                f"PAGE {page} verification failed",
            )
            return False

    def validate_register_raw(self, cmd, value, size):
        """Validate a scalar write value without device I/O.

        Checks access, representation and profile value rules.
        Does not establish board safety or command effectiveness.
        Returns the original value without normalization.
        """
        if (
            isinstance(cmd, bool)
            or not isinstance(cmd, int)
            or not 0 <= cmd <= 0xFF
        ):
            raise ValueError("Invalid command code")

        if size not in {"byte", "word"}:
            raise ValueError("Scalar size must be byte or word")

        if not self.can_write_register(cmd, size):
            raise ValueError("Register write is blocked")

        maximum = 0xFF if size == "byte" else 0xFFFF
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= maximum
        ):
            raise ValueError(
                f"Raw value must be in 0x00..0x{maximum:X}"
            )

        rule = self._metadata.get(
            "write_value_rules", {}
        ).get(cmd)

        if rule is None:
            return value

        name = self._regmap[cmd][0]

        if value in rule.get("requires_context", ()):
            raise ValueError(
                f"{name}: "
                + rule.get(
                    "context_error",
                    "Current device configuration is required.",
                )
            )

        if value not in rule["allowed"]:
            raise ValueError(
                f"{name}: "
                + rule.get(
                    "invalid_error",
                    "Value rejected by device profile.",
                )
            )

        return value

    def _write_checked(self, cmd, value, size):
        with self._lock:
            try:
                self.validate_register_raw(
                    cmd, value, size
                )
            except ValueError as exc:
                error_cmd = (
                    cmd
                    if isinstance(cmd, int)
                    and not isinstance(cmd, bool)
                    and 0 <= cmd <= 0xFF
                    else 0
                )
                self._error(
                    "write_register", error_cmd, exc
                )
                return False



            if self._regmap[cmd][3]:
                page = self._page
                if page is None or not self.set_page(page):
                    return False
            elif not self._wait_ready():
                return False

            return self._write_transport(cmd, value, size)

    def write_byte_data(self, cmd, val):
        return self._write_checked(cmd, val, "byte")

    def write_word_data(self, cmd, val):
        return self._write_checked(cmd, val, "word")

    def send_byte(self, cmd):
        with self._lock:
            action = self._metadata["send_commands"].get(cmd)
            if (
                not self._identified
                or action is None
                or action[0] not in EXPLICIT_ACTION_NAMES
            ):
                self._error("send_byte", cmd, "Action unavailable")
                return False

            name, paged = action
            if paged:
                page = self._page
                if page is None or not self.set_page(page):
                    return False
            elif not self._wait_ready():
                return False

            if not self._write_transport(cmd, size="send"):
                return False

            if name in NVM_ACTION_NAMES:
                time.sleep(0.05)
                if not self._wait_ready(NVM_TIMEOUT):
                    return False

            if name in {"RESTORE_USER_ALL", "MFR_RESET"}:
                self._page = None

            return True

    def _rb(self, cmd):
        return self.read_byte_data(cmd)

    def _rw(self, cmd):
        return self.read_word_data(cmd)

    def _wb(self, cmd, val):
        return self.write_byte_data(cmd, val)

    def _ww(self, cmd, val):
        return self.write_word_data(cmd, val)

    def _send(self, cmd):
        return self.send_byte(cmd)

    def _rblock(self, cmd, length=32):
        return self.read_i2c_block_data(cmd, length)

    def identify(self):
        with self._lock:
            self._reset_profile()
            self.special_id = None
            self.last_error = None
            self.last_errors.clear()

            try:
                sid = self.read_word_data(
                    Cmd.MFR_SPECIAL_ID, quiet=True
                )
                if sid is None:
                    return False

                if sid in {0x0000, 0xFFFF}:
                    self._error(
                        "identify", Cmd.MFR_SPECIAL_ID,
                        f"No recognized ID response: 0x{sid:04X}",
                        quiet=True,
                    )
                    return False

                self.special_id = sid
                if self._expected_id is not None:
                    if (
                        sid & 0xFFF0
                        != self._expected_id & 0xFFF0
                    ):
                        raise ValueError(
                            f"Expected ID 0x{self._expected_id:04X}, "
                            f"received 0x{sid:04X}"
                        )

                regmap, ro, globals_, name, pages = (
                    build_register_map(sid)
                )
                if name is None:
                    self._error(
                        "identify", Cmd.MFR_SPECIAL_ID,
                        f"Unknown ID 0x{sid:04X}",
                        quiet=True,
                    )
                    return False

                self._regmap = regmap
                self._read_only = ro
                self._global_cmds = globals_
                self._metadata = build_device_metadata(sid)
                self._generic_write_blocked = (
                    set(ro)
                    | self._metadata["no_generic_write"]
                )
                self.name = name
                self.num_pages = pages
                self._identifying = True

                for page in range(pages):
                    if not self.set_page(page):
                        raise OSError(f"Cannot select PAGE {page}")

                    mode = self._rb(Cmd.VOUT_MODE)
                    if mode is None or mode >> 5 != 0:
                        raise OSError(
                            f"Invalid VOUT_MODE on PAGE {page}: {mode!r}"
                        )

                    self.vout_mode[page] = mode

                    exponent = mode & 0x1F
                    if exponent & 0x10:
                        exponent -= 0x20

                    expected = self._metadata["vout_exponent"]
                    if expected is not None and exponent != expected:
                        raise ValueError(
                            f"VOUT exponent {exponent}, expected {expected}"
                        )
                    self.vout_exp[page] = exponent

                self.capability = self._rb(Cmd.CAPABILITY)
                self.pmbus_revision = self._rb(Cmd.PMBUS_REVISION)
                self.revision = f"ID revision 0x{sid & 0x0F:X}"

                if not self.set_page(0):
                    raise OSError("Cannot restore PAGE 0")

                self._identified = True
                return True

            except TransportDisconnectedError:
                self.name = "Unknown"
                self._identified = False
                self._page = None
                self._generic_write_blocked = set(self._regmap)
                raise

            except Exception as exc:
                self._error(
                    "identify",
                    Cmd.MFR_SPECIAL_ID,
                    exc,
                )
                self.name = "Unknown"
                self._identified = False
                self._page = None
                self._generic_write_blocked = set(self._regmap)
                return False

            finally:
                self._identifying = False

    def _read_scalar(self, cmd):
        if not self.can_read_register(cmd):
            return None
        size = self._regmap[cmd][1]
        if size == "byte":
            return self._rb(cmd)
        return self._rw(cmd)

    def _decode(self, cmd, raw, page, custom=False):
        if raw is None:
            return None

        info = self._regmap[cmd]
        if custom and cmd in self._metadata["custom_formats"]:
            signed, scale, _ = self._metadata["custom_formats"][cmd]
            bits = 8 if info[1] == "byte" else 16
            value = raw
            if signed and value & (1 << (bits - 1)):
                value -= 1 << bits
            return value * scale

        exponent = self.vout_exp.get(page)
        if info[2] == "L16" and exponent is None:
            self._error("decode", cmd, "VOUT exponent unavailable")
            return None

        return decode_value(
            raw, info[2], exponent if exponent is not None else -13
        )

    def read_register(self, page, cmd):
        with self._lock:
            if not self._identified or not self.can_read_register(cmd):
                self._error(
                    "read_register", cmd,
                    "Generic register read unavailable",
                )
                return None

            if self._regmap[cmd][3] and not self.set_page(page):
                return None
            return self._read_scalar(cmd)

    def _config_commands(self, paged):
        result = []
        for cmd, info in sorted(self._regmap.items()):
            if info[3] != paged:
                continue
            if not self.can_read_register(cmd):
                continue
            if cmd in STATUS_COMMANDS or cmd in TELEMETRY_COMMANDS:
                continue
            if cmd in self._read_only or cmd == Cmd.PAGE:
                continue
            result.append(cmd)
        return result

    def _read_config(self, paged, page=0):
        with self._lock:
            if not self._identified:
                return {}

            ready = not paged or self.set_page(page)
            result = {}
            for cmd in self._config_commands(paged):
                name, _, fmt, _ = self._regmap[cmd]
                raw = self._read_scalar(cmd) if ready else None
                result[name] = {
                    "raw": raw,
                    "value": self._decode(cmd, raw, page),
                    "cmd": cmd,
                    "fmt": fmt,
                    "writable": self.can_write_register(cmd),
                }
            return result

    def get_vout_mode_text(self):
        modes = getattr(self, "vout_mode", {})
        if not modes:
            return "N/A"

        lines = []
        for page in sorted(modes):
            mode = modes[page]
            format_code = (mode >> 5) & 0x07

            if format_code == 0:
                exponent = mode & 0x1F
                if exponent & 0x10:
                    exponent -= 0x20

                lines.append(
                    f"CH{page}: 0x{mode:02X}, Linear, "
                    f"exponent {exponent}"
                )
            else:
                lines.append(
                    f"CH{page}: 0x{mode:02X}, "
                    f"format code {format_code}"
                )

        return "\n".join(lines)

    def read_global_config(self):
        return self._read_config(False)

    def read_channel_config(self, page=0):
        result = self._read_config(True, page)
        if self._identified and self.supports(Cmd.WRITE_PROTECT, "byte"):
            raw = self.read_register(0, Cmd.WRITE_PROTECT)
            result["WRITE_PROTECT"] = {
                "raw": raw,
                "value": raw,
                "cmd": Cmd.WRITE_PROTECT,
                "fmt": "BYTE",
                "writable": self.can_write_register(Cmd.WRITE_PROTECT),
            }
        return result

    def _read_named_telemetry(self, names, page):
        result = dict.fromkeys(names)
        with self._lock:
            if not self._identified or not self.set_page(page):
                return result

            for label, name in names.items():
                cmd = self.command_code(name)
                if cmd is None or not self.can_read_register(cmd):
                    continue
                raw = self._read_scalar(cmd)
                result[label] = self._decode(
                    cmd, raw, page, custom=True
                )
        return result

    def read_global_telemetry(self):
        names = {
            "VIN": "READ_VIN",
            "IIN": "READ_IIN",
            "TEMP_IC": "READ_TEMPERATURE_2",
        }
        if self.name in {"LTM4673", "LTM4678"}:
            names["PIN"] = "READ_PIN"
        result = self._read_named_telemetry(names, 0)
        result.setdefault("PIN", None)
        return result

    def read_channel_telemetry(self, page=0):
        names = {
            "VOUT": "READ_VOUT",
            "IOUT": "READ_IOUT",
            "POUT": "READ_POUT",
            "TEMP1": "READ_TEMPERATURE_1",
        }

        if self.name == "LTM4677":
            if self.command_code("MFR_READ_IIN") is not None:
                names["IIN"] = "MFR_READ_IIN"

            if self.command_code("READ_DUTY_CYCLE") is not None:
                names["DUTY"] = "READ_DUTY_CYCLE"

        elif self.name == "LTM4678":
            if self.command_code("READ_FREQUENCY") is not None:
                names["FREQ"] = "READ_FREQUENCY"

            if self.command_code("READ_DUTY_CYCLE") is not None:
                names["DUTY"] = "READ_DUTY_CYCLE"

        result = self._read_named_telemetry(names, page)

        for label in (
            "IIN",
            "PIN",
            "DUTY",
            "FREQ",
        ):
            result.setdefault(label, None)

        return result

    def read_telemetry(self, page=0):
        with self._lock:
            result = self.read_global_telemetry()
            for label, value in self.read_channel_telemetry(page).items():
                if label not in result or value is not None:
                    result[label] = value
            return result

    def read_global_status(self):
        result = {
            "STATUS_INPUT": None,
            "STATUS_CML": None,
        }

        with self._lock:
            global_commands = (
                ("MFR_COMMON", Cmd.MFR_COMMON, "byte"),
                ("MFR_PADS", Cmd.MFR_PADS, "word"),
            )

            available = {}
            for name, cmd, size in global_commands:
                info = self._regmap.get(cmd)
                if (
                    info is not None
                    and info[1] == size
                    and not info[3]
                    and self.can_read_register(cmd, size)
                ):
                    result[name] = None
                    available[name] = cmd

            if not self._identified:
                return result

            if "MFR_COMMON" in available:
                common = self._read_scalar(
                    available["MFR_COMMON"]
                )
                result["MFR_COMMON"] = common

                if self.name == "LTM4673":
                    if common is None or not (common & 0x40):
                        return result

            if "MFR_PADS" in available:
                result["MFR_PADS"] = self._read_scalar(
                    available["MFR_PADS"]
                )

            if self.can_read_register(Cmd.STATUS_INPUT, "byte"):
                result["STATUS_INPUT"] = self._rb(
                    Cmd.STATUS_INPUT
                )

            if self.can_read_register(Cmd.STATUS_CML, "byte"):
                result["STATUS_CML"] = self._rb(
                    Cmd.STATUS_CML
                )

        return result

    def read_channel_status(self, page=0):
        commands = {
            "STATUS_WORD": Cmd.STATUS_WORD,
            "STATUS_VOUT": Cmd.STATUS_VOUT,
            "STATUS_IOUT": Cmd.STATUS_IOUT,
            "STATUS_TEMPERATURE": Cmd.STATUS_TEMPERATURE,
            "STATUS_MFR": Cmd.STATUS_MFR_SPECIFIC,
        }
        result = dict.fromkeys(commands)
        with self._lock:
            if not self._identified or not self.set_page(page):
                return result
            for name, cmd in commands.items():
                result[name] = self._read_scalar(cmd)
        return result

    def read_status(self, page=0):
        with self._lock:
            result = self.read_global_status()
            result.update(self.read_channel_status(page))
            return result

    def write_register(self, page, cmd_code, raw_value, size):
        with self._lock:
            # Validate before PAGE selection or any device I/O.
            try:
                self.validate_register_raw(
                    cmd_code, raw_value, size
                )
            except ValueError as exc:
                error_cmd = (
                    cmd_code
                    if isinstance(cmd_code, int)
                    and not isinstance(cmd_code, bool)
                    and 0 <= cmd_code <= 0xFF
                    else 0
                )
                self._error(
                    "write_register", error_cmd, exc
                )
                return False

            if self._regmap[cmd_code][3]:
                if not self.set_page(page):
                    return False

            return self._write_checked(
                cmd_code, raw_value, size
            )

    def encode_register_value(self, page, cmd, value, fmt):
        """Validate and encode a value without device I/O.

        Checks transport representation, not board-specific limits.
        Custom formats require a separate engineering encoder.
        """
        info = self._regmap.get(cmd)

        if info is None or not self.can_write_register(cmd):
            raise ValueError("Register write is blocked")

        name, size, actual_fmt, paged = info

        if fmt != actual_fmt:
            raise ValueError("Register format mismatch")

        if paged and not self._valid_page(page):
            raise ValueError(f"Invalid page {page!r}")

        if cmd in self._metadata.get("custom_formats", {}):
            raise ValueError(
                f"{name}: custom engineering encoding is unavailable; "
                "use an explicit raw write"
            )

        if isinstance(value, bool):
            raise ValueError("Boolean values are not accepted")

        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Value must be finite")

        if fmt in {"BYTE", "RAW"}:
            if not number.is_integer():
                raise ValueError("Raw value must be integral")
            raw = int(number)

        elif fmt == "L16":
            exponent = self.vout_exp.get(page)
            if exponent is None:
                raise ValueError("VOUT exponent unavailable")

            maximum_value = 0xFFFF * (2.0 ** exponent)
            if not 0 <= number <= maximum_value:
                raise ValueError("L16 value out of range")

            raw = encode_value(number, fmt, exponent)

        elif fmt == "L11":
            maximum_value = 1023 * 2 ** 15
            if not -maximum_value <= number <= maximum_value:
                raise ValueError("L11 value out of range")

            raw = encode_value(number, fmt)

        else:
            raise ValueError(f"Unsupported format {fmt}")

        maximum_raw = 0xFF if size == "byte" else 0xFFFF

        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or not 0 <= raw <= maximum_raw
        ):
            raise ValueError("Encoded value out of range")

        return self.validate_register_raw(
            cmd, raw, size
        )

    def write_val(self, page, cmd, value, fmt):
        try:
            raw = self.encode_register_value(
                page, cmd, value, fmt
            )

        except (ValueError, TypeError, OverflowError) as exc:
            self._error("write_val", cmd, exc)
            return False

        return self.write_register(
            page,
            cmd,
            raw,
            self._regmap[cmd][1],
        )

    def _send_named(self, name):
        for cmd, action in self._metadata["send_commands"].items():
            if action[0] == name:
                return self.send_byte(cmd)
        self._error("send_named", 0, f"Unsupported action {name}")
        return False

    def clear_faults(self, page=None):
        with self._lock:
            if not self._identified:
                return False
            action = self._metadata["send_commands"].get(Cmd.CLEAR_FAULTS)
            if action is None:
                return False
            if page is not None and not self._valid_page(page):
                return False
            if not action[1]:
                return self.send_byte(Cmd.CLEAR_FAULTS)

            previous = self._page
            success = True
            pages = range(self.num_pages) if page is None else [page]
            for selected in pages:
                if not self.set_page(selected):
                    success = False
                    break
                if not self.send_byte(Cmd.CLEAR_FAULTS):
                    success = False
                    break

            if previous is not None and not self.set_page(previous):
                success = False
            return success

    def store_user_all(self):
        return self._send_named("STORE_USER_ALL")

    def restore_user_all(self):
        with self._lock:
            if not self._send_named("RESTORE_USER_ALL"):
                return False
            return self.identify()

    def read_full_dump(self, page=0):
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")
            if not self.set_page(page):
                raise OSError(f"Cannot select PAGE {page}")

            result = []
            for cmd, info in sorted(self._regmap.items()):
                if not self.can_read_register(cmd):
                    continue

                name, size, fmt, paged = info
                raw = self._read_scalar(cmd)
                result.append({
                    "page": page,
                    "cmd": cmd,
                    "name": name,
                    "size": size,
                    "format": fmt,
                    "is_paged": paged,
                    "raw": raw,
                    "decoded": self._decode(cmd, raw, page),
                    "readonly": not self.can_write_register(cmd, size),
                })
            return result

def diagnostic_read_ltm4677_current(device):
    """Read-only LTM4677 current measurement diagnostic."""

    print()
    print("LTM4677 current diagnostic")
    print(f"model      = {device.name}")
    print(f"address    = 0x{device.address:02X}")
    print(
        f"special_id = 0x{device.special_id:04X}"
        if device.special_id is not None
        else "special_id = None"
    )
    print(f"pages      = {device.num_pages}")
    print()

    registers = (
        ("READ_VOUT", 0x8B, "word"),
        ("READ_IOUT", 0x8C, "word"),
        ("IOUT_CAL_GAIN", 0x38, "word"),
        ("STATUS_WORD", 0x79, "word"),
        ("STATUS_MFR_SPECIFIC", 0x80, "byte"),
    )

    for page in range(device.num_pages):
        print(f"PAGE {page}")
        print("-" * 72)

        for name, cmd, size in registers:
            try:
                raw = device.read_register(page, cmd)

                if raw is None:
                    print(
                        f"{name:22} raw=None"
                    )
                    continue

                try:
                    decoded = device._decode(
                        cmd,
                        raw,
                        page,
                        custom=True,
                    )
                except Exception as exc:
                    decoded = f"decode error: {exc}"

                width = 2 if size == "byte" else 4

                print(
                    f"{name:22} "
                    f"raw=0x{raw:0{width}X}  "
                    f"decoded={decoded!r}"
                )

            except Exception as exc:
                print(
                    f"{name:22} "
                    f"READ ERROR: {exc}"
                )

        try:
            telemetry = device.read_channel_telemetry(page)

            print()
            print("public telemetry:")
            for key in (
                "VOUT",
                "IOUT",
                "POUT",
                "TEMP1",
                "IIN",
                "DUTY",
            ):
                print(
                    f"{key:22} "
                    f"{telemetry.get(key)!r}"
                )

        except Exception as exc:
            print(f"public telemetry ERROR: {exc}")

        print()

def diagnostic_repeat_ltm4677_current(device, count=10):
    """Repeat read-only LTM4677 current and channel diagnostics."""

    paged_commands = (
        ("OPERATION", 0x01),
        ("ON_OFF_CONFIG", 0x02),
        ("READ_VOUT", 0x8B),
        ("READ_IOUT", 0x8C),
        ("IOUT_CAL_GAIN", 0x38),
        ("MFR_IOUT_CAL_GAIN_TC", 0xF6),
        ("MFR_CHAN_CONFIG", 0xD0),
        ("MFR_GPIO_PROPAGATE", 0xD2),
        ("MFR_PWM_MODE", 0xD4),
        ("MFR_GPIO_RESPONSE", 0xD5),
        ("STATUS_WORD", 0x79),
        ("STATUS_MFR_SPECIFIC", 0x80),
    )

    global_commands = (
        ("WRITE_PROTECT", 0x10),
        ("MFR_CONFIG_ALL", 0xD1),
    )

    print()
    print("Repeated LTM4677 current diagnostic")
    print(f"count={count}")

    print()
    print("GLOBAL")
    for name, cmd in global_commands:
        try:
            raw = device.read_register(0, cmd)

            if raw is None:
                print(f"{name:24} raw=None")
            else:
                print(f"{name:24} raw=0x{raw:02X}")

        except Exception as exc:
            print(f"{name:24} ERROR: {exc}")

    for page in range(device.num_pages):
        print()
        print(f"PAGE {page}")

        for sample in range(count):
            values = []

            for name, cmd in paged_commands:
                try:
                    raw = device.read_register(page, cmd)

                    if raw is None:
                        text = "None"
                    else:
                        width = 2 if cmd == 0x80 else 4
                        text = f"0x{raw:0{width}X}"

                    values.append(f"{name}={text}")

                except Exception as exc:
                    values.append(
                        f"{name}=ERROR:{exc}"
                    )

            print(f"{sample + 1:02d}: " + " ".join(values))
