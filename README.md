Language / Язык – [Русский](README.ru.md) | English

# LTM PMBus Tool

A Python/Tkinter application for inspecting and configuring Analog Devices LTM power-management and others devices over PMBus. It provides device discovery, telemetry, configuration editing, status decoding, and register dumps. A read-only demo mode works without an adapter.

## Support and validation

Supported models are LTM4673 with four channel pages, LTM4677 with two, and LTM4678 with two. The device register map defines command availability, page scope, and access permissions. GUI profiles define which telemetry and configuration fields to display.

Transport implementations are included for CH341T, FT232H, and CP2112. CH341T and FT232H have been tested with LTM4678 for scanning, ordinary reads, monitoring, and reconnection. FT232H uses PyFtdi. CP2112 still requires hardware validation. Model support does not mean that every command or operation has been tested on every model.

The current Config layout has been checked in demo mode for missing and duplicate eligible registers. Its latest individual read and write controls have not been validated on hardware. LTM4677 hardware testing is paused because of concerns about the test board's power stage.

## Run

Install the Python dependencies and configure USB access for the selected adapter and operating system. From the project root, run:

```bash
python main.py
```

To explore the interface without hardware, run:

```bash
python main.py --demo
```

Demo mode creates simulated supported devices. Start Monitor displays changing synthetic telemetry, not measurements or an electrical model. Configuration is read-only; individual reads and Read All inspect simulated values. Demo results do not validate USB communication, writes, protections, or NVM operations.

## Interface and register access

Each device has a tab with device-level views and side-by-side channel panels. Global and channel telemetry, configuration, and status are separate. Config groups eligible registers into Output, Protection, Timing, and Advanced. Global registers appear only at device level; paged registers appear in channel editors. For example, MFR_RETRY_DELAY is global on LTM4673 and paged on LTM4677 and LTM4678.

The blue arrow reads a register and replaces the field value, including unsaved input. The green arrow writes after confirmation and appears only where the application permits writing. Read-only registers have no write arrow, and demo mode permits no writes. The current editor includes readable scalar registers; write-only commands need dedicated support.

Standard L11 and L16 fields use engineering values. Other fields require explicitly marked raw hexadecimal input beginning with `0x`. Manufacturer-specific formats remain raw unless a dedicated editor exists. OPERATION and ON_OFF_CONFIG offer predefined selections, but their applicability still needs model-specific hardware validation. Unknown read values remain visible as unknown.

A successful write and readback do not establish the physical output state. The editor does not generally compare the exact encoded value with readback. Write All is disabled until changed-field detection, write order, and control registers such as WRITE_PROTECT are handled for the expanded layout.

Start Monitor refreshes telemetry and status, not configuration. Status rendering distinguishes faults, warnings, informational states, unknown values, and read errors where supported. Unverified manufacturer-specific statuses may remain raw.

## Disconnection, dumps, and NVM

Read All, monitoring, and individual Config operations handle detected adapter disconnection through the device tab. Monitoring stops, controls are disabled, and displayed data is marked stale. Reconnect the adapter, then use Refresh and Scan. Refresh removes device tabs and closes cached bus connections before rediscovery. Separate NVM, dump, and legacy callbacks do not yet have uniform disconnection handling.

Register dump reading, CSV import and export, dump writing, and NVM store and restore are available separately from Config. CSV import does not prove that a dump suits a device or board. Dump writes and NVM operations need separate hardware validation and should not be the first write test after an update.

The next hardware checks cover individual reads and Read All, global versus paged settings, a safe RAM write with readback and restoration, value-entry restrictions, and disconnection during active operations. NVM and dump writes are outside this initial pass.

## Project layout

- `core/drivers` contains model-specific support.
- `gui/profiles` defines telemetry and configuration placement.
- `gui/config_notebook.py` implements the shared Config editor.
- `gui/channel_frame.py` implements channel panels.
- `gui/device_tab.py` implements device views and monitoring.
- `gui/status_defs.py` implements status rendering.

## Adding a USB-to-I2C adapter

1. Follow an existing transport and inspect `core/bus_factory.py`, adapter discovery in `gui/app.py`, and calls in `core/pmbus_device.py`. Keep transport details out of device drivers.
2. Implement the required byte, word, block, and command-only transactions. Verify byte order, block lengths, repeated START, STOP, ACK/NACK, clock stretching, bus speed, SMBus framing, and PEC behavior.
3. Raise recognizable exceptions for removal and transfer failure. Distinguish address NACKs from disconnection, validate response lengths, and never replace failed reads with fabricated values. Set capability flags to match actual behavior.
4. Respect shared-bus caching, locking, and cleanup. Do not close a shared adapter after an isolated device error or perform automatic resets or device writes on open without validation.
5. Register adapter creation and discovery with identifiers that distinguish attached units. Missing optional dependencies must not prevent other transports or demo mode from starting.
6. Validate discovery and repeated reads on a known device, then test removal during scanning, Read All, and monitoring. After reconnection, verify Refresh, Scan, cached-handle cleanup, and multiple devices on one adapter.
7. Test a selected safe RAM write separately, read it back, and restore the original value. Leave NVM and dump writes for a later pass. Document dependencies, USB driver requirements, transaction support, and limitations.

## Adding a PMBus device

1. Obtain documentation for the exact model or firmware revision. Establish addresses, channel count, PAGE behavior, safe identification commands, transaction types, formats, and fault semantics.
2. Add precise identification and driver selection under `core/drivers`. Extend `core/pmbus_device.py` or `core/bus_scanner.py` if needed; do not probe unsupported commands indiscriminately.
3. Define each supported command's code, name, transaction size, format, scope, and separate read and write permissions. Exclude command-only actions, block operations, NVM, and shared-address writes from generic scalar editors.
4. Implement documented conversions, including VOUT_MODE behavior. Validate encoding before enabling engineering-value writes. Keep unsupported formats explicitly raw.
5. Register a profile in `gui/profiles` with global and channel telemetry and Config placement. The register map remains authoritative for availability, scope, and permissions.
6. Add model-specific status definitions in `gui/status_defs.py`. Do not reuse unverified manufacturer-status interpretations.
7. Add a read-only demo device and check page count, profile selection, field placement, and stale-data handling. Demo checks do not establish hardware compatibility.
8. Validate hardware in stages: safe reads, a controlled RAM write with restoration, then fault behavior, NVM, and dump writes separately. Document which operations are implemented, demo-checked, and hardware-validated.

## Safety

Writes can change power-system behavior. Before writing, check the datasheet, board limits, device address, model, page, and parameter meaning. Record the original value and use a controlled test setup. Avoid shared or broadcast addresses during individual-device tests, and do not store experimental settings in NVM before validating their behavior.
Verify the device address, model, selected page, and parameter meaning before writing. Record the original value and use a controlled test setup.
Do not write to shared or broadcast addresses during individual-device testing. Do not store experimental settings to NVM until their behavior has been verified.
