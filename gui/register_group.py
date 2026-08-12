"""RegisterGroup -- scrollable register list with collapsible sections.

All non-Output/Control registers in one scrollable frame,
grouped by category with section headers.
"""

import tkinter as tk
from tkinter import ttk
from core.pmbus_formats import decode_value, encode_value

class RegisterGroup(ttk.Frame):
    """Scrollable grid of registers with per-register read/write buttons."""

    def __init__(self, parent, device, page, reg_list, **kw):
        """
        reg_list: [(cmd, name, size, fmt, paged, is_ro), ...]
        """
        super().__init__(parent, **kw)
        self.device = device
        self.page = page
        self._regs = reg_list
        self._rows = {}
        self._build()

    def _build(self):
        canvas = tk.Canvas(self, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
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
        for col, w, txt in [(0,6,"Cmd"),(1,24,"Name"),(2,4,"Fmt"),
                            (3,10,"Raw"),(4,12,"Value"),(5,10,"New"),(6,2,"")]:
            ttk.Label(hdr, text=txt, font=('Segoe UI', 7, 'bold'),
                      width=w, anchor='w').grid(row=0, column=col, padx=1)
        ttk.Separator(inner, orient='horizontal').pack(fill='x', padx=2, pady=1)

        for cmd, name, size, fmt, paged, is_ro in self._regs:
            rf = ttk.Frame(inner)
            rf.pack(fill='x', padx=2, pady=0)
            ttk.Label(rf, text=f"0x{cmd:02X}", width=6,
                      font=('Consolas', 8)).grid(row=0, column=0, padx=1, sticky='w')
            ttk.Label(rf, text=name, width=24,
                      font=('Segoe UI', 8)).grid(row=0, column=1, padx=1, sticky='w')
            ttk.Label(rf, text=fmt, width=4,
                      font=('Segoe UI', 7)).grid(row=0, column=2, padx=1, sticky='w')
            raw_lbl = tk.Label(rf, text="---", width=10, font=('Consolas', 8),
                              bg='#f0f0f0', anchor='e', relief='sunken', padx=2)
            raw_lbl.grid(row=0, column=3, padx=1)
            dec_lbl = tk.Label(rf, text="---", width=12, font=('Consolas', 8),
                              bg='#f0f0f0', anchor='e', relief='sunken', padx=2)
            dec_lbl.grid(row=0, column=4, padx=1)
            sv = tk.StringVar()
            if not is_ro:
                ttk.Entry(rf, textvariable=sv, width=10,
                         font=('Consolas', 8), justify='right').grid(
                    row=0, column=5, padx=1)
                btn = self._wr_btn(rf, cmd, sv)
            else:
                ttk.Label(rf, text="R/O", width=10, font=('Segoe UI', 7),
                         foreground='#999').grid(row=0, column=5, padx=1)
                btn = self._rd_btn(rf, cmd)
            btn.grid(row=0, column=6, padx=1)
            self._rows[cmd] = {'raw_lbl': raw_lbl, 'dec_lbl': dec_lbl,
                               'sv': sv, 'btn': btn, 'is_ro': is_ro,
                               'size': size, 'fmt': fmt, 'name': name}

    def _rd_btn(self, parent, cmd):
        c = tk.Canvas(parent, width=14, height=14, highlightthickness=0,
                      bd=0, cursor='hand2')
        c.create_polygon(3,2,12,7,3,12, fill='#2196F3', outline='#1565C0', tags='tri')
        c.bind('<Button-1>', lambda e, cc=cmd: self._read_one(cc))
        c.bind('<Enter>', lambda e, w=c: w.itemconfig('tri', fill='#42A5F5'))
        c.bind('<Leave>', lambda e, w=c: w.itemconfig('tri', fill='#2196F3'))
        return c

    def _wr_btn(self, parent, cmd, sv):
        c = tk.Canvas(parent, width=14, height=14, highlightthickness=0,
                      bd=0, cursor='hand2')
        c.create_polygon(3,2,12,7,3,12, fill='#4CAF50', outline='#2E7D32', tags='tri')
        c.bind('<Button-1>', lambda e, cc=cmd, s=sv: self._write_one(cc, s))
        c.bind('<Enter>', lambda e, w=c: w.itemconfig('tri', fill='#66BB6A'))
        c.bind('<Leave>', lambda e, w=c: w.itemconfig('tri', fill='#4CAF50'))
        return c

    def _read_one(self, cmd):
        r = self._rows.get(cmd)
        if not r: return
        self.device.set_page(self.page)
        raw = self.device._rb(cmd) if r['size'] == 'byte' else self.device._rw(cmd)
        self._show(cmd, raw)
        b = r['btn']
        b.itemconfig('tri', fill='#00BCD4')
        self.after(400, lambda: b.itemconfig('tri', fill='#2196F3'))

    def _write_one(self, cmd, sv):
        r = self._rows.get(cmd)
        if not r or r['is_ro']: return
        text = sv.get().strip()
        if not text: return
        exp = self.device.vout_exp.get(self.page, -13)
        ok = False
        try:
            if text.lower().startswith('0x'):
                raw_val = int(text, 16)
            else:
                raw_val = encode_value(float(text), r['fmt'], exp)
            self.device.set_page(self.page)
            if r['size'] == 'byte':
                ok = self.device._wb(cmd, raw_val & 0xFF)
            else:
                ok = self.device._ww(cmd, raw_val & 0xFFFF)
        except (ValueError, TypeError):
            pass
        b = r['btn']
        b.itemconfig('tri', fill='#00E676' if ok else '#FF5252')
        self.after(500, lambda: b.itemconfig('tri', fill='#4CAF50'))
        if ok:
            self._read_one(cmd)

    def _show(self, cmd, raw):
        r = self._rows.get(cmd)
        if not r: return
        exp = self.device.vout_exp.get(self.page, -13)
        if raw is not None:
            r['raw_lbl'].configure(
                text=f"0x{raw:02X}" if r['size'] == 'byte' else f"0x{raw:04X}")
            dec = decode_value(raw, r['fmt'], exp)
            if dec is not None and r['fmt'] in ('L11','L16'):
                r['dec_lbl'].configure(text=f"{dec:.4f}")
            elif dec is not None:
                r['dec_lbl'].configure(text=str(int(dec)))
            else:
                r['dec_lbl'].configure(text="---")
        else:
            r['raw_lbl'].configure(text="ERR")
            r['dec_lbl'].configure(text="ERR")

    def read_all(self):
        self.device.set_page(self.page)
        for cmd in self._rows:
            r = self._rows[cmd]
            raw = self.device._rb(cmd) if r['size'] == 'byte' else self.device._rw(cmd)
            self._show(cmd, raw)

    @staticmethod
    def _bind_scroll(canvas):
        def _do(event):
            if event.num == 4: canvas.yview_scroll(-3, 'units')
            elif event.num == 5: canvas.yview_scroll(3, 'units')
            else: canvas.yview_scroll(int(-1*(event.delta/120)), 'units')
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
