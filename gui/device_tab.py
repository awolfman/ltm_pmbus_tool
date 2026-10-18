# gui/device_tab.py

"""Display a prepared plan with guarded VOUT application."""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from contextlib import contextmanager, nullcontext
from copy import deepcopy

from core.dump_csv import (
    dump_to_csv_string,
    csv_string_to_dump,
    save_csv_atomic,
    device_dump_to_csv_string,
)
from core.pmbus_device import (
    TransportDisconnectedError,
    TransportSessionFailedError,
)
from gui.channel_frame import ChannelColumn
from gui.config_notebook import ConfigNotebook
from gui.profiles import (
    available_telemetry,
    get_gui_profile,
)
from gui.status_defs import (
    configure_status_tree,
    reset_status_tree,
    render_standard_status,
    render_mfr_common,
    render_mfr_pads,
    set_global_status_indicator,
    render_mfr_adc_telemetry_status,
)

class DeviceTab(ttk.Frame):

    def __init__(self, parent, device, **kw):
        super().__init__(parent, **kw)

        self.device = device
        self.gui_profile = get_gui_profile(device)
        self._global_telemetry_fields = available_telemetry(
            device,
            self.gui_profile.global_telemetry,
        )

        self.channels = []
        self.monitoring = False
        self._monitor_after_id = None
        self._disconnected = False
        self._action_busy = False
        self._dump_data = {}
        self._dump_metadata = {}

        self._full_dump_data = None
        self._full_dump_metadata = None
        self._full_restore_report = None

        self.global_telem_lbl = {}
        self.global_cfg_vars = {}
        self._global_wr_btns = {}
        self.global_cfg_data = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.device_info_vars = {}
        self._build_top()
        self._build_channels()
        self._build_buttons()
        self.do_read_all()

    def _output_control_available(self):
        device = self.device
        expected_pages = {
            "LTM4673": 4,
            "LTM4677": 2,
            "LTM4678": 2,
        }

        return (
            not self._disconnected
            and device.identified
            and device.name in expected_pages
            and device.num_pages == expected_pages[device.name]
            and (
                not getattr(device, "is_demo", False)
                or getattr(
                    device, "demo_output_control_enabled", False
                )
            )
        )

    def _build_output_controls(self, parent):
        panel = ttk.LabelFrame(
            parent,
            text=" Output Control ",
        )
        panel.pack(
            side="right",
            fill="y",
            padx=(4, 2),
        )

        ttk.Label(
            panel,
            text=(
                f"All {self.device.num_pages} channels\n"
                f"{self.device.name} / "
                f"0x{self.device.address:02X}"
            ),
            justify="center",
            anchor="center",
        ).pack(fill="x", padx=5, pady=(5, 3))

        self._both_operation_buttons = {}

        for raw, label in (
            (0x80, "All On"),
            (0x40, "All Sequence Off"),
            (0x00, "All Immediate Off"),
        ):
            button = ttk.Button(
                panel,
                text=label,
                width=19,
                command=lambda value=raw:
                    self.do_operation_both(value),
            )
            button.pack(fill="x", padx=5, pady=3)
            self._both_operation_buttons[raw] = button

            if not self._output_control_available():
                button.state(["disabled"])

        if not self._output_control_available():
            notice = "Control unavailable"
        elif getattr(self.device, "is_demo", False):
            notice = "DEMO\nMemory only"
        else:
            notice = "This device only"

        ttk.Label(
            panel,
            text=notice,
            justify="center",
            anchor="center",
        ).pack(fill="x", padx=5, pady=(4, 5))

    def do_operation_both(self, target_raw):
        return self._run_guarded_action(
            lambda: self._do_operation_both_impl(target_raw)
        )

    def _do_operation_both_impl(self, target_raw):
        if not self._output_control_available():
            return

        labels = {
            0x80: "On",
            0x40: "Sequence Off",
            0x00: "Immediate Off",
        }

        if target_raw not in labels:
            raise ValueError("Unsupported all-channel action.")

        demo = getattr(self.device, "is_demo", False)

        mode_notice = (
            "DEMO: register changes are simulated in memory.\n"
            "No hardware access. Transition timing is not simulated.\n\n"
            if demo else ""
        )

        confirmed = messagebox.askyesno(
            f"All {self.device.name} channels",
            (
                f"{self.device.name} at "
                f"0x{self.device.address:02X}\n\n"
                f"{mode_notice}"
                f"OPERATION = 0x{target_raw:02X} / "
                f"{labels[target_raw]}\n\n"
                "Send one command to all channels using PAGE 0xFF?\n"
                "Every channel control pair will be checked first.\n"
                "ON_OFF_CONFIG will not be changed.\n"
                "LTM4673 requires MFR_PAGE_FF_MASK = 0x0F.\n"
                "The mask will not be changed automatically.\n\n"
                "OPERATION fields will be replaced by readback or ERR.\n"
                "Other Config input will be preserved.\n"
                "Monitoring will stop. NVM will not be stored."
            ),
            parent=self,
        )

        if not confirmed or not self._output_control_available():
            return

        self.stop_all()
        button = self._both_operation_buttons[target_raw]

        try:
            with self._button_activity(button, "Writing..."):
                report = self.device.apply_operation_all(
                    target_raw
                )

                if report["disconnect"] is not None:
                    exc = report["disconnect"]
                    self._handle_disconnect(
                        type(exc)(
                            self._operation_both_report_text(report)
                        )
                    )
                    return

                if report["attempted"]:
                    by_page = {
                        item["page"]: item
                        for item in report["channels"]
                    }

                    for channel in self.channels:
                        result = by_page.get(channel.page, {})
                        editor = channel.config_editor

                        if 0x01 in editor._rows:
                            editor._show(
                                0x01, result.get("actual_raw")
                            )

                        channel.update_status({
                            "STATUS_WORD": result.get(
                                "status_word_raw"
                            ),
                        })

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)
            return

        except Exception as exc:
            messagebox.showerror(
                "All-channel operation failed",
                f"{exc}\n\n"
                "The operation may have reached the device.\n"
                "No automatic retry or rollback was performed.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return

        text = self._operation_both_report_text(report)

        if demo:
            text = (
                "DEMO: in-memory register verification only.\n"
                "No physical output was controlled.\n\n"
                + text
            )

        if report["ok"]:
            messagebox.showinfo(
                "All-channel OPERATION verified",
                text,
                parent=self,
            )
        else:
            messagebox.showwarning(
                "All-channel operation stopped",
                text,
                parent=self,
            )

    @staticmethod
    def _operation_both_report_text(report):
        lines = [
            f"Target OPERATION = 0x{report['target_raw']:02X}",
            f"Control write attempted = {report['attempted']}",
            f"Transport completed = {report['write_completed']}",
        ]

        for item in report["channels"]:
            actual = item["actual_raw"]
            actual_text = (
                "unknown" if actual is None
                else f"0x{actual:02X}"
            )
            verified = (
                "matched" if item["verified"] else "not verified"
            )

            status = item["status_word_raw"]
            status_text = (
                "unknown" if status is None
                else f"0x{status:04X}"
            )
            off_text = (
                "unknown" if item["off"] is None
                else "set" if item["off"] else "clear"
            )

            lines.extend((
                "",
                f"PAGE {item['page']}",
                f"OPERATION = {actual_text} / {verified}",
                f"STATUS_WORD = {status_text}",
                f"OFF bit = {off_text}",
            ))

        if report["error"]:
            lines.extend(("", report["error"]))

        if not report["attempted"]:
            lines.extend((
                "",
                "No OPERATION write was sent.",
                "Service PAGE writes may have occurred.",
            ))

        lines.extend((
            "",
            "Register verification does not confirm output voltage.",
            "Status values are point-in-time observations.",
            "No automatic retry, rollback or NVM store.",
            "Monitoring remains stopped.",
        ))

        return "\n".join(lines)

    def _check_full_restore_impl(self):
        """Run diagnostic preflight under the existing action guard."""
        if not self._allow_device_action():
            return

        self._full_restore_report = None

        try:
            if self.device.name != "LTM4677":
                raise ValueError(
                    "Full Restore preflight currently supports "
                    "LTM4677 only."
                )

            if (
                self._full_dump_data is None
                or self._full_dump_metadata is None
            ):
                raise ValueError(
                    "Load or read a Full Dump first."
                )

            # Freeze the complete snapshot before any dialog.
            # CSV readonly flags must not select a restore subset.
            metadata = deepcopy(self._full_dump_metadata)
            records = deepcopy(self._full_dump_data)

            inspection = self.device.inspect_full_dump_restore(
                metadata, records
            )

            candidate_count = len(inspection["candidates"])
            excluded_count = len(inspection["excluded"])

            confirmed = messagebox.askyesno(
                "Check Full Restore",
                f"{self.device.name} at "
                f"0x{self.device.address:02X}\n\n"
                f"Restore candidates: {candidate_count}\n"
                f"Automatically excluded: {excluded_count}\n\n"
                "Check current protection, transport settings "
                "and candidate baselines?\n\n"
                "This is a diagnostic check, not restoration.\n"
                "Paged reads may write the service PAGE register.\n"
                "No configuration writes or NVM commands.\n"
                "Config input and dump snapshots are preserved.\n"
                "Monitoring will be stopped and will remain stopped.",
                parent=self,
            )

            # The confirmation dialog runs a nested Tk loop.
            if not confirmed or not self._allow_device_action():
                return

        except Exception as exc:
            messagebox.showerror(
                "Cannot prepare Full Restore check",
                str(exc),
                parent=self,
            )
            return

        self.stop_all()

        try:
            with self._button_activity(
                self.write_dump_btn, "Checking..."
            ):
                try:
                    report = (
                        self.device.preflight_ltm4677_full_restore(
                            deepcopy(metadata),
                            deepcopy(records),
                        )
                    )
                    self._full_restore_report = report

                    report["offline_summary"] = None
                    report["offline_summary_error"] = None

                    if report.get("inspection") is not None:
                        try:
                            report["offline_summary"] = (
                                self.device.summarize_ltm4677_full_restore(
                                    metadata, records, report
                                )
                            )
                        except Exception as exc:
                            # Do not replace the original diagnostic
                            # error or prevent disconnect handling.
                            report["offline_summary_error"] = (
                                f"{type(exc).__name__}: {exc}"
                            )

                    disconnect = report.get("disconnect")

                    if disconnect is not None:
                        # Mark stale before leaving the activity
                        # context so controls cannot be re-enabled.
                        self._handle_disconnect(
                            type(disconnect)(
                                self._full_restore_report_text(report)
                            )
                        )
                        return

                except TransportDisconnectedError as exc:
                    self._handle_disconnect(exc)
                    return

        except Exception as exc:
            messagebox.showerror(
                "Full Restore check failed",
                f"{exc}\n\n"
                "No configuration writes or NVM commands "
                "were requested by this check.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return

        text = self._full_restore_report_text(report)

        if (
            report["ok"]
            and report.get("offline_summary") is not None
            and not report.get("offline_summary_error")
        ):
            messagebox.showinfo(
                "Full Restore diagnostic check completed",
                text,
                parent=self,
            )
        else:
            messagebox.showwarning(
                "Full Restore diagnostic check stopped",
                text,
                parent=self,
            )

    @staticmethod
    def _full_restore_report_text(report):
        """Format a complete or partial report without device I/O."""
        inspection = report.get("inspection") or {}
        baseline_report = report.get("baseline_report") or {}
        baselines = baseline_report.get("baselines", [])

        candidates = inspection.get("candidates", [])
        excluded = inspection.get("excluded", [])
        matching = sum(
            bool(item.get("matches_target"))
            for item in baselines
        )

        lines = [
            (
                "Diagnostic preflight completed."
                if report.get("ok")
                else "Diagnostic preflight stopped."
            ),
            "",
            f"Restore candidates: {len(candidates)}",
            f"Automatically excluded: {len(excluded)}",
            f"Baselines read: {len(baselines)}",
            f"Read baselines matching targets: {matching}",
            (
                "Read baselines differing from targets: "
                f"{len(baselines) - matching}"
            ),
            "",
        ]

        for field, title in (
            ("protection_before", "Initial protection"),
            ("transport_before", "Initial transport"),
            ("baseline_report", "Baseline collection"),
            ("transport_after", "Final transport"),
            ("protection_after", "Final protection"),
        ):
            stage = report.get(field)
            state = (
                "not reached"
                if stage is None
                else "passed" if stage.get("ok") else "failed"
            )
            lines.append(f"{title}: {state}")

        for field, title in (
            ("protection_before", "Initial WRITE_PROTECT"),
            ("transport_before", "Initial MFR_CONFIG_ALL"),
            ("transport_after", "Final MFR_CONFIG_ALL"),
            ("protection_after", "Final WRITE_PROTECT"),
        ):
            stage = report.get(field) or {}
            raw = stage.get("current_raw")
            if raw is not None:
                lines.append(f"{title}: 0x{raw:02X}")

        before = report.get("protection_before") or {}
        if before.get("temporary_unlock_required"):
            lines.extend((
                "",
                "Temporary unlock would be required.",
                "No unlock was performed.",
            ))

        if report.get("reason"):
            lines.extend(("", str(report["reason"])))

        if report.get("error"):
            lines.extend(("", str(report["error"])))

        summary = report.get("offline_summary")

        if summary is None:
            lines.extend((
                "",
                "Combined offline audit unavailable.",
                "Planner blockers have not been displayed.",
            ))

            summary_error = report.get("offline_summary_error")
            if summary_error:
                lines.append(str(summary_error))

            # A preflight-only comparison remains diagnostic.
            comparison = report.get("address_comparison")
        else:
            lines.extend((
                "",
                "Combined offline audit",
                "Target consistency with saved inspection checked.",
                "Saved diagnostic success is not restore readiness.",
                "",
                "Planner blockers",
            ))

            blockers = summary["plan"]["blockers"]
            if blockers:
                lines.extend(str(item) for item in blockers)
            else:
                lines.append(
                    "No listed blockers; execution remains unauthorized."
                )

            comparison = summary.get("address_comparison")

        lines.extend(("", "Address register RAW comparison"))

        if comparison is None:
            lines.append("Not performed.")
        else:
            status = comparison.get("status", "unresolved")
            descriptions = {
                "unchanged": (
                    "All declared address RAW values match. "
                    "Address semantics and bus safety are not established."
                ),
                "changed": (
                    "Declared address RAW values differ. "
                    "No address transition is authorized."
                ),
                "unresolved": (
                    "Address comparison did not establish "
                    "complete matching data."
                ),
            }
            lines.append(descriptions.get(
                status, descriptions["unresolved"]
            ))

            for item in comparison.get("entries", []):
                scope = (
                    "GLOBAL"
                    if item["page"] is None
                    else f"PAGE {item['page']}"
                )
                width = 2 if item["size"] == "byte" else 4
                state = (
                    "match"
                    if item["matches_target"]
                    else "different"
                )
                lines.append(
                    f"{scope} / {item['name']} "
                    f"0x{item['cmd']:02X} | "
                    f"baseline 0x{item['current_raw']:0{width}X} | "
                    f"target 0x{item['target_raw']:0{width}X} | "
                    f"{state}"
                )

            for error in comparison.get("errors", []):
                lines.append(str(error))

        lines.extend(("", "Control register RAW comparison"))

        if summary is None:
            lines.append(
                "Unavailable without a combined offline audit."
            )
        else:
            controls = summary.get("control_comparison")

            if controls is None:
                lines.append("Not performed.")
            else:
                descriptions = {
                    "unchanged": (
                        "All control RAW values match."
                    ),
                    "changed": (
                        "Control RAW values differ."
                    ),
                    "unresolved": (
                        "Control comparison is unresolved. "
                        "Review missing data and pair-policy results."
                    ),
                }
                lines.append(descriptions.get(
                    controls.get("status"),
                    descriptions["unresolved"],
                ))
                lines.append(
                    "RAW comparison complete"
                    if controls.get("comparison_complete")
                    else "RAW comparison incomplete"
                )

                def control_raw(raw):
                    return "N/A" if raw is None else f"0x{raw:02X}"

                for channel in controls.get("channels", []):
                    lines.extend((
                        "",
                        f"PAGE {channel['page']}",
                    ))

                    for item in channel.get("registers", []):
                        changed = item.get("changed")
                        state = (
                            "different" if changed is True
                            else "match" if changed is False
                            else "not compared"
                        )
                        lines.append(
                            f"{item['name']} "
                            f"0x{item['cmd']:02X} | "
                            f"baseline {control_raw(item['current_raw'])} | "
                            f"target {control_raw(item['target_raw'])} | "
                            f"{state}"
                        )

                    for field, title in (
                        ("baseline_policy", "Baseline pair policy"),
                        ("target_policy", "Target pair policy"),
                    ):
                        policy = channel.get(field)
                        state = (
                            "not checked"
                            if policy is None
                            else "supported"
                            if policy.get("supported") is True
                            else "not supported"
                        )
                        lines.append(f"{title} | {state}")

                    analysis = channel.get("transition_analysis")
                    lines.append(
                        "Hypothetical control pair analysis"
                    )

                    if analysis is None:
                        lines.append(
                            "Analysis unavailable in this report."
                        )
                    else:
                        descriptions = {
                            "no_control_changes": (
                                "No changes to these two control "
                                "registers are required by the saved RAW."
                            ),
                            "single_register_change": (
                                "One control register differs. "
                                "No intermediate pair is needed."
                            ),
                            "hypothetical_pairs_checked": (
                                "Both hypothetical first-command "
                                "cases were checked."
                            ),
                            "unresolved": (
                                "Analysis unresolved. Complete "
                                "supported endpoint pairs required."
                            ),
                        }
                        lines.append(descriptions.get(
                            analysis.get("status"),
                            descriptions["unresolved"],
                        ))

                        for variant in analysis.get("variants", []):
                            first = variant[
                                "hypothetical_first_command"
                            ]
                            lines.append(
                                f"Hypothetical first command | {first}"
                            )

                            for field, title in (
                                ("intermediate_pair", "Intermediate pair"),
                                ("target_pair", "Target pair"),
                            ):
                                pair = variant.get(field)
                                if pair is None:
                                    continue

                                state = (
                                    "supported"
                                    if pair.get("supported") is True
                                    else "not supported"
                                )
                                lines.append(
                                    f"{title} | "
                                    "ON_OFF_CONFIG "
                                    f"{control_raw(pair['on_off_config_raw'])}"
                                    " | OPERATION "
                                    f"{control_raw(pair['operation_raw'])}"
                                    f" | {state}"
                                )
                                if pair.get("error"):
                                    lines.append(str(pair["error"]))

                        for error in analysis.get("errors", []):
                            lines.append(str(error))

                        lines.append(
                            "Hypothetical pairs are not observed "
                            "hardware states or an execution plan."
                        )

                for error in controls.get("errors", []):
                    lines.append(str(error))

        lines.extend((
            "",
            "Matching control RAW does not establish outputs OFF.",
            "Supported pairs do not establish transition safety.",
            "No control write order is defined.",
        ))

        lines.extend((
            "",
            "Physical device identity is not established "
            "by the combined report.",
            "Baseline freshness and saved report authenticity "
            "are not established.",
        ))

        lines.extend((
            "",
            "Execution is not authorized.",
            "No configuration writes or NVM commands.",
            "Service PAGE writes may have occurred.",
            "Config input and dump snapshots were not replaced.",
            "Monitoring remains stopped.",
            "This report is a historical observation, "
            "not a guarantee of the current device state.",
        ))

        return "\n".join(lines)

    @contextmanager
    def _button_activity(self, button, text):
        """Disable tab and operation-window buttons during device I/O."""
        original_text = button.cget("text")
        saved_states = []
        seen = set()
        transport_lost = False

        def collect(parent):
            for widget in parent.winfo_children():
                if widget in seen:
                    continue

                seen.add(widget)

                if isinstance(widget, ttk.Button):
                    saved_states.append(
                        (widget, widget.instate(["disabled"]))
                    )

                collect(widget)

        collect(self)

        # A preview can be a separate Toplevel.
        if button not in seen:
            collect(button.winfo_toplevel())

        try:
            for widget, _ in saved_states:
                widget.state(["disabled"])

            button.configure(text=text)
            self.update_idletasks()
            yield

        except TransportDisconnectedError:
            # Includes TransportSessionFailedError.
            transport_lost = True
            raise

        finally:
            try:
                if button.winfo_exists():
                    button.configure(text=original_text)
            except tk.TclError:
                pass

            if not self._disconnected and not transport_lost:
                for widget, was_disabled in saved_states:
                    try:
                        if widget.winfo_exists():
                            widget.state(
                                ["disabled"]
                                if was_disabled
                                else ["!disabled"]
                            )
                    except tk.TclError:
                        pass

    def _config_editors(self):
        """Return existing Config editors for this device."""
        editors = [self.global_config_editor]
        editors.extend(
            channel.config_editor
            for channel in self.channels
        )
        return editors

    def _confirm_config_reload(self):
        """Ask before replacing unwritten Config input."""
        has_edits = False

        for editor in self._config_editors():
            if editor._busy:
                messagebox.showwarning(
                    "Config operation in progress",
                    "Finish the current Config operation first.",
                    parent=self,
                )
                return False

            # Frequency uses a dedicated write path and is excluded
            # from get_pending_changes. Preserve its edit warning.
            for item in editor._rows.values():
                if not editor._is_guarded_frequency(item["row"]):
                    continue

                text = item["variable"].get().strip()
                baseline = item["baseline"]

                if baseline is not None:
                    if text != baseline.strip():
                        has_edits = True

                elif text not in {
                    "", "---", "ERR", "N/A", "N/S", "STALE",
                }:
                    has_edits = True

            try:
                if editor.get_pending_changes():
                    has_edits = True

            except ValueError:
                has_edits = True

            except RuntimeError as exc:
                messagebox.showerror(
                    "Cannot reload Config",
                    str(exc),
                    parent=self,
                )
                return False

        if not has_edits:
            return True

        return messagebox.askyesno(
            "Discard Config edits?",
            "Read All will replace Config fields with "
            "values read from the device.\n\n"
            "Unwritten input will be lost.\n"
            "Continue?",
            parent=self,
        )

    def do_read_all(self):
        if (
            self._disconnected
            or self._action_busy
            or self._other_tab_operation_busy()
        ):
            return

        self._action_busy = True

        try:
            if not self._confirm_config_reload():
                return

            if self._disconnected:
                return

            self.stop_all()

            with self._button_activity(
                self.read_all_btn, "Reading..."
            ):
                try:
                    self.update_device_info()
                    self.global_config_editor.read_all()

                    global_telemetry = (
                        self.device.read_global_telemetry()
                    )

                    for key, label in self.global_telem_lbl.items():
                        value = global_telemetry.get(key)
                        label.configure(
                            text=(
                                f"{value:.3f}"
                                if value is not None
                                else "N/A"
                            )
                        )

                    for channel in self.channels:
                        channel.read_all_reg_groups()

                        telemetry = (
                            self.device.read_channel_telemetry(
                                channel.page
                            )
                        )
                        channel.update_telemetry(telemetry)

                    self._refresh_status()

                except TransportDisconnectedError as exc:
                    # Includes TransportSessionFailedError.
                    # Mark stale inside the activity context so
                    # controls cannot be enabled on exit.
                    self._handle_disconnect(exc)

        except Exception as exc:
            messagebox.showerror(
                "Read error",
                str(exc),
                parent=self,
            )

        finally:
            self._action_busy = False

    def do_write_all(self):
        """Prepare and preview changes without device I/O."""
        if (
            self._disconnected
            or self._action_busy
            or self._config_operation_busy()
            or self._other_tab_operation_busy()
        ):
            return

        try:
            changes = []
            errors = []
            seen = set()

            for editor in self._config_editors():
                scope = (
                    f"CH{editor.page}"
                    if editor.paged
                    else "Global"
                )

                try:
                    prepared, editor_errors = (
                        editor.prepare_pending_changes()
                    )

                except (ValueError, RuntimeError) as exc:
                    errors.append(f"{scope}: {exc}")
                    continue

                errors.extend(editor_errors)

                for change in prepared:
                    identity = (
                        change["is_paged"],
                        change["page"],
                        change["cmd"],
                    )

                    if identity in seen:
                        errors.append(
                            "Duplicate pending register change: "
                            f"{identity!r}"
                        )
                        continue

                    seen.add(identity)
                    changes.append(change)

            # Do not show a partial plan if any field is invalid.
            if errors:
                messagebox.showerror(
                    "Cannot prepare changes",
                    "Fix all errors before preparing a write plan.\n"
                    "No registers were written.\n\n"
                    + "\n".join(errors),
                    parent=self,
                )
                return

            if not changes:
                messagebox.showinfo(
                    "Preview Changes",
                    "No changes requiring a write.\n"
                    "Edited values may encode to the "
                    "last-read raw values.\n\n"
                    "No registers were written.",
                    parent=self,
                )
                return

            self._show_changes_preview(changes)

        except Exception as exc:
            messagebox.showerror(
                "Cannot prepare changes",
                str(exc),
                parent=self,
            )

    def _check_preview_plan(
        self, changes, window, check_button=None
    ):
        return self._run_guarded_action(
            lambda: self._check_preview_plan_impl(
                changes, window, check_button
            )
        )

    def _check_preview_plan_impl(
        self, changes, window, check_button=None
    ):
        """Read current device values without changing Config fields."""
        if self._disconnected:
            return

        try:
            # Do not check an obsolete preview after the user
            # has edited or reread Config fields.
            current = []

            for editor in self._config_editors():
                prepared, errors = (
                    editor.prepare_pending_changes()
                )

                if errors:
                    raise ValueError("\n".join(errors))

                current.extend(prepared)

            def signature(records):
                return {
                    (
                        record["page"],
                        record["cmd"],
                    ): (
                        record["name"],
                        record["size"],
                        record["format"],
                        record["is_paged"],
                        record["previous_text"],
                        record["new_text"],
                        record["previous_raw"],
                        record["new_raw"],
                        record["manual_only"],
                    )
                    for record in records
                }

            if (
                len(current) != len(changes)
                or signature(current) != signature(changes)
            ):
                messagebox.showwarning(
                    "Preview is outdated",
                    (
                        "Config fields have changed since this "
                        "preview was opened.\n\n"
                        "Close this window and use Write All again."
                    ),
                    parent=window,
                )
                return

            candidates = [
                change
                for change in changes
                if not change["manual_only"]
            ]

            if not candidates:
                messagebox.showinfo(
                    "Check Device",
                    "The plan contains only individual-write controls.\n"
                    "No device check was performed.",
                    parent=window,
                )
                return

            # Restore monitoring without an immediate extra read.
            was_monitoring = self.monitoring
            self.stop_all()

            activity = (
                self._button_activity(
                    check_button, "Checking..."
                )
                if check_button is not None
                else nullcontext()
            )

            try:
                with activity:
                    try:
                        result = self.device.check_write_plan(
                            candidates
                        )

                    except TransportDisconnectedError as exc:
                        # Includes a blocked transport session.
                        self._handle_disconnect(exc)
                        return

            finally:
                if (
                    was_monitoring
                    and not self._disconnected
                    and self.winfo_exists()
                ):
                    self.monitoring = True
                    self.mon_btn.configure(text="Stop Monitor")
                    self._monitor_after_id = self.after(
                        500,
                        self._mon_loop,
                    )

            if result["ok"]:
                messagebox.showinfo(
                    "Device check passed",
                    f"Checked registers: {result['checked']}.\n"
                    "Current raw values match the plan baselines.\n\n"
                    "No registers were written.\n"
                    "This check does not validate board-specific "
                    "limits or the write order.",
                    parent=window,
                )
                return

            details = []

            for item in result["results"]:
                if item["status"] == "match":
                    continue

                scope = (
                    "Global"
                    if item["page"] is None
                    else f"CH{item['page']}"
                )
                width = 2 if item["size"] == "byte" else 4
                expected = (
                    f"0x{item['previous_raw']:0{width}X}"
                )

                if item["status"] == "read_error":
                    details.append(
                        f"{scope} / {item['name']}: read failed"
                    )
                else:
                    actual = (
                        f"0x{item['actual_raw']:0{width}X}"
                    )
                    details.append(
                        f"{scope} / {item['name']}: "
                        f"baseline {expected}, device {actual}"
                    )

            remaining = result["total"] - result["checked"]
            if remaining:
                details.append(
                    f"Not checked after read failure: {remaining}"
                )

            messagebox.showwarning(
                "Device check failed",
                "The plan cannot be used as-is.\n"
                "Config input has been preserved.\n"
                "No registers were written.\n\n"
                + "\n".join(details),
                parent=window,
            )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)

        except Exception as exc:
            messagebox.showerror(
                "Cannot check device",
                str(exc),
                parent=window,
            )

    def _apply_vout_preview(
        self, changes, window, apply_button=None
    ):
        return self._run_guarded_action(
            lambda: self._apply_vout_preview_impl(
                changes, window, apply_button
            )
        )

    def _apply_vout_preview_impl(
        self, changes, window, apply_button=None
    ):
        """Apply only VOUT_COMMAND edits from the displayed snapshot."""
        if self._disconnected:
            return

        try:
            if not changes:
                return

            if getattr(self.device, "is_demo", False):
                raise ValueError("Demo writes are disabled")

            unsupported = [
                change["name"]
                for change in changes
                if (
                    change["name"] != "VOUT_COMMAND"
                    or change["manual_only"]
                )
            ]

            if unsupported:
                raise ValueError(
                    "This stage permits VOUT_COMMAND only.\n"
                    "No subset of the plan will be applied.\n\n"
                    + "\n".join(sorted(set(unsupported)))
                )

            def ensure_current():
                current = []

                for editor in self._config_editors():
                    prepared, errors = (
                        editor.prepare_pending_changes()
                    )
                    if errors:
                        raise ValueError("\n".join(errors))

                    current.extend(prepared)

                # The editor traversal order is stable.
                if current != changes:
                    raise ValueError(
                        "Preview is outdated.\n"
                        "Close this window and use "
                        "Write All again."
                    )

            ensure_current()

            lines = []
            for change in changes:
                lines.append(
                    f"CH{change['page']} "
                    f"{change['previous_text']} -> "
                    f"{change['new_text']} V "
                    f"[0x{change['new_raw']:04X}]"
                )

            confirmed = messagebox.askyesno(
                "Apply output voltage changes?",
                f"{self.device.name} at "
                f"0x{self.device.address:02X}\n\n"
                + "\n".join(lines)
                + "\n\n"
                "This changes the output voltage setting.\n"
                "Confirm that the values are safe for the load.\n"
                "Execution stops at the first error.\n"
                "Earlier writes are not rolled back.\n"
                "Monitoring will be stopped.\n"
                "No NVM store will be performed.",
                parent=window,
            )

            if not confirmed or self._disconnected:
                return

            # The confirmation dialog runs a nested Tk loop.
            ensure_current()

        except Exception as exc:
            messagebox.showerror(
                "Cannot apply changes",
                str(exc),
                parent=window,
            )
            return

        self.stop_all()

        activity = (
            self._button_activity(apply_button, "Writing...")
            if apply_button is not None
            else nullcontext()
        )

        try:
            with activity:
                report = self.device.apply_vout_write_plan(
                    changes
                )

                # Fatal transport errors are returned in this report.
                if report["disconnect"] is not None:
                    verified = report["verified"]
                    attempted = report["attempted"]

                    lines = [
                        f"Plan entries: {len(changes)}",
                        f"Write attempts: {len(attempted)}",
                        f"Verified by readback: {len(verified)}",
                        (
                            f"Not attempted: "
                            f"{len(changes) - len(attempted)}"
                        ),
                    ]

                    for change in verified:
                        lines.append(
                            f"CH{change['page']} "
                            f"{change['name']}: "
                            f"0x{change['actual_raw']:04X} verified"
                        )

                    if report["error"]:
                        lines.extend(("", report["error"]))

                    window.destroy()

                    # Preserve the distinction between a failed
                    # session and a recognized USB disconnection.
                    self._handle_disconnect(
                        type(report["disconnect"])(
                            "\n".join(lines)
                            + "\n\n"
                            "An unverified write may have reached "
                            "the device."
                        )
                    )
                    return

        except TransportDisconnectedError as exc:
            # Defensive handling if the executor raises instead
            # of returning a partial execution report.
            if window.winfo_exists():
                window.destroy()

            self._handle_disconnect(
                type(exc)(
                    f"{exc}\n\n"
                    "Execution report is unavailable.\n"
                    "An unverified write may have reached the device."
                )
            )
            return

        except Exception as exc:
            messagebox.showerror(
                "Cannot apply changes",
                str(exc),
                parent=window,
            )
            return

        # A new attempt must use a newly prepared preview.
        window.destroy()

        verified = report["verified"]
        attempted = report["attempted"]

        lines = [
            f"Plan entries: {len(changes)}",
            f"Write attempts: {len(attempted)}",
            f"Verified by readback: {len(verified)}",
            f"Not attempted: {len(changes) - len(attempted)}",
        ]

        for change in verified:
            lines.append(
                f"CH{change['page']} "
                f"{change['name']}: "
                f"0x{change['actual_raw']:04X} verified"
            )

        if report["error"]:
            lines.extend(("", report["error"]))

        # Refresh only verified fields using existing readback.
        # Read All would discard remaining unwritten input.
        try:
            editors = {
                (
                    editor.page if editor.paged else None
                ): editor
                for editor in self._config_editors()
            }

            for change in verified:
                editor = editors[change["page"]]
                editor._show(
                    change["cmd"],
                    change["actual_raw"],
                )

        except Exception as exc:
            messagebox.showerror(
                "Config display update failed",
                "\n".join(lines)
                + "\n\n"
                f"Display update error: {exc}\n"
                "Do not assume the operation was rolled back.",
                parent=self,
            )
            return

        if report["ok"]:
            messagebox.showinfo(
                "VOUT changes applied",
                "\n".join(lines)
                + "\n\n"
                "Register readback matched the requested RAW values.\n"
                "Check telemetry separately.\n"
                "Monitoring remains stopped. NVM was not stored.",
                parent=self,
            )
        else:
            messagebox.showwarning(
                "VOUT changes stopped",
                "\n".join(lines)
                + "\n\n"
                "Earlier writes were not rolled back.\n"
                "An unverified write may have reached the device.\n"
                "Review the device state before preparing a new plan.\n"
                "Monitoring remains stopped.",
                parent=self,
            )

    def _other_tab_operation_busy(self):
        """Ask the application whether another tab is occupied."""
        widget = self.master

        while widget is not None:
            check = getattr(
                widget, "_other_tab_operation_busy", None
            )

            if callable(check):
                return bool(check(self))

            widget = getattr(widget, "master", None)

        return False

    def _config_operation_busy(self):
        """Return whether any Config editor has an active operation."""
        return any(
            editor._busy
            for editor in self._config_editors()
        )

    def _run_guarded_action(self, callback):
        """Reject repeated actions, including during confirmation."""
        if (
            self._disconnected
            or self._action_busy
            or self._config_operation_busy()
            or self._other_tab_operation_busy()
        ):
            return

        self._action_busy = True

        try:
            return callback()
        finally:
            self._action_busy = False

    def do_store(self):
        return self._run_guarded_action(self._do_store_impl)

    def do_restore(self):
        return self._run_guarded_action(self._do_restore_impl)

    def do_dump_read(self):
        return self._run_guarded_action(self._do_dump_read_impl)

    def do_dump_write(self):
        return self._run_guarded_action(self._do_dump_write_impl)

    def _show_changes_preview(self, changes):
        """Display a prepared snapshot with guarded VOUT application."""
        window = tk.Toplevel(self)
        window.title(
            f"Preview Changes - {self.device.name} "
            f"0x{self.device.address:02X}"
        )
        window.geometry("1000x420")
        window.transient(self.winfo_toplevel())

        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        manual_count = sum(
            change["manual_only"]
            for change in changes
        )

        ttk.Label(
            window,
            text=(
                f"Prepared changes: {len(changes)}. "
                f"Individual-write-only fields: {manual_count}.\n"
                "Encoding checked, not board-specific safety. "
                "Opening this preview performs no writes. "
                "Apply VOUT Changes requires confirmation."
            ),
            justify="left",
            wraplength=940,
        ).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=8,
            pady=8,
        )

        frame = ttk.Frame(window)
        frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=8,
        )
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            frame,
            columns=(
                "scope",
                "command",
                "name",
                "previous",
                "new",
                "policy",
            ),
            show="headings",
            selectmode="browse",
        )

        columns = (
            ("scope", "Scope", 65),
            ("command", "Cmd", 55),
            ("name", "Register", 215),
            ("previous", "Last read / RAW", 220),
            ("new", "Entered / encoded RAW", 220),
            ("policy", "Handling", 155),
        )

        for name, title, width in columns:
            tree.heading(name, text=title)
            tree.column(
                name,
                width=width,
                minwidth=50,
                stretch=name in {
                    "name",
                    "previous",
                    "new",
                },
            )

        vertical = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=tree.yview,
        )
        horizontal = ttk.Scrollbar(
            frame,
            orient="horizontal",
            command=tree.xview,
        )
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        tree.tag_configure(
            "manual",
            foreground="#A65E00",
        )

        for change in changes:
            scope = (
                f"CH{change['page']}"
                if change["is_paged"]
                else "Global"
            )

            width = 2 if change["size"] == "byte" else 4
            previous_raw = (
                f"0x{change['previous_raw']:0{width}X}"
            )
            new_raw = (
                f"0x{change['new_raw']:0{width}X}"
            )

            tree.insert(
                "",
                "end",
                values=(
                    scope,
                    f"0x{change['cmd']:02X}",
                    change["name"],
                    (
                        f"{change['previous_text']} "
                        f"[{previous_raw}]"
                    ),
                    (
                        f"{change['new_text']} "
                        f"[{new_raw}]"
                    ),
                    (
                        "Individual write"
                        if change["manual_only"]
                        else "Encoding checked"
                    ),
                ),
                tags=(
                    ("manual",)
                    if change["manual_only"]
                    else ()
                ),
            )

        buttons = ttk.Frame(window)
        buttons.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=8,
        )

        check_button = ttk.Button(
            buttons,
            text="Check Device",
            width=14,
            command=lambda: self._check_preview_plan(
                changes, window, check_button
            ),
        )
        check_button.pack(side="left")

        apply_button = ttk.Button(
            buttons,
            text="Apply VOUT Changes",
            width=20,
            command=lambda: self._apply_vout_preview(
                changes, window, apply_button
            ),
        )
        apply_button.pack(side="left", padx=6)

        if getattr(self.device, "is_demo", False):
            apply_button.state(["disabled"])

        ttk.Button(
            buttons,
            text="Close",
            command=window.destroy,
        ).pack(side="right")

    # Top bar.
    def _build_top(self):
        top = ttk.Frame(self)
        top.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=4,
            pady=(3, 0),
        )

        # Left: device info and global telemetry.
        left = ttk.Frame(top)
        left.pack(
            side="left",
            fill="y",
            padx=(0, 4),
        )

        info = ttk.LabelFrame(left, text=" Device ")
        info.pack(
            fill="x",
            padx=2,
            pady=(0, 2),
        )

        info_items = [
            ("Address", f"0x{self.device.address:02X}"),
            ("IC", self.device.name),
            (
                "ID",
                f"0x{self.device.special_id:04X}"
                if self.device.special_id is not None
                else "N/A",
            ),
            ("Revision", self.device.revision),
            ("Pages", str(self.device.num_pages)),
            (
                "Capability",
                f"0x{self.device.capability:02X}"
                if self.device.capability is not None
                else "N/A",
            ),
            ("VOUT_MODE", self.device.get_vout_mode_text()),
        ]

        for row, (label, value) in enumerate(info_items):
            ttk.Label(
                info,
                text=label,
                font=("Segoe UI", 8, "bold"),
            ).grid(
                row=row,
                column=0,
                sticky="nw",
                padx=3,
                pady=0,
            )

            variable = tk.StringVar(value=str(value))
            self.device_info_vars[label] = variable

            ttk.Label(
                info,
                textvariable=variable,
                font=("Segoe UI", 8),
                justify="left",
                anchor="w",
            ).grid(
                row=row,
                column=1,
                sticky="w",
                padx=3,
                pady=0,
            )

        gt_fr = ttk.LabelFrame(
            left,
            text=" Global Telemetry ",
        )
        gt_fr.pack(
            fill="x",
            padx=2,
            pady=(0, 2),
        )

        for field in self._global_telemetry_fields:
            key = field.key
            label = field.label
            unit = field.unit
            color = field.color

            row_frame = ttk.Frame(gt_fr)
            row_frame.pack(
                fill="x",
                padx=3,
                pady=1,
            )

            ttk.Label(
                row_frame,
                text=label,
                width=14,
                anchor="w",
                font=("Segoe UI", 8),
            ).pack(side="left")

            value_label = tk.Label(
                row_frame,
                text="---",
                font=("Consolas", 11, "bold"),
                fg=color,
                bg="#1a1a2e",
                width=9,
                anchor="e",
                relief="sunken",
                padx=3,
            )
            value_label.pack(
                side="left",
                padx=3,
            )

            ttk.Label(
                row_frame,
                text=unit,
                width=3,
                font=("Segoe UI", 8),
            ).pack(side="left")

            self.global_telem_lbl[key] = value_label

        # Middle: global configuration.
        gc = ttk.LabelFrame(
            top,
            text=" Global Config ",
            width=370,
        )
        gc.pack(
            side="left",
            fill="y",
            expand=False,
            padx=4,
        )

        # Keep a fixed requested width.
        gc.pack_propagate(False)

        self.global_config_editor = ConfigNotebook(
            gc,
            self.device,
            self.gui_profile,
            page=0,
            paged=False,
        )
        self.global_config_editor.pack(
            fill="both",
            expand=True,
            padx=2,
            pady=2,
        )

        self._build_output_controls(top)

        # Right: global status.
        gs = ttk.LabelFrame(
            top,
            text=" Global Status ",
        )
        gs.pack(
            side="left",
            fill="both",
            expand=True,
            padx=4,
        )

        self.global_status_ind = tk.Label(
            gs,
            text="---",
            font=("Consolas", 9, "bold"),
            bg="#1a1a2e",
            fg="#00ff00",
            anchor="center",
            relief="sunken",
        )
        self.global_status_ind.pack(
            fill="x",
            padx=2,
            pady=(2, 1),
        )

        gtf = ttk.Frame(gs)
        gtf.pack(
            fill="both",
            expand=True,
            padx=2,
            pady=(1, 2),
        )
        gtf.rowconfigure(0, weight=1)
        gtf.columnconfigure(0, weight=1)

        self.global_tree = ttk.Treeview(
            gtf,
            columns=("val", "hex"),
            height=8,
        )
        configure_status_tree(
            self.global_tree,
            global_view=True,
        )

        vertical = ttk.Scrollbar(
            gtf,
            orient="vertical",
            command=self.global_tree.yview,
        )
        horizontal = ttk.Scrollbar(
            gtf,
            orient="horizontal",
            command=self.global_tree.xview,
        )
        self.global_tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        self.global_tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        vertical.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        horizontal.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        for tag in ("fault", "warn", "ok"):
            self.global_tree.tag_configure(
                tag,
                foreground={
                    "fault": "#FF4444",
                    "warn": "#FFD700",
                    "ok": "#228B22",
                }[tag],
            )

    # Channel columns.
    def _build_channels(self):
        ch_frame = ttk.Frame(self)
        ch_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=4,
            pady=2,
        )
        ch_frame.rowconfigure(0, weight=1)

        for page in range(self.device.num_pages):
            ch_frame.columnconfigure(page, weight=1)

            channel = ChannelColumn(
                ch_frame,
                self.device,
                page,
            )
            channel.grid(
                row=0,
                column=page,
                sticky="nsew",
                padx=3,
                pady=2,
            )
            self.channels.append(channel)

    # Button bar.
    def _build_buttons(self):
        bf = ttk.Frame(self)
        bf.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=4,
            pady=(0, 4),
        )

        for text, command in [
            ("Read All", self.do_read_all),
            ("Write All", self.do_write_all),
            ("Store NVM", self.do_store),
            ("Restore NVM", self.do_restore),
            ("Clear Faults", self.do_clear),
        ]:
            button = ttk.Button(
                bf,
                text=text,
                command=command,
            )
            button.pack(
                side="left",
                padx=2,
            )

            button_attributes = {
                "Read All": "read_all_btn",
                "Store NVM": "store_nvm_btn",
                "Restore NVM": "restore_nvm_btn",
                "Clear Faults": "clear_faults_btn",
            }

            attribute = button_attributes.get(text)
            if attribute is not None:
                setattr(self, attribute, button)
                button.configure(width=11)

            if (
                getattr(self.device, "is_demo", False)
                and text in {
                    "Store NVM",
                    "Restore NVM",
                    "Clear Faults",
                }
            ):
                button.state(["disabled"])

        ttk.Separator(
            bf,
            orient="vertical",
        ).pack(
            side="left",
            fill="y",
            padx=6,
        )

        self.mon_btn = ttk.Button(
            bf,
            text="Start Monitor",
            command=self.toggle_monitor,
        )
        self.mon_btn.pack(
            side="left",
            padx=2,
        )

        ttk.Separator(
            bf,
            orient="vertical",
        ).pack(
            side="left",
            fill="y",
            padx=6,
        )

        self.dump_mode_var = tk.StringVar(value="Page Dump")

        self.dump_mode_combo = ttk.Combobox(
            bf,
            textvariable=self.dump_mode_var,
            values=("Page Dump", "Full Dump"),
            width=10,
            state="readonly",
        )
        self.dump_mode_combo.pack(
            side="left",
            padx=2,
        )
        self.dump_mode_combo.bind(
            "<<ComboboxSelected>>",
            self._update_dump_mode,
        )

        ttk.Label(
            bf,
            text="Page",
        ).pack(
            side="left",
            padx=(4, 1),
        )

        self.dump_page_var = tk.StringVar(value="0")

        self.dump_page_combo = ttk.Combobox(
            bf,
            textvariable=self.dump_page_var,
            values=[
                str(page)
                for page in range(self.device.num_pages)
            ],
            width=3,
            state="readonly",
        )
        self.dump_page_combo.pack(
            side="left",
            padx=2,
        )

        for text, command in [
            ("Read Dump", self.do_dump_read),
            ("Save CSV", self.do_dump_save),
            ("Load CSV", self.do_dump_load),
            ("Write Dump", self.do_dump_write),
        ]:
            button = ttk.Button(
                bf,
                text=text,
                command=command,
            )
            button.pack(
                side="left",
                padx=2,
            )

            if text == "Read Dump":
                self.read_dump_btn = button
                button.configure(width=11)

            elif text == "Write Dump":
                self.write_dump_btn = button
                button.configure(width=11)

                if getattr(self.device, "is_demo", False):
                    button.state(["disabled"])

    # Read and write.
    def _write_single_global(self, key):
        """Write one global parameter to device."""
        if key not in self.global_cfg_data:
            return

        try:
            new_value = float(self.global_cfg_vars[key].get())
        except ValueError:
            return

        config = self.global_cfg_data[key]
        ok = self.device.write_val(
            0,
            config["cmd"],
            new_value,
            config["fmt"],
        )

        widget = self._global_wr_btns.get(key)
        if widget:
            color = "#00E676" if ok else "#FF5252"
            widget.itemconfig("tri", fill=color)
            self.after(
                600,
                lambda current=widget: current.itemconfig(
                    "tri",
                    fill="#4CAF50",
                ),
            )

    def _is_full_dump_mode(self):
        variable = getattr(self, "dump_mode_var", None)
        return (
            variable is not None
            and variable.get() == "Full Dump"
        )

    def _update_dump_mode(self, event=None):
        if self._disconnected:
            return

        full_mode = self._is_full_dump_mode()

        self.dump_page_combo.configure(
            state="disabled" if full_mode else "readonly"
        )

        self.write_dump_btn.configure(
            text="Check Restore" if full_mode else "Write Dump",
            width=13 if full_mode else 11,
        )

    def _update_address_info_from_config(self):
        """Refresh channel address labels without device I/O."""
        if self.device.name != "LTM4677":
            return

        for channel in self.channels:
            channel.update_rail_address(
                disconnected=self._disconnected
            )

    def update_device_info(self):
        values = {
            "IC": self.device.name,
            "Address": f"0x{self.device.address:02X}",
            "ID": (
                f"0x{self.device.special_id:04X}"
                if self.device.special_id is not None
                else "N/A"
            ),
            "Revision": self.device.revision,
            "Pages": f"{self.device.num_pages} channel(s)",
            "Capability": (
                f"0x{self.device.capability:02X}"
                if self.device.capability is not None
                else "N/A"
            ),
            "VOUT_MODE": self.device.get_vout_mode_text(),
        }

        for key, value in values.items():
            variable = self.device_info_vars.get(key)
            if variable is not None:
                variable.set(str(value))
        self._update_address_info_from_config()

    def _disable_controls(self, parent):
        for widget in parent.winfo_children():
            if isinstance(
                widget,
                (
                    ttk.Button,
                    ttk.Entry,
                    ttk.Combobox,
                    ttk.Checkbutton,
                    ttk.Radiobutton,
                    ttk.Spinbox,
                ),
            ):
                widget.state(["disabled"])

            elif isinstance(
                widget,
                (
                    tk.Button,
                    tk.Entry,
                    tk.Checkbutton,
                    tk.Radiobutton,
                    tk.Spinbox,
                    tk.Scale,
                ),
            ):
                widget.configure(state="disabled")

            elif isinstance(widget, tk.Canvas):
                # Disable the Canvas write buttons used in this GUI.
                for sequence in (
                    "<Button-1>",
                    "<ButtonRelease-1>",
                    "<Enter>",
                    "<Leave>",
                ):
                    widget.unbind(sequence)

                widget.configure(cursor="")
                widget.itemconfigure(
                    "all",
                    state="disabled",
                )

            self._disable_controls(widget)

    def _handle_disconnect(self, exc):
        """Stop this tab after a fatal transport error."""
        if self._disconnected:
            return

        # This flag means the tab cannot perform further I/O.
        # It covers both USB removal and an unusable session.
        self._disconnected = True
        self.stop_all()

        session_failed = isinstance(
            exc,
            TransportSessionFailedError,
        )

        title = (
            "Transport session failed"
            if session_failed
            else "Adapter disconnected"
        )
        indicator = (
            "TRANSPORT FAILED / STALE DATA"
            if session_failed
            else "DISCONNECTED / STALE DATA"
        )
        tree_message = (
            "Transport session failed; previous data is stale"
            if session_failed
            else "Adapter disconnected; previous data is stale"
        )

        if hasattr(self, "global_config_editor"):
            self.global_config_editor.mark_stale()

        for channel in self.channels:
            if hasattr(channel, "config_editor"):
                channel.config_editor.mark_stale()

        self._update_address_info_from_config()
        self.global_cfg_data = {}

        for variable in self.global_cfg_vars.values():
            variable.set("STALE")

        for label in self.global_telem_lbl.values():
            label.configure(text="STALE")

        reset_status_tree(self.global_tree)
        self.global_tree.insert(
            "",
            "end",
            text=tree_message,
            values=("ERR", "---"),
            tags=("error",),
        )
        self.global_status_ind.configure(
            text=indicator,
            fg="#FF8A80",
        )

        # Channel values may remain as historical data.
        # Disable controls without reading the device.
        self._disable_controls(self)

        messagebox.showerror(
            title,
            f"{exc}\n\n"
            "Current operation stopped. Monitoring stopped.\n"
            "Previously displayed channel values are stale.\n"
            "Check the adapter connection, then use Refresh and Scan.",
            parent=self,
        )

    def _allow_device_action(self):
        """Reject hardware actions on an unusable or demo device."""
        return (
            not self._disconnected
            and not getattr(self.device, "is_demo", False)
        )

    def _do_store_impl(self):
        if not self._allow_device_action():
            return

        confirmed = messagebox.askyesno(
            "Store NVM",
            "Save the current device configuration to NVM?\n\n"
            "This stores device settings, not unwritten Config input.\n"
            "Monitoring will be stopped.",
            parent=self,
        )

        # Confirmation dialogs run a nested Tk event loop.
        if not confirmed or not self._allow_device_action():
            return

        self.stop_all()

        try:
            with self._button_activity(
                self.store_nvm_btn, "Storing..."
            ):
                if not self.device.store_user_all():
                    raise OSError(
                        self.device.last_error
                        or "Device did not confirm NVM store completion."
                    )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(
                type(exc)(
                    f"Store NVM\n{exc}\n\n"
                    "The command may have reached the device.\n"
                    "NVM store completion is unknown."
                )
            )
            return

        except Exception as exc:
            messagebox.showerror(
                "Store NVM failed",
                f"{exc}\n\n"
                "NVM store completion is not confirmed.\n"
                "Do not assume the previous NVM contents are intact.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return

        messagebox.showinfo(
            "Store NVM completed",
            "The NVM store command completed and the device "
            "reported ready.\n\n"
            "NVM contents were not independently compared.\n"
            "Monitoring remains stopped.",
            parent=self,
        )

    def _do_restore_impl(self):
        if not self._allow_device_action():
            return

        confirmed = messagebox.askyesno(
            "Restore NVM",
            "Restore the saved NVM configuration into the device?\n\n"
            "This can change active device settings.\n"
            "Monitoring will be stopped.\n"
            "Config fields will not be reloaded automatically.",
            parent=self,
        )

        if not confirmed or not self._allow_device_action():
            return

        self.stop_all()

        try:
            with self._button_activity(
                self.restore_nvm_btn, "Restoring..."
            ):
                if not self.device.restore_user_all():
                    raise OSError(
                        self.device.last_error
                        or "NVM restore or device identification failed."
                    )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(
                type(exc)(
                    f"Restore NVM\n{exc}\n\n"
                    "The command may have reached the device.\n"
                    "Active settings may already have changed."
                )
            )
            return

        except Exception as exc:
            messagebox.showerror(
                "Restore NVM failed",
                f"{exc}\n\n"
                "Active settings may already have changed.\n"
                "Displayed Config values are not confirmed current.\n"
                "Monitoring remains stopped.\n"
                "If device identification failed, use Refresh "
                "and Scan before further operations.",
                parent=self,
            )
            return

        messagebox.showinfo(
            "Restore NVM completed",
            "The restore command completed and the device "
            "was identified again.\n\n"
            "Displayed Config values have not been refreshed.\n"
            "Use Read All to reload them. It will ask before "
            "replacing unwritten input.\n"
            "Monitoring remains stopped.",
            parent=self,
        )

    def do_clear(self):
        return self._run_guarded_action(self._do_clear_impl)

    def _do_clear_impl(self):
        if not self._allow_device_action():
            return

        self.stop_all()

        try:
            with self._button_activity(
                self.clear_faults_btn, "Clearing..."
            ):
                if not self.device.clear_faults():
                    raise OSError(
                        self.device.last_error
                        or "Clear Faults did not complete."
                    )

                self._refresh_status()

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)

        except Exception as exc:
            messagebox.showerror(
                "Clear Faults failed",
                f"{exc}\n\nMonitoring remains stopped.",
                parent=self,
            )

    # Monitor.
    def toggle_monitor(self):
        if (
            self._disconnected
            or self._action_busy
            or self._config_operation_busy()
            or self._other_tab_operation_busy()
        ):
            return

        if self.monitoring:
            self.stop_all()
            return

        self.monitoring = True
        self.mon_btn.configure(text="Stop Monitor")
        self._mon_loop()

    def _mon_loop(self):
        self._monitor_after_id = None

        if not self.monitoring or self._disconnected:
            return

        if (
            self._action_busy
            or self._config_operation_busy()
            or self._other_tab_operation_busy()
        ):
            self._monitor_after_id = self.after(
                500,
                self._mon_loop,
            )
            return

        try:
            telemetry = self.device.read_global_telemetry()

            for key in self.global_telem_lbl:
                value = telemetry.get(key)
                label = self.global_telem_lbl.get(key)

                if label is not None:
                    label.configure(
                        text=(
                            f"{value:.3f}"
                            if value is not None
                            else "N/A"
                        )
                    )

            for channel in self.channels:
                telemetry = (
                    self.device.read_channel_telemetry(
                        channel.page
                    )
                )
                channel.update_telemetry(telemetry)

            self._refresh_status()

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)
            return

        except Exception as exc:
            self.stop_all()
            messagebox.showerror(
                "Monitor error",
                str(exc),
                parent=self,
            )
            return

        if self.monitoring and not self._disconnected:
            self._monitor_after_id = self.after(
                500,
                self._mon_loop,
            )

    def _refresh_status(self):
        if self._disconnected:
            return

        try:
            for channel in self.channels:
                try:
                    status = self.device.read_channel_status(
                        channel.page
                    )

                except TransportDisconnectedError:
                    raise

                except Exception:
                    status = {}

                channel.update_status(status)

            try:
                global_status = self.device.read_global_status()

            except TransportDisconnectedError:
                raise

            except Exception:
                global_status = {
                    "STATUS_INPUT": None,
                    "STATUS_CML": None,
                }

                regmap = getattr(self.device, "_regmap", {})

                for name, cmd in (
                    ("MFR_PADS", 0xE5),
                    ("MFR_COMMON", 0xEF),
                ):
                    info = regmap.get(cmd)
                    if info is not None and not info[3]:
                        global_status[name] = None

                adc_name = "MFR_ADC_TELEMETRY_STATUS"
                adc_cmd = self.device.command_code(adc_name)
                adc_info = regmap.get(adc_cmd)

                if (
                    adc_info is not None
                    and adc_info[1] == "byte"
                    and not adc_info[3]
                ):
                    global_status[adc_name] = None

            self._update_global_status(global_status)

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)

    def _update_global_status(self, status_data):
        tree = self.global_tree
        configure_status_tree(
            tree,
            global_view=True,
        )
        expanded = reset_status_tree(tree)
        levels = []

        for name in ("STATUS_INPUT", "STATUS_CML"):
            levels.append(
                render_standard_status(
                    tree,
                    expanded,
                    self.device,
                    name,
                    status_data.get(name),
                    8,
                )
            )

        if "MFR_PADS" in status_data:
            levels.append(
                render_mfr_pads(
                    tree,
                    expanded,
                    self.device,
                    status_data["MFR_PADS"],
                )
            )

        if "MFR_COMMON" in status_data:
            levels.append(
                render_mfr_common(
                    tree,
                    expanded,
                    self.device,
                    status_data["MFR_COMMON"],
                )
            )

        if "MFR_ADC_TELEMETRY_STATUS" in status_data:
            levels.append(
                render_mfr_adc_telemetry_status(
                    tree,
                    expanded,
                    self.device,
                    status_data["MFR_ADC_TELEMETRY_STATUS"],
                )
            )

        set_global_status_indicator(
            self.global_status_ind,
            levels,
            self.device,
        )

        if (
            self.device.name == "LTM4677"
            and "unknown" in levels
            and "fault" not in levels
            and "warn" not in levels
            and "error" not in levels
        ):
            self.global_status_ind.configure(
                text="GLOBAL STATUS / CHECK RAW",
            )

    # Dump.
    def _dump_page(self):
        try:
            return int(self.dump_page_var.get())
        except ValueError:
            return 0

    def _do_dump_read_impl(self):
        if self._is_full_dump_mode():
            return self._read_full_dump_impl()

        # Reading synthetic registers is allowed in demo mode.
        if self._disconnected:
            return

        page = self._dump_page()
        self.stop_all()

        try:
            with self._button_activity(
                self.read_dump_btn, "Reading..."
            ):
                records = self.device.read_full_dump(page)

            # Replace the previous snapshot only after completion.
            self._dump_data[page] = records
            self._dump_metadata.pop(page, None)

            successful = sum(
                record["raw"] is not None
                for record in records
            )
            failed = len(records) - successful

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)
            return

        except Exception as exc:
            messagebox.showerror(
                "Read Dump failed",
                f"{exc}\n\n"
                "No new dump snapshot was saved.\n"
                "Any previous snapshot remains unchanged.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return

        text = (
            f"Page {page}\n"
            f"Registers read: {successful}\n"
            f"Read errors: {failed}\n\n"
            "Monitoring remains stopped."
        )

        if failed:
            messagebox.showwarning(
                "Dump read with errors",
                text,
                parent=self,
            )
        else:
            messagebox.showinfo(
                "Dump read completed",
                text,
                parent=self,
            )

    def do_dump_save(self):
        return self._run_guarded_action(self._do_dump_save_impl)

    def _do_dump_save_impl(self):
        if self._is_full_dump_mode():
            return self._save_full_dump_impl()

        page = self._dump_page()

        if page not in self._dump_data:
            confirmed = messagebox.askyesno(
                "Save CSV",
                "Read dump first?",
                parent=self,
            )

            if not confirmed or self._disconnected:
                return

            # The outer Save CSV action already holds the guard.
            self._do_dump_read_impl()

            if self._disconnected or page not in self._dump_data:
                return

        # Serialize before the dialog to preserve one snapshot.
        try:
            if page in self._dump_metadata:
                csv_text = dump_to_csv_string(
                    self.device,
                    self._dump_data[page],
                    page,
                    metadata=dict(self._dump_metadata[page]),
                )
            else:
                csv_text = dump_to_csv_string(
                    self.device,
                    self._dump_data[page],
                    page,
                )

        except Exception as exc:
            messagebox.showerror(
                "CSV export failed",
                str(exc),
                parent=self,
            )
            return

        filename = (
            f"{self.device.name}_0x{self.device.address:02X}"
            f"_p{page}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )

        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=filename,
        )

        if not path or self._disconnected:
            return

        try:
            save_csv_atomic(path, csv_text)

        except Exception as exc:
            messagebox.showerror(
                "CSV save failed",
                str(exc),
                parent=self,
            )
            return

        messagebox.showinfo(
            "CSV saved",
            f"Saved: {path}",
            parent=self,
        )

    def _read_full_dump_impl(self):
        if self._disconnected:
            return False

        self.stop_all()

        try:
            with self._button_activity(
                self.read_dump_btn, "Reading..."
            ):
                records = self.device.read_device_dump()

                snapshot = [
                    dict(record)
                    for record in records
                ]

                # Prepare the summary before publishing anything.
                globals_count = sum(
                    not record["is_paged"]
                    for record in snapshot
                )
                channel_count = len(snapshot) - globals_count

                # Capture and validate source metadata now.
                text = device_dump_to_csv_string(
                    self.device, snapshot
                )
                metadata, _ = csv_string_to_dump(text)
                source_metadata = dict(metadata)

                summary = (
                    f"Pages: {self.device.num_pages}\n"
                    f"Global registers: {globals_count}\n"
                    f"Channel registers: {channel_count}\n"
                    f"Total registers: {len(snapshot)}\n\n"
                    "Monitoring remains stopped."
                )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)
            return False

        except Exception as exc:
            messagebox.showerror(
                "Full Dump read failed",
                f"{exc}\n\n"
                "The previous full snapshot remains unchanged.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return False

        # No event-loop calls between the two assignments.
        self._full_dump_data = snapshot
        self._full_dump_metadata = source_metadata
        self._full_restore_report = None

        messagebox.showinfo(
            "Full Dump read completed",
            summary,
            parent=self,
        )
        return True

    def _save_full_dump_impl(self):
        if self._full_dump_data is None:
            confirmed = messagebox.askyesno(
                "Save Full Dump",
                "Read a full device dump first?",
                parent=self,
            )
            if not confirmed or self._disconnected:
                return

            if not self._read_full_dump_impl():
                return

        try:
            csv_text = device_dump_to_csv_string(
                self.device,
                self._full_dump_data,
                metadata=dict(self._full_dump_metadata),
            )

        except Exception as exc:
            messagebox.showerror(
                "Full CSV export failed",
                str(exc),
                parent=self,
            )
            return

        filename = (
            f"{self.device.name}_0x{self.device.address:02X}"
            f"_full_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )

        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=filename,
        )

        if not path or self._disconnected:
            return

        try:
            save_csv_atomic(path, csv_text)
        except Exception as exc:
            messagebox.showerror(
                "Full CSV save failed",
                str(exc),
                parent=self,
            )
            return

        messagebox.showinfo(
            "CSV saved",
            f"Saved: {path}",
            parent=self,
        )

    def do_dump_load(self):
        return self._run_guarded_action(self._do_dump_load_impl)

    def _do_dump_load_impl(self):
        # Capture the destination before entering the file dialog.
        page = self._dump_page()

        path = filedialog.askopenfilename(
            parent=self,
            filetypes=[("CSV", "*.csv")],
        )

        if not path or self._disconnected:
            return

        try:
            with open(path, "r", encoding="utf-8") as stream:
                text = stream.read()

            meta, records = csv_string_to_dump(text)

            if meta.get("dump_scope") == "full":
                self._accept_full_dump(meta, records)
                return

            if self._is_full_dump_mode():
                raise ValueError(
                    "This CSV is a Page Dump.\n"
                    "Select Page Dump mode before loading it."
                )

            if not records:
                messagebox.showerror(
                    "Load CSV",
                    "No data in CSV.",
                    parent=self,
                )
                return

            writable = sum(
                1
                for record in records
                if (
                    not record["readonly"]
                    and record["raw"] is not None
                )
            )

            result_text = (
                f"Total: {len(records)}, writable: {writable}"
            )

        except Exception as exc:
            messagebox.showerror(
                "Error",
                str(exc),
                parent=self,
            )
            return

        # Commit only after parsing and summary preparation succeed.
        self._dump_data[page] = records
        self._dump_metadata[page] = dict(meta)

        messagebox.showinfo(
            "Loaded",
            result_text,
            parent=self,
        )

    def _accept_full_dump(self, metadata, records):
        if not records:
            raise ValueError("Full CSV contains no records")

        snapshot = [dict(record) for record in records]
        source_metadata = dict(metadata)

        pages = sorted({
            record["page"]
            for record in snapshot
            if record["is_paged"]
        })
        writable = sum(
            not record["readonly"]
            and record["raw"] is not None
            for record in snapshot
        )

        summary = (
            f"Source model: {source_metadata.get('device')}\n"
            f"Pages: {', '.join(map(str, pages))}\n"
            f"Total records: {len(snapshot)}\n"
            f"Marked writable in CSV: {writable}\n\n"
            "Use Check Restore for compatibility and diagnostic checks.\n"
            "Full Restore execution is unavailable.\n"
            "Page Dump snapshots have not been changed."
        )

        self._full_dump_data = snapshot
        self._full_dump_metadata = source_metadata
        self._full_restore_report = None
        self.dump_mode_var.set("Full Dump")
        self._update_dump_mode()

        messagebox.showinfo(
            "Full Dump loaded",
            summary,
            parent=self,
        )

    def _do_dump_write_impl(self):
        if not self._allow_device_action():
            return

        if self._is_full_dump_mode():
            return self._check_full_restore_impl()

        full_mode = False
        page = self._dump_page()

        if full_mode:
            snapshot = self._full_dump_data
            source_metadata = self._full_dump_metadata
            scope_text = "Full Dump"
        else:
            snapshot = self._dump_data.get(page)
            source_metadata = self._dump_metadata.get(page)
            scope_text = f"Page Dump / page {page}"

        if snapshot is None:
            messagebox.showwarning(
                "Write Dump",
                "Load or read a dump in the selected mode first.",
                parent=self,
            )
            return

        # Copy and validate the snapshot before confirmation.
        try:
            records = [
                dict(record)
                for record in snapshot
                if (
                    not record.get("readonly", True)
                    and record.get("raw") is not None
                )
            ]

            if not records:
                messagebox.showwarning(
                    "Write Dump",
                    "Load or read a dump with writable values first.",
                    parent=self,
                )
                return

            records = self.device.validate_dump_write_records(
                records,
                default_page=page,
            )

            if full_mode and source_metadata is None:
                raise ValueError(
                    "Full Dump source metadata is missing"
                )

            if source_metadata is not None:
                self.device.validate_dump_metadata(
                    source_metadata,
                    records,
                )

            if not full_mode:
                wrong_pages = {
                    record["page"]
                    for record in records
                    if record["is_paged"]
                    and record["page"] != page
                }
                if wrong_pages:
                    raise ValueError(
                        "Page Dump contains channel records "
                        "for a different page. Select the matching "
                        "page and load the CSV again."
                    )

            global_count = sum(
                not record["is_paged"]
                for record in records
            )
            target_pages = sorted({
                record["page"]
                for record in records
                if record["is_paged"]
            })

            # A generic dump is not a board-specific startup plan.
            # Require manual handling instead of silently omitting
            # controls or writing them in command-code order.
            if full_mode:
                manual_controls = [
                    record["name"]
                    for record in records
                    if record["name"] in {
                        "OPERATION",
                        "ON_OFF_CONFIG",
                        "WRITE_PROTECT",
                    }
                ]
                if manual_controls:
                    raise ValueError(
                        "Full Dump contains controls requiring "
                        "an explicit write selection:\n"
                        + "\n".join(sorted(set(manual_controls)))
                        + "\n\nFull Dump writing is blocked until "
                        "the write-selection preview is implemented. "
                        "No subset was applied."
                    )

        except Exception as exc:
            messagebox.showerror(
                "Cannot prepare dump write",
                f"{exc}\n\n"
                "No registers were written.\n"
                "The dump snapshot has been preserved.",
                parent=self,
            )
            return

        confirmed = messagebox.askyesno(
            "Write Dump",
            f"{scope_text}\n"
            f"Channel pages: {target_pages}\n"
            f"Global records: {global_count}\n"
            f"Attempt to write {len(records)} dump records?\n\n"
            "Selected records passed offline profile validation.\n"
            "Current values will be read before the first write.\n"
            "Every record will be checked again before its write.\n"
            "Every successful write will be verified by readback.\n"
            "Execution stops at the first error.\n"
            "Earlier writes are not rolled back.\n"
            "Monitoring will be stopped. NVM will not be stored.",
            parent=self,
        )

        if not confirmed or not self._allow_device_action():
            return

        self.stop_all()

        try:
            with self._button_activity(
                self.write_dump_btn,
                "Writing...",
            ):
                report = self.device.apply_dump_write_plan(
                    records
                )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(
                type(exc)(
                    f"Write Dump\n{exc}\n\n"
                    "An unverified write may have reached "
                    "the device.\n"
                    "Earlier writes were not rolled back."
                )
            )
            return

        except Exception as exc:
            messagebox.showerror(
                "Write Dump stopped",
                f"{exc}\n\n"
                "An unverified write may have reached "
                "the device.\n"
                "Earlier writes were not rolled back.\n"
                "Config fields were not refreshed.\n"
                "Monitoring remains stopped.",
                parent=self,
            )
            return

        verified = report.get("verified", [])
        attempted = report.get("attempted", [])
        error = report.get("error")
        disconnect = report.get("disconnect")

        lines = [
            f"Selected records: {len(records)}",
            f"Write attempts: {len(attempted)}",
            f"Verified by readback: {len(verified)}",
            f"Not attempted: {len(records) - len(attempted)}",
        ]

        for item in verified:
            width = (
                2
                if item.get("size") == "byte"
                else 4
            )

            actual = item.get("actual_raw")

            if actual is None:
                actual_text = "N/A"
            else:
                actual_text = (
                    f"0x{actual:0{width}X}"
                )

            lines.append(
                f"Page {item['page']}, "
                f"command 0x{item['cmd']:02X}: "
                f"{actual_text} verified"
            )

        if error:
            lines.extend(("", str(error)))

        if disconnect is not None:
            lines.extend(
                (
                    "",
                    "An unverified write may have reached "
                    "the device.",
                    "Earlier writes were not rolled back.",
                )
            )

            self._handle_disconnect(
                type(disconnect)(
                    "Write Dump\n" + "\n".join(lines)
                )
            )
            return

        if report.get("ok", False):
            messagebox.showinfo(
                "Dump write verified",
                "\n".join(lines)
                + "\n\n"
                "All write readbacks matched the requested "
                "values.\n"
                "Config fields were not refreshed.\n"
                "Monitoring remains stopped. NVM was not stored.",
                parent=self,
            )
            return

        messagebox.showwarning(
            "Dump write stopped",
            "\n".join(lines)
            + "\n\n"
            "Earlier writes were not rolled back.\n"
            "An unverified write may have reached the device.\n"
            "Review the device state before another operation.\n"
            "Config fields were not refreshed.\n"
            "Monitoring remains stopped.",
            parent=self,
        )

    def _do_read_regs(self):
        """Trigger read all in register tab."""
        if hasattr(self, "_reg_tab"):
            self._reg_tab._do_read_all()

    # Cleanup.
    def stop_all(self):
        self.monitoring = False

        after_id = self._monitor_after_id
        self._monitor_after_id = None

        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass

        if hasattr(self, "mon_btn"):
            self.mon_btn.configure(text="Start Monitor")

    def destroy(self):
        self.stop_all()
        super().destroy()
