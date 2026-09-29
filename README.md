Language / Язык – [Русский](README.ru.md) | English

# LTM PMBus Tool (V4.8+)

A Tkinter application for reading telemetry, inspecting status registers and configuring power modules over PMBus/I2C.

The current production version reflects the latest architecture updates, device profiles, and hardware validation states.

## Features

- **Model-specific register maps & layouts**: Comprehensive support for LTM4673, LTM4677, and LTM4678, with visual config management split into 3–5 dedicated tabs.
- **Advanced Telemetry & Monitoring**: Periodic monitoring according to the selected profile with automatic status refreshes after register polling.
- **Safe Configuration Architecture**: Baseline verification, input previewing, exact RAW readback validation, and protection against invalid configurations.
- **Explicit Control Actions**: Targeted execution of `CLEAR_FAULTS`, NVM storage/restoration operations, and specialized presets for `OPERATION` and `ON_OFF_CONFIG`.
- **Secure CSV Data Exchange**: Export and import of register maps with strict SHA-256 checksum verification and record validation before writing.
- **Robust Transport-Disconnection Recovery**: Graceful handling during scanning, Read All, and telemetry polling. Automatically cancels loops, marks tabs as disconnected, and flags data as stale.
- **Diagnostic Logging**: Pre-GUI startup dependency checks and runtime console diagnostics.

Opening a device tab automatically starts a `Read All` transaction. For unverified profiles or new modules, run a limited diagnostic test before operating through the GUI.

## Device profiles and validation

| Model | Channels | Operational Limits | Profile ID Matching (`MFR_SPECIAL_ID & 0xFFF0`) | Current Validation Status |
| :--- | :---: | :--- | :--- | :--- |
| **LTM4673** | 4 | VIN: 4.5–16V<br>VOUT: 0.5–5.5V<br>IOUT max: 8A | `0x448X`, `0x023X` (Engineering sample `0x0236` is explicitly supported) | Byte/word reads, PAGE selection, telemetry, and statuses tested with CH341T and FT232H. Context-dependent OPERATION encodings are currently blocked. |
| **LTM4677** | 2 | VIN: 4.5–16V<br>VOUT: 0.5–1.8V<br>IOUT max: 18A | `0x47BX` | **Power-stage testing is paused.** Communication completed with zero CML after explicit clearing, but unexpected current readings on a disabled channel remain under review. |
| **LTM4678** | 2 | VIN: 4.5–16V<br>VOUT: 0.5–3.4V<br>IOUT max: 25A | `0x410X` | Fully hardware-validated for discovery, bootstrap, GUI startup, `VOUT_COMMAND` writes, monitoring, and USB reconnection with CH341T and FT232H. |

## USB adapters and bus numbers

| Bus number range | Backend adapter / Transport | Python package | Current development & validation state |
| :--- | :--- | :--- | :--- |
| **0 – 99** | Native system I2C | `smbus2` or `smbus` | Not implemented in the current bus factory. |
| **100 – 199** | CH341T / Compatible CH341 device | `pyusb` | Hardware-tested for discovery, telemetry, and recovery scenarios (Bus `100` selects CH341 index 0). |
| **200 – 299** | FTDI MPSSE (FT232H targeted) | `pyftdi` | Hardware-tested with FT232H and PyFtdi 0.57.2, including disconnection loops (Bus `200` selects FTDI index 0). |
| **300 – 399** | CP2112 | `hidapi` | Transport driver implemented; hardware validation is currently pending. |

*Adapter indices represent dynamic enumeration positions, not permanent hardware identifiers. Reconnection routines do not establish automatic reassignment if the enumeration order changes.*

### CH341 backend limitations and behavior
- Driver raises explicit exceptions for USB transaction failures and incomplete packet responses.
- Intermediate read bytes are acknowledged; the final byte is terminated with a NACK.
- Slave ACK is not evaluated by this implementation; a successful USB completion does not guarantee the target accepted a write.
- Fixed-length I2C block reads are standard transfers, not SMBus Block Read transactions. PEC is not implemented.
- **Bus Reset Policy**: Unconditional `reset_bus()` during adapter initialization has been removed. An explicit `reset_bus()` call reproducibly shifts LTM4678 `STATUS_CML` from `0x00` to `0x02` in the tested setup due to an extra STOP sequence.

### FTDI backend behavior
- The wrapper propagates underlying PyFtdi transport errors instead of returning `0xFF` or `0xFFFF` fault markers.
- Initial bus frequency is set to 100 kHz with little-endian word transfers.
- Clock stretching is disabled by default and requires additional circuit wiring and separate software validation.
- FTDI enumeration flushes the PyFtdi discovery cache before listing devices to prevent stale connections. Old transport objects must be released manually before opening a new session.

