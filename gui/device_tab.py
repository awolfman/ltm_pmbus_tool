"""DeviceTab -- all channels side-by-side, global status TreeView."""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from core.dump_csv import dump_to_csv_string, csv_string_to_dump
from gui.channel_frame import ChannelColumn
from gui.status_defs import STATUS_INPUT_BITS, STATUS_CML_BITS


class DeviceTab(ttk.Frame):

    def __init__(self, parent, device, **kw):
        super().__init__(parent, **kw)
        self.device = device
        self.channels = []
        self.monitoring = False
        self._dump_data = {}

        self.global_telem_lbl = {}
        self.global_cfg_vars = {}
        self._global_wr_btns = {}
        self.global_cfg_data = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_top()
        self._build_channels()
        self._build_buttons()
        self.do_read_all()

    # ============================================================ top bar
    def _build_top(self):
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky='ew', padx=4, pady=(3, 0))

        # -- left: device info + global telemetry --
        left = ttk.Frame(top)
        left.pack(side='left', fill='y', padx=(0, 4))

        info = ttk.LabelFrame(left, text=" Device ")
        info.pack(fill='x', padx=2, pady=(0, 2))
        n_ch = f"{self.device.num_pages} channel(s)"
        for i, (lb, val) in enumerate([
            ("IC:",      self.device.name),
            ("Address:", f"0x{self.device.address:02X}"),
            ("ID:",      f"0x{self.device.special_id:04X}"
                         if self.device.special_id else "N/A"),
            ("Revision:", self.device.revision),
            ("Pages:",   n_ch),
            ("Capability:", f"0x{self.device.capability:02X}"
                            if hasattr(self.device, 'capability') else "N/A"),
        ]):
            ttk.Label(info, text=lb,
                      font=('Segoe UI', 8, 'bold')).grid(
                row=i, column=0, sticky='w', padx=3, pady=0)
            ttk.Label(info, text=val,
                      font=('Segoe UI', 8)).grid(
                row=i, column=1, sticky='w', padx=3, pady=0)

        gt_fr = ttk.LabelFrame(left, text=" Global Telemetry ")
        gt_fr.pack(fill='x', padx=2, pady=(0, 2))
        for key, label, unit, color in [
            ('VIN',     'VIN',              'V', '#2196F3'),
            ('TEMP_IC', 'IC Temperature',   'C', '#795548'),
        ]:
            rf = ttk.Frame(gt_fr)
            rf.pack(fill='x', padx=3, pady=1)
            ttk.Label(rf, text=label, width=14, anchor='w',
                      font=('Segoe UI', 8)).pack(side='left')
            vl = tk.Label(rf, text="---",
                          font=('Consolas', 11, 'bold'),
                          fg=color, bg='#1a1a2e',
                          width=9, anchor='e',
                          relief='sunken', padx=3)
            vl.pack(side='left', padx=3)
            ttk.Label(rf, text=unit, width=3,
                      font=('Segoe UI', 8)).pack(side='left')
            self.global_telem_lbl[key] = vl

        # -- middle: VIN config --
        gc = ttk.LabelFrame(top, text=" VIN Config (global) ")
        gc.pack(side='left', fill='y', padx=4)
        for i, (key, label, unit) in enumerate([
            ('VIN_ON',             'VIN On',        'V'),
            ('VIN_OFF',            'VIN Off',       'V'),
            ('VIN_OV_FAULT_LIMIT', 'VIN OV Fault',  'V'),
            ('VIN_OV_WARN_LIMIT',  'VIN OV Warn',   'V'),
            ('VIN_UV_WARN_LIMIT',  'VIN UV Warn',   'V'),
            ('VIN_UV_FAULT_LIMIT', 'VIN UV Fault',  'V'),
        ]):
            ttk.Label(gc, text=label, width=12, anchor='w',
                      font=('Segoe UI', 8)).grid(
                row=i, column=0, padx=2, pady=0, sticky='w')
            v = tk.StringVar(value="---")
            ttk.Entry(gc, textvariable=v, width=9,
                      justify='right',
                      font=('Consolas', 9)).grid(
                row=i, column=1, padx=1, pady=0)
            ttk.Label(gc, text=unit, width=2,
                      font=('Segoe UI', 8)).grid(
                row=i, column=2, padx=1, pady=0)
            self.global_cfg_vars[key] = v
            _c = tk.Canvas(gc, width=14, height=14,
                          highlightthickness=0, bd=0, cursor='hand2')
            _c.create_polygon(3, 2, 12, 7, 3, 12,
                             fill='#4CAF50', outline='#2E7D32',
                             tags='tri')
            _c.grid(row=i, column=3, padx=(0, 2), pady=0)
            _c.bind('<Button-1>',
                    lambda e, k=key: self._write_single_global(k))
            _c.bind('<Enter>',
                    lambda e, c=_c: c.itemconfig('tri', fill='#66BB6A'))
            _c.bind('<Leave>',
                    lambda e, c=_c: c.itemconfig('tri', fill='#4CAF50'))
            self._global_wr_btns[key] = _c

        # -- right: global status TreeView --
        gs = ttk.LabelFrame(top, text=" Global Status ")
        gs.pack(side='left', fill='both', expand=True, padx=4)

        self.global_status_ind = tk.Label(
            gs, text="---",
            font=('Consolas', 9, 'bold'),
            bg='#1a1a2e', fg='#00ff00',
            anchor='center', relief='sunken')
        self.global_status_ind.pack(fill='x', padx=2, pady=(2, 1))

        gtf = ttk.Frame(gs)
        gtf.pack(fill='both', expand=True, padx=2, pady=(1, 2))

        self.global_tree = ttk.Treeview(
            gtf, columns=('val', 'hex'), height=5)
        self.global_tree.heading('#0', text='Register / Bit',
                                  anchor='w')
        self.global_tree.heading('val', text='St')
        self.global_tree.heading('hex', text='Hex')
        self.global_tree.column('#0', width=180, minwidth=120)
        self.global_tree.column('val', width=28,
                                anchor='center', minwidth=28)
        self.global_tree.column('hex', width=52,
                                anchor='center', minwidth=42)
        gsb = ttk.Scrollbar(gtf, orient='vertical',
                             command=self.global_tree.yview)
        self.global_tree.configure(yscrollcommand=gsb.set)
        self.global_tree.pack(side='left', fill='both', expand=True)
        gsb.pack(side='right', fill='y')

        for tag in ('fault', 'warn', 'ok'):
            self.global_tree.tag_configure(tag, foreground={
                'fault': '#FF4444',
                'warn':  '#FFD700',
                'ok':    '#228B22',
            }[tag])

    # ======================================================= channel columns
    def _build_channels(self):
        ch_frame = ttk.Frame(self)
        ch_frame.grid(row=1, column=0, sticky='nsew', padx=4, pady=2)
        ch_frame.rowconfigure(0, weight=1)
        for p in range(self.device.num_pages):
            ch_frame.columnconfigure(p, weight=1)
            cc = ChannelColumn(ch_frame, self.device, p)
            cc.grid(row=0, column=p, sticky='nsew', padx=3, pady=2)
            self.channels.append(cc)

