"""Dependency check at startup."""
import sys

def check_deps():
    missing = []
    try:
        import tkinter
    except ImportError:
        missing.append("tkinter -- sudo zypper install python313-tk")
    if 'smbus2' not in sys.modules:
        try:
            import smbus2
        except ImportError:
            try:
                import smbus
            except ImportError:
                missing.append("smbus -- sudo zypper install python313-smbus")
    if missing:
        msg = "Missing:\n\n"
        for lib in missing:
            msg += f"  * {lib}\n"
        msg += "\nInstall and restart."
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb
            _r = _tk.Tk(); _r.withdraw()
            _mb.showerror("Dependencies", msg); _r.destroy()
        except Exception:
            print(msg)
        sys.exit(1)
