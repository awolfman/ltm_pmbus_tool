"""Profile-filtered register viewer and editor."""

import tkinter as tk
from tkinter import ttk, messagebox

from core.pmbus_formats import decode_value, encode_value


CATEGORIES = [
    ("Addressing", 0x00, 0x1F),
    ("Output Voltage", 0x20, 0x3F),
    ("Limits", 0x40, 0x6F),
    ("Status", 0x78, 0x9F),
    ("User / EEPROM", 0xB0, 0xCF),
    ("MFR Config", 0xD0, 0xEF),
    ("MFR Calibration", 0xF0, 0xFF),
]


def _category(cmd):
    for name, low, high in CATEGORIES:
        if low <= cmd <= high:
            return name
    return "Other"


class RegisterTab(ttk.Frame):
    def __init__(self, parent, device, **kw):
        super().__init__(parent, **kw)
        self.device = device
        self.regmap = {
            cmd: info
            for cmd, info in device._regmap.items()
            if device.can_read_register(cmd)
        }
        self.read_only = set(device.generic_write_blocked)
        self._entries = {}
        self._raw_cache = {}
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=4, pady=4)

        ttk.Label(top, text="Page").pack(side="left")
        self._page_var = tk.StringVar(value="0")
        selector = ttk.Combobox(
            top,
            textvariable=self._page_var,
            values=[str(p) for p in range(self.device.num_pages)],
            width=3,
            state="readonly",
        )
        selector.pack(side="left", padx=4)
        selector.bind("<<ComboboxSelected>>", self._page_changed)

        ttk.Button(
            top, text="Read All Registers",
            command=self._do_read_all,
        ).pack(side="left", padx=4)
        ttk.Button(
            top, text="Write All Changed",
            command=self._do_write_all,
        ).pack(side="left", padx=4)

        self._status_lbl = ttk.Label(top, text="")
        self._status_lbl.pack(side="right", padx=4)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=4, pady=4)

        groups = {}
        for cmd in sorted(self.regmap):
            groups.setdefault(_category(cmd), []).append(cmd)

        order = [name for name, _, _ in CATEGORIES] + ["Other"]
        for category in order:
            if category not in groups:
                continue

            frame = ttk.Frame(notebook)
            notebook.add(frame, text=category)

            canvas = tk.Canvas(frame, highlightthickness=0)
            scrollbar = ttk.Scrollbar(
                frame, orient="vertical", command=canvas.yview
            )
            inner = ttk.Frame(canvas)
            inner.bind(
                "<Configure>",
                lambda event, c=canvas:
                    c.configure(scrollregion=c.bbox("all")),
            )
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            for col, title in enumerate(
                ["Cmd", "Register", "Format", "Raw", "Value", "New"]
            ):
                ttk.Label(inner, text=title).grid(
                    row=0, column=col, padx=4, sticky="w"
                )

            for row_number, cmd in enumerate(groups[category], 1):
                self._build_row(inner, row_number, cmd)

    def _build_row(self, parent, row, cmd):
        name, size, fmt, paged = self.regmap[cmd]
        readonly = not self.device.can_write_register(cmd, size)

        ttk.Label(parent, text=f"0x{cmd:02X}").grid(
            row=row, column=0, padx=4, sticky="w"
        )
        ttk.Label(parent, text=name, width=32).grid(
            row=row, column=1, padx=4, sticky="w"
        )
        ttk.Label(parent, text=fmt).grid(
            row=row, column=2, padx=4
        )

        raw_label = ttk.Label(parent, text="---", width=10)
        raw_label.grid(row=row, column=3, padx=4)
        value_label = ttk.Label(parent, text="---", width=14)
        value_label.grid(row=row, column=4, padx=4)

        variable = tk.StringVar()
        if readonly:
            ttk.Label(parent, text="R/O").grid(
                row=row, column=5, padx=4
            )
        else:
            ttk.Entry(
                parent, textvariable=variable, width=14
            ).grid(row=row, column=5, padx=4)

        ttk.Button(
            parent, text="Read", width=6,
            command=lambda c=cmd: self._read_single(c),
        ).grid(row=row, column=6, padx=2, pady=1)

        if not readonly:
            ttk.Button(
                parent, text="Write", width=6,
                command=lambda c=cmd, v=variable:
                    self._write_single(c, v),
            ).grid(row=row, column=7, padx=2, pady=1)

        self._entries[cmd] = {
            "sv": variable,
            "raw_lbl": raw_label,
            "dec_lbl": value_label,
            "size": size,
            "fmt": fmt,
            "paged": paged,
            "is_ro": readonly,
        }

    def _page(self):
        return int(self._page_var.get())

    def _page_changed(self, event=None):
        for info in self._entries.values():
            info["sv"].set("")
            info["raw_lbl"].configure(text="---")
            info["dec_lbl"].configure(text="---")
        self._status_lbl.configure(text="Select Read All to refresh")

    def _update_display(self, cmd, raw):
        info = self._entries[cmd]
        page = self._page()
        self._raw_cache[(page, cmd)] = raw

        if raw is None:
            info["raw_lbl"].configure(text="ERR")
            info["dec_lbl"].configure(text="ERR")
            return

        width = 2 if info["size"] == "byte" else 4
        info["raw_lbl"].configure(text=f"0x{raw:0{width}X}")

        value = decode_value(
            raw, info["fmt"], self.device.vout_exp.get(page, -13)
        )
        text = (
            f"{value:.6g}"
            if info["fmt"] in {"L11", "L16"}
            else str(value)
        )
        info["dec_lbl"].configure(text=text)

    def _read_single(self, cmd):
        raw = self.device.read_register(self._page(), cmd)
        self._update_display(cmd, raw)
        return raw

    def _write_single(self, cmd, sv):
        info = self._entries[cmd]
        text = sv.get().strip()
        if info["is_ro"] or not text:
            return False

        try:
            if text.lower().startswith("0x"):
                ok = self.device.write_register(
                    self._page(), cmd, int(text, 16), info["size"]
                )
            else:
                ok = self.device.write_val(
                    self._page(), cmd, float(text), info["fmt"]
                )
        except (ValueError, TypeError, OverflowError):
            ok = False

        if ok:
            sv.set("")
            self._read_single(cmd)

        self._status_lbl.configure(
            text=f"0x{cmd:02X}: {'written' if ok else 'write failed'}"
        )
        return ok

    def _do_read_all(self):
        count = 0
        errors = 0
        for cmd in self._entries:
            if self._read_single(cmd) is None:
                errors += 1
            else:
                count += 1
        self._status_lbl.configure(
            text=f"Read {count}, errors {errors}"
        )

    def _do_write_all(self):
        page = self._page()
        pending = []

        for cmd, info in self._entries.items():
            if info["is_ro"]:
                continue
            text = info["sv"].get().strip()
            if not text:
                continue

            try:
                raw = (
                    int(text, 16)
                    if text.lower().startswith("0x")
                    else encode_value(
                        float(text),
                        info["fmt"],
                        self.device.vout_exp.get(page, -13),
                    )
                )
            except (ValueError, TypeError, OverflowError):
                messagebox.showerror(
                    "Invalid value", f"Invalid value for 0x{cmd:02X}"
                )
                return

            if raw != self._raw_cache.get((page, cmd)):
                pending.append(cmd)

        if not pending:
            self._status_lbl.configure(text="No changed values")
            return

        if not messagebox.askyesno(
            "Write registers",
            f"Write {len(pending)} edited registers?",
        ):
            return

        written = 0
        for cmd in pending:
            if self._write_single(cmd, self._entries[cmd]["sv"]):
                written += 1

        self._status_lbl.configure(
            text=f"Written {written}, failed {len(pending) - written}"
        )