## Configuration Editor Workflow

The configuration interface (`gui/config_notebook.py`) relies on strict validation rules to safeguard hardware:

1. **Read Action (Blue Arrow)**: Reads a single register value from the device and updates the input field.
2. **Write Action (Green Arrow)**: Executes a configuration write after explicit user confirmation.
3. **Read All Guard**: Asks for user confirmation before discarding any unsaved local edits.
4. **Telemetry Isolation**: Telemetry and periodic monitoring update status grids, but *never* overwrite active fields in the Config tab.
5. **Data Formatting**: L11/L16 fields accept engineering values. All other numeric fields require RAW hexadecimal values with a `0x` prefix. `OPERATION` and `ON_OFF_CONFIG` leverage model-specific presets.

### Preview & Batch Write Engine
- **Preview**: Prepares and structures changes locally without initiating device I/O.
- **Batch application**: Restricts batch operations strictly to `VOUT_COMMAND`. The engine checks baselines and verifies exact RAW readbacks post-write.
- **Individual execution**: `OPERATION`, `ON_OFF_CONFIG`, and `WRITE_PROTECT` require individual writes and strict readback validation. A failed read or write attempt invalidates the baseline, but preserves the requested user input for troubleshooting.

## CSV, NVM, and Disconnection Recovery

### CSV & Register Dumps
- **Checksum Verification**: Loading an external CSV requires a valid SHA-256 checksum. Mismatched or missing checksums are rejected immediately. Re-exporting register maps preserves the source metadata.
- **Dump Profiles**: Before executing dump writes, record selection and target device compatibility are validated against the active profile. Read Dump only queries registers allowed by the generic-access policy.
- **Write Restrictions**: Write Dump is not a raw clone operation; imported entries cannot bypass the PMBus layer's write restrictions.

### Non-Volatile Memory (NVM)
- `Store NVM` and `Restore NVM` are separate, explicit user operations.
- There is no automatic rollback mechanism or automatic saving to NVM after RAM writes.
- Hardware validation of NVM commands is treated independently from RAM operations. Do not replay NVM operations to diagnose simple read errors.

### Disconnection Workflow
When an adapter disconnection occurs (e.g., `Errno 19` converted to `TransportDisconnectedError`):
1. Telemetry monitoring loops and address scans are immediately stopped and cancelled.
2. The affected tab displays `DISCONNECTED / STALE DATA` and disables all interaction controls.
3. Cross-tab operation guards block conflicting manual actions across different device interfaces.
4. To recover: Reconnect the hardware adapter, click **Refresh** to re-enumerate the buses, select the target bus, and execute a new **Scan**. Do not use old tabs.

## Status Display and Color Policies

Global and model-specific statuses are decoded via `gui/status_defs.py`.

- **LTM4673**: Specific decoding implemented for standard statuses, `STATUS_MFR_SPECIFIC` (command `0x80`), `MFR_PADS`, and `MFR_COMMON`. `STATUS_MFR_SPECIFIC=0x18` is decoded as an informational state (servo target reached, DAC connected).
- **LTM4678**: Manufacturer-specific decoding enabled for `STATUS_MFR_SPECIFIC`, `MFR_PADS`, and `MFR_COMMON` (accounting for active-low signals).
- **LTM4677**: Manufacturer-specific status registers remain raw until full verification is complete. Standard statuses use generic PMBus definitions.

### GUI Severity Colors
The interface applies strict color mappings to differentiate states (note: these are GUI design rules, not direct representations of hardware `ALERT` lines):
- 🟢 **Green**: Active state is normal; no faults or warnings detected.
- 🔵 **Blue**: Informational state or child row metrics (e.g., `MFR_COMMON` ready markers).
- 🟡 **Yellow**: Warning condition flagged by the register.
- 🔴 **Red**: Critical fault or physical read error (row text explicitly distinguishes the two).
- 🔘 **Gray**: Raw register data or unverified decoding profiles.

## Hardware connection and Wiring

> ⚠️ **CRITICAL SAFETY WARNING**: Disconnect all power before changing signal or bus wiring. Verify the model, address, page, and physical board limits before committing any write. Do not use shared or broadcast addresses for individual device testing.

### Typical FT232H I2C Wiring Layout

