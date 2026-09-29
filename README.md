Language / Язык – [Русский](README.ru.md) | English

# 🚀 PMBus Device Manager & Telemetry Tool

Python/Tkinter application for PMBus discovery, telemetry, configuration and register dumps.

---

## 📋  Support
| Model | Chanel | VIN | VOUT | IOUT max |
|:---:|:---:|:---:|:---:|:---:|
| **LTM4673** | 4 | 4.5–16V | 0.5–5.5V | 8A |
| **LTM4677** | 2 | 4.5–16V | 0.5–1.8V | 18A |
| **LTM4678** | 2 | 4.5–16V | 0.5–3.4V | 25A |

---

• CH341T and FT232H – hardware-tested for discovery, reads, monitoring and reconnection with LTM4678.

• CP2112 – implemented, hardware validation pending.

---

VOUT_COMMAND writes have been tested on LTM4678. LTM4677 power-stage testing is paused because of unexpected current readings. Support does not imply hardware validation of every operation.

## 🚀 Run

Use a Python environment with Tkinter and install the dependencies.
```bash
python -m pip install -r requirements.txt hidapi
```

```bash
python main.py
```

Hardware access also requires the appropriate USB permissions, driver and native libraries, including libusb for PyUSB.
Read-only demo without an adapter.

```bash
python main.py --demo
```
Demo uses synthetic telemetry. Startup currently checks USB Python dependencies even in demo mode.

## ⚙️ Config

The blue arrow reads a register and replaces its input. The green arrow writes after confirmation. Read All asks before discarding edits. Monitoring updates telemetry and status, not Config.

L11/L16 fields use engineering values; other numeric fields use RAW hex with 0x. OPERATION and ON_OFF_CONFIG use model-specific presets. Values outside the preset list remain visible without normalization.

Preview prepares changes without device I/O. Batch application permits VOUT_COMMAND only, checks baselines and verifies exact RAW readback. OPERATION, ON_OFF_CONFIG and WRITE_PROTECT require individual writes.

Individual OPERATION and ON_OFF_CONFIG writes also require exact readback. Other individual writes do not generally compare RAW values. Failed reads or write attempts invalidate the affected baseline; a failed write attempt preserves the requested input.

Context-dependent LTM4673 OPERATION encodings are currently blocked. Register readback does not establish the physical output state.


## CSV, NVM and disconnection

CSV loading requires a valid SHA-256 checksum. Missing or mismatched checksums are rejected. Reexport preserves source metadata. Before dump writes, selected records and device compatibility are validated. A checksum does not authenticate the source or establish board safety.

Dump writes and NVM store/restore are separate operations. There is no automatic rollback or automatic NVM save. Their hardware validation remains separate from ordinary RAM writes.

Detected adapter disconnection stops monitoring and marks Config stale. Reconnect, then use Refresh and Scan. Operation guards block conflicting manual actions, including across device tabs.


## 📂 Project layout

• core/drivers – adapter transports.

• core/devices – device profiles and value rules.

• core/pmbus_device.py – register access and write validation.

• gui/profiles – telemetry and Config placement.

• gui/config_notebook.py – Config editor.

• gui/status_defs.py – status interpretation.

---

## Safety

Verify the model, address, page and board limits before writing. Record original values and use a controlled test setup. Do not use shared or broadcast addresses for individual-device tests. Validate RAM settings before storing them in NVM.
