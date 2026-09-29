"""Compact profile-driven configuration editor.

Standard L11/L16 fields use engineering values.
Other formats use explicit raw hexadecimal values.
Register availability and access come from the device profile.
"""

import math
import tkinter as tk
from tkinter import ttk, messagebox

from core.pmbus_device import TransportDisconnectedError
from core.pmbus_formats import decode_value
from gui.profiles.gui_config import (
    TAB_TITLES,
    build_config_layout,
)


# Restored from the existing channel editor.
CONTROL_OPTIONS = {
    "OPERATION": (
        (0x80, "On"),
        (0xA8, "Margin High"),
        (0x98, "Margin Low"),
        (0x40, "Soft Off"),
        (0x00, "Immediate Off"),
    ),
    "ON_OFF_CONFIG": (
        (0x1E, "CMD+RUN / Soft Off"),
        (0x1F, "CMD+RUN / Immed Off"),
        (0x16, "RUN only / Soft Off"),
        (0x17, "RUN only / Immed Off"),
    ),
}

LABELS = {
    "OPERATION": "Operation",
    "ON_OFF_CONFIG": "On/Off config",
    "WRITE_PROTECT": "Write protect",
    "VOUT_COMMAND": "VOUT Command",
    "VOUT_MAX": "VOUT Max",
    "VOUT_MARGIN_HIGH": "Margin High",
    "VOUT_MARGIN_LOW": "Margin Low",
    "VOUT_OV_FAULT_LIMIT": "OV Fault",
    "VOUT_OV_WARN_LIMIT": "OV Warn",
    "VOUT_UV_WARN_LIMIT": "UV Warn",
    "VOUT_UV_FAULT_LIMIT": "UV Fault",
    "IOUT_OC_FAULT_LIMIT": "OC Fault",
    "IOUT_OC_WARN_LIMIT": "OC Warn",
    "IOUT_UC_FAULT_LIMIT": "UC Fault",
    "VIN_ON": "VIN On",
    "VIN_OFF": "VIN Off",
    "VIN_OV_FAULT_LIMIT": "VIN OV Fault",
    "VIN_OV_WARN_LIMIT": "VIN OV Warn",
    "VIN_UV_WARN_LIMIT": "VIN UV Warn",
    "VIN_UV_FAULT_LIMIT": "VIN UV Fault",
    "IIN_OC_WARN_LIMIT": "IIN OC Warn",
    "FREQUENCY_SWITCH": "Frequency",
    "OT_FAULT_LIMIT": "OT Fault",
    "OT_WARN_LIMIT": "OT Warn",
    "UT_WARN_LIMIT": "UT Warn",
    "UT_FAULT_LIMIT": "UT Fault",
    "POWER_GOOD_ON": "Power Good On",
    "POWER_GOOD_OFF": "Power Good Off",
    "TON_DELAY": "On Delay",
    "TON_RISE": "On Rise",
    "TON_MAX_FAULT_LIMIT": "On Max Fault",
    "TOFF_DELAY": "Off Delay",
    "TOFF_FALL": "Off Fall",
    "TOFF_MAX_WARN_LIMIT": "Off Max Warn",
    "VOUT_TRANSITION_RATE": "VOUT Rate",
    "MFR_RETRY_DELAY": "Retry Delay",
    "MFR_RESTART_DELAY": "Restart Delay",
}

# Explicit units only. Do not infer units from address ranges.
UNITS = {
    "VOUT_COMMAND": "V",
    "VOUT_MAX": "V",
    "VOUT_MARGIN_HIGH": "V",
    "VOUT_MARGIN_LOW": "V",
    "VOUT_OV_FAULT_LIMIT": "V",
    "VOUT_OV_WARN_LIMIT": "V",
    "VOUT_UV_WARN_LIMIT": "V",
    "VOUT_UV_FAULT_LIMIT": "V",
    "VIN_ON": "V",
    "VIN_OFF": "V",
    "VIN_OV_FAULT_LIMIT": "V",
    "VIN_OV_WARN_LIMIT": "V",
    "VIN_UV_WARN_LIMIT": "V",
    "VIN_UV_FAULT_LIMIT": "V",
    "IOUT_OC_FAULT_LIMIT": "A",
    "IOUT_OC_WARN_LIMIT": "A",
    "IOUT_UC_FAULT_LIMIT": "A",
    "IIN_OC_WARN_LIMIT": "A",
    "OT_FAULT_LIMIT": "°C",
    "OT_WARN_LIMIT": "°C",
    "UT_WARN_LIMIT": "°C",
    "UT_FAULT_LIMIT": "°C",
    "POWER_GOOD_ON": "V",
    "POWER_GOOD_OFF": "V",
    "FREQUENCY_SWITCH": "kHz",
}


