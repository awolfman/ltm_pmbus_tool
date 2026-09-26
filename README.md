Language / Язык – [Русский](README.ru.md) | English

# LTM PMBus Tool

A Tkinter application for reading telemetry, inspecting status registers and configuring LTM power modules over PMBus/I2C.

The current development version is V4.8. Device profiles and USB adapters have different levels of hardware validation. See the tables below before connecting equipment.

## Features

- Model-specific register maps for LTM4673, LTM4677 and LTM4678.
- Telemetry and periodic monitoring according to the selected profile.
- Reading and writing supported configuration registers.
- Explicit control actions, including CLEAR_FAULTS and NVM store/restore.
- CSV export and import of accessible register data.
- Profile-aware restrictions on generic reads and writes.
- Startup dependency checks with console diagnostics.
- Transport-disconnection handling during scanning, Read All and monitoring.
- Model-aware manufacturer status display for LTM4673 and LTM4678.

Opening a device tab automatically starts Read All. For a new device or an unverified profile, run a limited diagnostic test before opening the GUI.

## Device profiles and validation

| Model | Pages | Expected L16 exponent | Current validation |
|---|---|---|---|
| LTM4673 | 4 | -13 | Byte/word reads, PAGE selection, telemetry and statuses tested with CH341T and FT232H |
| LTM4677 | 2 | -12 | Limited reads and PAGE selection tested with CH341T after explicit fault clearing |
| LTM4678 | 2 | -12 | Bootstrap, GUI startup, Scan, Read All, status display and USB reconnection scenarios tested with CH341T and FT232H |

Profile matching uses `MFR_SPECIAL_ID & 0xFFF0`.

| Model | Registered ID families |
|---|---|
| LTM4673 | `0x448X`, `0x023X` |
| LTM4677 | `0x47BX` |
| LTM4678 | `0x410X` |

The LTM4673 engineering sample with ID `0x0236` is intentionally supported. Unknown IDs are not assigned an LTM4673 profile automatically.

LTM4675 is not included in the current profile set.

Successful communication tests do not validate every register, configuration write, NVM operation or electrical operating condition.

Testing of the available LTM4677 module was paused because of suspected hardware problems. Its communication test completed with zero CML after explicit clearing, but the reported output current on one disabled channel remains unexplained.

### LTM4678 reference results

The tested module returned the following values.

| Register or property | Observed value |
|---|---|
| PMBus address | `0x4F` |
| MFR_SPECIAL_ID | `0x4101` |
| CAPABILITY | `0xB0` |
| PMBUS_REVISION | `0x22` |
| VOUT_MODE on both pages | `0x14` |
| L16 exponent | `-12` |
| MFR_COMMON in the tested ready state | `0xFC` |
| MFR_PADS in the tested operating state | `0x0333` |
| STATUS_CML during normal startup and Read All | `0x00` |

These are observations from the tested setup, not required values for every operating condition.

Commands `0x57` and `0x59` are excluded from the LTM4678 profile. Manufacturer-specific registers must not be decoded using LTM4673 definitions.

## USB adapters and bus numbers

| Bus number | Backend | Python package | Current state |
|---|---|---|---|
| 0–99 | Native system I2C | `smbus2` or `smbus` | Not implemented in the current bus factory |
| 100–199 | CH341T / compatible CH341 I2C device | `pyusb` | Tested with CH341T, including disconnection and recovery scenarios |
| 200–299 | FTDI MPSSE | `pyftdi` | Tested with FT232H and PyFtdi 0.57.2, including disconnection and recovery scenarios |
| 300–399 | CP2112 | `hidapi` | Driver present; hardware debugging pending |

Bus `100` selects CH341 index 0. Bus `200` selects FTDI index 0. Adapter indices are enumeration positions, not permanent hardware identifiers.

The FTDI backend targets FT232H, FT2232H and FT4232H and opens interface 1. FT2232H and FT4232H have not been hardware-validated in this project.

Reconnection tests do not establish safe automatic reassignment of multiple adapters after their enumeration order changes.

### CH341 limitations and initialization

- The driver raises exceptions for USB failures and incomplete responses.
- Intermediate read bytes are acknowledged; the final byte is terminated with NACK.
- Slave ACK is not checked by this implementation.
- Successful USB completion does not prove that the target accepted a write.
- Fixed-length I2C block reads are not SMBus Block Read transactions.
- PEC is not implemented.

Automatic `reset_bus()` during adapter opening has been removed. Opening configures the adapter and drains pending USB input without that additional reset.

An explicit `reset_bus()` call reproducibly changed LTM4678 STATUS_CML from `0x00` to `0x02` in the tested setup. The extra STOP is the suspected bus-level trigger; the exact electrical sequence has not been verified with an I2C analyzer.

The reset method remains available for explicit use. Do not reintroduce it as an unconditional startup action.

### FTDI behavior

