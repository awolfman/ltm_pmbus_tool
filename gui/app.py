"""Main application window.

Refresh updates adapter enumeration before the first bus use.
Hot-plug reconnection is not implemented here because bus numbers
are based on enumeration indices and transports are cached.
"""

import logging
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from core.bus_scanner import find_buses, scan_bus, bus_label
from core.bus_factory import (
    CH341_OFFSET,
    FTDI_OFFSET,
    CP2112_OFFSET,
    close_all_buses,
)
from core.pmbus_device import PMBusDevice
from gui.device_tab import DeviceTab


logger = logging.getLogger(__name__)


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("LTM PMBus Tool v4.8")
        self.geometry("1380x850")
        self.minsize(1100, 700)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TNotebook.Tab",
            padding=[14, 5],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TLabelframe.Label",
            font=("Segoe UI", 10, "bold"),
        )

        self.tabs = []
        self.devices = []
        self._bus_map = {}
        self._bus_used = False
        self._closing = False
        self._busy = False
        self._welcome_frame = None

        self._toolbar()

        self.nb = ttk.Notebook(self)
        self.nb.pack(
            fill="both", expand=True, padx=5, pady=5
        )
        self._welcome()

        self.statusbar = ttk.Label(
            self,
            text="Ready",
            relief="sunken",
            anchor="w",
            padding=3,
        )
        self.statusbar.pack(fill="x", side="bottom")

        self.protocol("WM_DELETE_WINDOW", self._quit)
        self._refresh_buses()

        if "--sim" in sys.argv or "--demo" in sys.argv:
            self.after(300, self.scan)

    def _toolbar(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=5, pady=3)

        ttk.Label(toolbar, text="Bus").pack(
            side="left", padx=3
        )

        self.bus_var = tk.StringVar()
        self.bus_cb = ttk.Combobox(
            toolbar,
            textvariable=self.bus_var,
            values=(),
            width=28,
            state="readonly",
        )
        self.bus_cb.pack(side="left", padx=3)

        self.refresh_btn = ttk.Button(
            toolbar,
            text="Refresh",
            command=self._refresh_buses,
        )
        self.refresh_btn.pack(side="left", padx=2)

        self.scan_btn = ttk.Button(
            toolbar, text="Scan", command=self.scan
        )
        self.scan_btn.pack(side="left", padx=6)

        ttk.Separator(
            toolbar, orient="vertical"
        ).pack(side="left", fill="y", padx=6)

        self.cnt_lbl = ttk.Label(
            toolbar,
            text="Devices: 0",
            font=("Segoe UI", 10, "bold"),
        )
        self.cnt_lbl.pack(side="left", padx=4)

        ttk.Separator(
            toolbar, orient="vertical"
        ).pack(side="left", fill="y", padx=6)

        ttk.Label(toolbar, text="Addr").pack(
            side="left", padx=3
        )
        self.addr_var = tk.StringVar(value="0x40")

        ttk.Entry(
            toolbar,
            textvariable=self.addr_var,
            width=6,
        ).pack(side="left", padx=3)

        self.connect_btn = ttk.Button(
            toolbar,
            text="Connect",
            command=self.connect_manual,
        )
        self.connect_btn.pack(side="left", padx=3)

    def _refresh_buses_silent(self):
        new_map = {}

        for bus_num in find_buses():
            label = bus_label(bus_num)
            new_map[f"{bus_num}: {label}"] = bus_num

        # Replace only after successful enumeration.
        self._bus_map = new_map

    def _refresh_buses(self):
        if self._closing or self._busy:
            return

        self._busy = True

        try:
            for tab in self.tabs:
                tab.stop_all()

            for tab in self.tabs:
                self.nb.forget(tab)
                tab.destroy()

            self.tabs.clear()
            self.devices.clear()
            self.cnt_lbl.configure(text="Devices: 0")

            close_all_buses()

            self._refresh_buses_silent()

            values = list(self._bus_map)
            self.bus_cb.configure(values=values)

            if values:
                self.bus_var.set(values[0])
                self._status(
                    f"Buses: {len(self._bus_map)} found"
                )
            else:
                self.bus_var.set("")
                self._status("No buses found")

        except Exception as exc:
            logger.exception("Bus refresh failed")
            self.bus_var.set("")
            self.bus_cb.configure(values=())
            self._status("Bus refresh failed")
            messagebox.showerror(
                "Refresh error",
                str(exc),
                parent=self,
            )

        finally:
            self._busy = False

    def _get_bus_num(self):
        # Never derive a bus number from a placeholder string.
        return self._bus_map.get(self.bus_var.get())

    def _selected_bus_label(self):
        return self.bus_var.get() or "No adapter"

    def _welcome(self):
        if self._welcome_frame is not None:
            return

        frame = ttk.Frame(self.nb)
        self._welcome_frame = frame
        self.nb.add(frame, text="  Welcome  ")

        tk.Label(
            frame,
            text=(
                "LTM PMBus Tool v4.8\n\n"
                "Device profiles\n"
                "LTM4673 / LTM4677 / LTM4678\n\n"
                "USB adapters\n"
                "CH341T/A / FT232H / CP2112\n\n"
                "Refresh updates the adapter list before connection.\n"
                "Scan finds devices on the selected bus.\n"
                "Read All updates registers in a device tab.\n\n"
                "Restart the application after USB reconnection."
            ),
            font=("Segoe UI", 11),
            justify="center",
        ).pack(expand=True)

    def _remove_welcome(self):
        frame = self._welcome_frame
        if frame is not None:
            self.nb.forget(frame)
            frame.destroy()
            self._welcome_frame = None

    def _status(self, text):
        self.statusbar.configure(text=text)
        self.update_idletasks()

    def _stop_monitoring(self):
        for tab in self.tabs:
            tab.stop_all()
            if hasattr(tab, "mon_btn"):
                tab.mon_btn.configure(text="Start Monitor")

    def _clear_tabs(self):
        self._stop_monitoring()

        for tab in self.tabs:
            self.nb.forget(tab)
            tab.destroy()

        self.tabs.clear()
        self.devices.clear()
        self._remove_welcome()
        self.cnt_lbl.configure(text="Devices: 0")

    def _add_device(self, device):
        tab = DeviceTab(self.nb, device)
        self._remove_welcome()

        title = (
            f"{device.name} "
            f"[bus {device.bus_num}, 0x{device.address:02X}]"
        )
        self.nb.add(tab, text=f"  {title}  ")

        self.tabs.append(tab)
        self.devices.append(device)
        self.nb.select(tab)
        self.cnt_lbl.configure(
            text=f"Devices: {len(self.devices)}"
        )

    def scan(self):
        if self._closing or self._busy:
            return

        bus_num = self._get_bus_num()
        if bus_num is None:
            messagebox.showerror(
                "Bus", "Select an available bus.", parent=self
            )
            return

        label = self._selected_bus_label()
        self._busy = True
        self._bus_used = True

        try:
            self._status(f"Scanning {label}...")
            self._clear_tabs()

            found = scan_bus(bus_num)
            for device in found:
                self._add_device(device)

            if self.devices:
                self._status(
                    f"Found {len(self.devices)} on {label}"
                )
            else:
                self._welcome()
                self._status(f"No devices on {label}")

        except PermissionError as exc:
            self._status("Adapter access denied")
            messagebox.showerror(
                "Access denied",
                f"{label}\n\n{exc}\n\n"
                "Check USB permissions and adapter driver setup.",
                parent=self,
            )
            if not self.tabs:
                self._welcome()

        except ImportError as exc:
            self._status("Missing dependency")
            messagebox.showerror(
                "Missing library", str(exc), parent=self
            )
            if not self.tabs:
                self._welcome()

        except Exception as exc:
            logger.exception("Scan failed")
            self._status("Scan failed")
            messagebox.showerror(
                "Scan error", str(exc), parent=self
            )
            if not self.tabs:
                self._welcome()

        finally:
            self._busy = False

    def connect_manual(self):
        if self._closing or self._busy:
            return

        try:
            text = self.addr_var.get().strip()
            address = (
                int(text, 16)
                if text.lower().startswith("0x")
                else int(text)
            )
            if not 0x08 <= address <= 0x77:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Address",
                "Enter a 7-bit address in 0x08..0x77.",
                parent=self,
            )
            return

        bus_num = self._get_bus_num()
        if bus_num is None:
            messagebox.showerror(
                "Bus", "Select an available bus.", parent=self
            )
            return

        for tab in self.tabs:
            device = tab.device
            if (
                device.bus_num == bus_num
                and device.address == address
            ):
                self.nb.select(tab)
                self._status("Device is already connected")
                return

        self._busy = True
        self._bus_used = True

        try:
            self._stop_monitoring()
            self._status(
                f"Connecting bus {bus_num}, address 0x{address:02X}..."
            )

            device = PMBusDevice(bus_num, address)
            if not device.identify():
                raise RuntimeError(
                    device.last_error
                    or f"Cannot identify device at 0x{address:02X}"
                )

            self._add_device(device)
            self._status(
                f"Connected {device.name} at 0x{address:02X}"
            )

        except Exception as exc:
            logger.exception("Manual connection failed")
            self._status("Connection failed")
            messagebox.showerror(
                "Connection failed", str(exc), parent=self
            )

        finally:
            self._busy = False

    def _quit(self):
        if self._closing:
            return

        self._closing = True

        try:
            self._stop_monitoring()
            close_all_buses()
        finally:
            self.destroy()
