"""RegisterTab -- full register map viewer/editor with per-register R/W buttons."""

import tkinter as tk
from tkinter import ttk
from core.pmbus_formats import decode_value, encode_value

# Register categories for grouping
CATEGORIES = [
    ("Addressing",    0x00, 0x1F),
    ("Output Voltage",0x20, 0x3F),
    ("Limits",        0x40, 0x6F),
    ("Status",        0x78, 0x9F),
    ("User / EEPROM", 0xB0, 0xCF),
    ("MFR Config",    0xD0, 0xEF),
    ("MFR Calibration",0xF0, 0xFF),
]

def _category(cmd):
    for name, lo, hi in CATEGORIES:
        if lo <= cmd <= hi:
            return name
    return "Other"


class RegisterTab(ttk.Frame):
    """Full register map viewer/editor for one device."""

    def __init__(self, parent, device, **kw):
        super().__init__(parent, **kw)
        self.device = device
        self.regmap = device._regmap
        self.read_only = device._read_only
        self._entries = {}  # cmd -> StringVar for editing
        self._raw_cache = {}  # (page, cmd) -> raw value
        self._build_ui()

    def _build_ui(self):
        # Top bar: page selector + Read All / Write All
        top = ttk.Frame(self)
        top.pack(fill='x', padx=4, pady=(4, 2))

        ttk.Label(top, text="Page:").pack(side='left', padx=(0, 2))
        self._page_var = tk.StringVar(value="0")
        pages = [str(p) for p in range(self.device.num_pages)]
        ttk.Combobox(top, textvariable=self._page_var,
                     values=pages, width=3,
                     state='readonly').pack(side='left', padx=2)

        ttk.Button(top, text="Read All Registers",
                   command=self._do_read_all).pack(side='left', padx=6)
        ttk.Button(top, text="Write All Changed",
                   command=self._do_write_all).pack(side='left', padx=2)

        self._status_lbl = ttk.Label(top, text="", font=('Segoe UI', 9))
        self._status_lbl.pack(side='right', padx=6)

        # Notebook with category tabs
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill='both', expand=True, padx=4, pady=(2, 4))

        # Group registers by category
        groups = {}
        for cmd in sorted(self.regmap.keys()):
            name, size, fmt, paged = self.regmap[cmd]
            if size == 'block':
                continue
            cat = _category(cmd)
            groups.setdefault(cat, []).append(cmd)

        # Build tabs in category order
        seen_cats = set()
        for cat_name, _, _ in CATEGORIES:
            if cat_name in groups and cat_name not in seen_cats:
                seen_cats.add(cat_name)
                self._build_cat_tab(cat_name, groups[cat_name])
        # "Other" if any
        if "Other" in groups:
            self._build_cat_tab("Other", groups["Other"])

    def _build_cat_tab(self, cat_name, cmds):
        frame = ttk.Frame(self._nb)
        self._nb.add(frame, text=f" {cat_name} ")

        # Scrollable area
        canvas = tk.Canvas(frame, highlightthickness=0)
        sb = ttk.Scrollbar(frame, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind('<Configure>',
                   lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self._bind_scroll(canvas)

        # Header
        hdr = ttk.Frame(inner)
        hdr.pack(fill='x', padx=2, pady=(2, 0))
        for col, w, text in [
            (0, 6,  "Cmd"),
            (1, 28, "Register Name"),
            (2, 5,  "Size"),
            (3, 4,  "Fmt"),
            (4, 2,  "P"),
            (5, 10, "Raw Hex"),
            (6, 12, "Decoded"),
            (7, 10, "New Value"),
            (8, 3,  ""),
        ]:
            ttk.Label(hdr, text=text, font=('Segoe UI', 8, 'bold'),
                      width=w, anchor='w').grid(row=0, column=col,
                      padx=1, sticky='w')

        ttk.Separator(inner, orient='horizontal').pack(fill='x', padx=2, pady=1)

        # Register rows
        for i, cmd in enumerate(cmds):
            name, size, fmt, paged = self.regmap[cmd]
            is_ro = cmd in self.read_only

            rf = ttk.Frame(inner)
            rf.pack(fill='x', padx=2, pady=0)

            # Cmd hex
            ttk.Label(rf, text=f"0x{cmd:02X}", width=6,
                      font=('Consolas', 8),
                      anchor='w').grid(row=0, column=0, padx=1, sticky='w')

            # Name
            fg = '#666666' if is_ro else '#000000'
            nl = ttk.Label(rf, text=name, width=28,
                          font=('Segoe UI', 8), anchor='w')
            nl.grid(row=0, column=1, padx=1, sticky='w')

            # Size
            ttk.Label(rf, text=size, width=5,
                      font=('Segoe UI', 7),
                      anchor='w').grid(row=0, column=2, padx=1, sticky='w')

            # Format
            ttk.Label(rf, text=fmt, width=4,
                      font=('Segoe UI', 7),
                      anchor='w').grid(row=0, column=3, padx=1, sticky='w')

            # Paged
            ttk.Label(rf, text="Y" if paged else "N", width=2,
                      font=('Segoe UI', 7),
                      anchor='center').grid(row=0, column=4, padx=1)

            # Raw hex display
            raw_lbl = tk.Label(rf, text="---", width=10,
                              font=('Consolas', 8),
                              bg='#f0f0f0', anchor='e',
                              relief='sunken', padx=2)
            raw_lbl.grid(row=0, column=5, padx=1, sticky='w')

            # Decoded display
            dec_lbl = tk.Label(rf, text="---", width=12,
                              font=('Consolas', 8),
                              bg='#f0f0f0', anchor='e',
                              relief='sunken', padx=2)
            dec_lbl.grid(row=0, column=6, padx=1, sticky='w')

            # Edit entry (only for writable)
            sv = tk.StringVar(value="")
            if not is_ro:
                ent = ttk.Entry(rf, textvariable=sv, width=10,
                               font=('Consolas', 8), justify='right')
                ent.grid(row=0, column=7, padx=1, sticky='w')
            else:
                ttk.Label(rf, text="(R/O)", width=10,
                         font=('Segoe UI', 7),
                         foreground='#999999').grid(
                    row=0, column=7, padx=1, sticky='w')

            # Action button
            if is_ro:
                # Read button for read-only
                btn = self._make_read_btn(rf, cmd)
                btn.grid(row=0, column=8, padx=1)
            else:
                # Write button for writable
                btn = self._make_write_btn(rf, cmd, sv)
                btn.grid(row=0, column=8, padx=1)

            self._entries[cmd] = {
                'sv': sv, 'raw_lbl': raw_lbl, 'dec_lbl': dec_lbl,
                'name': name, 'size': size, 'fmt': fmt, 'paged': paged,
                'is_ro': is_ro, 'btn': btn,
            }

    def _make_read_btn(self, parent, cmd):
        """Blue refresh triangle for read-only registers."""
        c = tk.Canvas(parent, width=14, height=14,
                      highlightthickness=0, bd=0, cursor='hand2')
        c.create_polygon(3, 2, 12, 7, 3, 12,
                         fill='#2196F3', outline='#1565C0', tags='tri')
        c.bind('<Button-1>', lambda e, cc=cmd: self._read_single(cc))
        c.bind('<Enter>', lambda e, w=c: w.itemconfig('tri', fill='#42A5F5'))
        c.bind('<Leave>', lambda e, w=c: w.itemconfig('tri', fill='#2196F3'))
        return c

    def _make_write_btn(self, parent, cmd, sv):
        """Green write triangle for writable registers."""
        c = tk.Canvas(parent, width=14, height=14,
                      highlightthickness=0, bd=0, cursor='hand2')
        c.create_polygon(3, 2, 12, 7, 3, 12,
                         fill='#4CAF50', outline='#2E7D32', tags='tri')
        c.bind('<Button-1>', lambda e, cc=cmd, s=sv: self._write_single(cc, s))
        c.bind('<Enter>', lambda e, w=c: w.itemconfig('tri', fill='#66BB6A'))
        c.bind('<Leave>', lambda e, w=c: w.itemconfig('tri', fill='#4CAF50'))
        return c

    @staticmethod
    def _bind_scroll(canvas):
        def _do(event):
            if event.num == 4:
                canvas.yview_scroll(-3, 'units')
            elif event.num == 5:
                canvas.yview_scroll(3, 'units')
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        def _enter(e):
            canvas.bind_all('<MouseWheel>', _do)
            canvas.bind_all('<Button-4>', _do)
            canvas.bind_all('<Button-5>', _do)
        def _leave(e):
            canvas.unbind_all('<MouseWheel>')
            canvas.unbind_all('<Button-4>')
            canvas.unbind_all('<Button-5>')
        canvas.bind('<Enter>', _enter)
        canvas.bind('<Leave>', _leave)

    # ---- page helper ----
    def _page(self):
        try:
            return int(self._page_var.get())
        except ValueError:
            return 0

    # ---- read single register ----
    def _read_single(self, cmd):
        page = self._page()
        info = self._entries.get(cmd)
        if not info:
            return
        self.device.set_page(page)
        if info['size'] == 'byte':
            raw = self.device._rb(cmd)
        else:
            raw = self.device._rw(cmd)

        self._update_display(cmd, raw)
        # Flash blue
        btn = info.get('btn')
        if btn:
            btn.itemconfig('tri', fill='#00BCD4')
            self.after(400, lambda b=btn: b.itemconfig('tri', fill='#2196F3'))

    # ---- write single register ----
    def _write_single(self, cmd, sv):
        page = self._page()
        info = self._entries.get(cmd)
        if not info or info['is_ro']:
            return
        text = sv.get().strip()
        if not text:
            return

        exp = self.device.vout_exp.get(page, -13)
        ok = False
        try:
            # Try as hex first if starts with 0x
            if text.lower().startswith('0x'):
                raw_val = int(text, 16)
                self.device.set_page(page)
                if info['size'] == 'byte':
                    ok = self.device._wb(cmd, raw_val & 0xFF)
                else:
                    ok = self.device._ww(cmd, raw_val & 0xFFFF)
            else:
                # Parse as float, encode
                val = float(text)
                raw_val = encode_value(val, info['fmt'], exp)
                self.device.set_page(page)
                if info['size'] == 'byte':
                    ok = self.device._wb(cmd, raw_val & 0xFF)
                else:
                    ok = self.device._ww(cmd, raw_val & 0xFFFF)
        except (ValueError, TypeError):
            ok = False

        btn = info.get('btn')
        if btn:
            color = '#00E676' if ok else '#FF5252'
            btn.itemconfig('tri', fill=color)
            self.after(500, lambda b=btn: b.itemconfig('tri', fill='#4CAF50'))

        if ok:
            # Re-read to confirm
            self._read_single(cmd)

    # ---- update display for one register ----
    def _update_display(self, cmd, raw):
        info = self._entries.get(cmd)
        if not info:
            return
        page = self._page()
        exp = self.device.vout_exp.get(page, -13)
        self._raw_cache[(page, cmd)] = raw

        if raw is not None:
            if info['size'] == 'byte':
                info['raw_lbl'].configure(text=f"0x{raw:02X}")
            else:
                info['raw_lbl'].configure(text=f"0x{raw:04X}")

            decoded = decode_value(raw, info['fmt'], exp)
            if decoded is not None and info['fmt'] in ('L11', 'L16'):
                info['dec_lbl'].configure(text=f"{decoded:.4f}")
            elif decoded is not None:
                info['dec_lbl'].configure(text=str(int(decoded)))
            else:
                info['dec_lbl'].configure(text="---")

            # Pre-fill edit field for writable
            if not info['is_ro'] and not info['sv'].get():
                if info['fmt'] in ('L11', 'L16') and decoded is not None:
                    info['sv'].set(f"{decoded:.4f}")
                elif raw is not None:
                    info['sv'].set(f"0x{raw:02X}" if info['size'] == 'byte'
                                   else f"0x{raw:04X}")
        else:
            info['raw_lbl'].configure(text="ERR")
            info['dec_lbl'].configure(text="ERR")

    # ---- read all ----
    def _do_read_all(self):
        page = self._page()
        self.device.set_page(page)
        exp = self.device.vout_exp.get(page, -13)
        count = 0
        errors = 0

        for cmd, info in self._entries.items():
            if info['size'] == 'byte':
                raw = self.device._rb(cmd)
            else:
                raw = self.device._rw(cmd)
            self._update_display(cmd, raw)
            if raw is not None:
                count += 1
            else:
                errors += 1

        self._status_lbl.configure(
            text=f"Page {page}: {count} read, {errors} errors")

    # ---- write all changed ----
    def _do_write_all(self):
        page = self._page()
        exp = self.device.vout_exp.get(page, -13)
        written = 0
        failed = 0

        self.device.set_page(page)
        for cmd, info in self._entries.items():
            if info['is_ro']:
                continue
            text = info['sv'].get().strip()
            if not text:
                continue

            try:
                if text.lower().startswith('0x'):
                    raw_val = int(text, 16)
                else:
                    val = float(text)
                    raw_val = encode_value(val, info['fmt'], exp)

                if info['size'] == 'byte':
                    ok = self.device._wb(cmd, raw_val & 0xFF)
                else:
                    ok = self.device._ww(cmd, raw_val & 0xFFFF)

                if ok:
                    written += 1
                else:
                    failed += 1
            except (ValueError, TypeError):
                failed += 1

        self._status_lbl.configure(
            text=f"Written: {written}, Failed: {failed}")
        # Re-read all
        self._do_read_all()
        