The FTDI wrapper uses PyFtdi for I2C transactions. It propagates transport errors rather than returning `0xFF` or `0xFFFF` as failure markers.

The initial bus frequency is 100 kHz. Word transfers use little-endian byte order.

Clock stretching is disabled. Enabling PyFtdi clock stretching requires appropriate additional wiring and separate validation.

FTDI enumeration flushes the PyFtdi discovery cache before listing devices. The project enumeration helper returns descriptor objects, not descriptor/interface pairs.

Clearing the enumeration cache does not repair an already disconnected controller. Old transport objects must be released before creating a new session.

Fixed-length block helpers do not implement SMBus Block Read with a count byte. PEC is not implemented by this wrapper.

## Refresh and transport disconnection

Refresh updates the adapter list. In the current recovery workflow it stops monitoring, removes old device tabs and closes cached buses before re-enumeration. It is not a register-refresh operation; use Read All to update device data.

An adapter disappearance reported as `Errno 19` or an equivalent recognized disconnect error is converted to `TransportDisconnectedError`. It is not treated as a retryable PMBus read failure.

The tested behavior is:

- Scan stops address enumeration after a recognized transport disconnection.
- Read All stops rather than continuing readiness waits and subsequent reads.
- Monitoring stops and its scheduled callback is cancelled.
- The affected tab displays `DISCONNECTED / STALE DATA` and disables its controls.
- Additional status polling is not started after disconnection is detected.

Some channel values may remain visible as historical data. They are not current measurements once the tab is marked disconnected.

Reconnect the adapter, press Refresh, select the intended bus and run Scan. Do not continue using an old device tab or assume that its adapter index still identifies the same hardware.

Unplugging FT232H during Read All was followed by `STATUS_CML=0x02` on the next connection. This is consistent with interrupted communication, but software logs alone do not isolate the exact I2C event from adapter termination or reinitialization.

CLEAR_FAULTS remains an explicit user action. It is not performed automatically during Scan, Refresh or reconnection.

Disconnection handling during configuration writes, NVM operations and dump workflows requires further review. Independent channel controls and any internal exception handlers must also be checked.

### Temporary scan diagnostics

The current scanner still contains temporary CML diagnostics targeting LTM4678 at `0x4F`. STATUS_CML is read as a byte using command `0x7E`.

These reads occur around identification and add transactions to the scan. They are not a model-independent feature and should be removed or placed behind an explicit diagnostic option before general deployment.

A diagnostic read failure must not be interpreted as a zero status. Do not assume that reading STATUS_CML clears a latched fault; a change in its value requires accounting for intervening operations.

## Hardware connection

Disconnect power before changing signal wiring. Use one active USB adapter on the bus during initial testing.

Typical FT232H wiring is shown below.

| FT232H signal | Connection |
|---|---|
| ADBUS0 / D0 | SCL |
| ADBUS1 / D1 | SDA output, joined to D2 and target SDA |
| ADBUS2 / D2 | SDA input, joined to D1 and target SDA |
| GND | Target GND |

Check the module schematic. Some boards already join the SDA signals or include level translation.

SCL and SDA require pull-ups to a voltage compatible with both the adapter and the target. A 3.3 V interface was used in the tested setup. Pull-up resistance must suit the bus capacitance, speed and existing resistors; 2.2 kΩ is not a universal requirement.

Do not connect the adapter power output to an independently powered target without checking the power arrangement.

Disconnection tests are fault-handling tests, not a recommendation to unplug equipment during normal transactions.

## Dependencies

Use a Python 3 environment with Tkinter. Python 3.13 was used in the reported development setup; a complete minimum-version compatibility matrix has not been established.

Install the Python packages with:

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

Startup checks currently require the driver dependencies even when only one adapter is used. Making dependencies optional per adapter remains planned.

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

Place the rule in a file such as `/etc/udev/rules.d/70-ltm-pmbus.rules`, then reload rules and reconnect the adapter:

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

A failed or interrupted write does not establish whether the device accepted it. Do not automatically replay configuration or NVM operations after reconnection.

## Status display

LTM4673 has model-specific decoding for standard statuses, `STATUS_MFR_SPECIFIC`, `MFR_PADS` and `MFR_COMMON`.

LTM4678 manufacturer-specific decoding is enabled for `STATUS_MFR_SPECIFIC`, `MFR_COMMON` and `MFR_PADS`. Its MFR_COMMON handling accounts for active-low state indications.

Standard status rows for LTM4677 and LTM4678 use generic PMBus bit meanings. This does not establish that every displayed bit is implemented by each model. Full per-model verification remains planned.

LTM4677 manufacturer-specific registers remain raw where decoding has not been verified.

`STATUS_MFR_SPECIFIC` is command `0x80`. The internal compatibility name `STATUS_MFR` does not refer to a separate command. STATUS_CML is byte command `0x7E`.

