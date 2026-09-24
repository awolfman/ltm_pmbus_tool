Language / Язык – [Русский](README.ru.md) | English

# LTM PMBus Tool

A Tkinter application for reading telemetry, inspecting status registers and configuring LTM power modules over PMBus/I2C.

The current development version is V4.8. Device profiles and USB adapter support have different levels of hardware validation. See the tables below before connecting equipment.

## Features

- Model-specific register maps for LTM4673, LTM4677 and LTM4678.
- Telemetry and periodic monitoring, with available measurements depending on the device profile.
- Reading and writing supported configuration registers.
- Explicit control actions, including CLEAR_FAULTS and NVM store/restore.
- CSV export and import of accessible register data.
- Profile-aware restrictions on generic reads and writes.
- Startup dependency checks with console diagnostics.

Opening a device tab automatically starts Read All. For a new device or an unverified profile, run a limited diagnostic test before opening the GUI.

## Device profiles and validation

| Model | Pages | Expected L16 exponent | Current validation |
|---|---|---|---|
| LTM4673 | 4 | -13 | Byte/word reads, PAGE selection, telemetry and statuses tested with CH341T and FT232H |
| LTM4677 | 2 | -12 | Limited reads and PAGE selection tested with CH341T after explicit fault clearing |
| LTM4678 | 2 | -12 | Profile implemented; hardware validation pending |

Profile matching uses `MFR_SPECIAL_ID & 0xFFF0`.

| Model | Registered ID families |
|---|---|
| LTM4673 | `0x448X`, `0x023X` |
| LTM4677 | `0x47BX` |
| LTM4678 | `0x4100X` |

The LTM4673 engineering sample with ID `0x0236` is intentionally supported. Unknown IDs are not assigned an LTM4673 profile automatically.

LTM4675 is not included in the current profile set.

Successful communication tests do not validate every register, configuration write, NVM operation or electrical operating condition.

Testing of the available LTM4677 module was paused because of suspected hardware problems. Its communication test completed with zero CML after explicit clearing, but the reported output current on one disabled channel remains unexplained.

## USB adapters and bus numbers

| Bus number | Backend | Python package | Current state |
|---|---|---|---|
| 0–99 | Native system I2C | `smbus2` or `smbus` | Not implemented in the current bus factory |
| 100–199 | CH341T / compatible CH341 I2C device | `pyusb` | Tested with CH341T |
| 200–299 | FTDI MPSSE | `pyftdi` | Tested with FT232H and PyFtdi 0.57.2 |
| 300–399 | CP2112 | `hidapi` | Driver present; hardware debugging pending |

Bus `100` selects CH341 index 0. Bus `200` selects FTDI index 0. Adapter indices are enumeration positions, not permanent hardware identifiers.

The FTDI backend also enumerates FT2232H and FT4232H devices and opens interface 1. These variants have not been hardware-validated in this project.

### CH341 limitations

- The driver raises exceptions for USB failures and incomplete responses.
- Intermediate read bytes are acknowledged; the final byte is terminated with NACK.
- Slave ACK is not checked by this implementation.
- Successful USB completion does not prove that the target accepted a write.
- Fixed-length I2C block reads are not SMBus Block Read transactions.
- PEC is not implemented.

### FTDI behavior

The FTDI wrapper uses PyFtdi for I2C transactions. It propagates transport errors rather than returning `0xFF` or `0xFFFF` as failure markers.

The initial bus frequency is 100 kHz. Word transfers use little-endian byte order.

Clock stretching is disabled in the current configuration. Enabling PyFtdi clock stretching requires appropriate additional wiring and separate validation.

Fixed-length block helpers do not implement SMBus Block Read with a count byte. PEC is not implemented by this wrapper.

## Hardware connection

Disconnect power before changing signal wiring. Use one active USB adapter on the bus during initial testing.

For a typical FT232H module:

| FT232H signal | Connection |
|---|---|
| ADBUS0 / D0 | SCL |
| ADBUS1 / D1 | SDA output, joined to D2 and target SDA |
| ADBUS2 / D2 | SDA input, joined to D1 and target SDA |
| GND | Target GND |

Check the module schematic: some boards already join the SDA signals or include level translation.

SCL and SDA require pull-ups to a voltage compatible with both the adapter and the target. A 3.3 V interface was used in the tested setup. Pull-up resistance must suit the bus capacitance, speed and existing resistors; 2.2 kΩ is not a universal requirement.

Do not connect the adapter power output to an independently powered target without checking the power arrangement.

## Dependencies

Use a Python 3 environment with Tkinter. Python 3.13 was the installation target in the existing setup instructions; a complete minimum-version compatibility matrix has not been established.

Python packages:

```bash
python -m pip install smbus2 pyusb pyftdi hidapi
```

The `hid` import used by the CP2112 driver is provided by `hidapi`. Installing a different package named `hid` may create a conflict.

Tkinter is supplied by the operating system or Python installer, not by pip. CH341 and FTDI access also require the native libusb-1.0 runtime.

On Debian or Ubuntu:

```bash
sudo apt install python3-venv python3-tk libusb-1.0-0
```

On openSUSE with Python 3.13:

```bash
sudo zypper install python313-tk libusb-1_0-0
```

Package names must match the interpreter and distribution in use.

### Startup checks

`main.py` calls `dep_check.py` before importing the application.

The checker reports missing or unusable modules, the active Python executable and installation guidance to the console. It exits with a nonzero status if requirements are unavailable.

The current check covers:

- Tkinter and ttk.
- PyUSB.
- PyFtdi.
- HIDAPI.
- SMBus compatibility modules in hardware mode.
- The libusb backend in hardware mode.

