"""ChannelColumn -- per-channel: telemetry + config tabs + status tree.
Registers grouped into tabs; Output and Control keep existing layout,
remaining regs shown in category tabs via RegisterGroup."""

import tkinter as tk
from tkinter import ttk
from gui.status_defs import (STATUS_WORD_BITS, STATUS_VOUT_BITS,
                              STATUS_IOUT_BITS, STATUS_TEMP_BITS)
from gui.register_group import RegisterGroup

OPERATION_OPTS = [
    (0x80, 'On'), (0xA8, 'Margin High'), (0x98, 'Margin Low'),
    (0x40, 'Soft Off'), (0x00, 'Immediate Off'),
]
ON_OFF_CFG_OPTS = [
    (0x1E, 'CMD+RUN / Soft Off'), (0x1F, 'CMD+RUN / Immed Off'),
    (0x16, 'RUN only / Soft Off'), (0x17, 'RUN only / Immed Off'),
]
WRITE_PROTECT_OPTS = [
    (0x00, 'No protection'),
    (0x20, 'VOUT_COMMAND only'),
    (0x40, 'Write VOUT + Operation'),
    (0x80, 'All writes disabled'),
]

# Cmds handled by Output tab manually
OUTPUT_TAB_CMDS = {
    0x21, 0x24, 0x25, 0x26,  # VOUT_COMMAND..MARGIN_LOW
    0x40, 0x42, 0x43, 0x44,  # VOUT OV/UV limits
    0x46, 0x4A, 0x4B,        # IOUT limits
    0x4F, 0x51, 0x52, 0x53,  # OT/UT limits
    0x33, 0x60, 0x61, 0x64,  # Frequency, timing
}
# Cmds handled by Control tab manually
CONTROL_TAB_CMDS = {0x01, 0x02, 0x03, 0x10}
# Status regs shown in status tree
STATUS_CMDS = {0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x80}
# Telemetry shown in telemetry panel
TELEM_CMDS = {0x88, 0x89, 0x8B, 0x8C, 0x8D, 0x8E, 0x94, 0x95, 0x96, 0x97}
# Regs in global panel of device_tab
GLOBAL_GUI_CMDS = {0x35, 0x36, 0x55, 0x57, 0x58, 0x59}
# Misc already shown or not useful in reg grid
SKIP_CMDS = {0x00, 0x19, 0x20, 0x98} | STATUS_CMDS | TELEM_CMDS

# Categories for remaining registers
REG_CATEGORIES = [
    ("Prot / Resp", lambda c: c in {0x41,0x45,0x47,0x4C,0x50,0x54,0x56,0x5A,0x63}),
    ("Power Good",  lambda c: 0x5E <= c <= 0x5F),
    ("Timing",      lambda c: c in {0x62,0x65,0x66,0x27}),
    ("User Data",   lambda c: 0xB0 <= c <= 0xB4),
    ("Calibration", lambda c: c in {0x38} or 0xB5 <= c <= 0xBC
                              or c in {0xF6,0xF8,0xF9,0xFA}),
    ("Input/Power", lambda c: 0xC0 <= c <= 0xCA or c in {0xE8}),
    ("MFR Config",  lambda c: 0xD0 <= c <= 0xDC and c not in {0xD7,0xD8}),
    ("Peaks / Min", lambda c: c in {0xD7,0xD8,0xDD,0xDE,0xDF,0xC4,0xC5,0xC6,0xC7,
                                    0xFB,0xFC,0xFD}),
    ("EEPROM",      lambda c: c in {0xBD,0xBE,0xBF}),
    ("Misc MFR",    lambda c: 0xE0 <= c <= 0xEF or c == 0xF0 or c == 0xF7
                              or c in {0xF4,0xF5}),
]

def _categorize(cmd):
    for name, pred in REG_CATEGORIES:
        if pred(cmd):
            return name
    return "Other"