For LTM4673, `STATUS_MFR_SPECIFIC=0x18` indicates that the servo target has been reached and the DAC is connected. These bits are informational, not faults.

MFR_PADS and MFR_COMMON are displayed in Global Status. Set informational bits are not automatically faults.

### Colors and indicators

- Green indicates no active fault or warning under the current decoding policy.
- Blue indicates informational state.
- Yellow indicates a warning.
- Red indicates a fault or read error, with the row text distinguishing them.
- Gray indicates raw or unverified decoding.

In the tested LTM4678 state, zero STATUS_INPUT, STATUS_CML and STATUS_MFR_SPECIFIC values are green. MFR_COMMON `0xFC` and MFR_PADS `0x0333` have green root rows, while informational child rows remain blue.

MFR_COMMON `0xFC` is shown as READY; MFR_PADS `0x0333` is shown as STATE. The global indicator shows OK when its required values are available and no fault or warning is present.

Severity colors are GUI policy, not a direct representation of hardware ALERT behavior or configured fault responses.

After a successful Read All, statuses are refreshed after the remaining register groups to expose CML events generated during polling. This refresh is skipped after a recognized transport disconnection.

CML bits are not masked. Failed status reads must not be interpreted as OK.

## Data formats

| Format | Meaning |
|---|---|
| L11 | Signed 11-bit mantissa multiplied by two to the signed 5-bit exponent |
| L16 | Unsigned 16-bit value multiplied by two to the exponent obtained from VOUT_MODE |
| BYTE / RAW | Unscaled values used for command fields and status bits |
| Model-specific formats | Applied only where explicitly defined by the profile |

The VOUT exponent does not control L11 current decoding. LTM4677's L16 exponent `-12` must not be applied to READ_IOUT.

## Simulation

The entry point retains both flags:

```bash
python main.py --sim
python main.py --demo
```

The scanner has a simulation branch returning SimDevice. The simulated device does not yet implement the full interface expected by the current GUI and profile-aware access layer.

Simulation remains under repair and has not been validated end to end. Do not treat it as a verified test of current device maps or transport-disconnection handling.

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
        cp2112_i2c.py
gui/
    app.py
    device_tab.py
    channel_frame.py
    register_tab.py
    register_group.py
    status_defs.py
    config_notebook.py
    profiles/
        __init__.py
        gui_base.py
        gui_config.py
        gui_registry.py
        ltm4673_gui.py
        ltm4677_gui.py
        ltm4678_gui.py
sim/
    sim_bus.py
```

Register profiles reside in `core/devices/`. Extracting model-specific GUI descriptions into `gui/profiles/` is planned and is not described here as completed.

## Validation completed in the current iteration

- [x] Test LTM4678 bootstrap and normal GUI polling with CH341T and FT232H.
- [x] Remove automatic CH341 reset during opening.
- [x] Fix FTDI enumeration descriptor handling and refresh its discovery cache.
- [x] Test Refresh and reconnection workflows with CH341T and FT232H.
- [x] Stop scan enumeration on recognized adapter disconnection.
- [x] Stop Read All and monitoring on recognized adapter disconnection.
- [x] Display disconnected tabs as stale and disable their controls.
- [x] Enable LTM4678 manufacturer status decoding and verify normal-state colors.

## Known limitations and roadmap

- [ ] Consolidate Config into three to five tabs.
- [ ] Extract model-specific GUI descriptions into separate files while retaining shared builders and handlers.
- [ ] Review independent channel handlers and exception propagation.
- [ ] Complete disconnection handling for writes, NVM actions and dump workflows.
- [ ] Remove temporary hard-coded CML scan diagnostics or make them explicitly optional.
- [ ] Validate stable adapter selection when multiple USB devices are re-enumerated.
- [ ] Complete CP2112 hardware debugging.
- [ ] Add verified LTM4677 manufacturer status decoding.
- [ ] Verify standard PMBus status bits against each model's documentation.
- [ ] Distinguish unsupported fields from read failures consistently.
- [ ] Hide unavailable telemetry consistently across profiles.
- [ ] Provide consistent per-register Read controls alongside Write controls.
- [ ] Make driver dependencies optional per adapter.
- [ ] Restore and validate simulation.
- [ ] Implement native system I2C support.
- [ ] Expand automated map, conversion and access-policy tests.
- [ ] Validate special block protocols before exposing them in generic workflows.

## Authors and AI contributions

- awolfman – architecture, task definition, coordination and hardware testing.
- Claude 4.6 – initial application code, PMBus processing and Tkinter interface.
- Gemini – testing assistance, refactoring and stabilization.
- DeepSeek – USB–I2C driver debugging and error-handling work.
- ChatGPT – profile-aware access, CH341 and FTDI wrapper revisions, status display, transport-disconnection handling, diagnostic tests and dependency checks.

Hardware validation results are reported separately from code review and AI-generated proposals.
