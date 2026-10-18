"""Profile-aware PMBus access.

Generic access excludes reserved and special-access commands.
Send Byte actions are explicit and never restored from dumps.
"""

import logging
import math
import threading
import time
from copy import deepcopy

from core.bus_factory import create_bus
from core.pmbus_constants import (
    Cmd,
    STATUS_COMMANDS,
    TELEMETRY_COMMANDS,
    build_register_map,
    build_device_metadata,
    compare_address_register_raw,
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

class ControlWriteBlockedError(ValueError):
    """A control write was rejected before configuration transport."""

    def __init__(
        self, message, *, page, cmd, previous_raw=None
    ):
        super().__init__(message)
        self.page = page
        self.cmd = cmd
        self.previous_raw = previous_raw

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

    def start_common_timing_probe(self, duration=30.0):
        """Observe global MFR_COMMON using the existing device lock.

        No PAGE selection and no configuration writes.
        Run inside the GUI process, not in a second adapter session.
        """
        if not self._identified:
            raise ValueError("An identified device is required.")

        masks = {
            "LTM4673": 0x40,
            "LTM4677": 0x70,
            "LTM4678": 0x70,
        }

        if self.name not in masks:
            raise ValueError(
                "This probe supports LTM4673, LTM4677 "
                "and LTM4678 only."
            )

        mask = masks[self.name]
        model = self.name

        existing = getattr(self, "_common_probe_thread", None)
        if existing is not None and existing.is_alive():
            raise RuntimeError("MFR_COMMON probe is already running.")

        self._common_probe_epoch = time.perf_counter()
        self._common_probe_write = None

        def worker():
            previous = object()
            tracked_event = None
            ready_reported = False
            saw_not_ready = False
            last_sample_time = None
            maximum_gap = 0.0
            deadline = time.perf_counter() + duration

            print(
                f"\n[COMMON] Probe started. "
                f"Model={model}; mask=0x{mask:02X}. "
                "Global reads only.",
                flush=True,
            )

            try:
                while time.perf_counter() < deadline:
                    # The same lock protects GUI transactions.
                    with self._lock:
                        started = time.perf_counter()
                        raw = self._read_transport(
                            Cmd.MFR_COMMON,
                            "byte",
                            attempts=1,
                            quiet=True,
                        )
                        finished = time.perf_counter()
                        event = self._common_probe_write

                    if last_sample_time is not None:
                        maximum_gap = max(
                            maximum_gap,
                            finished - last_sample_time,
                        )
                    last_sample_time = finished

                    valid = (
                        isinstance(raw, int)
                        and not isinstance(raw, bool)
                        and 0 <= raw < 0xFF
                    )

                    ready = (
                        valid and (raw & mask) == mask
                    )

                    command_ready = (
                        valid and (raw & 0x60) == 0x60
                    )

                    if event is not tracked_event:
                        tracked_event = event
                        ready_reported = False
                        saw_not_ready = False

                    if raw != previous:
                        raw_text = (
                            f"0x{raw:02X}" if valid else repr(raw)
                        )
                        missing = (
                            f"0x{mask & ~raw:02X}"
                            if valid else "unknown"
                        )
                        extra = (
                            f" command_ready_60={command_ready}"
                            if model in {"LTM4677", "LTM4678"}
                            else ""
                        )

                        print(
                            f"[COMMON] "
                            f"t={finished - self._common_probe_epoch:.6f}s "
                            f"raw={raw_text} "
                            f"ready_{mask:02X}={ready}"
                            f"{extra} "
                            f"missing={missing} "
                            f"read={(finished - started) * 1000:.3f}ms",
                            flush=True,
                        )
                        previous = raw

                    if event is not None:
                        if valid and not ready:
                            saw_not_ready = True

                        if ready and not ready_reported:
                            elapsed = finished - event["started"]
                            print(
                                f"[COMMON] PAGE {event['page']} "
                                f"{event['name']}="
                                f"0x{event['value']:02X}: "
                                f"first READY after write start "
                                f"{elapsed * 1000:.3f}ms; "
                                f"observed NOT_READY={saw_not_ready}",
                                flush=True,
                            )
                            ready_reported = True

                    time.sleep(0.01)

            except TransportDisconnectedError as exc:
                print(
                    f"[COMMON] Transport stopped: {exc}",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"[COMMON] Probe failed: {exc}",
                    flush=True,
                )
            finally:
                if tracked_event is not None and not ready_reported:
                    print(
                        "[COMMON] No READY sample observed "
                        "after the latest write during this probe.",
                        flush=True,
                    )

                print(
                    f"[COMMON] Probe stopped. "
                    f"Maximum sample gap={maximum_gap * 1000:.3f}ms",
                    flush=True,
                )

        thread = threading.Thread(
            target=worker,
            name="ltm4677-common-probe",
            daemon=True,
        )
        self._common_probe_thread = thread
        thread.start()

    def apply_ltm4677_frequency(self, target_raw):
        """Write a supported frequency only after observing both OFF.

        No automatic output control, unlock, rollback or NVM store.
        PAGE service writes are possible.
        """
        report = {
            "ok": False,
            "target_raw": target_raw,
            "previous_raw": None,
            "actual_raw": None,
            "attempted": False,
            "unchanged": False,
            "protection": None,
            "transport": None,
            "outputs": None,
            "error": None,
            "disconnect": None,
        }

        supported = frozenset({
            0x0000,
            0xF3E8,
            0xFABC,
            0xFB52,
            0xFBE8,
            0x023F,
            0x028A,
            0x02EE,
            0x03E8,
        })

        with self._lock:
            active = "Frequency offline validation"

            try:
                if not self._identified:
                    raise RuntimeError("Device is not identified")

                if getattr(self, "is_demo", False):
                    raise ValueError("Demo writes are disabled")

                if self.name != "LTM4677":
                    raise ValueError(
                        "Frequency executor supports LTM4677 only"
                    )

                if (
                    isinstance(target_raw, bool)
                    or not isinstance(target_raw, int)
                    or target_raw not in supported
                ):
                    raise ValueError(
                        "Unsupported LTM4677 frequency encoding"
                    )

                cmd = self.command_code("FREQUENCY_SWITCH")
                info = self._regmap.get(cmd)

                if (
                    info is None
                    or info[1] != "word"
                    or info[3]
                    or not self.can_read_register(cmd, "word")
                ):
                    raise ValueError(
                        "FREQUENCY_SWITCH: global word access unavailable"
                    )

                def accept_stage(field, stage):
                    report[field] = stage
                    if stage["ok"]:
                        return True
                    report["error"] = stage["error"]
                    report["disconnect"] = stage["disconnect"]
                    return False

                active = "Check frequency write protection"
                protection = self.inspect_ltm4677_restore_protection(
                    0x00
                )
                if not accept_stage("protection", protection):
                    return report

                if protection["current_raw"] != 0x00:
                    raise ValueError(
                        "WRITE_PROTECT must be 0x00 for this operation. "
                        "No automatic unlock will be performed."
                    )

                active = "Check non-PEC transport"
                # This operation does not write MFR_CONFIG_ALL.
                # Zero here requests inspection for a non-PEC path.
                transport = self.inspect_ltm4677_restore_transport(
                    0x00
                )
                if not accept_stage("transport", transport):
                    return report

                active = "Read current frequency"
                previous = self.read_register(None, cmd)

                if (
                    isinstance(previous, bool)
                    or not isinstance(previous, int)
                    or not 0 <= previous <= 0xFFFF
                ):
                    raise OSError("Invalid frequency baseline")

                report["previous_raw"] = previous

                active = "Check both outputs OFF"
                outputs = self.inspect_ltm4677_outputs_off()
                if not accept_stage("outputs", outputs):
                    return report

                if previous == target_raw:
                    report["actual_raw"] = previous
                    report["unchanged"] = True
                    report["ok"] = True
                    return report

                active = "Wait for frequency write readiness"
                if not self._wait_ready():
                    raise OSError(
                        self.last_error or "Device is not ready"
                    )

                active = "Recheck frequency baseline"
                current = self.read_register(None, cmd)
                if (
                    isinstance(current, bool)
                    or not isinstance(current, int)
                    or not 0 <= current <= 0xFFFF
                ):
                    raise OSError("Invalid frequency baseline recheck")

                if current != previous:
                    raise ValueError(
                        "Frequency baseline changed before write"
                    )

                # Recheck after readiness and baseline operations.
                active = "Recheck frequency write protection"
                protection = self.inspect_ltm4677_restore_protection(
                    0x00
                )
                if not accept_stage("protection", protection):
                    return report

                if protection["current_raw"] != 0x00:
                    raise ValueError(
                        "WRITE_PROTECT changed before frequency write"
                    )

                active = "Recheck non-PEC transport"
                transport = self.inspect_ltm4677_restore_transport(
                    0x00
                )
                if not accept_stage("transport", transport):
                    return report

                active = "Recheck both outputs OFF"
                outputs = self.inspect_ltm4677_outputs_off()
                if not accept_stage("outputs", outputs):
                    return report

                active = "Write FREQUENCY_SWITCH"
                report["attempted"] = True

                # Dedicated guarded path. Generic writes stay blocked.
                if not self._write_transport(
                    cmd, target_raw, "word"
                ):
                    raise OSError(
                        self.last_error or "Frequency write failed"
                    )

                active = "Read back FREQUENCY_SWITCH"
                actual = self.read_register(None, cmd)

                if (
                    isinstance(actual, bool)
                    or not isinstance(actual, int)
                    or not 0 <= actual <= 0xFFFF
                ):
                    raise OSError("Invalid frequency readback")

                report["actual_raw"] = actual

                if actual != target_raw:
                    raise OSError(
                        "Frequency readback mismatch: "
                        f"requested 0x{target_raw:04X}, "
                        f"received 0x{actual:04X}"
                    )

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

    def inspect_ltm4677_outputs_off(self):
        """Observe OFF status on every LTM4677 channel.

        No output-control, configuration or NVM commands.
        Reading paged status may perform service PAGE writes.

        Success is a point-in-time observation only.
        """
        report = {
            "ok": False,
            "execution_ready": False,
            "channels": [],
            "reason": None,
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Output-state offline validation"

            try:
                if not self._identified:
                    raise RuntimeError("Device is not identified")

                if getattr(self, "is_demo", False):
                    raise ValueError(
                        "Demo output-state inspection is disabled"
                    )

                if self.name != "LTM4677":
                    raise ValueError(
                        "Output-state inspection supports "
                        "LTM4677 only"
                    )

                if self.num_pages != 2:
                    raise ValueError(
                        "Expected two LTM4677 pages"
                    )

                cmd = self.command_code("STATUS_WORD")
                info = self._regmap.get(cmd)

                if (
                    info is None
                    or info[1] != "word"
                    or not info[3]
                    or not self.can_read_register(cmd, "word")
                ):
                    raise ValueError(
                        "STATUS_WORD: paged word read unavailable"
                    )

                for page in range(self.num_pages):
                    active = f"Check OFF state on PAGE {page}"
                    raw = self.read_register(page, cmd)

                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= 0xFFFF
                        or raw == 0xFFFF
                    ):
                        report["reason"] = "output_state_unknown"
                        raise OSError(
                            f"PAGE {page}: invalid or ambiguous "
                            f"STATUS_WORD response: {raw!r}"
                        )

                    off = bool(raw & 0x0040)

                    report["channels"].append({
                        "page": page,
                        "status_word_raw": raw,
                        "off": off,
                    })

                    if not off:
                        report["reason"] = "outputs_not_off"
                        raise ValueError(
                            f"PAGE {page}: OFF is not asserted. "
                            "Turn off both LTM4677 outputs "
                            "before writing configuration. "
                            "No configuration write was attempted "
                            "by this check."
                        )

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["reason"] = "output_state_unknown"
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

    @staticmethod
    def validate_ltm4677_operation_target(
        on_off_config_raw, operation_raw
    ):
        """Validate one channel's target pair without hardware I/O.

        This checks final-state compatibility only.
        It does not authorize writes or define transition ordering.
        """
        for name, raw in (
            ("ON_OFF_CONFIG", on_off_config_raw),
            ("OPERATION", operation_raw),
        ):
            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= 0xFF
            ):
                raise ValueError(
                    f"{name} must be an integer byte"
                )

        supported_configs = frozenset({
            0x16, 0x17, 0x1E, 0x1F,
        })

        if on_off_config_raw not in supported_configs:
            raise ValueError(
                "Unsupported LTM4677 ON_OFF_CONFIG "
                f"0x{on_off_config_raw:02X}"
            )

        pmbus_on_off_enabled = bool(on_off_config_raw & 0x08)

        allowed_operations = frozenset({
            0x80, 0x98, 0xA8,
        })

        if pmbus_on_off_enabled:
            allowed_operations = allowed_operations | {
                0x00, 0x40,
            }

        if operation_raw not in allowed_operations:
            raise ValueError(
                f"LTM4677 OPERATION 0x{operation_raw:02X} "
                "is incompatible with "
                f"ON_OFF_CONFIG 0x{on_off_config_raw:02X}"
            )

        return {
            "on_off_config_raw": on_off_config_raw,
            "operation_raw": operation_raw,
            "pmbus_on_off_enabled": pmbus_on_off_enabled,
            "execution_ready": False,
        }

    def _analyze_ltm4677_control_channel(self, channel):
        """Analyze a freshly validated comparison channel without I/O.

        Private helper for compare_ltm4677_control_raw.
        Hypothetical pairs are not observed hardware states.
        No command acceptance, timing or transition safety is inferred.
        """
        report = {
            "status": "unresolved",
            "analysis_complete": False,
            "changed_commands": [],
            "variants": [],
            "errors": [],
            "basis": "ltm4677_rev_b_tables_21_22",
            "datasheet_pages": [85, 86],
            "hardware_state_verified": False,
            "transition_safety_verified": False,
            "blockers": ["control_transition_unresolved"],
            "execution_ready": False,
            "write_order": None,
            "write_sequence": None,
        }

        if channel["comparison_complete"] is not True:
            report["errors"].append(
                "Complete baseline and target control pairs required"
            )
            return report

        for field in ("baseline_policy", "target_policy"):
            policy = channel[field]
            if policy is None or policy["supported"] is not True:
                report["errors"].append(
                    f"{field}: supported pair required"
                )

        if report["errors"]:
            return report

        registers = {
            item["cmd"]: item
            for item in channel["registers"]
        }
        changed = [
            cmd for cmd in (Cmd.OPERATION, Cmd.ON_OFF_CONFIG)
            if registers[cmd]["changed"] is True
        ]
        report["changed_commands"] = changed

        if not changed:
            report["status"] = "no_control_changes"
            report["analysis_complete"] = True
            return report

        def pair_check(config, operation):
            supported = True
            error = None
            try:
                self.validate_ltm4677_operation_target(
                    config, operation
                )
            except ValueError as exc:
                supported = False
                error = str(exc)

            return {
                "on_off_config_raw": config,
                "operation_raw": operation,
                "supported": supported,
                "error": error,
            }

        target_config = registers[Cmd.ON_OFF_CONFIG]["target_raw"]
        target_operation = registers[Cmd.OPERATION]["target_raw"]

        if len(changed) == 1:
            cmd = changed[0]
            report["variants"].append({
                "case": "single_register_change",
                "hypothetical_first_command": registers[cmd]["name"],
                "intermediate_pair": None,
                "target_pair": pair_check(
                    target_config, target_operation
                ),
                "execution_ready": False,
                "write_order": None,
                "write_sequence": None,
            })
            report["status"] = "single_register_change"
        else:
            baseline_config = registers[
                Cmd.ON_OFF_CONFIG
            ]["current_raw"]
            baseline_operation = registers[
                Cmd.OPERATION
            ]["current_raw"]

            for case, first, config, operation in (
                (
                    "operation_first",
                    "OPERATION",
                    baseline_config,
                    target_operation,
                ),
                (
                    "on_off_config_first",
                    "ON_OFF_CONFIG",
                    target_config,
                    baseline_operation,
                ),
            ):
                report["variants"].append({
                    "case": case,
                    "hypothetical_first_command": first,
                    "intermediate_pair": pair_check(
                        config, operation
                    ),
                    "target_pair": pair_check(
                        target_config, target_operation
                    ),
                    "execution_ready": False,
                    "write_order": None,
                    "write_sequence": None,
                })

            report["status"] = "hypothetical_pairs_checked"

        # Complete means the encoding checks were performed.
        # An unsupported intermediate pair does not make the
        # analysis incomplete and does not alter RAW comparison.
        report["analysis_complete"] = True
        return report

    def compare_ltm4677_control_raw(
        self, target_records, baseline_records
    ):
        """Compare saved control pairs without I/O or write ordering."""
        report = {
            "status": "unresolved",
            "comparison_complete": False,
            "control_registers_unchanged": None,
            "channels": [],
            "errors": [],
            "blockers": ["control_transition_unresolved"],
            "execution_ready": False,
            "write_order": None,
        }

        with self._lock:
            if (
                not self._identified
                or self.name != "LTM4677"
                or self.num_pages != 2
            ):
                report["errors"].append(
                    "Expected an identified two-page LTM4677"
                )
                return report

            descriptors = {
                0x01: ("OPERATION", "byte", "BYTE", True),
                0x02: ("ON_OFF_CONFIG", "byte", "BYTE", True),
            }
            names = {item[0] for item in descriptors.values()}

            for cmd, descriptor in descriptors.items():
                if self._regmap.get(cmd) != descriptor:
                    report["errors"].append(
                        "Unexpected LTM4677 control descriptor"
                    )
                    return report

            def collect(records, raw_field, source):
                if not isinstance(records, (list, tuple)):
                    raise ValueError(
                        f"{source}: expected a list or tuple"
                    )

                collected = {}
                for item in records:
                    if not isinstance(item, dict):
                        raise ValueError(f"{source}: invalid record")

                    cmd = item.get("cmd")
                    if (
                        isinstance(cmd, bool)
                        or not isinstance(cmd, int)
                        or not 0 <= cmd <= 0xFF
                    ):
                        raise ValueError(
                            f"{source}: invalid command"
                        )

                    name = item.get("name")
                    known_name = (
                        isinstance(name, str) and name in names
                    )
                    if cmd not in descriptors and not known_name:
                        continue

                    if cmd not in descriptors:
                        raise ValueError(
                            f"{source}: control command mismatch"
                        )

                    descriptor = descriptors[cmd]
                    if (
                        name != descriptor[0]
                        or item.get("size") != descriptor[1]
                        or (
                            "format" in item
                            and item["format"] != descriptor[2]
                        )
                        or (
                            "is_paged" in item
                            and item["is_paged"] is not True
                        )
                    ):
                        raise ValueError(
                            f"{source}: control descriptor mismatch"
                        )

                    page = item.get("page")
                    if (
                        isinstance(page, bool)
                        or not isinstance(page, int)
                        or page not in (0, 1)
                    ):
                        raise ValueError(
                            f"{source}: invalid control page"
                        )

                    identity = (page, cmd)
                    if identity in collected:
                        raise ValueError(
                            f"{source}: duplicate control record"
                        )

                    raw = item.get(raw_field)
                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= 0xFF
                    ):
                        raise ValueError(
                            f"{source}: invalid {raw_field}"
                        )
                    collected[identity] = raw

                return collected

            try:
                target = collect(target_records, "raw", "target")
                baseline = collect(
                    baseline_records, "current_raw", "baseline"
                )
            except ValueError as exc:
                report["errors"].append(str(exc))
                return report

            def policy_check(values, page, source):
                config = values[(page, 0x02)]
                operation = values[(page, 0x01)]
                try:
                    self.validate_ltm4677_operation_target(
                        config, operation
                    )
                except ValueError as exc:
                    return {
                        "supported": False,
                        "error": f"{source}: {exc}",
                    }
                return {"supported": True, "error": None}

            all_complete = True
            all_supported = True
            all_unchanged = True

            for page in range(2):
                missing = [
                    f"{source}: PAGE {page} missing {descriptor[0]}"
                    for source, values in (
                        ("target", target), ("baseline", baseline)
                    )
                    for cmd, descriptor in descriptors.items()
                    if (page, cmd) not in values
                ]
                complete = not missing
                all_complete = all_complete and complete
                report["errors"].extend(missing)

                channel = {
                    "page": page,
                    "comparison_complete": complete,
                    "registers": [],
                    "baseline_policy": None,
                    "target_policy": None,
                    "unchanged": None,
                    "execution_ready": False,
                    "write_order": None,
                }

                for cmd, descriptor in descriptors.items():
                    identity = (page, cmd)
                    current = baseline.get(identity)
                    desired = target.get(identity)
                    channel["registers"].append({
                        "cmd": cmd,
                        "name": descriptor[0],
                        "current_raw": current,
                        "target_raw": desired,
                        "changed": (
                            current != desired
                            if identity in baseline and identity in target
                            else None
                        ),
                    })

                if complete:
                    channel["unchanged"] = all(
                        not item["changed"]
                        for item in channel["registers"]
                    )
                    all_unchanged = (
                        all_unchanged and channel["unchanged"]
                    )

                    for source, values in (
                        ("baseline", baseline),
                        ("target", target),
                    ):
                        check = policy_check(values, page, source)
                        channel[f"{source}_policy"] = check
                        if not check["supported"]:
                            all_supported = False
                            report["errors"].append(
                                f"PAGE {page}: {check['error']}"
                            )

                channel["transition_analysis"] = (
                    self._analyze_ltm4677_control_channel(channel)
                )
                report["channels"].append(channel)

            report["comparison_complete"] = all_complete
            if all_complete:
                report["control_registers_unchanged"] = all_unchanged
                if all_supported:
                    report["status"] = (
                        "unchanged" if all_unchanged else "changed"
                    )

            return report

    def inspect_ltm4677_restore_transport(self, target_raw):
        """Check restore compatibility with a non-PEC transport.

        No configuration writes, PAGE selection or NVM commands.
        Reject a PEC-requiring target before hardware access.

        This is a point-in-time check, not execution authorization.
        """
        report = {
            "ok": False,
            "execution_ready": False,
            "target_raw": target_raw,
            "current_raw": None,
            "target_pec_required": None,
            "current_pec_required": None,
            "reason": None,
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Transport offline validation"

            try:
                if not self._identified:
                    raise RuntimeError("Device is not identified")

                if getattr(self, "is_demo", False):
                    raise ValueError("Demo writes are disabled")

                if self.name != "LTM4677":
                    raise ValueError(
                        "Restore transport inspection supports "
                        "LTM4677 only"
                    )

                if (
                    isinstance(target_raw, bool)
                    or not isinstance(target_raw, int)
                    or not 0 <= target_raw <= 0xFF
                ):
                    raise ValueError(
                        "Target MFR_CONFIG_ALL must be "
                        "an integer byte"
                    )

                cmd = self.command_code("MFR_CONFIG_ALL")
                info = self._regmap.get(cmd)

                if (
                    info is None
                    or info[1] != "byte"
                    or info[3]
                    or not self.can_read_register(cmd, "byte")
                ):
                    raise ValueError(
                        "MFR_CONFIG_ALL: global byte read unavailable"
                    )

                report["target_pec_required"] = bool(
                    target_raw & 0x04
                )

                if report["target_pec_required"]:
                    report["reason"] = "pec_required_but_unsupported"
                    raise ValueError(
                        "Target MFR_CONFIG_ALL requires PEC. "
                        "Full restoration with the current "
                        "non-PEC transport is blocked."
                    )

                active = "Read current MFR_CONFIG_ALL"
                current = self.read_register(None, cmd)

                if (
                    isinstance(current, bool)
                    or not isinstance(current, int)
                    or not 0 <= current <= 0xFF
                ):
                    raise OSError(
                        "Invalid MFR_CONFIG_ALL response"
                    )

                report["current_raw"] = current
                report["current_pec_required"] = bool(
                    current & 0x04
                )

                if report["current_pec_required"]:
                    report["reason"] = "pec_required_but_unsupported"
                    raise ValueError(
                        "Current MFR_CONFIG_ALL requires PEC. "
                        "Do not start paged baseline reads "
                        "with the non-PEC transport."
                    )

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

    def inspect_ltm4677_restore_protection(self, target_raw):
        """Read protection state without changing configuration.

        Accepted WRITE_PROTECT values are an application policy.
        A successful check is not authorization to execute a restore.
        The WP pin and protection must be rechecked before writes.
        """
        report = {
            "ok": False,
            "execution_ready": False,
            "target_raw": target_raw,
            "current_raw": None,
            "mfr_common_raw": None,
            "wp_asserted": None,
            "temporary_unlock_required": None,
            "error": None,
            "disconnect": None,
        }

        accepted_values = frozenset({
            0x00, 0x20, 0x40, 0x80,
        })

        with self._lock:
            active = "Protection offline validation"

            try:
                if not self._identified:
                    raise RuntimeError("Device is not identified")

                if getattr(self, "is_demo", False):
                    raise ValueError("Demo writes are disabled")

                if self.name != "LTM4677":
                    raise ValueError(
                        "Protection inspection supports LTM4677 only"
                    )

                if (
                    isinstance(target_raw, bool)
                    or not isinstance(target_raw, int)
                    or target_raw not in accepted_values
                ):
                    raise ValueError(
                        "Target WRITE_PROTECT is outside "
                        "the accepted application policy"
                    )

                commands = {}

                for name in ("MFR_COMMON", "WRITE_PROTECT"):
                    cmd = self.command_code(name)
                    info = self._regmap.get(cmd)

                    if (
                        info is None
                        or info[1] != "byte"
                        or info[3]
                        or not self.can_read_register(cmd, "byte")
                    ):
                        raise ValueError(
                            f"{name}: global byte read unavailable"
                        )

                    commands[name] = cmd

                active = "Read current MFR_COMMON"
                common = self.read_register(
                    None, commands["MFR_COMMON"]
                )

                if (
                    isinstance(common, bool)
                    or not isinstance(common, int)
                    or not 0 <= common <= 0xFF
                ):
                    raise OSError("Invalid MFR_COMMON response")

                report["mfr_common_raw"] = common
                report["wp_asserted"] = bool(common & 0x01)

                if report["wp_asserted"]:
                    raise ValueError(
                        "Hardware WP is asserted. "
                        "Full restoration is blocked."
                    )

                active = "Read current WRITE_PROTECT"
                current = self.read_register(
                    None, commands["WRITE_PROTECT"]
                )

                if (
                    isinstance(current, bool)
                    or not isinstance(current, int)
                    or not 0 <= current <= 0xFF
                ):
                    raise OSError("Invalid WRITE_PROTECT response")

                report["current_raw"] = current

                if current not in accepted_values:
                    raise ValueError(
                        "Current WRITE_PROTECT is outside "
                        "the accepted application policy"
                    )

                report["temporary_unlock_required"] = (
                    current != 0x00
                )
                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

    def plan_ltm4677_full_restore(self, metadata, records):
        """Build an offline restore grouping, never a write sequence.

        Every candidate is preserved exactly once in groups.
        Group order and record order are for display only.
        Unknown restore policies remain unresolved.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            if getattr(self, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            if self.name != "LTM4677" or self.num_pages != 2:
                raise ValueError(
                    "Restore grouping supports two-page LTM4677 only"
                )

            source_metadata = deepcopy(metadata)
            source_records = deepcopy(list(records))
            inspection = self.inspect_full_dump_restore(
                source_metadata, source_records
            )

            def record_order(item):
                page = item["page"]
                return (
                    -1 if page is None else page,
                    item["cmd"],
                )

            groups = {
                "frequency": [],
                "transport": [],
                "addresses": [],
                "controls": [],
                "final_protection": [],
                "user_data": [],
                "unresolved": [],
            }

            # Explicit identities prevent a changed profile descriptor
            # from silently receiving an old restore policy.
            policies = {
                "FREQUENCY_SWITCH": (
                    0x33, "word", False, "frequency"
                ),
                "MFR_CONFIG_ALL": (
                    0xD1, "byte", False, "transport"
                ),
                "MFR_ADDRESS": (
                    0xE6, "byte", False, "addresses"
                ),
                "MFR_RAIL_ADDRESS": (
                    0xFA, "byte", True, "addresses"
                ),
                "OPERATION": (
                    0x01, "byte", True, "controls"
                ),
                "ON_OFF_CONFIG": (
                    0x02, "byte", True, "controls"
                ),
                "WRITE_PROTECT": (
                    0x10, "byte", False, "final_protection"
                ),
                "USER_DATA_00": (
                    0xB0, "word", False, "user_data"
                ),
                "USER_DATA_01": (
                    0xB1, "word", True, "user_data"
                ),
                "USER_DATA_02": (
                    0xB2, "word", False, "user_data"
                ),
                "USER_DATA_03": (
                    0xB3, "word", True, "user_data"
                ),
                "USER_DATA_04": (
                    0xB4, "word", False, "user_data"
                ),
            }

            for record in sorted(
                inspection["candidates"], key=record_order
            ):
                policy = policies.get(record["name"])
                group = "unresolved"

                if policy is not None:
                    cmd, size, paged, group = policy
                    if (
                        record["cmd"] != cmd
                        or record["size"] != size
                        or record["is_paged"] != paged
                    ):
                        raise ValueError(
                            f"{record['name']}: unexpected "
                            "restore policy descriptor"
                        )

                groups[group].append(deepcopy(record))

            def require_targets(group, expected):
                actual = {
                    (item["page"], item["name"])
                    for item in groups[group]
                }
                if actual != expected:
                    raise ValueError(
                        f"Incomplete restore group: {group}"
                    )

            require_targets(
                "frequency", {(None, "FREQUENCY_SWITCH")}
            )
            require_targets(
                "transport", {(None, "MFR_CONFIG_ALL")}
            )
            require_targets(
                "addresses",
                {
                    (None, "MFR_ADDRESS"),
                    (0, "MFR_RAIL_ADDRESS"),
                    (1, "MFR_RAIL_ADDRESS"),
                },
            )
            require_targets(
                "controls",
                {
                    (page, name)
                    for page in range(2)
                    for name in ("OPERATION", "ON_OFF_CONFIG")
                },
            )
            require_targets(
                "user_data",
                {
                    (None, "USER_DATA_00"),
                    (0, "USER_DATA_01"),
                    (1, "USER_DATA_01"),
                    (None, "USER_DATA_02"),
                    (0, "USER_DATA_03"),
                    (1, "USER_DATA_03"),
                    (None, "USER_DATA_04"),
                },
            )
            require_targets(
                "final_protection", {(None, "WRITE_PROTECT")}
            )

            controls = {
                (item["page"], item["name"]): item["raw"]
                for item in groups["controls"]
            }
            control_pairs = []

            for page in range(2):
                config = controls[(page, "ON_OFF_CONFIG")]
                operation = controls[(page, "OPERATION")]
                validated = self.validate_ltm4677_operation_target(
                    config, operation
                )
                control_pairs.append({
                    "page": page,
                    **validated,
                    "write_order": None,
                })

            blockers = [
                "restore_executor_not_implemented",
                "live_baselines_and_guards_required",
                "address_transition_unresolved",
                "control_transition_unresolved",
                "transport_transition_unresolved",
                "protection_transition_unresolved",
                "user_data_restore_policy_unresolved",
            ]

            transport_raw = groups["transport"][0]["raw"]
            target_pec_required = bool(transport_raw & 0x04)
            if target_pec_required:
                blockers.append("pec_required_but_unsupported")

            # Match the currently supported dedicated executor.
            supported_frequency = {
                0x0000, 0xF3E8, 0xFABC, 0xFB52, 0xFBE8,
                0x023F, 0x028A, 0x02EE, 0x03E8,
            }
            if (
                groups["frequency"][0]["raw"]
                not in supported_frequency
            ):
                blockers.append("frequency_target_unsupported")

            if groups["final_protection"][0]["raw"] not in {
                0x00, 0x20, 0x40, 0x80,
            }:
                blockers.append("protection_target_unsupported")

            if groups["unresolved"]:
                blockers.append("register_restore_policy_unresolved")

            user_data_usage = {
                "USER_DATA_00": "oem_reserved",
                "USER_DATA_01": "manufacturer_reserved_ltpowerplay",
                "USER_DATA_02": "oem_reserved",
                "USER_DATA_03": "user_available",
                "USER_DATA_04": "user_available",
            }

            user_data_policy = {
                "execution_ready": False,
                "representation": {
                    "data_bytes": 2,
                    "format": "RAW",
                    "minimum_raw": 0x0000,
                    "maximum_raw": 0xFFFF,
                    "all_bit_patterns_allowed": True,
                    "preserve_exact_raw": True,
                },
                "nvm": {
                    "listed_as_nvm": True,
                    "persistence_mechanism": "store_user_all",
                    "register_write_destination": "operating_memory",
                    "readback_verifies": "operating_memory",
                    "store_scope": "all_user_nvm_backed_operating_memory",
                    "automatic_store": False,
                },
                "requirements": [
                    "baseline_before_write",
                    "baseline_recheck_before_write",
                    "readback_after_write",
                    "reserved_data_overwrite_confirmation",
                ],
                "blockers": [
                    "user_data_executor_not_implemented",
                    "reserved_data_confirmation_not_implemented",
                ],
                "targets": [],
            }

            for item in groups["user_data"]:
                usage = user_data_usage[item["name"]]
                reserved = usage != "user_available"

                user_data_policy["targets"].append({
                    "page": item["page"],
                    "cmd": item["cmd"],
                    "name": item["name"],
                    "usage": usage,
                    "reserved": reserved,
                    "requires_reserved_data_confirmation": reserved,
                    "warning": (
                        "Restoring this reserved field may overwrite "
                        "product serialization or manufacturing data "
                        "and affect inventory control or LTpowerPlay "
                        "compatibility."
                        if reserved else None
                    ),
                })

            # Diagnostic requirements only, not restore authorization.
            requirement_families = {
                "voltage_relationships": {
                    "VOUT_COMMAND", "VOUT_MAX",
                    "VOUT_MARGIN_HIGH", "VOUT_MARGIN_LOW",
                    "VOUT_OV_FAULT_LIMIT", "VOUT_OV_WARN_LIMIT",
                    "VOUT_UV_WARN_LIMIT", "VOUT_UV_FAULT_LIMIT",
                },
                "input_voltage_relationships": {
                    "VIN_ON", "VIN_OFF",
                    "VIN_OV_FAULT_LIMIT", "VIN_OV_WARN_LIMIT",
                    "VIN_UV_WARN_LIMIT", "VIN_UV_FAULT_LIMIT",
                },
                "protection_limits": {
                    "IOUT_OC_FAULT_LIMIT", "IOUT_OC_WARN_LIMIT",
                    "IOUT_UC_FAULT_LIMIT", "IIN_OC_WARN_LIMIT",
                    "OT_FAULT_LIMIT", "OT_WARN_LIMIT",
                    "UT_WARN_LIMIT", "UT_FAULT_LIMIT",
                },
                "fault_response_semantics": {
                    "VOUT_OV_FAULT_RESPONSE",
                    "VOUT_UV_FAULT_RESPONSE",
                    "IOUT_OC_FAULT_RESPONSE",
                    "IOUT_UC_FAULT_RESPONSE",
                    "VIN_OV_FAULT_RESPONSE",
                    "VIN_UV_FAULT_RESPONSE",
                    "OT_FAULT_RESPONSE", "UT_FAULT_RESPONSE",
                },
                "timing_and_transition": {
                    "VOUT_TRANSITION_RATE",
                    "TON_DELAY", "TON_RISE",
                    "TON_MAX_FAULT_LIMIT",
                    "TON_MAX_FAULT_RESPONSE",
                    "TOFF_DELAY", "TOFF_FALL",
                    "TOFF_MAX_WARN_LIMIT",
                    "MFR_RETRY_DELAY", "MFR_RESTART_DELAY",
                },
                "calibration_semantics": {
                    "IOUT_CAL_GAIN", "IOUT_CAL_OFFSET",
                    "MFR_IIN_OFFSET", "MFR_IOUT_CAL_GAIN_TC",
                    "MFR_TEMP_1_GAIN", "MFR_TEMP_1_OFFSET",
                },
                "manufacturer_configuration": {
                    "MFR_CHAN_CONFIG", "MFR_GPIO_PROPAGATE",
                    "MFR_PWM_MODE", "MFR_GPIO_RESPONSE",
                    "MFR_ADC_CONTROL", "MFR_PWM_CONFIG",
                },
            }

            requirements_by_name = {}
            for family, names in requirement_families.items():
                for name in names:
                    if name in requirements_by_name:
                        raise ValueError(
                            f"Duplicate requirement family: {name}"
                        )
                    requirements_by_name[name] = family

            unresolved_requirements = [
                {
                    "page": item["page"],
                    "cmd": item["cmd"],
                    "name": item["name"],
                    "family": requirements_by_name.get(
                        item["name"], "unclassified"
                    ),
                    "execution_ready": False,
                    "write_order": None,
                }
                for item in groups["unresolved"]
            ]

            voltage_descriptors = {
                "VOUT_COMMAND": 0x21,
                "VOUT_MAX": 0x24,
                "VOUT_MARGIN_HIGH": 0x25,
                "VOUT_MARGIN_LOW": 0x26,
            }
            voltage_targets = {
                (item["page"], item["name"]): item
                for item in inspection["candidates"]
                if item["name"] in voltage_descriptors
            }
            voltage_target_checks = []

            for page in range(2):
                values = {}

                for name, expected_cmd in voltage_descriptors.items():
                    item = voltage_targets.get((page, name))
                    if item is None:
                        raise ValueError(
                            f"PAGE {page}: missing voltage target {name}"
                        )

                    if (
                        item["cmd"] != expected_cmd
                        or item["size"] != "word"
                        or item["format"] != "L16"
                        or item["is_paged"] is not True
                    ):
                        raise ValueError(
                            f"{name}: unexpected voltage descriptor"
                        )

                    values[name] = item["raw"]

                # All four targets share the same positive L16 scale.
                # Compare exact RAW values without float conversion.
                maximum = values["VOUT_MAX"]
                for name in (
                    "VOUT_COMMAND",
                    "VOUT_MARGIN_HIGH",
                    "VOUT_MARGIN_LOW",
                ):
                    if values[name] > maximum:
                        raise ValueError(
                            f"PAGE {page}: target {name} "
                            f"0x{values[name]:04X} exceeds "
                            f"target VOUT_MAX 0x{maximum:04X}"
                        )

                voltage_target_checks.append({
                    "page": page,
                    "raw": values,
                    "within_target_vout_max": True,
                    "execution_ready": False,
                    "write_order": None,
                })

            voltage_chain = (
                ("VOUT_UV_FAULT_LIMIT", 0x44),
                ("VOUT_UV_WARN_LIMIT", 0x43),
                ("VOUT_MARGIN_LOW", 0x26),
                ("VOUT_COMMAND", 0x21),
                ("VOUT_MARGIN_HIGH", 0x25),
                ("VOUT_OV_WARN_LIMIT", 0x42),
                ("VOUT_OV_FAULT_LIMIT", 0x40),
            )
            candidate_targets = {
                (item["page"], item["name"]): item
                for item in inspection["candidates"]
            }
            voltage_relationship_checks = []

            for page in range(2):
                values = {}
                for name, cmd in voltage_chain:
                    item = candidate_targets.get((page, name))
                    if item is None:
                        raise ValueError(
                            f"PAGE {page}: missing voltage target {name}"
                        )
                    if (
                        item["cmd"] != cmd
                        or item["size"] != "word"
                        or item["format"] != "L16"
                        or item["is_paged"] is not True
                    ):
                        raise ValueError(
                            f"{name}: unexpected voltage descriptor"
                        )
                    values[name] = item["raw"]

                relations = []
                for left, right in zip(
                    voltage_chain, voltage_chain[1:]
                ):
                    lower, upper = left[0], right[0]
                    basis = (
                        "datasheet"
                        if (lower, upper) in {
                            ("VOUT_MARGIN_LOW", "VOUT_COMMAND"),
                            ("VOUT_COMMAND", "VOUT_MARGIN_HIGH"),
                        }
                        else "restore_policy"
                    )
                    relations.append({
                        "lower": lower,
                        "upper": upper,
                        "basis": basis,
                        "ok": values[lower] < values[upper],
                    })

                passed = all(item["ok"] for item in relations)
                voltage_relationship_checks.append({
                    "page": page,
                    "raw": values,
                    "relations": relations,
                    "ok": passed,
                    "execution_ready": False,
                    "write_order": None,
                })

            if any(
                not item["ok"]
                for item in voltage_relationship_checks
            ):
                blockers.append(
                    "voltage_target_relationships_failed"
                )

            # Datasheet Rev. B, page 120.
            # A supported encoding does not imply restore safety.
            adc_modes = {
                0x00: "standard_round_robin",
                0x01: "svin",
                0x04: "internal_ic_temperature",
                0x05: "channel_0_vout",
                0x06: "channel_0_iout",
                0x08: "channel_0_temperature",
                0x09: "channel_1_vout",
                0x0A: "channel_1_iout",
                0x0C: "channel_1_temperature",
                0x0D: "short_round_robin",
            }

            adc_target = candidate_targets.get(
                (None, "MFR_ADC_CONTROL")
            )
            if adc_target is None:
                raise ValueError(
                    "Missing global MFR_ADC_CONTROL target"
                )

            if (
                adc_target["cmd"] != 0xD8
                or adc_target["size"] != "byte"
                or adc_target["is_paged"] is not False
            ):
                raise ValueError(
                    "MFR_ADC_CONTROL: unexpected restore descriptor"
                )

            adc_raw = adc_target["raw"]
            adc_supported = adc_raw in adc_modes
            adc_standard = adc_raw == 0x00

            adc_target_check = {
                "page": None,
                "cmd": 0xD8,
                "name": "MFR_ADC_CONTROL",
                "raw": adc_raw,
                "mode": adc_modes.get(adc_raw, "reserved"),
                "encoding_supported": adc_supported,
                "encoding_basis": "datasheet",
                "datasheet_pages": [120],
                "standard_round_robin": adc_standard,
                "ok": adc_supported and adc_standard,
                "policy": "full_restore_requires_standard_adc",
                "policy_basis": "restore_policy",
                "effects": (
                    []
                    if adc_standard
                    else [
                        "voltage_servo_disabled",
                        "unselected_telemetry_faults_and_warnings_disabled",
                    ]
                    if adc_supported
                    else [
                        "fallback_to_internal_ic_temperature",
                        "cml_invalid_data_reasserted_until_valid_command",
                    ]
                ),
                "requirements": [
                    "live_adc_baseline_required",
                    "adc_baseline_recheck_before_write",
                    "telemetry_freshness_validation_required",
                ],
                "execution_ready": False,
                "write_order": None,
            }

            if not adc_supported:
                blockers.append("adc_target_reserved")
            elif not adc_standard:
                blockers.append(
                    "adc_target_disables_full_monitoring"
                )

            # Calibration diagnostics only. No write authorization.
            # Datasheet Rev. B, pages 93 and 95.
            calibration_descriptors = (
                ("MFR_IOUT_CAL_GAIN_TC", 0xF6, "RAW", 93),
                ("MFR_TEMP_1_GAIN", 0xF8, "RAW", 95),
                ("MFR_TEMP_1_OFFSET", 0xF9, "L11", 95),
            )
            calibration_target_checks = []

            for page in range(2):
                for name, cmd, fmt, source_page in (
                    calibration_descriptors
                ):
                    item = candidate_targets.get((page, name))
                    if item is None:
                        raise ValueError(
                            f"PAGE {page}: missing calibration target "
                            f"{name}"
                        )

                    if (
                        item["cmd"] != cmd
                        or item["size"] != "word"
                        or item["format"] != fmt
                        or item["is_paged"] is not True
                    ):
                        raise ValueError(
                            f"{name}: unexpected calibration descriptor"
                        )

                    raw = item["raw"]
                    signed = raw if raw < 0x8000 else raw - 0x10000

                    check = {
                        "page": page,
                        "cmd": cmd,
                        "name": name,
                        "raw": raw,
                        "basis": "datasheet",
                        "datasheet_pages": [source_page],
                        "value_check": "representation_only",
                        "within_documented_range": None,
                        "minimum_raw": None,
                        "maximum_raw": None,
                        "manufacturer_recommendation": None,
                        "board_safety_checked": False,
                        "execution_ready": False,
                        "write_order": None,
                    }

                    if name == "MFR_TEMP_1_GAIN":
                        check.update({
                            "decoded": signed * (2.0 ** -14),
                            "units": "dimensionless",
                            "value_check": "documented_range",
                            "within_documented_range": (
                                0x2000 <= raw <= 0x7FFF
                            ),
                            "minimum_raw": 0x2000,
                            "maximum_raw": 0x7FFF,
                        })
                    elif name == "MFR_IOUT_CAL_GAIN_TC":
                        check.update({
                            "decoded": signed,
                            "units": "ppm/degC",
                            "manufacturer_recommendation": (
                                "use_factory_default_writes_not_recommended"
                            ),
                        })
                    else:
                        check.update({
                            "decoded": decode_value(raw, "L11"),
                            "units": "degC",
                        })

                    calibration_target_checks.append(check)

            if any(
                item["within_documented_range"] is False
                for item in calibration_target_checks
            ):
                blockers.append(
                    "temperature_gain_target_out_of_range"
                )

            # Explicitly verify that the non-writable factory
            # calibration has not become a restore candidate.
            excluded_targets = {
                (item["page"], item["name"]): item
                for item in inspection["excluded"]
            }
            calibration_write_restrictions = []

            for page in range(2):
                name = "IOUT_CAL_GAIN"
                if (page, name) in candidate_targets:
                    raise ValueError(
                        "IOUT_CAL_GAIN: restore write is prohibited"
                    )

                item = excluded_targets.get((page, name))
                if item is None:
                    raise ValueError(
                        f"PAGE {page}: missing excluded IOUT_CAL_GAIN"
                    )

                if (
                    item["cmd"] != 0x38
                    or item["size"] != "word"
                    or item["format"] != "L11"
                    or item["is_paged"] is not True
                    or item["exclude_reason"] != "read_only"
                ):
                    raise ValueError(
                        "IOUT_CAL_GAIN: unexpected exclusion descriptor"
                    )

                calibration_write_restrictions.append({
                    "page": page,
                    "cmd": 0x38,
                    "name": name,
                    "snapshot_raw": item["raw"],
                    "basis": "datasheet",
                    "datasheet_pages": [93],
                    "write_behavior": "nack_no_telemetry_effect",
                    "restore_write_allowed": False,
                    "live_context_required_for_current_limits": True,
                    "execution_ready": False,
                })

            # Project policy: representation and an isolated gain
            # range check cannot establish calibration safety.
            blockers.append("calibration_context_unresolved")

            # Voltage diagnostics only, not an executable transition.
            # Use integer arithmetic for exact L16 comparisons.
            voltage_range_checks = []

            for page in range(2):
                exponent = self.vout_exp.get(page)
                if (
                    isinstance(exponent, bool)
                    or not isinstance(exponent, int)
                    or exponent != -12
                ):
                    raise ValueError(
                        f"PAGE {page}: voltage range checks require "
                        "LTM4677 exponent -12"
                    )

                mode = candidate_targets.get((page, "MFR_PWM_MODE"))
                if mode is None:
                    raise ValueError(
                        f"PAGE {page}: missing MFR_PWM_MODE target"
                    )

                if (
                    mode["cmd"] != 0xD4
                    or mode["size"] != "byte"
                    or mode["format"] != "BYTE"
                    or mode["is_paged"] is not True
                ):
                    raise ValueError(
                        "MFR_PWM_MODE: unexpected voltage range descriptor"
                    )

                low_range = bool(mode["raw"] & 0x02)

                # Comparator detection envelopes, not a complete
                # specification of accepted command encodings.
                comparator_min_mv = 500 if low_range else 1000
                ov_max_mv = 2700 if low_range else 5600
                uv_max_mv = 2700 if low_range else 5400

                specifications = (
                    (
                        "VOUT_COMMAND", 0x21, 500, 1800,
                        "module_operating_range", [5, 11],
                    ),
                    (
                        "VOUT_MARGIN_LOW", 0x26, 500, 1800,
                        "module_operating_range", [5, 11],
                    ),
                    (
                        "VOUT_MARGIN_HIGH", 0x25, 500, 1800,
                        "module_operating_range", [5, 11],
                    ),
                    (
                        "VOUT_OV_FAULT_LIMIT", 0x40,
                        comparator_min_mv, ov_max_mv,
                        "comparator_detection_range", [6],
                    ),
                    (
                        "VOUT_UV_FAULT_LIMIT", 0x44,
                        comparator_min_mv, uv_max_mv,
                        "comparator_detection_range", [7],
                    ),
                )
                checks = []

                for name, cmd, minimum_mv, maximum_mv, kind, pages in (
                    specifications
                ):
                    item = candidate_targets.get((page, name))
                    if item is None:
                        raise ValueError(
                            f"PAGE {page}: missing voltage target {name}"
                        )

                    if (
                        item["cmd"] != cmd
                        or item["size"] != "word"
                        or item["format"] != "L16"
                        or item["is_paged"] is not True
                    ):
                        raise ValueError(
                            f"{name}: unexpected voltage range descriptor"
                        )

                    raw = item["raw"]
                    within = (
                        minimum_mv * 4096
                        <= raw * 1000
                        <= maximum_mv * 4096
                    )
                    checks.append({
                        "name": name,
                        "cmd": cmd,
                        "raw": raw,
                        "range_kind": kind,
                        "minimum_mv": minimum_mv,
                        "maximum_mv": maximum_mv,
                        "range_basis": "datasheet",
                        "datasheet_pages": pages,
                        "acceptance_basis": "restore_policy",
                        "acceptance_policy": (
                            "exact_decoded_value_inside_envelope"
                        ),
                        "within_range": within,
                    })

                voltage_range_checks.append({
                    "page": page,
                    "mfr_pwm_mode_raw": mode["raw"],
                    "low_range": low_range,
                    "vout_exponent": exponent,
                    "checks": checks,
                    "ranges_ok": all(
                        item["within_range"] for item in checks
                    ),
                    "not_validated_here": [
                        "VOUT_MAX",
                        "VOUT_OV_WARN_LIMIT",
                        "VOUT_UV_WARN_LIMIT",
                    ],
                    "high_range_module_applicability_unresolved": (
                        not low_range
                    ),
                    "pwm_mode_other_bits_checked": False,
                    "comparator_quantization_checked": False,
                    "board_safety_checked": False,
                    "execution_ready": False,
                    "write_order": None,
                })

            if any(
                not item["ranges_ok"] for item in voltage_range_checks
            ):
                blockers.append("voltage_target_ranges_failed")

            if any(
                item["high_range_module_applicability_unresolved"]
                for item in voltage_range_checks
            ):
                blockers.append(
                    "voltage_high_range_module_applicability_unresolved"
                )

            blockers.append("voltage_board_limits_unresolved")
            blockers.append("voltage_threshold_semantics_unresolved")

            return {
                "execution_ready": False,
                "user_data_policy": user_data_policy,
                "write_sequence": None,
                "total": inspection["total"],
                "candidate_count": len(inspection["candidates"]),
                "groups": groups,
                "unresolved_requirements": unresolved_requirements,
                "voltage_relationship_checks": voltage_relationship_checks,
                "voltage_range_checks": voltage_range_checks,
                "adc_target_check": adc_target_check,
                "calibration_target_checks": calibration_target_checks,
                "calibration_write_restrictions": (
                    calibration_write_restrictions
                ),
                "voltage_target_checks": voltage_target_checks,
                "excluded": deepcopy(sorted(
                    inspection["excluded"], key=record_order
                )),
                "control_pairs": control_pairs,
                "target_pec_required": target_pec_required,
                "blockers": blockers,
                "constraints": {
                    "outputs_off_before_configuration": True,
                    "repeat_guards_before_each_write": True,
                    "readback_required": True,
                    "controls_after_configuration": True,
                    "protection_after_controls": True,
                    "control_transition_may_enable_outputs": True,
                    "address_stage_position": None,
                    "automatic_output_control": False,
                    "automatic_unlock": False,
                    "rollback": False,
                    "store_nvm": False,
                },
            }

    def summarize_ltm4677_full_restore(
        self, metadata, records, preflight
    ):
        """Combine target planning and saved diagnostics without I/O.

        Exact target agreement establishes data consistency only.
        Saved diagnostics do not authenticate a physical device
        or establish baseline freshness.
        """
        with self._lock:
            if not isinstance(preflight, dict):
                raise ValueError("Expected a preflight report")

            if type(preflight.get("ok")) is not bool:
                raise ValueError("Invalid preflight diagnostic status")

            inspection = preflight.get("inspection")
            if not isinstance(inspection, dict):
                raise ValueError(
                    "Preflight target inspection is unavailable"
                )

            source_metadata = deepcopy(metadata)
            source_records = deepcopy(list(records))
            saved_inspection = deepcopy(inspection)

            plan = self.plan_ltm4677_full_restore(
                source_metadata, source_records
            )

            def index_records(items, source):
                if not isinstance(items, (list, tuple)):
                    raise ValueError(
                        f"{source}: expected record list"
                    )

                indexed = {}
                required = (
                    "page", "cmd", "name", "size",
                    "format", "is_paged", "raw",
                )

                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError(
                            f"{source}: invalid record"
                        )
                    if any(field not in item for field in required):
                        raise ValueError(
                            f"{source}: incomplete record descriptor"
                        )

                    page = item["page"]
                    cmd = item["cmd"]
                    raw = item["raw"]

                    if (
                        isinstance(cmd, bool)
                        or not isinstance(cmd, int)
                        or not 0 <= cmd <= 0xFF
                    ):
                        raise ValueError(
                            f"{source}: invalid command"
                        )
                    if page is not None and (
                        isinstance(page, bool)
                        or not isinstance(page, int)
                        or not 0 <= page < self.num_pages
                    ):
                        raise ValueError(
                            f"{source}: invalid page"
                        )
                    if type(item["is_paged"]) is not bool:
                        raise ValueError(
                            f"{source}: invalid scope"
                        )
                    if any(
                        not isinstance(item[field], str)
                        for field in ("name", "size", "format")
                    ):
                        raise ValueError(
                            f"{source}: invalid descriptor"
                        )
                    if raw is not None and (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                    ):
                        raise ValueError(
                            f"{source}: invalid RAW type"
                        )

                    identity = (page, cmd)
                    if identity in indexed:
                        raise ValueError(
                            f"{source}: duplicate target {identity!r}"
                        )

                    # Source display flags and decoded floats do not
                    # define the target. Preserve exact scalar RAW.
                    indexed[identity] = tuple(
                        item[field] for field in required
                    )

                return indexed

            planned_candidates = [
                item
                for items in plan["groups"].values()
                for item in items
            ]

            for label, expected in (
                ("candidates", planned_candidates),
                ("excluded", plan["excluded"]),
            ):
                saved = index_records(
                    saved_inspection.get(label),
                    f"preflight {label}",
                )
                current = index_records(
                    expected, f"planned {label}"
                )
                if saved != current:
                    raise ValueError(
                        f"Preflight target mismatch: {label}"
                    )

            baseline_report = preflight.get("baseline_report")
            address_comparison = None
            control_comparison = None
            baseline_ok = None

            if baseline_report is not None:
                if not isinstance(baseline_report, dict):
                    raise ValueError("Invalid saved baseline report")
                if type(baseline_report.get("ok")) is not bool:
                    raise ValueError("Invalid saved baseline status")

                baseline_ok = baseline_report["ok"]
                address_comparison = compare_address_register_raw(
                    self._metadata,
                    planned_candidates,
                    deepcopy(baseline_report.get("baselines")),
                )
                control_comparison = self.compare_ltm4677_control_raw(
                    planned_candidates,
                    deepcopy(baseline_report.get("baselines")),
                )

            return {
                "execution_ready": False,
                "write_sequence": None,
                "target_matches_preflight": True,
                "plan": plan,
                "diagnostics": {
                    "reported_ok": preflight["ok"],
                    "baseline_reported_ok": baseline_ok,
                    "reason": preflight.get("reason"),
                    "error": preflight.get("error"),
                    # Preserve the original exception object.
                    "disconnect": preflight.get("disconnect"),
                },
                "address_comparison": address_comparison,
                "control_comparison": control_comparison,
                "evidence": {
                    "target_consistency_checked": True,
                    "physical_device_identity_verified": False,
                    "baseline_freshness_verified": False,
                    "saved_report_authenticity_verified": False,
                },
                "limitations": [
                    "saved_diagnostics_are_not_execution_authorization",
                    "physical_device_identity_not_established",
                    "baseline_freshness_not_established",
                    "saved_report_authenticity_not_established",
                    "live_guards_and_baseline_rechecks_required",
                ],
            }

    def preflight_ltm4677_full_restore(self, metadata, records):
        """Inspect targets, protection, transport and baselines.

        No configuration writes, unlock, rollback or NVM commands.
        Paged baseline reads may perform service PAGE writes.

        Success is diagnostic only, not execution authorization.
        External masters are not locked out by these checks.
        """
        report = {
            "ok": False,
            "execution_ready": False,
            "inspection": None,
            "protection_before": None,
            "transport_before": None,
            "baseline_report": None,
            "address_comparison": None,
            "transport_after": None,
            "protection_after": None,
            "reason": None,
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Full restore offline inspection"

            try:
                if not self._identified:
                    raise RuntimeError("Device is not identified")

                if getattr(self, "is_demo", False):
                    raise ValueError("Demo writes are disabled")

                if self.name != "LTM4677":
                    raise ValueError(
                        "Full restore preflight supports LTM4677 only"
                    )

                source_metadata = deepcopy(metadata)
                source_records = deepcopy(list(records))

                inspection = self.inspect_full_dump_restore(
                    source_metadata,
                    source_records,
                )
                report["inspection"] = inspection

                def global_target(name):
                    matches = [
                        item
                        for item in inspection["candidates"]
                        if item["name"] == name
                        and item["page"] is None
                    ]
                    if len(matches) != 1:
                        raise ValueError(
                            f"Expected one global {name} target"
                        )
                    return matches[0]

                protection_target = global_target("WRITE_PROTECT")
                transport_target = global_target("MFR_CONFIG_ALL")

                # Reject unsupported target transport before any I/O.
                # The full snapshot remains valid for offline analysis.
                if transport_target["raw"] & 0x04:
                    report["reason"] = "pec_required_but_unsupported"
                    raise ValueError(
                        "Target MFR_CONFIG_ALL requires PEC. "
                        "Full restoration with the current "
                        "non-PEC transport is blocked."
                    )

                def accept_stage(field, stage):
                    report[field] = stage
                    if stage["ok"]:
                        return True
                    report["reason"] = stage.get("reason")
                    report["error"] = stage["error"]
                    report["disconnect"] = stage["disconnect"]
                    return False

                active = "Initial protection check"
                before = self.inspect_ltm4677_restore_protection(
                    protection_target["raw"]
                )
                if not accept_stage("protection_before", before):
                    return report

                active = "Initial transport check"
                transport_before = (
                    self.inspect_ltm4677_restore_transport(
                        transport_target["raw"]
                    )
                )
                if not accept_stage(
                    "transport_before", transport_before
                ):
                    return report

                active = "Read full restore baselines"
                baselines = self.read_full_dump_restore_baselines(
                    source_metadata,
                    source_records,
                )
                baseline_ok = accept_stage(
                    "baseline_report", baselines
                )

                # Offline diagnostics over already collected records.
                # Preserve the baseline failure before comparing.
                # Address equality never authorizes restoration.
                active = "Compare address register RAW"
                try:
                    report["address_comparison"] = (
                        compare_address_register_raw(
                            self._metadata,
                            inspection["candidates"],
                            baselines["baselines"],
                        )
                    )
                except Exception as exc:
                    report["address_comparison"] = {
                        "status": "unresolved",
                        "comparison_complete": False,
                        "address_registers_unchanged": None,
                        "entries": [],
                        "errors": [
                            "Address comparison failed: "
                            f"{type(exc).__name__}: {exc}"
                        ],
                        "execution_ready": False,
                        "write_sequence": None,
                    }
                    # A comparison failure must not replace an
                    # already recorded transport or baseline error.
                    if baseline_ok:
                        raise
                    return report

                if not baseline_ok:
                    return report

                def baseline_raw(target):
                    matches = [
                        item
                        for item in baselines["baselines"]
                        if item["cmd"] == target["cmd"]
                        and item["page"] is None
                    ]
                    if len(matches) != 1:
                        raise ValueError(
                            f"Expected one {target['name']} baseline"
                        )

                    raw = matches[0]["current_raw"]
                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= 0xFF
                    ):
                        raise ValueError(
                            f"Invalid {target['name']} baseline"
                        )
                    return raw

                active = "Compare protection and transport baselines"
                protection_baseline = baseline_raw(protection_target)
                transport_baseline = baseline_raw(transport_target)

                if protection_baseline != before["current_raw"]:
                    raise ValueError(
                        "WRITE_PROTECT changed during baseline reads: "
                        f"initial 0x{before['current_raw']:02X}, "
                        f"baseline 0x{protection_baseline:02X}"
                    )

                if transport_baseline != transport_before["current_raw"]:
                    raise ValueError(
                        "MFR_CONFIG_ALL changed during baseline reads: "
                        f"initial 0x{transport_before['current_raw']:02X}, "
                        f"baseline 0x{transport_baseline:02X}"
                    )

                active = "Final transport check"
                transport_after = (
                    self.inspect_ltm4677_restore_transport(
                        transport_target["raw"]
                    )
                )
                if not accept_stage(
                    "transport_after", transport_after
                ):
                    return report

                if transport_after["current_raw"] != transport_baseline:
                    raise ValueError(
                        "MFR_CONFIG_ALL changed after baseline reads: "
                        f"baseline 0x{transport_baseline:02X}, "
                        f"final 0x{transport_after['current_raw']:02X}"
                    )

                active = "Final protection check"
                after = self.inspect_ltm4677_restore_protection(
                    protection_target["raw"]
                )
                if not accept_stage("protection_after", after):
                    return report

                if after["current_raw"] != protection_baseline:
                    raise ValueError(
                        "WRITE_PROTECT changed after baseline reads: "
                        f"baseline 0x{protection_baseline:02X}, "
                        f"final 0x{after['current_raw']:02X}"
                    )

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

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
        """Validate source compatibility without device I/O.

        Records must have validated identities and scopes.
        Every command must belong to the scalar register map.

        Metadata is a compatibility declaration, not authentication.
        This method does not authorize writes or check coverage.
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

            version = metadata.get("dump_format_version")
            scope = metadata.get("dump_scope")
            full_dump = version is not None or scope is not None

            def read_integer(key, description):
                text = metadata.get(key)
                if not isinstance(text, str):
                    raise ValueError(
                        f"CSV {description} is missing"
                    )
                try:
                    return int(text, 10)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid CSV {description}"
                    ) from exc

            def check_exponent(page, key):
                exponent = read_integer(key, "VOUT exponent")
                if not -16 <= exponent <= 15:
                    raise ValueError(
                        "CSV VOUT exponent is outside the signed "
                        "five-bit range"
                    )

                actual = self.vout_exp.get(page)
                if actual is None or actual != exponent:
                    raise ValueError(
                        "CSV VOUT exponent does not match "
                        f"target page {page}"
                    )

            if full_dump:
                if version != "2" or scope != "full":
                    raise ValueError(
                        "Unsupported CSV dump version or scope"
                    )

                pages = read_integer("pages", "page count")
                if pages != self.num_pages:
                    raise ValueError(
                        "CSV page count does not match the device"
                    )

                if (
                    "page" in metadata
                    or "vout_mode_exp" in metadata
                ):
                    raise ValueError(
                        "Full dump contains page-dump metadata"
                    )

                prefix = "vout_mode_exp_page_"
                expected = {
                    f"{prefix}{page}"
                    for page in range(self.num_pages)
                }
                supplied = {
                    key
                    for key in metadata
                    if isinstance(key, str)
                    and key.startswith(prefix)
                }
                if supplied != expected:
                    raise ValueError(
                        "CSV exponent fields do not match page count"
                    )

                # Full snapshot compatibility covers every page,
                # even if the selected write subset contains no L16.
                for page in range(self.num_pages):
                    check_exponent(page, f"{prefix}{page}")

                for record in records:
                    paged = bool(self._regmap[record["cmd"]][3])
                    page = record["page"]

                    if paged:
                        if not self._valid_page(page):
                            raise ValueError(
                                "Invalid full dump target page"
                            )
                    elif page is not None:
                        raise ValueError(
                            "Full dump global register "
                            "must use page=None"
                        )

                return True

            # Legacy page metadata has one exponent.
            l16_pages = {
                record["page"]
                for record in records
                if self._regmap[record["cmd"]][2] == "L16"
            }

            for page in sorted(l16_pages):
                check_exponent(page, "vout_mode_exp")

            return True

    def read_full_dump_restore_baselines(self, metadata, records):
        """Inspect the full snapshot, then read candidate baselines.

        No configuration writes, rollback or NVM actions.
        Paged reads may perform service PAGE writes.

        Return partial progress on failure. This report does not
        authorize execution and must not replace pre-write rechecks.
        """
        report = {
            "ok": False,
            "execution_ready": False,
            "inspection": None,
            "baselines": [],
            "error": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Full restore offline inspection"

            try:
                inspection = self.inspect_full_dump_restore(
                    metadata, records
                )
                report["inspection"] = inspection

                for record in inspection["candidates"]:
                    page = record["page"]
                    cmd = record["cmd"]
                    name = record["name"]
                    scope = (
                        "GLOBAL"
                        if page is None
                        else f"PAGE {page}"
                    )
                    active = (
                        f"Baseline {scope} / {name} "
                        f"0x{cmd:02X}"
                    )

                    current = self.read_register(page, cmd)
                    maximum = (
                        0xFF
                        if record["size"] == "byte"
                        else 0xFFFF
                    )

                    if (
                        isinstance(current, bool)
                        or not isinstance(current, int)
                        or not 0 <= current <= maximum
                    ):
                        raise OSError(
                            "Invalid baseline response: "
                            f"{current!r}"
                        )

                    item = dict(record)
                    item["current_raw"] = current
                    item["target_raw"] = record["raw"]
                    item["matches_target"] = (
                        current == record["raw"]
                    )
                    report["baselines"].append(item)

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["error"] = f"{active}\n{exc}"

            return report

    def inspect_full_dump_restore(self, metadata, records):
        """Inspect full restore targets without hardware access.

        Validate snapshot coverage, compatibility and available
        profile value rules. Preserve context-dependent candidates.

        The result is not an executable write plan.
        """
        with self._lock:
            classified = self.validate_full_dump_snapshot(
                metadata, records
            )

            rules = self._metadata.get("write_value_rules", {})
            checks = []
            requirements = []

            for record in classified["candidates"]:
                cmd = record["cmd"]
                raw = record["raw"]
                name = record["name"]
                page = record["page"]
                rule = rules.get(cmd)

                check = {
                    "page": page,
                    "cmd": cmd,
                    "name": name,
                    "raw": raw,
                    "value_check": "representation_only",
                }

                if rule is not None:
                    if "allowed" not in rule:
                        raise ValueError(
                            f"{name}: profile value rule "
                            "has no allowed set"
                        )

                    if raw in rule.get("requires_context", ()):
                        check["value_check"] = "requires_context"
                        requirements.append({
                            "page": page,
                            "cmd": cmd,
                            "name": name,
                            "kind": "value_context",
                            "detail": rule.get(
                                "context_error",
                                "Device configuration context "
                                "must be checked before writing.",
                            ),
                        })

                    elif raw in rule["allowed"]:
                        check["value_check"] = "profile_allowed"

                    else:
                        raise ValueError(
                            f"PAGE {page} / {name}: "
                            + rule.get(
                                "invalid_error",
                                "Value rejected by device profile.",
                            )
                        )

                if name == "WRITE_PROTECT":
                    requirements.append({
                        "page": page,
                        "cmd": cmd,
                        "name": name,
                        "kind": "write_protection",
                        "detail": (
                            "Validate current and target protection, "
                            "temporary access and final protection."
                        ),
                    })

                elif name in {
                    "MFR_ADDRESS",
                    "MFR_I2C_BASE_ADDRESS",
                    "MFR_RAIL_ADDRESS",
                }:
                    requirements.append({
                        "page": page,
                        "cmd": cmd,
                        "name": name,
                        "kind": "address_handling",
                        "detail": (
                            "Validate model-specific address semantics "
                            "and preserve verified device access."
                        ),
                    })

                elif name in {"OPERATION", "ON_OFF_CONFIG"}:
                    requirements.append({
                        "page": page,
                        "cmd": cmd,
                        "name": name,
                        "kind": "control_transition",
                        "detail": (
                            "Validate current-to-target control "
                            "transition and intermediate states."
                        ),
                    })

                checks.append(check)

            targets = {
                (record["page"], record["name"]): record
                for record in classified["candidates"]
            }

            for check in checks:
                rule = rules.get(check["cmd"])
                if rule is None:
                    continue

                context = rule.get("restore_context")
                if context is None:
                    continue

                context_name = context["command_name"]
                context_cmd = self.command_code(context_name)

                if context_cmd is None:
                    raise ValueError(
                        f"{check['name']}: context command "
                        f"{context_name} is absent from the profile"
                    )

                context_info = self._regmap[context_cmd]
                context_page = (
                    check["page"] if context_info[3] else None
                )

                target = targets.get(
                    (context_page, context_name)
                )
                if target is None:
                    raise ValueError(
                        f"{check['name']}: target context "
                        f"{context_name} is missing"
                    )

                context_raw = target["raw"]
                enabled = bool(context_raw & context["mask"])
                allowed = context[
                    "allowed_when_set"
                    if enabled
                    else "allowed_when_clear"
                ]

                if check["raw"] not in allowed:
                    raise ValueError(
                        f"PAGE {check['page']} / "
                        f"{check['name']}: target value "
                        f"0x{check['raw']:02X} is incompatible "
                        f"with target {context_name} "
                        f"0x{context_raw:02X}"
                    )

                check["target_context_check"] = "compatible"
                check["target_context"] = {
                    "page": context_page,
                    "cmd": context_cmd,
                    "name": context_name,
                    "raw": context_raw,
                }

            return {
                **classified,
                "value_checks": checks,
                "requirements": requirements,
                "execution_ready": False,
            }

    def validate_full_dump_snapshot(self, metadata, records):
        """Validate full snapshot coverage and source compatibility.

        Offline only. No baseline reads or configuration writes.
        Return classified independent records, not an execution plan.

        Coverage follows read_device_dump:
        all readable scalar registers, except PAGE, across all pages.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            if getattr(self, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            if not isinstance(metadata, dict):
                raise ValueError(
                    "CSV metadata must be a dictionary"
                )

            if (
                metadata.get("dump_format_version") != "2"
                or metadata.get("dump_scope") != "full"
            ):
                raise ValueError(
                    "Full snapshot requires version 2 "
                    "and full scope"
                )

            classified = self.classify_dump_snapshot(records)

            all_records = (
                classified["candidates"]
                + classified["excluded"]
            )

            # Full snapshots require an explicit scope identity.
            # Do not normalize legacy numeric global pages here.
            actual = set()

            for record in all_records:
                cmd = record["cmd"]
                page = record["page"]

                if not record["is_paged"] and page is not None:
                    raise ValueError(
                        "Full dump global register must use "
                        f"page=None: command 0x{cmd:02X}"
                    )

                actual.add((page, cmd))

            expected = set()

            for cmd, info in self._regmap.items():
                if cmd == Cmd.PAGE:
                    continue

                if not self.can_read_register(cmd):
                    continue

                if info[3]:
                    for page in range(self.num_pages):
                        expected.add((page, cmd))
                else:
                    expected.add((None, cmd))

            def describe(targets):
                ordered = sorted(
                    targets,
                    key=lambda target: (
                        -1 if target[0] is None else target[0],
                        target[1],
                    ),
                )
                return ", ".join(
                    (
                        "GLOBAL"
                        if page is None
                        else f"PAGE {page}"
                    )
                    + f"/0x{cmd:02X}"
                    for page, cmd in ordered
                )

            missing = expected - actual
            unexpected = actual - expected

            if missing or unexpected:
                details = []

                if missing:
                    details.append(
                        "missing targets: " + describe(missing)
                    )

                if unexpected:
                    details.append(
                        "unexpected targets: "
                        + describe(unexpected)
                    )

                raise ValueError(
                    "Full dump coverage mismatch; "
                    + "; ".join(details)
                )

            # Coverage has established that every command belongs
            # to the scalar register map. Compatibility must cover
            # the entire snapshot, not just writable candidates.
            self.validate_dump_metadata(
                metadata,
                all_records,
            )

            return classified

    def classify_dump_snapshot(self, records, default_page=0):
        """Classify a snapshot without I/O or write authorization.

        Source readonly flags are preserved but do not determine
        hardware access. Candidates are not an executable plan.

        Validate record identity, scope and scalar representation.
        Value rules, metadata and execution order are checked later.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            if getattr(self, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            if not self._valid_page(default_page):
                raise ValueError("Invalid default page")

            candidates = []
            excluded = []
            seen = set()

            send_commands = self._metadata.get(
                "send_commands", {}
            )
            protocols = self._metadata.get(
                "special_protocols", {}
            )
            special_access = self._metadata.get(
                "special_access", set()
            )
            w1c = self._metadata.get(
                "write_one_to_clear", set()
            )

            for index, source in enumerate(records, start=1):
                prefix = f"Record {index}"

                if not isinstance(source, dict):
                    raise ValueError(
                        f"{prefix}: expected a record dictionary"
                    )

                cmd = source.get("cmd")
                if (
                    isinstance(cmd, bool)
                    or not isinstance(cmd, int)
                    or not 0 <= cmd <= 0xFF
                ):
                    raise ValueError(
                        f"{prefix}: invalid command code"
                    )

                reason = None
                info = self._regmap.get(cmd)

                if info is not None:
                    name, size, fmt, paged = info

                    if cmd == Cmd.PAGE:
                        reason = "service_page"
                    elif cmd in special_access:
                        reason = "special_access"
                    elif size == "block":
                        reason = "block"
                    elif cmd in w1c:
                        reason = "write_one_to_clear"
                    elif cmd in self._read_only:
                        reason = "read_only"

                elif cmd in send_commands:
                    name, paged = send_commands[cmd]
                    size = "send"
                    fmt = None
                    reason = "action"

                elif cmd in protocols:
                    name, size, paged = protocols[cmd]
                    fmt = None
                    reason = "special_protocol"

                else:
                    raise ValueError(
                        f"{prefix}: unknown command 0x{cmd:02X}"
                    )

                prefix = f"{prefix} / {name}"

                if source.get("size") != size:
                    raise ValueError(
                        f"{prefix}: size mismatch"
                    )

                if (
                    "name" in source
                    and source["name"] != name
                ):
                    raise ValueError(
                        f"{prefix}: name mismatch"
                    )

                if (
                    fmt is not None
                    and "format" in source
                    and source["format"] != fmt
                ):
                    raise ValueError(
                        f"{prefix}: format mismatch"
                    )

                if "is_paged" in source:
                    scope = source["is_paged"]
                    if (
                        not isinstance(scope, bool)
                        or scope != bool(paged)
                    ):
                        raise ValueError(
                            f"{prefix}: scope mismatch"
                        )

                if (
                    "readonly" in source
                    and not isinstance(source["readonly"], bool)
                ):
                    raise ValueError(
                        f"{prefix}: readonly must be boolean"
                    )

                page = source.get("page", default_page)

                if paged:
                    valid_page = self._valid_page(page)
                else:
                    valid_page = (
                        page is None
                        or self._valid_page(page)
                    )

                if not valid_page:
                    raise ValueError(
                        f"{prefix}: invalid page {page!r}"
                    )

                identity = (
                    page if paged else None,
                    cmd,
                )
                if identity in seen:
                    raise ValueError(
                        f"{prefix}: duplicate target register"
                    )
                seen.add(identity)

                raw = source.get("raw")
                if size in {"byte", "word"}:
                    maximum = (
                        0xFF if size == "byte" else 0xFFFF
                    )
                    if raw is not None and (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= maximum
                    ):
                        raise ValueError(
                            f"{prefix}: invalid raw value"
                        )

                    if reason is None and raw is None:
                        raise ValueError(
                            f"{prefix}: writable register "
                            "has no raw value"
                        )

                item = dict(source)
                item.update(
                    page=page,
                    name=name,
                    size=size,
                    is_paged=bool(paged),
                )
                if fmt is not None:
                    item["format"] = fmt

                if reason is not None:
                    item["exclude_reason"] = reason
                    excluded.append(item)
                else:
                    if size not in {"byte", "word"}:
                        raise ValueError(
                            f"{prefix}: unsupported restore size"
                        )
                    candidates.append(item)

            if not seen:
                raise ValueError("Dump snapshot is empty")

            return {
                "candidates": candidates,
                "excluded": excluded,
                "total": len(seen),
            }

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

                if paged:
                    valid_page = self._valid_page(page)
                else:
                    # New full dumps use None for globals.
                    # Legacy page dumps retain a numeric page.
                    valid_page = (
                        page is None
                        or self._valid_page(page)
                    )

                if not valid_page:
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
        # Diagnostic marker for control command transmission.
        if (
            self.name in {"LTM4673", "LTM4677", "LTM4678"}
            and cmd in (Cmd.OPERATION, Cmd.ON_OFF_CONFIG)
            and size == "byte"
        ):
            with self._lock:
                started = time.perf_counter()
                probe = getattr(
                    self, "_common_probe_thread", None
                )

                if probe is not None and probe.is_alive():
                    self._common_probe_write = {
                        "started": started,
                        "page": self._page,
                        "name": self._regmap[cmd][0],
                        "value": value,
                    }

                print(
                    f"[CONTROL TX ATTEMPT] "
                    f"model={self.name} "
                    f"clock={started:.6f}s "
                    f"page={self._page!r} "
                    f"cmd=0x{cmd:02X} value={value!r}",
                    flush=True,
                )

        # Existing transport implementation.
        try:
            bus = self._get_bus()

            if size == "send":
                result = bus.write_byte(
                    self.address,
                    cmd,
                )

            elif size == "byte":
                result = bus.write_byte_data(
                    self.address,
                    cmd,
                    value,
                )

            elif size == "word":
                result = bus.write_word_data(
                    self.address,
                    cmd,
                    value,
                )

            else:
                raise ValueError("Unsupported write type")

            if result is False:
                raise OSError("Driver reported write failure")

            time.sleep(PMBUS_PAUSE)
            return True

        except Exception as exc:
            self._raise_if_transport_lost(
                exc,
                f"write_{size}",
                cmd,
            )

            self._error(
                f"write_{size}",
                cmd,
                exc,
            )
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

        if self.name == "LTM4673":
            mask = 0x40
        elif self.name == "LTM4677":
            # Datasheet Rev. B, pages 63 and 115:
            # not busy, calculations not pending,
            # output not in transition.
            # The 0x68 example on page 63 disagrees with
            # the documented bit assignments.
            mask = 0x70
        else:
            mask = 0x60

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

    def _wait_ltm4677_observation_ready(
        self, timeout=READY_TIMEOUT
    ):
        """Check interface readiness, not output transition completion.

        Diagnostic read policy only. Never authorizes configuration
        writes or establishes the physical output state.
        """
        if (
            self.name != "LTM4677"
            or not (self._identified or self._identifying)
            or not self.can_read_register(Cmd.MFR_COMMON, "byte")
        ):
            return False

        deadline = time.monotonic() + timeout
        last_raw = None

        while time.monotonic() < deadline:
            last_raw = self._read_transport(
                Cmd.MFR_COMMON,
                "byte",
                attempts=1,
                quiet=True,
            )

            valid = (
                isinstance(last_raw, int)
                and not isinstance(last_raw, bool)
                and 0 <= last_raw < 0xFF
            )

            if valid and (last_raw & 0x60) == 0x60:
                return True

            time.sleep(PMBUS_PAUSE)

        raw_text = (
            f"0x{last_raw:02X}"
            if isinstance(last_raw, int)
            and not isinstance(last_raw, bool)
            and 0 <= last_raw <= 0xFF
            else repr(last_raw)
        )

        self._error(
            "wait_observation_ready",
            Cmd.MFR_COMMON,
            f"Mask 0x60 was not satisfied within {timeout:g} seconds. "
            f"Last RAW={raw_text}.",
        )
        return False

    def _set_page_for_read(self, page):
        """Select and verify PAGE for observation.

        LTM4677 observation does not require bit 4 of MFR_COMMON.
        Other models retain their existing PAGE policy.
        """
        with self._lock:
            if not (self._identified or self._identifying):
                self._error(
                    "set_page_for_read",
                    Cmd.PAGE,
                    "Unknown device",
                )
                return False

            if not self._valid_page(page):
                self._error(
                    "set_page_for_read",
                    Cmd.PAGE,
                    f"Invalid page {page!r}",
                )
                return False

            if self.name != "LTM4677":
                return self.set_page(page)

            # Never trust a cached PAGE for this diagnostic path.
            self._page = None

            for _ in range(RETRY_COUNT):
                if not self._wait_ltm4677_observation_ready():
                    return False

                if not self._write_transport(
                    Cmd.PAGE, page, "byte"
                ):
                    return False

                time.sleep(PAGE_PAUSE)

                if not self._wait_ltm4677_observation_ready():
                    return False

                current = self._read_transport(
                    Cmd.PAGE,
                    "byte",
                    attempts=1,
                    quiet=True,
                )

                if (
                    isinstance(current, int)
                    and not isinstance(current, bool)
                    and current == page
                ):
                    self._page = page
                    return True

            self._error(
                "set_page_for_read",
                Cmd.PAGE,
                f"PAGE {page} verification failed.",
            )
            return False

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

    def _is_ltm4677_control_command(self, cmd):
        return (
            self.name == "LTM4677"
            and isinstance(cmd, int)
            and not isinstance(cmd, bool)
            and cmd in (Cmd.OPERATION, Cmd.ON_OFF_CONFIG)
        )

    def _validate_all_operation_pair(self, config, target):
        """Validate effectiveness prerequisites for panel actions."""
        if self.name == "LTM4677":
            return self.validate_ltm4677_operation_target(
                config, target
            )

        for label, raw in (
            ("ON_OFF_CONFIG", config),
            ("OPERATION", target),
        ):
            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= 0xFF
            ):
                raise ValueError(f"{label} must be an integer byte.")

        if target not in {0x00, 0x40, 0x80}:
            raise ValueError(
                "All-channel panel supports On, Sequence Off "
                "and Immediate Off only."
            )

        if self.name == "LTM4678":
            if config not in {0x16, 0x17, 0x1E, 0x1F}:
                raise ValueError(
                    f"Unsupported LTM4678 ON_OFF_CONFIG 0x{config:02X}."
                )

            if not config & 0x08:
                raise ValueError(
                    "LTM4678 OPERATION on/off control is ignored "
                    "with the current ON_OFF_CONFIG. "
                    "ON_OFF_CONFIG was not changed."
                )

        elif self.name == "LTM4673":
            rule = self._metadata["write_value_rules"].get(
                Cmd.ON_OFF_CONFIG
            )
            if rule is None or config not in rule["allowed"]:
                raise ValueError(
                    "ON_OFF_CONFIG is outside the LTM4673 "
                    "profile policy."
                )

            if (config & 0x18) != 0x18:
                raise ValueError(
                    "LTM4673 all-channel output control requires "
                    "ON_OFF_CONFIG bit 4 and bit 3 set. "
                    "Otherwise startup is automatic or PMBus "
                    "on/off control is ignored. "
                    "ON_OFF_CONFIG was not changed."
                )

        else:
            raise ValueError("Unsupported output-control model.")

    def _check_all_operation_mask(self):
        """Require all four LTM4673 channels without changing the mask."""
        if self.name != "LTM4673":
            return

        cmd = self.command_code("MFR_PAGE_FF_MASK")
        descriptor = (
            "MFR_PAGE_FF_MASK", "byte", "BYTE", False
        )
        if (
            cmd != 0xE4
            or self._regmap.get(cmd) != descriptor
            or not self.can_read_register(cmd, "byte")
        ):
            raise ValueError(
                "LTM4673 MFR_PAGE_FF_MASK access is unavailable."
            )

        raw = self.read_register(None, cmd)
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or not 0 <= raw <= 0x0F
        ):
            raise OSError(
                f"Invalid MFR_PAGE_FF_MASK response: {raw!r}."
            )

        if raw != 0x0F:
            raise ValueError(
                f"MFR_PAGE_FF_MASK is 0x{raw:02X}. "
                "All-channel control requires 0x0F. "
                "No OPERATION was sent and the mask was not changed."
            )

    def apply_operation_all(self, target_raw):
        """Send one OPERATION command to all channels of this device.

        Uses PAGE 0xFF at the individual device address.
        LTM4673 requires every channel enabled in MFR_PAGE_FF_MASK.
        Readback uses explicit individual pages.
        No ON_OFF_CONFIG or mask changes, rollback or NVM store.
        """
        report = {
            "ok": False,
            "target_raw": target_raw,
            "attempted": False,
            "write_completed": False,
            "channels": [],
            "error": None,
            "exception": None,
            "disconnect": None,
        }

        with self._lock:
            active = "Validate all-channel OPERATION"

            try:
                expected_pages = {
                    "LTM4673": 4,
                    "LTM4677": 2,
                    "LTM4678": 2,
                }

                if (
                    not self._identified
                    or self.name not in expected_pages
                    or self.num_pages != expected_pages[self.name]
                ):
                    raise ValueError(
                        "An identified supported device with "
                        "the expected page count is required."
                    )

                if (
                    getattr(self, "is_demo", False)
                    and not getattr(
                        self, "demo_output_control_enabled", False
                    )
                ):
                    raise ValueError(
                        "Demo output control is disabled."
                    )

                descriptors = {
                    Cmd.OPERATION: (
                        "OPERATION", "byte", "BYTE", True
                    ),
                    Cmd.ON_OFF_CONFIG: (
                        "ON_OFF_CONFIG", "byte", "BYTE", True
                    ),
                }

                for cmd, descriptor in descriptors.items():
                    if self._regmap.get(cmd) != descriptor:
                        raise ValueError(
                            "Unexpected output-control descriptor."
                        )
                    if not self.can_read_register(cmd, "byte"):
                        raise ValueError(
                            f"{descriptor[0]} cannot be read."
                        )

                self.validate_register_raw(
                    Cmd.OPERATION, target_raw, "byte"
                )

                status_cmd = self.command_code("STATUS_WORD")
                status_info = self._regmap.get(status_cmd)
                if (
                    status_info is None
                    or status_info[1] != "word"
                    or not status_info[3]
                    or not self.can_read_register(status_cmd, "word")
                ):
                    raise ValueError(
                        "Paged STATUS_WORD access is unavailable."
                    )

                def read_control(cmd):
                    raw = self._read_scalar(cmd)
                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw < 0xFF
                    ):
                        raise OSError(
                            f"Invalid control response at 0x{cmd:02X}: "
                            f"{raw!r}"
                        )
                    return raw

                active = "Check all-channel broadcast mask"
                self._check_all_operation_mask()

                # Validate every target before sending OPERATION.
                for page in range(self.num_pages):
                    active = f"Check control pair on PAGE {page}"

                    if not self.set_page(page):
                        raise OSError(
                            self.last_error
                            or f"Cannot select PAGE {page}."
                        )

                    previous = read_control(Cmd.OPERATION)
                    config = read_control(Cmd.ON_OFF_CONFIG)

                    channel = {
                        "page": page,
                        "previous_raw": previous,
                        "on_off_config_raw": config,
                        "actual_raw": None,
                        "verified": False,
                        "status_word_raw": None,
                        "off": None,
                    }
                    report["channels"].append(channel)

                    self._validate_all_operation_pair(
                        config, target_raw
                    )

                active = "Wait before selecting all channels"
                if not self._wait_ready():
                    raise OSError(
                        self.last_error or "Device is not ready."
                    )

                active = "Recheck all-channel broadcast mask"
                self._check_all_operation_mask()

                active = "Select PAGE 0xFF"
                self._page = None

                if not self._write_transport(
                    Cmd.PAGE, 0xFF, "byte"
                ):
                    raise OSError(
                        self.last_error
                        or "Cannot select all channels."
                    )

                # Never read paged registers while PAGE is 0xFF.
                # LTM4673 reports CML for such reads.
                self._page = 0xFF
                time.sleep(PAGE_PAUSE)

                active = "Wait before all-channel OPERATION"
                if not self._wait_ready():
                    raise OSError(
                        self.last_error or "Device is not ready."
                    )

                active = "Write OPERATION to all channels"
                report["attempted"] = True

                if not self._write_transport(
                    Cmd.OPERATION, target_raw, "byte"
                ):
                    raise OSError(
                        self.last_error or "OPERATION write failed."
                    )

                report["write_completed"] = True

                # Leave broadcast selection out of the software cache.
                # Every following paged read selects an explicit page.
                self._page = None

                for channel in report["channels"]:
                    page = channel["page"]
                    active = f"Read OPERATION on PAGE {page}"

                    if self.name == "LTM4677":
                        actual = self.read_ltm4677_control_readback(
                            page, Cmd.OPERATION
                        )
                    else:
                        actual = self.read_register(
                            page, Cmd.OPERATION
                        )

                    if (
                        isinstance(actual, bool)
                        or not isinstance(actual, int)
                        or not 0 <= actual < 0xFF
                    ):
                        raise OSError(
                            f"PAGE {page}: OPERATION readback failed."
                        )

                    channel["actual_raw"] = actual
                    channel["verified"] = actual == target_raw

                    if not channel["verified"]:
                        raise OSError(
                            f"PAGE {page}: OPERATION mismatch; "
                            f"requested 0x{target_raw:02X}, "
                            f"received 0x{actual:02X}."
                        )

                # Output state is an observation, not a condition
                # for successful OPERATION register verification.
                for channel in report["channels"]:
                    page = channel["page"]
                    active = f"Read STATUS_WORD on PAGE {page}"
                    status = self.read_register(page, status_cmd)

                    if (
                        isinstance(status, bool)
                        or not isinstance(status, int)
                        or not 0 <= status < 0xFFFF
                    ):
                        raise OSError(
                            f"PAGE {page}: invalid STATUS_WORD "
                            f"response {status!r}."
                        )

                    channel["status_word_raw"] = status
                    channel["off"] = bool(status & 0x0040)

                report["ok"] = True

            except TransportDisconnectedError as exc:
                report["disconnect"] = exc
                report["exception"] = exc
                report["error"] = f"{active}\n{exc}"

            except Exception as exc:
                report["exception"] = exc
                report["error"] = f"{active}\n{exc}"

            finally:
                # No cleanup transactions after failure.
                # The next paged operation selects its own page.
                self._page = None

            return report

    def apply_ltm4677_operation_both(self, target_raw):
        """Compatibility entry point restricted to LTM4677."""
        with self._lock:
            if self.name != "LTM4677":
                exc = ValueError(
                    "An identified two-page LTM4677 is required."
                )
                return {
                    "ok": False,
                    "target_raw": target_raw,
                    "attempted": False,
                    "write_completed": False,
                    "channels": [],
                    "error": str(exc),
                    "exception": exc,
                    "disconnect": None,
                }

            return self.apply_operation_all(target_raw)

    def _write_ltm4677_control_checked(
        self, page, cmd, value, size
    ):
        """Check the live same-page pair before a control write.

        The lock covers context reads and configuration transport.
        No rollback, output sequencing or NVM store is performed.
        Private transport callers must not bypass this guard.
        """
        with self._lock:
            previous = None

            def blocked(message):
                return ControlWriteBlockedError(
                    message,
                    page=page,
                    cmd=cmd,
                    previous_raw=previous,
                )

            try:
                if (
                    not self._identified
                    or self.name != "LTM4677"
                    or self.num_pages != 2
                ):
                    raise ValueError(
                        "An identified two-page LTM4677 is required."
                    )

                if getattr(self, "is_demo", False):
                    raise ValueError("Demo writes are disabled.")

                if not self._valid_page(page):
                    raise ValueError(
                        "An explicit PAGE 0 or PAGE 1 is required. "
                        "Broadcast control writes are blocked."
                    )

                descriptors = {
                    Cmd.OPERATION: (
                        "OPERATION", "byte", "BYTE", True
                    ),
                    Cmd.ON_OFF_CONFIG: (
                        "ON_OFF_CONFIG", "byte", "BYTE", True
                    ),
                }

                if cmd not in descriptors:
                    raise ValueError("Unexpected control command.")

                for code, descriptor in descriptors.items():
                    if self._regmap.get(code) != descriptor:
                        raise ValueError(
                            "Unexpected LTM4677 control descriptor."
                        )
                    if not self.can_read_register(code, "byte"):
                        raise ValueError(
                            f"{descriptor[0]} cannot be read."
                        )

                if size != "byte":
                    raise ValueError(
                        "Control writes require byte access."
                    )

                # Preserve all existing access and profile rules.
                self.validate_register_raw(cmd, value, size)

                if not self.set_page(page):
                    raise OSError(
                        self.last_error
                        or "Cannot select the control page."
                    )

                def read_control(code):
                    raw = self._read_scalar(code)
                    if (
                        isinstance(raw, bool)
                        or not isinstance(raw, int)
                        or not 0 <= raw <= 0xFF
                        or raw == 0xFF
                    ):
                        raise OSError(
                            f"Cannot confirm {descriptors[code][0]} "
                            f"on PAGE {page}. "
                            f"Response was {raw!r}."
                        )
                    return raw

                # Read the edited register first so a confirmed
                # display value is available if context reading fails.
                previous = read_control(cmd)

                other_cmd = (
                    Cmd.ON_OFF_CONFIG
                    if cmd == Cmd.OPERATION
                    else Cmd.OPERATION
                )
                other = read_control(other_cmd)

                config = (
                    value
                    if cmd == Cmd.ON_OFF_CONFIG
                    else other
                )
                operation = (
                    value
                    if cmd == Cmd.OPERATION
                    else other
                )

                try:
                    self.validate_ltm4677_operation_target(
                        config, operation
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"PAGE {page}\n"
                        f"Proposed ON_OFF_CONFIG = 0x{config:02X}\n"
                        f"Proposed OPERATION = 0x{operation:02X}\n\n"
                        "This combination is not supported by "
                        "Tables 21 and 22 of the LTM4677 "
                        "Rev. B datasheet.\n"
                        f"{exc}"
                    ) from exc

                if not self._wait_ready():
                    raise OSError(
                        self.last_error
                        or "Control write readiness was not confirmed."
                    )

            except TransportDisconnectedError:
                raise

            except ControlWriteBlockedError:
                raise

            except Exception as exc:
                raise blocked(str(exc)) from exc

            # All pre-write checks have completed.
            # Keep the device lock held through the transport call.
            # Transport failures below are NOT pre-write rejections.
            return self._write_transport(cmd, value, size)

    def _write_checked(self, cmd, value, size):
        with self._lock:
            if self._is_ltm4677_control_command(cmd):
                return self._write_ltm4677_control_checked(
                    self._page,
                    cmd,
                    value,
                    size,
                )

            try:
                self.validate_register_raw(
                    cmd,
                    value,
                    size,
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
                    "write_register",
                    error_cmd,
                    exc,
                )
                return False

            if self._regmap[cmd][3]:
                page = self._page
                if page is None or not self.set_page(page):
                    return False
            elif not self._wait_ready():
                return False

            return self._write_transport(
                cmd,
                value,
                size,
            )

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
                    if not self._set_page_for_read(page):
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

                if not self._set_page_for_read(0):
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

    def _select_page_for_observation(self, page):
        """Use the common verified observation PAGE path."""
        return self._set_page_for_read(page)

    def read_ltm4677_control_readback(self, page, cmd):
        """Read LTM4677 control registers during a transition.

        This method is for observation after a command has been sent.
        It deliberately does not call _wait_ready().
        """

        with self._lock:
            if (
                self.name != "LTM4677"
                or not self._identified
                or self.num_pages != 2
                or not self._valid_page(page)
                or cmd not in {
                    Cmd.OPERATION,
                    Cmd.ON_OFF_CONFIG,
                }
            ):
                return None

            if not self.can_read_register(cmd, "byte"):
                return None

            if not self._select_page_for_observation(page):
                return None

            raw = self._read_transport(
                cmd,
                "byte",
                attempts=1,
                quiet=True,
            )

            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= 0xFF
                or raw == 0xFF
            ):
                self._error(
                    "control_readback",
                    cmd,
                    f"Invalid control response: {raw!r}",
                )
                return None

            return raw

    def read_register(self, page, cmd):
        with self._lock:
            if (
                not self._identified
                or not self.can_read_register(cmd)
            ):
                self._error(
                    "read_register",
                    cmd,
                    "Generic register read unavailable",
                )
                return None

            if self._regmap[cmd][3]:
                if not self._set_page_for_read(page):
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

            ready = not paged or self._set_page_for_read(page)
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
            if not self._identified:
                return result

            if self.name != "LTM4677":
                if not self.set_page(page):
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

            page_selected = False

            for label, name in names.items():
                cmd = self.command_code(name)

                if cmd is None or not self.can_read_register(cmd):
                    continue

                if self._regmap[cmd][3] and not page_selected:
                    if not self._set_page_for_read(page):
                        return result
                    page_selected = True

                if not self._wait_ltm4677_observation_ready():
                    return result

                raw = self._read_scalar(cmd)
                if raw is None:
                    return result

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

            adc_name = "MFR_ADC_TELEMETRY_STATUS"
            adc_cmd = self.command_code(adc_name)

            if adc_cmd is not None:
                info = self._regmap.get(adc_cmd)
                if (
                    info is not None
                    and info[1] == "byte"
                    and not info[3]
                    and self.can_read_register(adc_cmd, "byte")
                ):
                    result[adc_name] = None
                    available[adc_name] = adc_cmd

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

            if adc_name in available:
                result[adc_name] = self._read_scalar(
                    available[adc_name]
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
            if not self._identified:
                return result

            if not self._set_page_for_read(page):
                return result

            for name, cmd in commands.items():
                if not self.can_read_register(cmd):
                    continue

                if self.name == "LTM4677":
                    if not self._wait_ltm4677_observation_ready():
                        return result

                raw = self._read_scalar(cmd)
                result[name] = raw

                if raw is None:
                    return result

        return result

    def read_status(self, page=0):
        with self._lock:
            result = self.read_global_status()
            result.update(self.read_channel_status(page))
            return result

    def write_register(self, page, cmd_code, raw_value, size):
        with self._lock:
            if self._is_ltm4677_control_command(cmd_code):
                return self._write_ltm4677_control_checked(
                    page,
                    cmd_code,
                    raw_value,
                    size,
                )

            # Validate before PAGE selection or any device I/O.
            try:
                self.validate_register_raw(
                    cmd_code,
                    raw_value,
                    size,
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
                    "write_register",
                    error_cmd,
                    exc,
                )
                return False

            if self._regmap[cmd_code][3]:
                if not self.set_page(page):
                    return False

            return self._write_checked(
                cmd_code,
                raw_value,
                size,
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

    def read_device_dump(self):
        """Read all generic scalar registers across all pages.

        Global registers occur once with page=None.
        PAGE and special-access commands are excluded.

        Return a complete list or raise on the first read failure.
        No configuration writes or NVM actions are performed.
        PAGE selection still requires service writes.

        Restore the previous known page only after successful reads.
        Never perform cleanup I/O after an error.
        """
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")

            previous_page = self._page
            result = []

            global_commands = []
            paged_commands = []

            for cmd, info in sorted(self._regmap.items()):
                if cmd == Cmd.PAGE:
                    continue

                if not self.can_read_register(cmd):
                    continue

                if info[3]:
                    paged_commands.append(cmd)
                else:
                    global_commands.append(cmd)

            def append_record(cmd, page):
                name, size, fmt, paged = self._regmap[cmd]

                # Generic global reads ignore the supplied page.
                access_page = 0 if page is None else page
                raw = self.read_register(access_page, cmd)

                maximum = 0xFF if size == "byte" else 0xFFFF
                if (
                    isinstance(raw, bool)
                    or not isinstance(raw, int)
                    or not 0 <= raw <= maximum
                ):
                    scope = (
                        "Global" if page is None
                        else f"PAGE {page}"
                    )
                    raise OSError(
                        f"Full dump read failed: {scope}, "
                        f"{name}, command 0x{cmd:02X}, "
                        f"response={raw!r}"
                    )

                result.append({
                    "page": page,
                    "cmd": cmd,
                    "name": name,
                    "size": size,
                    "format": fmt,
                    "is_paged": bool(paged),
                    "raw": raw,
                    "decoded": self._decode(
                        cmd,
                        raw,
                        access_page,
                        custom=True,
                    ),
                    "readonly": not self.can_write_register(
                        cmd, size
                    ),
                })

            for cmd in global_commands:
                append_record(cmd, None)

            for page in range(self.num_pages):
                for cmd in paged_commands:
                    append_record(cmd, page)

            if (
                self._valid_page(previous_page)
                and self._page != previous_page
            ):
                if not self._set_page_for_read(previous_page):
                    raise OSError(
                        "Full dump read completed, but restoring "
                        f"PAGE {previous_page} failed"
                    )

            return result

    def read_full_dump(self, page=0):
        with self._lock:
            if not self._identified:
                raise RuntimeError("Device is not identified")
            if not self._set_page_for_read(page):
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
                    "decoded": self._decode(
                        cmd, raw, page, custom=True
                    ),
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
                    print(f"{name:22} raw=None")
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

            except TransportDisconnectedError:
                raise

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

        except TransportDisconnectedError:
            raise

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

        except TransportDisconnectedError:
            raise

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
                        size = device._regmap[cmd][1]
                        width = 2 if size == "byte" else 4
                        text = f"0x{raw:0{width}X}"

                    values.append(f"{name}={text}")

                except TransportDisconnectedError:
                    raise

                except Exception as exc:
                    values.append(
                        f"{name}=ERROR:{exc}"
                    )

            print(
                f"{sample + 1:02d}: " + " ".join(values)
            )
