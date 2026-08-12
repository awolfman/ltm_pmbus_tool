"""Main application window."""
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from core.bus_scanner import find_buses, scan_bus, bus_label
from core.bus_factory import CH341_OFFSET, FTDI_OFFSET
from core.pmbus_device import PMBusDevice
from gui.device_tab import DeviceTab


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LTM PMBus Tool v4.6")
        self.geometry("1380x850")
        self.minsize(1100, 700)
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('TNotebook.Tab',
                        padding=[14, 5],
                        font=('Segoe UI', 10, 'bold'))
        style.configure('TLabelframe.Label',
                        font=('Segoe UI', 10, 'bold'))
        self.tabs = []
        self.devices = []
        self._bus_map = {}
        self._toolbar()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=5, pady=5)
        self._welcome()
        self.statusbar = ttk.Label(
            self, text="Ready", relief='sunken',
            anchor='w', padding=3)
        self.statusbar.pack(fill='x', side='bottom')
        self.protocol("WM_DELETE_WINDOW", self._quit)

        if '--sim' in sys.argv or '--demo' in sys.argv:
            self.after(300, self.scan)

    def _toolbar(self):
        tb = ttk.Frame(self)
        tb.pack(fill='x', padx=5, pady=3)
        ttk.Label(tb, text="Bus:").pack(side='left', padx=3)
        self.bus_var = tk.StringVar()
        self._refresh_buses_silent()
        vals = list(self._bus_map.keys()) or ['1: N/A']
        self.bus_cb = ttk.Combobox(
            tb, textvariable=self.bus_var,
            values=vals, width=22, state='readonly')
        self.bus_cb.pack(side='left', padx=3)
        if vals:
            self.bus_cb.current(0)
        ttk.Button(tb, text="Refresh",
                   command=self._refresh_buses).pack(
            side='left', padx=2)
        ttk.Button(tb, text="Scan",
                   command=self.scan).pack(side='left', padx=6)
        ttk.Separator(tb, orient='vertical').pack(
            side='left', fill='y', padx=6)
        self.cnt_lbl = ttk.Label(
            tb, text="Devices: 0",
            font=('Segoe UI', 10, 'bold'))
        self.cnt_lbl.pack(side='left', padx=4)
        ttk.Separator(tb, orient='vertical').pack(
            side='left', fill='y', padx=6)
        ttk.Label(tb, text="Addr:").pack(side='left', padx=3)
        self.addr_var = tk.StringVar(value="0x40")
        ttk.Entry(tb, textvariable=self.addr_var,
                  width=6).pack(side='left', padx=3)
        ttk.Button(tb, text="Connect",
                   command=self.connect_manual).pack(
            side='left', padx=3)

    def _refresh_buses_silent(self):
        buses = find_buses()
        self._bus_map = {}
        for b in buses:
            lbl = bus_label(b)
            self._bus_map[f"{b}: {lbl}"] = b

    def _refresh_buses(self):
        self._refresh_buses_silent()
        vals = list(self._bus_map.keys()) or ['1: N/A']
        self.bus_cb.configure(values=vals)
        if vals:
            self.bus_cb.current(0)
        self._status(f"Buses: {len(self._bus_map)} found")

    def _get_bus_num(self):
        sel = self.bus_var.get()
        if sel in self._bus_map:
            return self._bus_map[sel]
        try:
            return int(sel.split(':')[0].strip())
        except ValueError:
            return None

    def _welcome(self):
        w = ttk.Frame(self.nb)
        self.nb.add(w, text="  Welcome  ")
        tk.Label(
            w,
            text=("LTM PMBus Tool v4.6\n\n"
                  "LTM4671 / LTM4673 / LTM4675\n"
                  "LTM4676 / LTM4677 / LTM4678\n\n"
                  "Buses:\n"
                  "  Linux /dev/i2c-N  (smbus2)\n"
                  "  CH341T/A USB-I2C  (pyusb)\n"
                  "  FT232H  USB-I2C   (pyftdi)\n\n"
                  "Click Scan or enter address.\n"
                  "--sim for simulation."),
            font=('Segoe UI', 11),
            justify='center').pack(expand=True)

    def _status(self, t):
        self.statusbar.configure(text=t)
        self.update_idletasks()

    def scan(self):
        bus_num = self._get_bus_num()
        if bus_num is None:
            messagebox.showerror("", "Select a bus.")
            return
        bl = bus_label(bus_num)
        self._status(f"Scanning {bl}...")

        for t in self.tabs:
            t.stop_all()
        for tid in list(self.nb.tabs()):
            self.nb.forget(tid)
        self.tabs.clear()

        try:
            self.devices = scan_bus(bus_num)
        except PermissionError:
            msg = f"No access to {bl}."
            if bus_num >= FTDI_OFFSET:
                msg += ("\n\nTry:\n"
                        "  sudo rmmod ftdi_sio usbserial\n"
                        "  sudo python main.py\n"
                        "  -- or install libusbK via Zadig (Win)")
            elif bus_num >= CH341_OFFSET:
                msg += "\n\nAdd udev rule or use sudo."
            else:
                msg += "\nTry: sudo python main.py"
            messagebox.showerror("Access denied", msg)
            self._welcome(); return
        except ImportError as e:
            messagebox.showerror("Missing library", str(e))
            self._welcome(); return
        except Exception as e:
            import traceback; traceback.print_exc()
            messagebox.showerror("Scan error", str(e))
            self._welcome(); return

        self.cnt_lbl.configure(
            text=f"Devices: {len(self.devices)}")
        if not self.devices:
            self._welcome()
            self._status(f"No devices on {bl}.")
            return

        nc = {}; ni = {}
        for d in self.devices:
            nc[d.name] = nc.get(d.name, 0) + 1
        for d in self.devices:
            if nc[d.name] > 1:
                ni[d.name] = ni.get(d.name, 0) + 1
                tn = f"{d.name} #{ni[d.name]} (0x{d.address:02X})"
            else:
                tn = f"{d.name} (0x{d.address:02X})"
            dt = DeviceTab(self.nb, d)
            self.nb.add(dt, text=f"  {tn}  ")
            self.tabs.append(dt)
        self._status(f"Found {len(self.devices)} on {bl}")

    def connect_manual(self):
        try:
            s = self.addr_var.get().strip()
            addr = (int(s, 16) if s.lower().startswith('0x')
                    else int(s))
            if not 0x08 <= addr <= 0x77:
                raise ValueError
        except ValueError:
            messagebox.showerror("", "Bad address (0x08..0x77).")
            return
        bus_num = self._get_bus_num()
        if bus_num is None:
            messagebox.showerror("", "Select a bus.")
            return
        dev = PMBusDevice(bus_num, addr)
        dev.identify()
        if dev.name == "Unknown":
            dev.name = f"PMBus@0x{addr:02X}"
        dt = DeviceTab(self.nb, dev)
        self.nb.add(dt, text=f"  {dev.name} (0x{addr:02X})  ")
        self.tabs.append(dt)
        self.nb.select(dt)
        self.devices.append(dev)
        self.cnt_lbl.configure(
            text=f"Devices: {len(self.devices)}")

    def _quit(self):
        for t in self.tabs:
            t.stop_all()
        # release USB adapters
        for mod, cls in [
            ('core.ch341_i2c', 'CH341Bus'),
            ('core.ftdi_i2c',  'FtdiBus'),
        ]:
            try:
                m = __import__(mod, fromlist=[cls])
                getattr(m, cls).close_all()
            except Exception:
                pass
        self.destroy()