class ConfigNotebook(ttk.Frame):
    def __init__(
        self,
        parent,
        device,
        profile,
        page=0,
        paged=True,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)

        self.device = device
        self.page = page
        self.paged = paged

        self._rows = {}
        self._stale = False
        self._busy = False
        self._feedback_after_ids = {}

        self.layout = build_config_layout(
            device,
            profile,
            paged=paged,
        )

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        for title in TAB_TITLES:
            self._build_tab(title)

    def _owner(self):
        widget = self.master

        while widget is not None:
            if callable(
                getattr(widget, "_handle_disconnect", None)
            ):
                return widget
            widget = getattr(widget, "master", None)

        return None

    def _is_disconnected(self):
        owner = self._owner()
        return self._stale or bool(
            owner is not None
            and getattr(owner, "_disconnected", False)
        )

    def _individual_operation_blocked(self):
        """Reject individual actions while another operation is active."""
        if self._busy or self._is_disconnected():
            return True

        owner = self._owner()
        if owner is None:
            return False

        if getattr(owner, "_action_busy", False):
            return True

        check_local = getattr(
            owner, "_config_operation_busy", None
        )
        if callable(check_local) and check_local():
            return True

        check_other = getattr(
            owner, "_other_tab_operation_busy", None
        )
        return bool(
            callable(check_other) and check_other()
        )

    def prepare_pending_changes(self):
        """Encode edited fields without reading or writing hardware.

        Return prepared changes and validation errors separately.
        Manual-only controls remain visible but are not batch targets.
        """
        changes = self.get_pending_changes()
        prepared = []
        errors = []

        for change in changes:
            cmd = change["cmd"]
            item = self._rows[cmd]
            row = item["row"]

            scope = (
                f"CH{change['page']}"
                if change["is_paged"]
                else "Global"
            )

            try:
                baseline_raw = item["baseline_raw"]
                if baseline_raw is None:
                    raise ValueError(
                        "No successful baseline read"
                    )

                if not self._can_write(row):
                    raise ValueError("Register write is blocked")

                text = change["new_text"]

                if item["options"]:
                    if text not in item["options"]:
                        raise ValueError(
                            "Select a supported control value"
                        )
                    raw = item["options"][text]

                elif item["engineering"]:
                    raw = self.device.encode_register_value(
                        self.page,
                        cmd,
                        text,
                        row.fmt,
                    )

                else:
                    if not text.lower().startswith("0x"):
                        raise ValueError(
                            "Raw value must start with 0x"
                        )
                    raw = int(text, 16)

                maximum = (
                    0xFF if row.size == "byte" else 0xFFFF
                )
                if (
                    isinstance(raw, bool)
                    or not isinstance(raw, int)
                    or not 0 <= raw <= maximum
                ):
                    raise ValueError(
                        f"Raw value must be in 0x00..0x{maximum:X}"
                    )

                validator = getattr(
                    self.device,
                    "validate_register_raw",
                    None,
                )
                if callable(validator):
                    validator(cmd, raw, row.size)

                # Unchanged text was already excluded by
                # get_pending_changes. Now exclude equivalent
                # encodings of edited text.
                if raw == baseline_raw:
                    continue

                record = dict(change)
                record["previous_raw"] = baseline_raw
                record["new_raw"] = raw
                prepared.append(record)

            except (ValueError, TypeError, OverflowError) as exc:
                errors.append(
                    f"{scope} / {row.name} "
                    f"0x{cmd:02X}: {exc}"
                )

        return prepared, errors

    def _can_read(self, row):
        return (
            not self._is_disconnected()
            and self.device.can_read_register(
                row.cmd, row.size
            )
        )

    def _can_write(self, row):
        return (
            not self._is_disconnected()
            and not getattr(self.device, "is_demo", False)
            and row.writable
            and self.device.can_write_register(
                row.cmd, row.size
            )
        )

    def _build_tab(self, title):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=title)

        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            frame,
            highlightthickness=0,
            width=280,
            height=140,
        )
        vertical = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=canvas.yview,
        )
        horizontal = ttk.Scrollbar(
            frame,
            orient="horizontal",
            command=canvas.xview,
        )
        canvas.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        inner = ttk.Frame(canvas)
        window = canvas.create_window(
            0, 0, window=inner, anchor="nw"
        )

        def resize(event=None):
            width = max(
                canvas.winfo_width(),
                inner.winfo_reqwidth(),
            )
            canvas.itemconfigure(window, width=width)
            canvas.configure(
                scrollregion=canvas.bbox("all")
            )

        inner.bind("<Configure>", resize)
        canvas.bind("<Configure>", resize)

        groups = [
            group
            for group in self.layout
            if group.tab == title
        ]

        for group in groups:
            section = ttk.LabelFrame(
                inner,
                text=group.title,
            )
            section.pack(
                fill="x",
                padx=2,
                pady=2,
            )

            for index, row in enumerate(group.rows):
                self._build_row(section, index, row)

        if not groups:
            ttk.Label(
                inner,
                text="No registers in this scope",
            ).pack(
                anchor="w",
                padx=5,
                pady=5,
            )

        self._bind_wheel(canvas, canvas)

    def get_pending_changes(self):
        """Collect edited writable fields without device I/O."""
        if self._is_disconnected():
            raise RuntimeError(
                "Cannot collect changes from stale device data."
            )

        if self._busy:
            raise RuntimeError(
                "A Config operation is still running."
            )

        changes = []
        missing_baseline = []

        placeholders = {
            "",
            "---",
            "ERR",
            "N/A",
            "N/S",
            "STALE",
        }

        for cmd, item in self._rows.items():
            row = item["row"]

            if not self._can_write(row):
                continue

            text = item["variable"].get().strip()
            baseline = item["baseline"]

            if baseline is None:
                if text not in placeholders:
                    missing_baseline.append(row.name)
                continue

            # Compare against the displayed baseline first.
            # Unedited rounded values must not become write targets.
            if text == baseline.strip():
                continue

            if item["options"]:
                input_kind = "control"
            elif item["engineering"]:
                input_kind = "engineering"
            else:
                input_kind = "raw"

            changes.append({
                "page": self.page if self.paged else None,
                "cmd": cmd,
                "name": row.name,
                "size": row.size,
                "format": row.fmt,
                "is_paged": self.paged,
                "previous_text": baseline,
                "new_text": text,
                "input_kind": input_kind,
                "manual_only": row.name in {
                    "OPERATION",
                    "ON_OFF_CONFIG",
                    "WRITE_PROTECT",
                },
            })

        if missing_baseline:
            raise ValueError(
                "Read these registers before preparing changes.\n"
                "Reading replaces the current input.\n\n"
                + "\n".join(missing_baseline)
            )

        return changes

    def _build_row(self, parent, index, row):
        if row.cmd in self._rows:
            raise ValueError(
                f"Duplicate Config command 0x{row.cmd:02X}"
            )

        metadata = getattr(self.device, "_metadata", {})
        custom_formats = (
            metadata.get("custom_formats", {}) or {}
        )
        custom = row.cmd in custom_formats

        profile_options = metadata.get(
            "control_options", {}
        )

        options = (
            profile_options.get(
                row.name,
                CONTROL_OPTIONS.get(row.name, ()),
            )
            if not custom
            else ()
        )
        engineering = (
            not custom
            and not options
            and row.fmt in {"L11", "L16"}
        )

        readable = self._can_read(row)
        writable = self._can_write(row)

        label = LABELS.get(
            row.name,
            row.name.replace("_", " "),
        )
        ttk.Label(
            parent,
            text=label,
            width=14,
            wraplength=110,
            anchor="w",
            font=("Segoe UI", 8),
        ).grid(
            row=index,
            column=0,
            sticky="w",
            padx=2,
            pady=0,
        )

        variable = tk.StringVar(value="---")
        option_values = {
            f"0x{raw:02X}  {description}": raw
            for raw, description in options
        }

        if options:
            field = ttk.Combobox(
                parent,
                textvariable=variable,
                values=tuple(option_values),
                width=22,
                state=(
                    "readonly" if writable else "disabled"
                ),
                font=("Consolas", 9),
            )
            field.grid(
                row=index,
                column=1,
                columnspan=2,
                sticky="w",
                padx=1,
            )
        else:
            field = ttk.Entry(
                parent,
                textvariable=variable,
                width=9,
                justify="right",
                state=(
                    "normal" if writable else "readonly"
                ),
                font=("Consolas", 9),
            )
            field.grid(
                row=index,
                column=1,
                padx=1,
            )

            unit = "RAW hex"
            if engineering:
                register_units = (
                    metadata.get("register_units", {}) or {}
                )
                unit = register_units.get(
                    row.cmd,
                    UNITS.get(row.name, ""),
                )

            ttk.Label(
                parent,
                text=unit,
                font=("Segoe UI", 8),
                anchor="w",
            ).grid(
                row=index,
                column=2,
                sticky="w",
                padx=2,
            )

        read_button = None
        if readable:
            read_button = self._make_button(
                parent,
                color="#2196F3",
                outline="#1565C0",
                direction="left",
                callback=lambda cmd=row.cmd:
                    self._run_read(cmd),
            )
            read_button.grid(
                row=index,
                column=3,
                padx=(1, 2),
            )

        write_button = None
        if writable:
            write_button = self._make_button(
                parent,
                color="#4CAF50",
                outline="#2E7D32",
                direction="right",
                callback=lambda cmd=row.cmd:
                    self._run_write(cmd),
            )
            write_button.grid(
                row=index,
                column=4,
                padx=(1, 3),
            )

        self._rows[row.cmd] = {
            "row": row,
            "variable": variable,
            "field": field,
            "read_button": read_button,
            "write_button": write_button,
            "engineering": engineering,
            "options": option_values,
            "baseline": None,
            "baseline_raw": None,
        }

    @staticmethod
    def _make_button(
        parent,
        color,
        outline,
        direction,
        callback,
    ):
        button = tk.Canvas(
            parent,
            width=14,
            height=14,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )

        points = (
            (11, 2, 2, 7, 11, 12)
            if direction == "left"
            else (3, 2, 12, 7, 3, 12)
        )
        button.create_polygon(
            *points,
            fill=color,
            outline=outline,
            tags="tri",
        )
        button.bind(
            "<Button-1>",
            lambda event: callback(),
        )

        return button

    @staticmethod
    def _bind_wheel(widget, canvas):
        def scroll(event):
            if getattr(event, "num", None) == 4:
                steps = -3
            elif getattr(event, "num", None) == 5:
                steps = 3
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return
                steps = -1 if delta > 0 else 1

            canvas.yview_scroll(steps, "units")
            return "break"

        for sequence in (
            "<MouseWheel>",
            "<Button-4>",
            "<Button-5>",
        ):
            widget.bind(sequence, scroll, add="+")

        for child in widget.winfo_children():
            ConfigNotebook._bind_wheel(child, canvas)

    def _cancel_feedback(self, cmd, name):
        token = self._feedback_after_ids.pop(
            (cmd, name), None
        )
        if token is not None:
            try:
                self.after_cancel(token)
            except tk.TclError:
                pass

    def _cancel_all_feedback(self):
        for cmd, name in tuple(self._feedback_after_ids):
            self._cancel_feedback(cmd, name)

    def _begin_feedback(self, cmd, name):
        self._cancel_feedback(cmd, name)
        self._set_button_color(
            self._rows[cmd],
            name,
            "#FFC107",
        )

        # Paint the busy color before synchronous I2C access.
        # Do not start a nested event loop with update().
        self.update_idletasks()

    def _finish_feedback(self, cmd, name, success):
        self._cancel_feedback(cmd, name)

        item = self._rows[cmd]
        if (
            self._is_disconnected()
            or item.get(name) is None
        ):
            return

        self._set_button_color(
            item,
            name,
            "#00E676" if success else "#FF5252",
        )

        def restore():
            self._feedback_after_ids.pop(
                (cmd, name), None
            )
            if self._is_disconnected():
                return

            self._set_button_color(
                item,
                name,
                "#2196F3"
                if name == "read_button"
                else "#4CAF50",
            )

        self._feedback_after_ids[(cmd, name)] = (
            self.after(600, restore)
        )

    def _set_button_color(self, item, name, color):
        if self._is_disconnected():
            return

        button = item.get(name)
        if button is not None:
            button.itemconfigure("tri", fill=color)

    def _show(self, cmd, raw):
        item = self._rows[cmd]

        item["baseline"] = None
        item["baseline_raw"] = None

        self._show_value(cmd, raw)

        if self._is_disconnected() or raw is None:
            return

        text = item["variable"].get()

        if text in {"", "---", "ERR", "N/A", "N/S", "STALE"}:
            return

        item["baseline"] = text
        item["baseline_raw"] = raw

    def _show_value(self, cmd, raw):
        item = self._rows[cmd]
        row = item["row"]
        variable = item["variable"]

        if self._is_disconnected():
            variable.set("STALE")
            return

        if raw is None:
            variable.set("ERR")
            return

        if item["options"]:
            for text, value in item["options"].items():
                if value == raw:
                    variable.set(text)
                    return

            variable.set(f"0x{raw:02X}  Not in preset list")
            return

        if item["engineering"]:
            exponent = self.device.vout_exp.get(
                self.page, -13
            )
            value = decode_value(
                raw,
                row.fmt,
                exponent,
            )
            variable.set(
                f"{value:.4f}"
                if value is not None
                else "ERR"
            )
        else:
            width = 2 if row.size == "byte" else 4
            variable.set(f"0x{raw:0{width}X}")

    def _handle_disconnect(self, exc):
        # Use a different lookup start point in _owner:
        # the owner must be an ancestor, not this editor.
        owner = self._owner()

        if owner is not None:
            owner._handle_disconnect(exc)
        else:
            self.mark_stale()
            messagebox.showerror(
                "Adapter disconnected",
                str(exc),
                parent=self,
            )

    def _run_read(self, cmd):
        if self._individual_operation_blocked():
            return

        item = self._rows[cmd]
        row = item["row"]

        if not self._can_read(row):
            return

        self._busy = True

        try:
            self._begin_feedback(cmd, "read_button")

            raw = self.device.read_register(
                self.page, cmd
            )
            self._show(cmd, raw)
            self._finish_feedback(
                cmd,
                "read_button",
                raw is not None,
            )

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)

        except Exception as exc:
            if not self._is_disconnected():
                self._show(cmd, None)

            self._finish_feedback(
                cmd, "read_button", False
            )
            messagebox.showerror(
                "Register read failed",
                f"{row.name}\n{exc}",
                parent=self,
            )

        finally:
            self._busy = False

    def _run_write(self, cmd):
        if self._individual_operation_blocked():
            return

        item = self._rows[cmd]
        row = item["row"]

        if not self._can_write(row):
            return

        self._busy = True

        try:
            self._write_one(cmd)

        except TransportDisconnectedError as exc:
            self._handle_disconnect(exc)

        except Exception as exc:
            self._finish_feedback(
                cmd, "write_button", False
            )
            messagebox.showerror(
                "Register write failed",
                f"{row.name}\n{exc}",
                parent=self,
            )

        finally:
            self._busy = False

    def _write_one(self, cmd):
        item = self._rows[cmd]
        row = item["row"]
        text = item["variable"].get().strip()

        raw = None
        value = None

        if item["options"]:
            if text not in item["options"]:
                raise ValueError(
                    "Select a supported control value."
                )
            raw = item["options"][text]

        elif item["engineering"]:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError(
                    "Enter a finite engineering value."
                )

        else:
            if not text.lower().startswith("0x"):
                raise ValueError(
                    "Enter a raw hexadecimal value "
                    "starting with 0x."
                )
            raw = int(text, 16)

        if raw is not None:
            maximum = (
                0xFF if row.size == "byte" else 0xFFFF
            )
            if (
                isinstance(raw, bool)
                or not isinstance(raw, int)
                or not 0 <= raw <= maximum
            ):
                raise ValueError(
                    f"Raw value must be in 0x00..0x{maximum:X}"
                )

            validator = getattr(
                self.device,
                "validate_register_raw",
                None,
            )
            if callable(validator):
                validator(cmd, raw, row.size)

        scope = (
            f"Channel {self.page}"
            if self.paged
            else "Global device setting"
        )

        control_notice = ""

        if row.name == "OPERATION":
            control_notice = (
                "\n\nThe effect depends on ON_OFF_CONFIG "
                "and the external control pin. "
                "This selection does not indicate output state."
            )

            if (
                self.device.name == "LTM4673"
                and raw in {0x94, 0xA4, 0x54, 0x64}
            ):
                control_notice += (
                    "\n\nWARNING: This mode ignores faults "
                    "and warnings as described in the datasheet."
                )

        elif row.name == "ON_OFF_CONFIG":
            control_notice = (
                "\n\nThis changes startup and on/off control "
                "and may affect the output immediately."
            )

        confirmed = messagebox.askyesno(
            "Confirm register write",
            f"{self.device.name} at "
            f"0x{self.device.address:02X}\n"
            f"{scope}\n"
            f"{row.name} at 0x{cmd:02X}\n\n"
            f"Write {text}?\n\n"
            "This can change device operation."
            f"{control_notice}",
            parent=self,
        )

        # The dialog runs a nested Tk event loop.
        # Monitoring may detect a disconnect while it is open.
        if not confirmed or not self._can_write(row):
            return

        self._begin_feedback(cmd, "write_button")

        try:
            if raw is not None:
                ok = self.device.write_register(
                    self.page,
                    cmd,
                    raw,
                    row.size,
                )
            else:
                ok = self.device.write_val(
                    self.page,
                    cmd,
                    value,
                    row.fmt,
                )

        except TransportDisconnectedError:
            item["baseline"] = None
            item["baseline_raw"] = None
            raise

        except Exception as exc:
            # The transaction may have reached the device.
            # Preserve the requested input, not the old baseline.
            item["baseline"] = None
            item["baseline_raw"] = None
            raise OSError(
                f"{exc}\n"
                "The device state is not confirmed. "
                "Read the register before preparing changes. "
                "No automatic retry was performed."
            ) from exc

        if not ok:
            # A failed transaction may still have reached the device.
            # Keep the requested input, but invalidate the baseline.
            item["baseline"] = None
            item["baseline_raw"] = None

            detail = (
                getattr(self.device, "last_error", None)
                or "Register write failed."
            )
            raise OSError(
                f"{detail}\n"
                "The device state is not confirmed. "
                "Read the register before preparing changes. "
                "No automatic retry was performed."
            )

        # Write feedback covers both the write and readback.
        # Canonical OPERATION and ON_OFF_CONFIG writes require
        # an exact match. Other registers retain existing behavior.
        if self._can_read(row):
            self._begin_feedback(cmd, "read_button")

            try:
                readback = self.device.read_register(
                    self.page, cmd
                )
                self._show(cmd, readback)

            except TransportDisconnectedError:
                raise

            except Exception:
                self._show(cmd, None)
                self._finish_feedback(
                    cmd, "read_button", False
                )
                raise

            self._finish_feedback(
                cmd,
                "read_button",
                readback is not None,
            )

            if readback is None:
                raise OSError(
                    "Write completed, but readback failed."
                )

            if (
                row.name in {"OPERATION", "ON_OFF_CONFIG"}
                and raw is not None
                and readback != raw
            ):
                raise OSError(
                    "Control readback mismatch: "
                    f"requested 0x{raw:02X}, "
                    f"received 0x{readback:02X}. "
                    "The displayed value is the actual readback. "
                    "No automatic retry was performed."
                )

        self._finish_feedback(
            cmd, "write_button", True
        )

    def read_all(self):
        """Propagate errors to the owning DeviceTab."""
        if self._busy or self._is_disconnected():
            return

        self._busy = True

        try:
            for cmd, item in self._rows.items():
                if self._is_disconnected():
                    return

                if not self._can_read(item["row"]):
                    continue

                self._begin_feedback(cmd, "read_button")

                try:
                    raw = self.device.read_register(
                        self.page, cmd
                    )
                    self._show(cmd, raw)

                except TransportDisconnectedError:
                    raise

                except Exception:
                    self._show(cmd, None)
                    self._finish_feedback(
                        cmd, "read_button", False
                    )
                    raise

                self._finish_feedback(
                    cmd,
                    "read_button",
                    raw is not None,
                )

        finally:
            self._busy = False

    def mark_stale(self):
        self._stale = True
        self._cancel_all_feedback()

        for item in self._rows.values():
            item["baseline"] = None
            item["baseline_raw"] = None
            item["variable"].set("STALE")
            item["field"].state(["disabled"])

            for name in ("read_button", "write_button"):
                button = item.get(name)
                if button is None:
                    continue

                button.unbind("<Button-1>")
                button.configure(cursor="")
                button.itemconfigure(
                    "tri", fill="#AAAAAA"
                )

    def destroy(self):
        self._stale = True
        self._cancel_all_feedback()
        super().destroy()
