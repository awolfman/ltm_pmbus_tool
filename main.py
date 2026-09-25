#!/usr/bin/env python3
"""LTM PMBus Tool entry point.

    python main.py
    python main.py --sim
    python main.py --demo
"""

import sys


def configure_simulation():
    """Preserve the existing SMBus substitution for simulation."""
    import types

    print("*** SIMULATION MODE ***")

    try:
        import smbus2
    except ImportError:
        smbus2 = types.ModuleType("smbus2")
        smbus2.SMBus = None
        sys.modules["smbus2"] = smbus2
        print("  [SIM] smbus2 stub created")

    from sim.sim_bus import SimBus

    smbus2.SMBus = SimBus
    print("  [SIM] smbus2.SMBus = SimBus")


def main():
    sim_mode = "--sim" in sys.argv or "--demo" in sys.argv

    from dep_check import check_deps

    if not check_deps(sim_mode=sim_mode):
        return 1

    try:
        if sim_mode:
            configure_simulation()

        from gui.app import App

    except (ImportError, OSError) as exc:
        print(
            "\nApplication initialization failed.\n"
            "A dependency or project module could not be loaded.\n"
            f"{type(exc).__name__}: {exc}\n"
            f"Python executable: {sys.executable}",
            file=sys.stderr,
        )
        return 1

    import tkinter as tk

    try:
        app = App()
    except tk.TclError as exc:
        print(
            "\nCannot initialize the graphical interface.\n"
            "Tkinter is installed, but Tk could not create "
            "the application window.\n"
            "Check the graphical session and Tcl/Tk installation.\n"
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