# ========================================================== button bar
    def _build_buttons(self):
        bf = ttk.Frame(self)
        bf.grid(row=2, column=0, sticky='ew', padx=4, pady=(0, 4))

        for text, cmd in [
            ("Read All",      self.do_read_all),
            ("Write All",     self.do_write_all),
            ("Store NVM",     self.do_store),
            ("Restore NVM",   self.do_restore),
            ("Clear Faults",  self.do_clear),

        ]:
            ttk.Button(bf, text=text, command=cmd).pack(
                side='left', padx=2)

        ttk.Separator(bf, orient='vertical').pack(
            side='left', fill='y', padx=6)
        self.mon_btn = ttk.Button(bf, text="Start Monitor",
                                  command=self.toggle_monitor)
        self.mon_btn.pack(side='left', padx=2)

        ttk.Separator(bf, orient='vertical').pack(
            side='left', fill='y', padx=6)
        ttk.Label(bf, text="Dump page:").pack(side='left', padx=(4, 1))
        self.dump_page_var = tk.StringVar(value="0")
        dv = [str(p) for p in range(self.device.num_pages)]
        ttk.Combobox(bf, textvariable=self.dump_page_var,
                     values=dv, width=3,
                     state='readonly').pack(side='left', padx=2)
        for text, cmd in [
            ("Read Dump",  self.do_dump_read),
            ("Save CSV",   self.do_dump_save),
            ("Load CSV",   self.do_dump_load),
            ("Write Dump", self.do_dump_write),
        ]:
            ttk.Button(bf, text=text, command=cmd).pack(
                side='left', padx=2)

        for text, cmd in [
        ]:
            ttk.Button(bf, text=text, command=cmd).pack(
                side='left', padx=2)

    # ========================================================== read / write
    def _write_single_global(self, key):
        """Write one global parameter to device."""
        if key not in self.global_cfg_data:
            return
        try:
            nv = float(self.global_cfg_vars[key].get())
        except ValueError:
            return
        c = self.global_cfg_data[key]
        ok = self.device.write_val(0, c['cmd'], nv, c['fmt'])
        w = self._global_wr_btns.get(key)
        if w:
            color = '#00E676' if ok else '#FF5252'
            w.itemconfig('tri', fill=color)
            self.after(600, lambda c=w: c.itemconfig('tri', fill='#4CAF50'))

    def do_read_all(self):
        try:
            self.global_cfg_data = self.device.read_global_config()
            for key, sv in self.global_cfg_vars.items():
                d = self.global_cfg_data.get(key)
                if d and d['value'] is not None:
                    sv.set(f"{d['value']:.4f}")
                else:
                    sv.set("ERR")

            gt = self.device.read_global_telemetry()
            for key in ('VIN', 'TEMP_IC'):
                v = gt.get(key)
                lbl = self.global_telem_lbl.get(key)
                if lbl:
                    lbl.configure(
                        text=f"{v:.3f}" if v is not None else "N/A")

            for ch in self.channels:
                cfg = self.device.read_channel_config(ch.page)
                ch.update_config(cfg)
                ct = self.device.read_channel_telemetry(ch.page)
                ch.update_telemetry(ct)
                cs = self.device.read_channel_status(ch.page)
                ch.update_status(cs)

            gs = self.device.read_global_status()
            self._update_global_status(gs)

            # Read all register groups in channel tabs
            for ch in self.channels:
                ch.read_all_reg_groups()
        except Exception as e:
            messagebox.showerror("Read error", str(e))

    def do_write_all(self):
        errs = []
        for key, sv in self.global_cfg_vars.items():
            if key not in self.global_cfg_data:
                continue
            try:
                nv = float(sv.get())
                c = self.global_cfg_data[key]
                if not self.device.write_val(0, c['cmd'], nv, c['fmt']):
                    errs.append(f"Global: {key}")
            except ValueError:
                if sv.get() not in ("---", "ERR", "N/A"):
                    errs.append(f"Global: {key}")
        for ch in self.channels:
            for key, val_str in ch.get_write_data():
                if key not in ch.cfg_data:
                    continue
                try:
                    nv = float(val_str)
                    c = ch.cfg_data[key]
                    if not self.device.write_val(
                            ch.page, c['cmd'], nv, c['fmt']):
                        errs.append(f"CH{ch.page}: {key}")
                except ValueError:
                    if val_str not in ("---", "ERR", "N/A"):
                        errs.append(f"CH{ch.page}: {key}")
        if errs:
            messagebox.showwarning(
                "Warning", "Not written:\n" + "\n".join(errs))
        else:
            messagebox.showinfo("OK", "All values written to RAM.")

    def do_store(self):
        if messagebox.askyesno("RAM -> NVM", "Save all to NVM?"):
            if self.device.store_user_all():
                messagebox.showinfo("OK", "Stored to NVM.")
            else:
                messagebox.showerror("Error", "Store failed.")

    def do_restore(self):
        if messagebox.askyesno("NVM -> RAM", "Restore from NVM?"):
            if self.device.restore_user_all():
                messagebox.showinfo("OK", "Restored from NVM.")
                self.do_read_all()
            else:
                messagebox.showerror("Error", "Restore failed.")

    def do_clear(self):
        self.device.clear_faults()
        self._refresh_status()

    # ============================================================ monitor
    def toggle_monitor(self):
        if self.monitoring:
            self.monitoring = False
            self.mon_btn.configure(text="Start Monitor")
        else:
            self.monitoring = True
            self.mon_btn.configure(text="Stop Monitor")
            self._mon_loop()

    def _mon_loop(self):
        if not self.monitoring:
            return
        try:
            gt = self.device.read_global_telemetry()
            for key in ('VIN', 'TEMP_IC'):
                v = gt.get(key)
                lbl = self.global_telem_lbl.get(key)
                if lbl:
                    lbl.configure(
                        text=f"{v:.3f}" if v is not None else "N/A")
            for ch in self.channels:
                ct = self.device.read_channel_telemetry(ch.page)
                ch.update_telemetry(ct)
            self._refresh_status()
        except Exception:
            pass
        if self.monitoring:
            self.after(500, self._mon_loop)

    def _refresh_status(self):
        try:
            gs = self.device.read_global_status()
            self._update_global_status(gs)
            for ch in self.channels:
                cs = self.device.read_channel_status(ch.page)
                ch.update_status(cs)
        except Exception:
            pass

    def _update_global_status(self, gs):
        tree = self.global_tree
        for i in tree.get_children():
            tree.delete(i)

        any_fault = False
        any_warn = False

        # ---- STATUS_INPUT ----
        si = gs.get('STATUS_INPUT')
        if si is not None:
            tg = 'fault' if si else 'ok'
            rid = tree.insert(
                '', 'end', text='STATUS_INPUT',
                values=('F' if si else 'OK', f'0x{si:02X}'),
                tags=(tg,), open=False)
            for b in range(7, -1, -1):
                bv = (si >> b) & 1
                d = STATUS_INPUT_BITS.get(b, f"b{b}")
                if bv and b >= 4:
                    bt = 'fault'; any_fault = True
                elif bv:
                    bt = 'warn'; any_warn = True
                else:
                    bt = 'ok'
                tree.insert(rid, 'end',
                            text=f"  b{b}: {d}",
                            values=(bv, ''), tags=(bt,))

        # ---- STATUS_CML ----
        sc = gs.get('STATUS_CML')
        if sc is not None:
            tg = 'fault' if sc else 'ok'
            rid = tree.insert(
                '', 'end', text='STATUS_CML',
                values=('F' if sc else 'OK', f'0x{sc:02X}'),
                tags=(tg,), open=False)
            for b in range(7, -1, -1):
                bv = (sc >> b) & 1
                d = STATUS_CML_BITS.get(b, f"b{b}")
                if bv:
                    bt = 'fault'; any_fault = True
                else:
                    bt = 'ok'
                tree.insert(rid, 'end',
                            text=f"  b{b}: {d}",
                            values=(bv, ''), tags=(bt,))

        if any_fault:
            self.global_status_ind.configure(
                text="FAULT", fg='#FF4444')
        elif any_warn:
            self.global_status_ind.configure(
                text="WARNING", fg='#FFD700')
        else:
            self.global_status_ind.configure(
                text="OK", fg='#00FF00')

    # ============================================================= dump
    def _dump_page(self):
        try:
            return int(self.dump_page_var.get())
        except ValueError:
            return 0

    def do_dump_read(self):
        page = self._dump_page()
        try:
            self._dump_data[page] = self.device.read_full_dump(page)
            ok = sum(1 for r in self._dump_data[page]
                     if r['raw'] is not None)
            messagebox.showinfo("Dump",
                                f"Page {page}: {ok} registers read.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def do_dump_save(self):
        page = self._dump_page()
        if page not in self._dump_data:
            if messagebox.askyesno("", "Read dump first?"):
                self.do_dump_read()
            if page not in self._dump_data:
                return
        fn = (f"{self.device.name}_0x{self.device.address:02X}"
              f"_p{page}_{datetime.now():%Y%m%d_%H%M%S}.csv")
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=fn)
        if not path:
            return
        try:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                f.write(dump_to_csv_string(
                    self.device, self._dump_data[page], page))
            messagebox.showinfo("OK", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def do_dump_load(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                txt = f.read()
            meta, recs = csv_string_to_dump(txt)
            if not recs:
                messagebox.showerror("", "No data in CSV.")
                return
            page = self._dump_page()
            self._dump_data[page] = recs
            w = sum(1 for r in recs
                    if not r['readonly'] and r['raw'] is not None)
            messagebox.showinfo("Loaded",
                                f"Total: {len(recs)}, writable: {w}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def do_dump_write(self):
        page = self._dump_page()
        if page not in self._dump_data:
            messagebox.showwarning("", "Load or read dump first.")
            return
        wr = [r for r in self._dump_data[page]
              if not r.get('readonly', True)
              and r['raw'] is not None]
        if not wr:
            messagebox.showwarning("", "No writable registers.")
            return
        if not messagebox.askyesno(
                "Write Dump",
                f"Write {len(wr)} registers to page {page}?"):
            return
        ok = 0
        fl = []
        for r in wr:
            if self.device.write_register(
                    r.get('page', page),
                    r['cmd'], r['raw'], r['size']):
                ok += 1
            else:
                fl.append(f"0x{r['cmd']:02X}")
        if fl:
            messagebox.showwarning(
                "Done", f"OK: {ok}, Failed: {len(fl)}")
        else:
            messagebox.showinfo("Done", f"Written: {ok}")
        self.do_read_all()

    def _do_read_regs(self):
        """Trigger read all in register tab."""
        if hasattr(self, '_reg_tab'):
            self._reg_tab._do_read_all()

# ============================================================ cleanup
    def stop_all(self):
        self.monitoring = False
