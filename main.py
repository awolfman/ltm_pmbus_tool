#!/usr/bin/env python3
"""LTM PMBus Tool -- entry point.

    python main.py --sim       Simulation mode
    python main.py             Real hardware (i2c / CH341)
"""
import sys

SIM_MODE = '--sim' in sys.argv or '--demo' in sys.argv

if SIM_MODE:
    try:
        import tkinter
    except ImportError:
        print("ERROR: tkinter required.\n"
              "  sudo apt install python3-tk")
        sys.exit(1)

    print("*** SIMULATION MODE ***")

    try:
        import smbus2
    except ImportError:
        import types
        smbus2 = types.ModuleType("smbus2")
        smbus2.SMBus = None
        sys.modules["smbus2"] = smbus2
        print("  [SIM] smbus2 stub created")

    from sim.sim_bus import SimBus
    smbus2.SMBus = SimBus
    print(f"  [SIM] smbus2.SMBus = SimBus")
else:
    from dep_check import check_deps
    check_deps()

from gui.app import App

if __name__ == "__main__":
    app = App()
    app.mainloop()
