Language / Язык: [Russian](README.ru.md) | **English**

# 🚀 PMBus Device Manager & Telemetry Tool

A versatile graphical user interface (Tkinter) utility designed for real-time monitoring, configuration, and management of DC/DC converters (LTM polyphase power modules) using the PMBus/I2C protocol. It supports operations with physical hardware via various USB adapters as well as a standalone simulation mode.

---

## ✨ Features

* **Telemetry:** Real-time reading of parameters such as `VOUT`, `IOUT`, `POUT`, `VIN`, `IIN`, `PIN`, temperature, operating frequency, and duty cycle.
* **Configuration:** Flexible adjustments for output voltage, fault/warning thresholds (`OV`/`UV`/`OC`/`OT`), power-up sequencing/timings, and switching frequency.
* **Control:** Direct command execution for `OPERATION`, `ON_OFF_CONFIG`, and fault clearing via `CLEAR_FAULTS`.
* **Fault Monitoring:** Comprehensive polling and bitwise decoding of status registers, including `STATUS_WORD`, `STATUS_VOUT`, `STATUS_IOUT`, `STATUS_INPUT`, `STATUS_TEMPERATURE`, and `STATUS_CML`.
* **NVM Management:** Non-Volatile Memory control to store and restore configuration settings between operational RAM and device EEPROM (`Store/Restore RAM ↔ EEPROM`).
* **Configuration Dumps:** Sequential bulk reading and writing of the entire register map, featuring configuration export and import via CSV files.

---

## 📋 Supported Devices

| Model | Channels | VIN Range | VOUT Range | Max IOUT |
|:---:|:---:|:---:|:---:|:---:|
| **LTM4671** | 4 | 4.5–16V | 0.5–5.5V | 8A |
| **LTM4673** | 4 | 4.5–16V | 0.5–5.5V | 8A |
| **LTM4675** | 2 | 4.5–16V | 0.5–5.5V | 13A |
| **LTM4676/A** | 2 | 4.5–16V | 0.5–5.5V | 13A |
| **LTM4677** | 2 | 4.5–16V | 0.5–1.8V | 18A |
| **LTM4678** | 2 | 4.5–16V | 0.5–3.4V | 25A |

---

## ⚙️ Interface & Driver Configuration

The application automatically identifies the connection type based on the specified Bus ID parameter:

| Bus ID | Target Adapter | Required Library | Installation Command |
|:---:|:---|:---:|:---|
| **0–99** | Native Linux SMBus `/dev/i2c-N` | `smbus2` | `pip install smbus2` |
| **100–199** | CH341T/A USB-to-I2C Adapter (VID 1a86:5512) | `pyusb` | `pip install pyusb` |
| **200–299** | FT232H / FT2232H / FT4232H ICs | `pyftdi` | `pip install pyftdi` |

### 🛠 Connecting FT232H

When working with an FT232H adapter, implement the hardware wiring according to the schematic below:

```text
FT232H          LTM467x
 AD0 (SCL) ───── SCL
 AD1 (SDA) ───── SDA
 GND ─────────── GND

[Important: External 2.2kΩ pull-up resistors to a 3.3V rail are required on both SCL and SDA lines]
```

To operate the FT232H board under Linux environments, the default kernel Virtual COM Port driver must be unloaded. Otherwise, `pyftdi` will fail to claim exclusive access to the MPSSE engine:
```bash
sudo rmmod ftdi_sio usbserial
```

---

## 📊 PMBus Data Formatting

Under the hood, the utility manages low-level data type conversions natively defined by the PMBus specifications:
* **L11 (Linear_5s_11s):** Consists of a 5-bit signed exponent combined with an 11-bit signed mantissa. This format is typically used for currents, power ratings, temperatures, and frequencies.
* **L16 (Linear_16u):** An unsigned 16-bit integer parsed and scaled according to the device's `VOUT_MODE` exponent. This format handles high-precision voltage measurements.
* **BYTE / RAW:** Unformatted hexadecimal data blocks applied directly to status bitmasks and structural command registers.