| FT232H Pin / Signal | Connection Target | Notes |
| :--- | :--- | :--- |
| **ADBUS0 / D0** | SCL (Serial Clock) | Requires external pull-up |
| **ADBUS1 / D1** | SDA Output | Must be joined to D2 and target SDA line |
| **ADBUS2 / D2** | SDA Input | Must be joined to D1 and target SDA line |
| **GND** | Target Board GND | Common ground reference |

Ensure SCL and SDA lines are equipped with pull-up resistors tied to a voltage compatible with both the adapter and the target module (a 3.3V interface was validated in the reference setup; resistor values must match bus capacitance). Do not bridge adapter power outputs to independently powered target boards.

## Dependencies and Installation

### Software Requirements
- **Python 3.13** (Development environment benchmark; minimum-version matrix not fully established).
- **Tkinter** (Supplied by your operating system package manager or Python installer).
- **Native Runtime**: `libusb-1.0` is required for PyUSB and hardware-level operations.

### Installation
Create an isolated virtual environment and install the required bindings:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt hidapi
```
*(Alternatively, install explicitly: `pip install smbus2 pyusb pyftdi hidapi`).*

### System-Specific Package Names
- **Debian / Ubuntu**:
  ```bash
  sudo apt install python3-venv python3-tk libusb-1.0-0
  ```
- **openSUSE (Python 3.13)**:
  ```bash
  sudo zypper install python313-tk libusb-1_0-0
  ```

### Linux USB Permissions (udev)
To access USB adapters without `sudo`, create a custom udev rule at `/etc/udev/rules.d/70-ltm-pmbus.rules`. Example for CH341 (`1a86:5512`):
```text
SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5512", TAG+="uaccess"
```
Reload the system rules and reconnect your adapter:
```bash
sudo udevadm control --reload-rules
```

## Launch and Execution Modes

Always execute the tool from a terminal window to ensure startup checks and console diagnostics remain visible.

### Hardware Mode
```bash
python main.py
```
*The tool executes `dep_check.py` on launch to verify Tkinter, PyUSB, PyFtdi, HIDAPI, and libusb compliance.*

### Demo / Simulation Mode
If no hardware adapter is available, you can run a read-only demonstration using synthetic telemetry:
```bash
python main.py --demo
# or
python main.py --sim
```
*Note: The driver dependency check is currently required even when booting into demo/simulation mode. The simulation framework is under active repair and should not be used to validate device maps end-to-end.*

## Project layout

```text
main.py                   # Application entry point and flag evaluation
dep_check.py              # Startup dependency validation engine
core/
    bus_factory.py        # Adapter instantiation layer
    bus_scanner.py        # I2C bus scanning and temporary diagnostic loops
    pmbus_constants.py    # Standard PMBus command and register definitions
    demo_device.py        # Read-only GUI demo using actual device register profiles
    pmbus_device.py       # Register access state machine and validation
    pmbus_formats.py      # L11, L16, and RAW format bit-parsers
    dump_csv.py           # SHA-256 protected CSV import/export handlers
    i2c_backend.py        # I2C backend -- smbus2, smbus
    devices/
        base.py           # Device profile base abstractions
        registry.py       # Profile registration and tracking
        ltm4673.py        # LTM4673 register maps and specific decoding
        ltm4677.py        # LTM4677 register maps
        ltm4678.py        # LTM4678 validated profile definitions
    drivers/
        base_driver.py    # Hardware driver interface structure
        ch341_i2c.py      # CH341 PyUSB transport wrapper
        ftdi_i2c.py       # FTDI PyFtdi MPSSE transport wrapper
        cp2112_i2c.py     # Silicon Labs CP2112 HIDAPI transport implementation
gui/
    profiles/             # Telemetry and Config visual placements
    app.py                # Main Tkinter window container
    device_tab.py         # Multi-channel layout container
    channel_frame.py      # Telemetry display blocks per channel
    config_notebook.py    # Interactive validation register editor
    register_tab.py       # Low-level layout controller
    register_group.py     # Structural grid groupings
    status_defs.py        # Color policies and register decoding maps
sim/
    sim_bus.py            # Simulated I2C bus environment (Under repair)
```

---

## Authors and AI contributions

- **awolfman** – Architecture definition, task coordination, and core hardware validation.
- **Claude 4.6** – Base application structure, PMBus processing engines, and Tkinter interface components.
- **Gemini** – Testing frameworks, refactoring, and code stabilization.
- **DeepSeek** – Low-level USB-to-I2C driver debugging and edge-case error handling.
- **ChatGPT** – Profile-aware data access, validation engines, wrapper synchronization, reconnection workflows, and diagnostic checkers.