class ChannelColumn(ttk.LabelFrame):
    def __init__(self, parent, device, page, **kw):
        super().__init__(parent, text=f"  CH{page}  ", **kw)
        self.device = device
        self.page = page
        self.cfg_data = {}
        self.cfg_vars = {}
        self.telem_lbl = {}
        self._wr_btns = {}
        self._ctrl_btns = {}
        self._op_var = None
        self._ooc_var = None
        self._wp_var = None
        self._reg_groups = []

        self.configure(labelanchor='n')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=3)
        self.rowconfigure(2, weight=2)

        self._build_telemetry()
        self._build_config()
        self._build_status()

    # ======================================================= telemetry
    def _build_telemetry(self):
        tf = ttk.LabelFrame(self, text=" Telemetry ")
        tf.grid(row=0, column=0, sticky='ew', padx=3, pady=(2,1))
        for key, label, unit, color in [
            ('VOUT','VOUT','V','#4CAF50'),('IOUT','IOUT','A','#F44336'),
            ('POUT','POUT','W','#9C27B0'),('IIN','IIN','A','#FF9800'),
            ('PIN','PIN','W','#9E9E9E'),('TEMP1','Temperature','C','#E91E63'),
            ('DUTY','Duty Cycle','%','#009688'),('FREQ','Frequency','kHz','#607D8B'),
        ]:
            rf = ttk.Frame(tf); rf.pack(fill='x', padx=2, pady=0)
            ttk.Label(rf, text=label, width=12, anchor='w',
                      font=('Segoe UI',8)).pack(side='left')
            vl = tk.Label(rf, text="---", font=('Consolas',10,'bold'),
                          fg=color, bg='#1a1a2e', width=9, anchor='e',
                          relief='sunken', padx=3)
            vl.pack(side='left', padx=2)
            ttk.Label(rf, text=unit, width=3, font=('Segoe UI',8)).pack(side='left')
            self.telem_lbl[key] = vl

    # ======================================================= config
    def _build_config(self):
        cf = ttk.LabelFrame(self, text=" Config ")
        cf.grid(row=1, column=0, sticky='nsew', padx=3, pady=1)
        self._cfg_nb = ttk.Notebook(cf)
        self._cfg_nb.pack(fill='both', expand=True)

        # ---- Output tab (manual layout) ----
        out_tab = ttk.Frame(self._cfg_nb)
        self._cfg_nb.add(out_tab, text=' Output ')
        canvas = tk.Canvas(out_tab, highlightthickness=0)
        sb = ttk.Scrollbar(out_tab, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0,0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self._bind_scroll(canvas)

        self._sec(inner, "VOUT Settings", [
            ('VOUT_COMMAND','VOUT Command','V'),('VOUT_MAX','VOUT Max','V'),
            ('VOUT_MARGIN_HIGH','Margin High','V'),('VOUT_MARGIN_LOW','Margin Low','V'),
        ])
        self._sec(inner, "VOUT Protection", [
            ('VOUT_OV_FAULT_LIMIT','OV Fault','V'),('VOUT_OV_WARN_LIMIT','OV Warn','V'),
            ('VOUT_UV_WARN_LIMIT','UV Warn','V'),('VOUT_UV_FAULT_LIMIT','UV Fault','V'),
        ])
        self._sec(inner, "IOUT Protection", [
            ('IOUT_OC_FAULT_LIMIT','OC Fault','A'),('IOUT_OC_WARN_LIMIT','OC Warn','A'),
            ('IOUT_UC_FAULT_LIMIT','UC Fault','A'),
        ])
        self._sec(inner, "Temperature", [
            ('OT_FAULT_LIMIT','OT Fault','C'),('OT_WARN_LIMIT','OT Warn','C'),
            ('UT_WARN_LIMIT','UT Warn','C'),('UT_FAULT_LIMIT','UT Fault','C'),
        ])
        self._sec(inner, "Timing", [
            ('FREQUENCY_SWITCH','Frequency','kHz'),('TON_DELAY','TON Delay','ms'),
            ('TON_RISE','TON Rise','ms'),('TOFF_DELAY','TOFF Delay','ms'),
        ])

        # ---- Control tab ----
        ctrl_tab = ttk.Frame(self._cfg_nb)
        self._cfg_nb.add(ctrl_tab, text=' Control ')
        self._build_control_tab(ctrl_tab)

        # ---- Dynamic category tabs for remaining registers ----
        self._build_reg_tabs()

    def _build_reg_tabs(self):
        """Build tabs for registers not already in Output/Control."""
        regmap = getattr(self.device, '_regmap', {})
        ro_set = getattr(self.device, '_read_only', set())
        handled = OUTPUT_TAB_CMDS | CONTROL_TAB_CMDS | SKIP_CMDS | GLOBAL_GUI_CMDS

        # Group remaining regs by category
        groups = {}
        for cmd in sorted(regmap):
            if cmd in handled:
                continue
            name, size, fmt, paged = regmap[cmd]
            if size == 'block':
                continue
            cat = _categorize(cmd)
            is_ro = cmd in ro_set
            groups.setdefault(cat, []).append((cmd, name, size, fmt, paged, is_ro))

        # Create tabs in category order
        seen = set()
        for cat_name, _ in REG_CATEGORIES:
            if cat_name in groups and cat_name not in seen:
                seen.add(cat_name)
                rg = RegisterGroup(self._cfg_nb, self.device, self.page,
                                  groups[cat_name])
                self._cfg_nb.add(rg, text=f' {cat_name} ')
                self._reg_groups.append(rg)
        if "Other" in groups:
            rg = RegisterGroup(self._cfg_nb, self.device, self.page,
                              groups["Other"])
            self._cfg_nb.add(rg, text=' Other ')
            self._reg_groups.append(rg)

    def _sec(self, parent, title, items):
        s = ttk.LabelFrame(parent, text=title)
        s.pack(fill='x', padx=2, pady=1)
        for i, (key, label, unit) in enumerate(items):
            ttk.Label(s, text=label, width=14, anchor='w',
                      font=('Segoe UI',8)).grid(row=i, column=0, padx=2, pady=0, sticky='w')
            v = tk.StringVar(value="---")
            ttk.Entry(s, textvariable=v, width=9, justify='right',
                      font=('Consolas',9)).grid(row=i, column=1, padx=1, pady=0)
            ttk.Label(s, text=unit, width=3,
                      font=('Segoe UI',8)).grid(row=i, column=2, padx=1, pady=0, sticky='w')
            self.cfg_vars[key] = v
            _c = tk.Canvas(s, width=14, height=14, highlightthickness=0, bd=0, cursor='hand2')
            _c.create_polygon(3,2,12,7,3,12, fill='#4CAF50', outline='#2E7D32', tags='tri')
            _c.grid(row=i, column=3, padx=(0,2), pady=0)
            _c.bind('<Button-1>', lambda e, k=key: self._write_single(k))
            _c.bind('<Enter>', lambda e, c=_c: c.itemconfig('tri', fill='#66BB6A'))
            _c.bind('<Leave>', lambda e, c=_c: c.itemconfig('tri', fill='#4CAF50'))
            self._wr_btns[key] = _c

    @staticmethod
    def _bind_scroll(canvas):
        def _do(event):
            if event.num == 4: canvas.yview_scroll(-3,'units')
            elif event.num == 5: canvas.yview_scroll(3,'units')
            else: canvas.yview_scroll(int(-1*(event.delta/120)),'units')
        def _enter(e):
            canvas.bind_all('<MouseWheel>',_do);canvas.bind_all('<Button-4>',_do);canvas.bind_all('<Button-5>',_do)
        def _leave(e):
            canvas.unbind_all('<MouseWheel>');canvas.unbind_all('<Button-4>');canvas.unbind_all('<Button-5>')
        canvas.bind('<Enter>',_enter);canvas.bind('<Leave>',_leave)

    # ======================================================= control tab
    def _build_control_tab(self, parent):
        # OPERATION
        of = ttk.LabelFrame(parent, text=' OPERATION (0x01) ')
        of.pack(fill='x', padx=3, pady=(4,2))
        self._op_var = tk.StringVar()
        op_vals = [f'0x{v:02X}  {d}' for v,d in OPERATION_OPTS]
        ttk.Combobox(of, textvariable=self._op_var, values=op_vals,
                     state='readonly', width=24).pack(side='left', padx=4, pady=3)
        self._ctrl_btn(of, 'OPERATION')

        # ON_OFF_CONFIG
        oof = ttk.LabelFrame(parent, text=' ON_OFF_CONFIG (0x02) ')
        oof.pack(fill='x', padx=3, pady=2)
        self._ooc_var = tk.StringVar()
        ooc_vals = [f'0x{v:02X}  {d}' for v,d in ON_OFF_CFG_OPTS]
        ttk.Combobox(oof, textvariable=self._ooc_var, values=ooc_vals,
                     state='readonly', width=24).pack(side='left', padx=4, pady=3)
        self._ctrl_btn(oof, 'ON_OFF_CONFIG')

        # WRITE_PROTECT
        wpf = ttk.LabelFrame(parent, text=' WRITE_PROTECT (0x10) ')
        wpf.pack(fill='x', padx=3, pady=2)
        self._wp_var = tk.StringVar()
        wp_vals = [f'0x{v:02X}  {d}' for v,d in WRITE_PROTECT_OPTS]
        ttk.Combobox(wpf, textvariable=self._wp_var, values=wp_vals,
                     state='readonly', width=24).pack(side='left', padx=4, pady=3)
        self._ctrl_btn(wpf, 'WRITE_PROTECT')

        # CLEAR_FAULTS
        ttk.Button(parent, text='Clear Faults (0x03)',
                   command=self._do_clear_faults).pack(fill='x', padx=6, pady=(8,2))

    def _ctrl_btn(self, parent, key):
        c = tk.Canvas(parent, width=14, height=14, highlightthickness=0, bd=0, cursor='hand2')
        c.create_polygon(3,2,12,7,3,12, fill='#4CAF50', outline='#2E7D32', tags='tri')
        c.pack(side='left', padx=2)
        c.bind('<Button-1>', lambda e, k=key: self._write_ctrl(k))
        c.bind('<Enter>', lambda e, w=c: w.itemconfig('tri', fill='#66BB6A'))
        c.bind('<Leave>', lambda e, w=c: w.itemconfig('tri', fill='#4CAF50'))
        self._ctrl_btns[key] = c

    def _write_ctrl(self, key):
        opts_map = {
            'OPERATION':     (self._op_var, OPERATION_OPTS, 0x01),
            'ON_OFF_CONFIG': (self._ooc_var, ON_OFF_CFG_OPTS, 0x02),
            'WRITE_PROTECT': (self._wp_var, WRITE_PROTECT_OPTS, 0x10),
        }
        if key not in opts_map: return
        var, opt_list, cmd = opts_map[key]
        text = var.get(); ok = False
        for val, desc in opt_list:
            if f'0x{val:02X}' in text:
                ok = self.device.write_val(self.page, cmd, val, 'BYTE'); break
        w = self._ctrl_btns.get(key)
        if w:
            w.itemconfig('tri', fill='#00E676' if ok else '#FF5252')
            self.after(600, lambda c=w: c.itemconfig('tri', fill='#4CAF50'))

    def _do_clear_faults(self):
        self.device.set_page(self.page)
        self.device.clear_faults()

    # ======================================================= status
    def _build_status(self):
        sf = ttk.LabelFrame(self, text=" Status ")
        sf.grid(row=2, column=0, sticky='nsew', padx=3, pady=(1,3))
        self.status_ind = tk.Label(sf, text="---", font=('Consolas',9,'bold'),
                                  bg='#1a1a2e', fg='#00ff00', anchor='center', relief='sunken')
        self.status_ind.pack(fill='x', padx=2, pady=(2,1))
        tf = ttk.Frame(sf); tf.pack(fill='both', expand=True, padx=2, pady=(1,2))
        self.status_tree = ttk.Treeview(tf, columns=('val','hex'), height=7)
        self.status_tree.heading('#0', text='Register / Bit', anchor='w')
        self.status_tree.heading('val', text='St')
        self.status_tree.heading('hex', text='Hex')
        self.status_tree.column('#0', width=165, minwidth=100)
        self.status_tree.column('val', width=28, anchor='center', minwidth=28)
        self.status_tree.column('hex', width=52, anchor='center', minwidth=42)
        tsb = ttk.Scrollbar(tf, orient='vertical', command=self.status_tree.yview)
        self.status_tree.configure(yscrollcommand=tsb.set)
        self.status_tree.pack(side='left', fill='both', expand=True)
        tsb.pack(side='right', fill='y')
        for tag in ('fault','warn','ok'):
            self.status_tree.tag_configure(tag, foreground={
                'fault':'#FF4444','warn':'#FFD700','ok':'#228B22'}[tag])

    # ======================================================= public API
    def update_telemetry(self, data):
        for key in ('VOUT','IOUT','POUT','IIN','PIN'):
            v = data.get(key)
            lbl = self.telem_lbl.get(key)
            if lbl: lbl.configure(text=f"{v:.3f}" if v is not None else "N/A")
        for key in ('TEMP1','FREQ','DUTY'):
            v = data.get(key)
            lbl = self.telem_lbl.get(key)
            if lbl: lbl.configure(text=f"{v:.1f}" if v is not None else "N/A")

    def update_config(self, cfg_data):
        self.cfg_data = cfg_data
        for key, sv in self.cfg_vars.items():
            if key in cfg_data and cfg_data[key]['value'] is not None:
                sv.set(f"{cfg_data[key]['value']:.4f}")
            else:
                sv.set("ERR")
        # OPERATION combo
        op = cfg_data.get('OPERATION',{}).get('raw')
        if op is not None and self._op_var:
            m = False
            for val, desc in OPERATION_OPTS:
                if val == op: self._op_var.set(f'0x{val:02X}  {desc}'); m=True; break
            if not m: self._op_var.set(f'0x{op:02X}  Unknown')
        # ON_OFF_CONFIG combo
        ooc = cfg_data.get('ON_OFF_CONFIG',{}).get('raw')
        if ooc is not None and self._ooc_var:
            m = False
            for val, desc in ON_OFF_CFG_OPTS:
                if val == ooc: self._ooc_var.set(f'0x{val:02X}  {desc}'); m=True; break
            if not m: self._ooc_var.set(f'0x{ooc:02X}  Unknown')
        # WRITE_PROTECT combo
        wp = cfg_data.get('WRITE_PROTECT',{}).get('raw')
        if wp is not None and self._wp_var:
            m = False
            for val, desc in WRITE_PROTECT_OPTS:
                if val == wp: self._wp_var.set(f'0x{val:02X}  {desc}'); m=True; break
            if not m: self._wp_var.set(f'0x{wp:02X}  Unknown')

    def update_status(self, status_data):
        tree = self.status_tree
        for i in tree.get_children(): tree.delete(i)
        any_fault = any_warn = False

        sw = status_data.get('STATUS_WORD')
        if sw is not None:
            tg = 'fault' if sw & 0xFFC0 else 'ok'
            sid = tree.insert('','end', text='STATUS_WORD',
                             values=('F' if sw else 'OK', f'0x{sw:04X}'), tags=(tg,), open=False)
            for b in range(15,-1,-1):
                bv = (sw>>b)&1; d = STATUS_WORD_BITS.get(b, f"b{b}")
                if bv and b>=5: bt='fault'; any_fault=True
                elif bv: bt='warn'; any_warn=True
                else: bt='ok'
                tree.insert(sid,'end', text=f"  b{b}: {d}", values=(bv,''), tags=(bt,))

        for rn, rv, bits in [
            ('STATUS_VOUT', status_data.get('STATUS_VOUT'), STATUS_VOUT_BITS),
            ('STATUS_IOUT', status_data.get('STATUS_IOUT'), STATUS_IOUT_BITS),
            ('STATUS_TEMPERATURE', status_data.get('STATUS_TEMPERATURE'), STATUS_TEMP_BITS),
        ]:
            if rv is None: continue
            tg = 'fault' if rv else 'ok'
            rid = tree.insert('','end', text=rn, values=('F' if rv else 'OK', f'0x{rv:02X}'), tags=(tg,))
            for b in range(7,-1,-1):
                bv = (rv>>b)&1; d = bits.get(b, f"b{b}")
                if bv and b>=4: bt='fault'; any_fault=True
                elif bv: bt='warn'; any_warn=True
                else: bt='ok'
                tree.insert(rid,'end', text=f"  b{b}: {d}", values=(bv,''), tags=(bt,))

        sm = status_data.get('STATUS_MFR')
        if sm is not None and sm:
            tree.insert('','end', text='STATUS_MFR', values=('F',f'0x{sm:02X}'), tags=('fault',))
            any_fault = True

        if any_fault: self.status_ind.configure(text="FAULT", fg='#FF4444')
        elif any_warn: self.status_ind.configure(text="WARNING", fg='#FFD700')
        else: self.status_ind.configure(text="OK", fg='#00FF00')

    def read_all_reg_groups(self):
        for rg in self._reg_groups:
            rg.read_all()

    def get_write_data(self):
        return [(k, v.get()) for k, v in self.cfg_vars.items()]

    def _write_single(self, key):
        if key not in self.cfg_data: return
        try: nv = float(self.cfg_vars[key].get())
        except ValueError: return
        c = self.cfg_data[key]
        ok = self.device.write_val(self.page, c['cmd'], nv, c['fmt'])
        w = self._wr_btns.get(key)
        if w:
            w.itemconfig('tri', fill='#00E676' if ok else '#FF5252')
            self.after(600, lambda c=w: c.itemconfig('tri', fill='#4CAF50'))