---

## 📦 System Dependencies & Requirements

Ensure that your target execution environment satisfies the following baseline prerequisites:
* **Python 3.13+**
* The **tkinter** package component (on Linux systems, this may necessitate a dedicated installation step: `sudo apt install python3-tk`).
* The system-level **libusb** library binaries (mandated for `pyusb` interactions with the CH341 chip architecture).

---

## 🚀 Quick Start

### Deployment & Execution

To explore the graphical components and evaluate layout features in a mock environment without physical hardware connected:
```bash
python main.py --sim
```

To bind the utility to an active local I2C bus or attached USB host adapters under Linux (requires administrative privileges):
```bash
sudo python main.py
```

### Configuring User Permissions for CH341 (Optional)

To circumvent the necessity of invoking `sudo` privileges for every session when utilizing the **CH341** interface, deploy a custom `udev` rule targeting your current user group:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5512", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/99-ch341.rules

sudo udevadm control --reload-rules
```

---

## 📂 Project Directory Structure

```text
.
├── main.py                  # Main application entry point
├── core/                    # Core engine (protocol implementation & hardware drivers)
│   ├── pmbus_constants.py   # Command sets, register mapping, and device profiles
│   ├── pmbus_formats.py     # Data converters for L11/L16 types ↔ float types
│   ├── pmbus_device.py      # PMBusDevice main class (R/W, telemetry, tracking)
│   ├── bus_factory.py       # Bus abstraction factory (dynamic smbus2/CH341/FTDI selection)
│   ├── bus_scanner.py       # I2C network probing for discovering active device nodes
│   ├── ch341_i2c.py         # Hardware driver layer made for CH341 USB-to-I2C adapters
│   ├── ftdi_i2c.py          # Hardware driver layer made for FTDI MPSSE I2C engine engines
│   └── dump_csv.py          # CSV import and export utility for hardware register maps
├── gui/                     # Graphical User Interface component layer (Tkinter)
│   ├── app.py               # Main window and layout definition
│   ├── device_tab.py        # Tab configuration layout for individual device targets
│   ├── channel_frame.py     # Channel column layouts (telemetry fields, configs, and statuses)
│   └── status_defs.py       # Bitmask mappings for STATUS_* registers used in error parsing
└── sim/                     # Mock environments for hardware-free debugging
    └── sim_bus.py           # Mock I2C bus environment for demonstration modes (--sim)
```

## 👥 Authors & AI Contributors

* **awolfman** — *System Architecture, Task Formulation, General Coordination, and Hardware Testing*

* **Claude 4.6** — *Core Application Codebase Generation (PMBus Stack, Protocol Parsing, Tkinter Layout Assembly)*
* **Gemini** — *System Testing, Code Refactoring, and Stabilization Engineering*
* **DeepSeek** — *Interface Driver Debugging (USB-to-I2C Hardware Layer), Error Handling Optimization, and Linux Platform Profiling*

## 🗺️ Roadmap / Future Enhancements (To-Do)

The following development tasks and feature optimizations are scheduled for upcoming release iterations:

- [ ] **⚙️ UI & Graphical User Interface (GUI) Refinement**
  - Optimize the **Refresh** routine to streamline on-the-fly bus re-scans and data updates without requiring application restarts.
  - Redesign the **Config** tabs to improve PMBus parameter categorizations and establish field editing flows.

- [ ] **🐛 Bug Fixes & Telemetry Enhancements**
  - Correct the reading and layout decoration logic assigned to manufacturer-specific status tracking (**STATUS_MFR**).
  - Resolve parsing failures and display behavior errors tied to communication integrity flags (**STATUS_CML**).

- [ ] **🐛 Исправление ошибок и телеметрия**
  - Исправить логику чтения и декорирования регистров специфического статуса производителей (**STATUS_MFR**).
  - Починить корректное отображение и парсинг битов ошибок связи в регистре статуса (**STATUS_CML**).
