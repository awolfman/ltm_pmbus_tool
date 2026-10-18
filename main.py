#!/usr/bin/env python3

"""LTM PMBus Tool entry point.

    python3 main.py
    python3 main.py --sim
    python3 main.py --demo
"""

import sys
import signal

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

    exit_requested = False
    exit_notice_shown = False
    poll_id = None
    app = None

    def request_exit(signum, frame):
        nonlocal exit_requested
        # No Tk calls, transport access or exceptions in the handler.
        exit_requested = True

    def poll_exit():
        nonlocal poll_id, exit_notice_shown
        poll_id = None

        if app._closing:
            return

        if exit_requested:
            if not app._operation_in_progress():
                app._quit()
                return

            if not exit_notice_shown:
                exit_notice_shown = True
                app._status(
                    "Exit requested by Ctrl+C. "
                    "Waiting for the current operation. "
                    "Finish or cancel any open operation dialog."
                )

        poll_id = app.after(100, poll_exit)

    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, request_exit)

    try:
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

        poll_id = app.after(100, poll_exit)
        app.mainloop()
        return 0

    finally:
        try:
            if app is not None and poll_id is not None:
                try:
                    app.after_cancel(poll_id)
                except tk.TclError:
                    pass
        finally:
            signal.signal(signal.SIGINT, previous_sigint)

if __name__ == "__main__":
    sys.exit(main())
