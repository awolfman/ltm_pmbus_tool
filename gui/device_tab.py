# gui/device_tab.py

"""Display a prepared plan with guarded VOUT application."""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from contextlib import contextmanager, nullcontext

from core.dump_csv import (
    dump_to_csv_string,
    csv_string_to_dump,
    save_csv_atomic,
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
            # Do not reload Config while an individual operation
            # is waiting in a confirmation dialog.
            if editor._busy:
                messagebox.showwarning(
                    "Config operation in progress",
                    "Finish the current Config operation first.",
                    parent=self,
                )
                return False

            try:
                if editor.get_pending_changes():
                    has_edits = True

            except ValueError:
                # Input exists without a usable baseline.
                # Reloading would replace that input too.
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
                    "Config fields have changed since this "
                    "preview was opened.\n\n"
                    "Close this window and use Preview Changes again.",
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
                        "Preview Changes again."
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
            ("IC", self.device.name),
            ("Address", f"0x{self.device.address:02X}"),
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
            ("Preview Changes", self.do_write_all),
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

        ttk.Label(
            bf,
            text="Dump page:",
        ).pack(
            side="left",
            padx=(4, 1),
        )

        self.dump_page_var = tk.StringVar(value="0")
        page_values = [
            str(page)
            for page in range(self.device.num_pages)
        ]

        ttk.Combobox(
            bf,
            textvariable=self.dump_page_var,
            values=page_values,
            width=3,
            state="readonly",
        ).pack(
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

    def _do_dump_write_impl(self):
        if not self._allow_device_action():
            return

        page = self._dump_page()

        if page not in self._dump_data:
            messagebox.showwarning(
                "Write Dump",
                "Load or read a dump first.",
                parent=self,
            )
            return

        # Copy and validate the snapshot before confirmation.
        try:
            records = [
                dict(record)
                for record in self._dump_data[page]
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

            if page in self._dump_metadata:
                self.device.validate_dump_metadata(
                    self._dump_metadata[page],
                    records,
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
