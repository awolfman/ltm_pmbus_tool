LTM PMBus Tool
A Python/Tkinter application for inspecting and configuring supported Analog Devices LTM power-management devices over PMBus.
The application provides device scanning, model-specific telemetry, configuration editors, status decoding, and register dump operations. A read-only demo mode allows the interface to be used without a USB adapter or connected hardware.
Supported devices

• LTM4673 with four channel pages.
• LTM4677 with two channel pages.
• LTM4678 with two channel pages.

Register availability, page scope, and access permissions are determined by the device register map. GUI profiles define telemetry fields and configuration placement separately from transport code.
Support does not imply that every command or hardware operation has been validated on all three models.
USB adapters
The project includes transport support for CH341T, FT232H, and CP2112.
CH341T and FT232H have been used in hardware testing with LTM4678. FT232H uses PyFtdi. CP2112 remains subject to separate hardware validation.
Hardware operation requires the Python dependencies and USB access configuration appropriate to the selected adapter and operating system. Launch commands below assume that the project environment is already configured.
Launch
Run the application from the project root.
python main.py

Launch the read-only demo without a connected adapter.
python main.py --demo

Demo mode
Demo mode creates simulated supported devices and supplies synthetic register values.
Start Monitor updates telemetry with smoothly changing simulated values. These values demonstrate GUI behavior and do not represent measurements or an electrical model of the device.
Configuration fields are read-only in demo mode. Individual register reads and Read All can be used to inspect the simulated data.
Demo testing does not validate USB communication, hardware write behavior, protection thresholds, or NVM operations.
Interface
Each connected device has its own tab. Channel panels are displayed side by side.
The upper area contains device identification, global telemetry, global configuration, and global status. Each channel contains its own telemetry, configuration, and status views.
Global Config has a limited width so that it does not consume the available space intended for Global Status. Scrollbars provide access to content that does not fit.
Configuration editor
Configuration uses compact fields grouped into four tabs.

• Output contains output control and voltage settings, with input and switching settings where applicable.
• Protection contains protection thresholds, fault responses, and Power Good settings.
• Timing contains startup, shutdown, transition, and retry settings.
• Advanced contains calibration, manufacturer configuration, diagnostics, and other eligible registers.

Placement is model-specific. Global registers are shown only in the device-level editor; paged registers are shown in channel editors.
For example, MFR_RETRY_DELAY is global on LTM4673 and channel-specific on LTM4677 and LTM4678.
Individual register access
A blue arrow reads the register and replaces the field contents with the current value. Unsaved input in that field is overwritten.
A green arrow writes the field value after confirmation. It is shown only when writing is permitted by the application.

• Readable and writable registers have both arrows.
• Read-only registers have only the read arrow.
• Demo registers have no write arrow.

The current profile layout selects readable scalar registers. Write-only commands are not automatically added to Config.
Value formats
Standard L11 and L16 fields use engineering values.
Other fields use explicitly marked hexadecimal input beginning with 0x. Manufacturer-specific custom formats currently remain raw in this compact editor unless a dedicated editor is provided.
OPERATION and ON_OFF_CONFIG use predefined selections restored from the earlier interface. Unrecognized read values are displayed as unknown rather than silently replaced by a listed option. The selections still require model-specific hardware validation.
A successful write followed by a successful readback does not prove that the requested physical output state has been reached. The editor does not perform a general exact-value comparison after encoding and readback.
Batch writes
Write All is disabled during the Config migration.
The previous batch writer is not reused for the expanded register layout. Reintroducing batch writes requires explicit handling of changed fields, write order, and control registers such as WRITE_PROTECT.
Telemetry and status
Start Monitor periodically refreshes global and channel telemetry and status. It does not continuously refresh configuration fields.
Status views distinguish faults, warnings, informational states, unknown values, and read errors where supported by the renderer.
Manufacturer-specific decoding is model-dependent. Unsupported manufacturer status layouts may remain raw rather than use another model's interpretation.
Adapter disconnection
Read All, monitoring, and individual operations in the new Config editor handle transport-disconnection exceptions through the device tab.
When a disconnection is detected, monitoring stops, controls are disabled, and displayed data is marked as stale. Previously displayed channel values must not be treated as current measurements.
Reconnect the adapter, then use Refresh and Scan. Refresh clears existing device tabs and closes cached bus connections before enumerating adapters again.
Disconnect handling for separate NVM, dump, and legacy callbacks is not yet uniformly covered by this mechanism.
Register dumps and NVM
The application includes register dump reading, CSV export and import, dump writing, and NVM store and restore controls.
These operations are separate from the new Config editor and are not covered by its demo validation. CSV import does not establish that a dump is suitable for a particular device or board.
NVM operations and dump writes require separate hardware testing. Do not use them as the first write test after updating the application.
Current validation status
The current compact Config interface has been visually checked in demo mode. Layout checks passed without missing or duplicate eligible registers for the tested demo models.
The latest individual read/write controls have not yet been validated on real hardware.
Earlier CH341T and FT232H testing with LTM4678 covered scanning, ordinary reads, monitoring, and reconnection handling. Those results do not validate writes introduced or changed by the current GUI revision.
LTM4677 hardware testing is paused because of concerns about the tested board's power stage. Suspicious measurements are not hidden or replaced with normal-looking values.
Planned hardware checks

• Individual reads of read-only and readable/writable registers, followed by Read All.
• Correct separation of global settings and channel pages.
• Writing an explicitly selected safe parameter, reading it back, and restoring its original value.
• Engineering-value entry and raw-input restrictions.
• Adapter removal during reading, monitoring shutdown, and stale-data lockout.

NVM operations and dump writes are outside this initial validation pass.
Project areas

• core/drivers contains model-specific device support.
• gui/profiles contains telemetry and configuration layout descriptions.
• gui/config_notebook.py implements the compact shared Config editor.
• gui/channel_frame.py implements channel panels.
• gui/device_tab.py implements device-level views and monitoring.
• gui/status_defs.py implements status rendering.

Safety
This application can change power-system behavior. Register access permissions are not a substitute for checking the device datasheet and the electrical limits of the connected board.
Verify the device address, model, selected page, and parameter meaning before writing. Record the original value and use a controlled test setup.
Do not write to shared or broadcast addresses during individual-device testing. Do not store experimental settings to NVM until their behavior has been verified.
