"""Check startup dependencies before importing application modules.

No automatic installation and no GUI dialogs.
Diagnostics are written to stderr.

Driver modules are currently imported eagerly, so their Python
dependencies are checked even when a particular adapter is not used.
"""

import importlib
import subprocess
import sys


def _load_module(name, attributes=()):
    """Return an imported module after checking required attributes."""
    module = importlib.import_module(name)

    for attribute in attributes:
        if not hasattr(module, attribute):
            raise ImportError(
                f"Module {name!r} has no attribute {attribute!r}. "
                "Check the installed package and local module names."
            )

    return module


def _describe_error(exc):
    detail = str(exc).strip()
    if not detail:
        detail = "No additional details"
    return f"{type(exc).__name__}: {detail}"


def check_deps(sim_mode=False):
    """Return True if startup requirements are available.

    Import failures are collected instead of stopping at the first
    missing library. A missing native library may raise OSError rather
    than ImportError, so both are covered by the general exception
    handler.
    """
    problems = []
    pip_packages = set()

    checks = [
        ("tkinter", ("Tk",), None),
        ("tkinter.ttk", ("Frame",), None),
        ("usb.core", ("find",), "pyusb"),
        ("usb.util", ("dispose_resources",), "pyusb"),
        ("pyftdi.ftdi", ("Ftdi",), "pyftdi"),
        ("pyftdi.i2c", ("I2cController",), "pyftdi"),
        ("hid", ("device", "enumerate"), "hidapi"),
    ]

    tkinter_failed = False
    pyusb_available = True

    for module_name, attributes, package in checks:
        try:
            _load_module(module_name, attributes)
        except Exception as exc:
            problems.append(
                f"{module_name}\n"
                f"    {_describe_error(exc)}"
            )

            if package is not None:
                pip_packages.add(package)

            if module_name.startswith("tkinter"):
                tkinter_failed = True

            if module_name.startswith("usb."):
                pyusb_available = False

    if not sim_mode:
        smbus_errors = []

        for module_name in ("smbus2", "smbus"):
            try:
                _load_module(module_name, ("SMBus",))
                break
            except Exception as exc:
                smbus_errors.append(
                    f"{module_name}: {_describe_error(exc)}"
                )
        else:
            problems.append(
                "smbus2 / smbus\n    "
                + "\n    ".join(smbus_errors)
            )
            pip_packages.add("smbus2")

    libusb_failed = False

    if not sim_mode and pyusb_available:
        try:
            backend_module = importlib.import_module(
                "usb.backend.libusb1"
            )
            backend = backend_module.get_backend()

            if backend is None:
                raise RuntimeError(
                    "PyUSB is installed, but the libusb-1.0 "
                    "backend could not be loaded."
                )
        except Exception as exc:
            libusb_failed = True
            problems.append(
                "libusb-1.0 backend\n"
                f"    {_describe_error(exc)}"
            )

    if not problems:
        return True

    lines = [
        "",
        "LTM PMBus Tool cannot start.",
        "",
        f"Python executable: {sys.executable}",
        f"Python version: {sys.version.split()[0]}",
        "",
        "Missing or unusable dependencies:",
    ]

    for problem in problems:
        lines.append(f"  - {problem}")

    if pip_packages:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            *sorted(pip_packages),
        ]

        if sys.platform == "win32":
            command_text = subprocess.list2cmdline(command)
        else:
            import shlex
            command_text = shlex.join(command)

        lines.extend([
            "",
            "Python package installation command:",
            f"  {command_text}",
            "",
            "Use the virtual environment intended for this project.",
            "If the system Python blocks pip installation, create "
            "a virtual environment rather than overriding that block.",
        ])

    if tkinter_failed:
        version = (
            f"python{sys.version_info.major}"
            f"{sys.version_info.minor}-tk"
        )
        lines.extend([
            "",
            "Tkinter is supplied by the Python or OS installation, "
            "not by pip.",
            "Examples:",
            "  Debian/Ubuntu: sudo apt install python3-tk",
            f"  openSUSE: sudo zypper install {version}",
            "  Windows: enable Tcl/Tk support in the Python installer.",
            "The package must match the Python interpreter above.",
        ])

    if libusb_failed:
        lines.extend([
            "",
            "Install the native libusb-1.0 runtime.",
            "Examples:",
            "  Debian/Ubuntu: sudo apt install libusb-1.0-0",
            "  openSUSE: sudo zypper install libusb-1_0-0",
            "  Windows: install a compatible libusb runtime and "
            "configure the adapter driver as required by PyUSB.",
        ])

    lines.extend([
        "",
        "For hid import errors, check that the package is hidapi.",
        "Do not install both hid and hidapi into the same environment "
        "without checking for module conflicts.",
        "",
        "Install or repair the dependencies, then restart.",
        "",
    ])

    print("\n".join(lines), file=sys.stderr)
    return False