Drivers are currently imported eagerly. Their Python dependencies are therefore checked even when only one adapter is used.

The checker does not install packages, open adapters or test USB permissions. A successful dependency check does not prove that hardware is accessible.

## Installation and launch

Create an isolated environment from the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install smbus2 pyusb pyftdi hidapi
python main.py
```

Run from a terminal to retain startup diagnostics.

Do not use `sudo` as the normal application launch method. Configure USB permissions for the current user instead.

### Linux USB permissions

On desktop systems using systemd-logind, a device-specific udev rule can grant access to the active local user. For CH341 with USB ID `1a86:5512`, an example rule is:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5512", TAG+="uaccess"
```

Place the rule in an appropriate file such as `/etc/udev/rules.d/70-ltm-pmbus.rules`, then reload rules and reconnect the adapter:

```bash
sudo udevadm control --reload-rules
```

Use rules matching the actual USB IDs for other adapters. Headless systems may require group-based permissions instead of `uaccess`.

If a kernel driver prevents interface claiming, inspect the specific device binding. Unloading `ftdi_sio` and `usbserial` globally is not a required startup step and may disrupt unrelated devices.

## Register access and safety

Identification uses a word read of `MFR_SPECIAL_ID` at `0xE7`. PAGE writes are verified by readback.

Generic access is limited by the selected profile:

- Register sizes and availability are checked before access.
- Special-protocol commands are excluded from ordinary polling.
- Status registers are blocked from generic writes and dump restoration.
- Send Byte actions are separate from normal configuration writes.
- CLEAR_FAULTS is not executed automatically when connecting.
- Reserved LTM4673 registers `0xB5` and `0xBC` are excluded from generic access.

Read Dump does not read every documented address. It reads only registers allowed by the current generic-access policy.

Write Dump is not a raw clone operation. Imported CSV entries cannot override the PMBus layer's write restrictions. Verify the target model, address and page before writing.

Store NVM and Restore NVM are explicit actions. Communication tests should not use them merely to diagnose a read error.

## Status display

Verified LTM4673 decoding is provided for standard statuses, `STATUS_MFR_SPECIFIC`, `MFR_PADS` and `MFR_COMMON`.

`STATUS_MFR_SPECIFIC` is command `0x80`. The internal compatibility name `STATUS_MFR` does not refer to a separate command.

For LTM4673, `STATUS_MFR_SPECIFIC=0x18` indicates that the servo target has been reached and the DAC is connected. These bits are informational, not faults.

`MFR_PADS` and `MFR_COMMON` are displayed in Global Status. Their information bits are not treated as faults merely because they are set.

Status updates after Read All run after the remaining register groups, so the display can expose CML errors generated during polling.

LTM4677 and LTM4678 currently use raw gray status displays where model-specific decoding has not been enabled. This also affects standard status rows in the current implementation.

CML bits are not masked. Failed status reads must not be interpreted as OK.

## Data formats

| Format | Meaning |
|---|---|
| L11 | Signed 11-bit mantissa multiplied by two to the signed 5-bit exponent |
| L16 | Unsigned 16-bit value multiplied by two to the exponent obtained from VOUT_MODE |
| BYTE / RAW | Unscaled values used for command fields and status bits |
| Model-specific formats | Applied only where explicitly defined by the profile |

The VOUT exponent does not control L11 current decoding. LTM4677's L16 exponent `-12` must not be applied to `READ_IOUT`.

## Simulation

The entry point retains both flags:

```bash
python main.py --sim
python main.py --demo
```

Simulation is currently under repair. Updating the SMBus substitution alone does not restore the scanner path or bring simulated IDs, PAGE behavior and VOUT_MODE into agreement with the current profiles.

Do not treat simulation as a validated test of the current device maps.

## Project layout

```text
main.py
dep_check.py
core/
    bus_factory.py
    bus_scanner.py
    pmbus_constants.py
    pmbus_device.py
    pmbus_formats.py
    dump_csv.py
    devices/
        base.py
        registry.py
        ltm4673.py
        ltm4677.py
        ltm4678.py
    drivers/
        base_driver.py
        ch341_i2c.py
        ftdi_i2c.py
        ... CP2112 backend
gui/
    app.py
    device_tab.py
    channel_frame.py
    register_tab.py
    register_group.py
    status_defs.py
sim/
    sim_bus.py
```

## Known limitations and roadmap

- [ ] Validate LTM4678 with CH341T and FT232H.
- [ ] Complete CP2112 hardware debugging.
- [ ] Add verified LTM4677 and LTM4678 status decoding.
- [ ] Distinguish unsupported fields from read failures consistently.
- [ ] Hide telemetry fields that the selected profile does not provide.
- [ ] Consolidate configuration into three to five tabs.
- [ ] Provide consistent per-register Read controls alongside Write controls.
- [ ] Make driver dependencies optional through conditional imports.
- [ ] Restore and validate simulation.
- [ ] Implement native system I2C support.
- [ ] Expand automated map, conversion and access-policy tests.
- [ ] Validate special block protocols before exposing them in generic workflows.

## Authors and AI contributions

- awolfman – architecture, task definition, coordination and hardware testing.
- Claude 4.6 – initial application code, PMBus processing and Tkinter interface.
- Gemini – testing assistance, refactoring and stabilization.
- DeepSeek – USB–I2C driver debugging and error-handling work.
- ChatGPT – profile-aware access, CH341 and FTDI wrapper revisions, status display changes, diagnostic tests and dependency checks.

Hardware validation results are reported separately from code review and AI-generated proposals.
