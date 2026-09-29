"""Offline checks of Config and DeviceTab operation guards."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_vout_gui as fixture
from core.pmbus_constants import Cmd
from core.pmbus_device import TransportDisconnectedError
from gui.app import App
from test_vout_write_plan import MemoryDevice

class ConfigGuardTests(unittest.TestCase):
    _patch = fixture.VoutGuiTests._patch
    _prepare_changes = fixture.VoutGuiTests._prepare_changes

    def test_failed_control_write_invalidates_baseline_without_retry(self):
        for sid in (0x0236, 0x47B8, 0x4101):
            for cmd, baseline_raw, requested in (
                (Cmd.OPERATION, 0x80, 0x98),
                (Cmd.ON_OFF_CONFIG, 0x16, 0x1E),
            ):
                with self.subTest(
                    special_id=hex(sid),
                    cmd=hex(cmd),
                ):
                    device, editor = self._make_control_editor(sid)
                    item = editor._rows[cmd]
                    editor._show(cmd, baseline_raw)

                    selected = next(
                        text
                        for text, raw in item["options"].items()
                        if raw == requested
                    )
                    item["variable"].set(selected)

                    other_before = {
                        code: (
                            row["variable"].get(),
                            row["baseline"],
                            row["baseline_raw"],
                        )
                        for code, row in editor._rows.items()
                        if code != cmd
                    }

                    events_before = list(device.events)
                    device.last_error = "Injected write failure"

                    self.confirm.reset_mock()
                    self.confirm.side_effect = None
                    self.confirm.return_value = True
                    self.error.reset_mock()

                    with (
                        patch.object(
                            device,
                            "write_register",
                            return_value=False,
                        ) as write,
                        patch.object(
                            device, "read_register"
                        ) as read,
                        patch.object(
                            device, "write_val"
                        ) as write_val,
                    ):
                        editor._run_write(cmd)

                        write.assert_called_once_with(
                            0, cmd, requested, "byte"
                        )
                        read.assert_not_called()
                        write_val.assert_not_called()

                        self.assertEqual(
                            item["variable"].get(), selected
                        )
                        self.assertIsNone(item["baseline"])
                        self.assertIsNone(item["baseline_raw"])

                        with self.assertRaisesRegex(
                            ValueError,
                            "Read these registers",
                        ):
                            editor.prepare_pending_changes()

                        # Preparing changes must not retry or read.
                        write.assert_called_once()
                        read.assert_not_called()

                    self.confirm.assert_called_once()
                    self.error.assert_called_once()
                    message = self.error.call_args[0][1]
                    self.assertIn(
                        "Injected write failure", message
                    )
                    self.assertIn(
                        "The device state is not confirmed",
                        message,
                    )
                    self.assertIn(
                        "No automatic retry was performed",
                        message,
                    )

                    self.assertFalse(editor._busy)
                    self.assertFalse(editor._stale)
                    self.assertEqual(
                        item["write_button"].itemcget(
                            "tri", "fill"
                        ).lower(),
                        "#ff5252",
                    )

                    other_after = {
                        code: (
                            row["variable"].get(),
                            row["baseline"],
                            row["baseline_raw"],
                        )
                        for code, row in editor._rows.items()
                        if code != cmd
                    }
                    self.assertEqual(
                        other_after, other_before
                    )
                    self.assertEqual(
                        device.events, events_before
                    )

        self.factory.assert_not_called()

    def test_write_exception_invalidates_only_target_baseline(self):
        cases = (
            (Cmd.OPERATION, 0x80, "write_register"),
            (Cmd.VOUT_COMMAND, 0x1000, "write_val"),
        )

        for cmd, baseline_raw, method in cases:
            with self.subTest(cmd=hex(cmd), method=method):
                self._clear_editor_pending_input()
                editor = self.editor
                item = editor._rows[cmd]

                editor._show(cmd, baseline_raw)

                if cmd == Cmd.OPERATION:
                    selected = next(
                        text
                        for text, raw in item["options"].items()
                        if raw == 0x98
                    )
                else:
                    self.assertTrue(item["engineering"])
                    selected = "1.1000"

                item["variable"].set(selected)

                other_before = {
                    code: (
                        row["variable"].get(),
                        row["baseline"],
                        row["baseline_raw"],
                    )
                    for code, row in editor._rows.items()
                    if code != cmd
                }
                events_before = list(self.device.events)

                self.confirm.reset_mock()
                self.confirm.side_effect = None
                self.confirm.return_value = True
                self.error.reset_mock()

                with (
                    patch.object(
                        self.device, "write_register"
                    ) as write_raw,
                    patch.object(
                        self.device, "write_val"
                    ) as write_val,
                    patch.object(
                        self.device, "read_register"
                    ) as read,
                ):
                    target = (
                        write_raw
                        if method == "write_register"
                        else write_val
                    )
                    target.side_effect = OSError(
                        "Injected write exception"
                    )

                    editor._run_write(cmd)

                    if method == "write_register":
                        write_raw.assert_called_once_with(
                            editor.page,
                            cmd,
                            0x98,
                            item["row"].size,
                        )
                        write_val.assert_not_called()
                    else:
                        write_val.assert_called_once_with(
                            editor.page,
                            cmd,
                            1.1,
                            item["row"].fmt,
                        )
                        write_raw.assert_not_called()

                    read.assert_not_called()

                    self.assertIsNone(item["baseline"])
                    self.assertIsNone(item["baseline_raw"])
                    self.assertEqual(
                        item["variable"].get(), selected
                    )

                    with self.assertRaisesRegex(
                        ValueError,
                        "Read these registers",
                    ):
                        editor.prepare_pending_changes()

                    target.assert_called_once()
                    read.assert_not_called()

                self.confirm.assert_called_once()
                self.error.assert_called_once()
                message = self.error.call_args[0][1]
                self.assertIn(
                    "Injected write exception", message
                )
                self.assertIn(
                    "The device state is not confirmed",
                    message,
                )
                self.assertIn(
                    "No automatic retry was performed",
                    message,
                )

                self.assertFalse(editor._busy)
                self.assertFalse(editor._stale)
                self.assertFalse(self.tab._disconnected)
                self.assertEqual(
                    item["write_button"].itemcget(
                        "tri", "fill"
                    ).lower(),
                    "#ff5252",
                )

                other_after = {
                    code: (
                        row["variable"].get(),
                        row["baseline"],
                        row["baseline_raw"],
                    )
                    for code, row in editor._rows.items()
                    if code != cmd
                }
                self.assertEqual(other_after, other_before)
                self.assertEqual(
                    self.device.events, events_before
                )

        self.factory.assert_not_called()

    def test_control_write_checks_exact_readback(self):
        for sid in (0x0236, 0x47B8, 0x4101):
            for cmd, requested, different in (
                (Cmd.OPERATION, 0x98, 0x80),
                (Cmd.ON_OFF_CONFIG, 0x1E, 0x16),
            ):
                for matches in (True, False):
                    with self.subTest(
                        special_id=hex(sid),
                        cmd=hex(cmd),
                        matches=matches,
                    ):
                        device, editor = (
                            self._make_control_editor(sid)
                        )
                        item = editor._rows[cmd]
                        editor._show(cmd, different)

                        selected = next(
                            text
                            for text, raw in item["options"].items()
                            if raw == requested
                        )
                        item["variable"].set(selected)

                        actual = (
                            requested if matches else different
                        )
                        events_before = list(device.events)
                        calls = []

                        self.confirm.reset_mock()
                        self.confirm.side_effect = None
                        self.confirm.return_value = True
                        self.error.reset_mock()

                        def write(page, code, raw, size):
                            calls.append(
                                ("write", page, code, raw, size)
                            )
                            return True

                        def read(page, code):
                            calls.append(("read", page, code))
                            return actual

                        with (
                            patch.object(
                                device,
                                "write_register",
                                side_effect=write,
                            ) as write_register,
                            patch.object(
                                device,
                                "read_register",
                                side_effect=read,
                            ) as read_register,
                            patch.object(
                                device, "write_val"
                            ) as write_val,
                        ):
                            editor._run_write(cmd)

                            write_register.assert_called_once_with(
                                0, cmd, requested, "byte"
                            )
                            read_register.assert_called_once_with(
                                0, cmd
                            )
                            write_val.assert_not_called()

                        self.assertEqual(
                            calls,
                            [
                                ("write", 0, cmd, requested, "byte"),
                                ("read", 0, cmd),
                            ],
                        )
                        self.confirm.assert_called_once()
                        self.assertFalse(editor._busy)

                        expected_text = next(
                            text
                            for text, raw in item["options"].items()
                            if raw == actual
                        )
                        self.assertEqual(
                            item["variable"].get(), expected_text
                        )
                        self.assertEqual(
                            item["baseline"], expected_text
                        )
                        self.assertEqual(
                            item["baseline_raw"], actual
                        )

                        # The actual readback is displayed, rather
                        # than leaving a pending requested value.
                        self.assertEqual(
                            editor.get_pending_changes(), []
                        )

                        write_color = item[
                            "write_button"
                        ].itemcget("tri", "fill")
                        read_color = item[
                            "read_button"
                        ].itemcget("tri", "fill")

                        self.assertEqual(
                            read_color.lower(), "#00e676"
                        )

                        if matches:
                            self.error.assert_not_called()
                            self.assertEqual(
                                write_color.lower(), "#00e676"
                            )
                        else:
                            self.error.assert_called_once()
                            message = self.error.call_args[0][1]
                            self.assertIn(
                                "Control readback mismatch",
                                message,
                            )
                            self.assertIn(
                                f"requested 0x{requested:02X}",
                                message,
                            )
                            self.assertIn(
                                f"received 0x{actual:02X}",
                                message,
                            )
                            self.assertEqual(
                                write_color.lower(), "#ff5252"
                            )

                        self.assertEqual(
                            device.events, events_before
                        )

        self.factory.assert_not_called()

    def test_failed_reads_clear_affected_baseline(self):
        cmd = self.cmd
        item = self.editor._rows[cmd]

        cases = (
            ("individual_exception", True),
            ("individual_none", False),
            ("read_all_exception", True),
            ("readback_exception", True),
        )

        for mode, raises in cases:
            with self.subTest(mode=mode):
                self._clear_editor_pending_input()
                self.editor._show(cmd, 0x1000)

                self.assertIsNotNone(item["baseline"])
                self.assertEqual(
                    item["baseline_raw"], 0x1000
                )

                other_before = {
                    code: (
                        row["variable"].get(),
                        row["baseline"],
                        row["baseline_raw"],
                    )
                    for code, row in self.editor._rows.items()
                    if code != cmd
                }

                self.confirm.reset_mock()
                self.confirm.side_effect = None
                self.confirm.return_value = True
                self.error.reset_mock()

                events_before = list(self.device.events)

                with (
                    patch.object(
                        self.device,
                        "read_register",
                        side_effect=(
                            OSError("Injected baseline read failure")
                            if raises
                            else None
                        ),
                        return_value=None,
                    ) as read,
                    patch.object(
                        self.device,
                        "write_val",
                        return_value=True,
                    ) as write_val,
                    patch.object(
                        self.device,
                        "write_register",
                        return_value=True,
                    ) as write_raw,
                ):
                    if mode == "read_all_exception":
                        # Isolate one readable row without rebuilding
                        # the editor or modifying other baselines.
                        with patch.object(
                            self.editor,
                            "_can_read",
                            side_effect=lambda row: row.cmd == cmd,
                        ):
                            with self.assertRaisesRegex(
                                OSError,
                                "Injected baseline read failure",
                            ):
                                self.editor.read_all()

                    elif mode == "readback_exception":
                        item["variable"].set("1.0000")
                        self.editor._run_write(cmd)
                        self.confirm.assert_called_once()
                        write_val.assert_called_once()

                    else:
                        self.editor._run_read(cmd)

                    read.assert_called_once_with(
                        self.editor.page, cmd
                    )
                    write_raw.assert_not_called()

                    if mode != "readback_exception":
                        write_val.assert_not_called()
                        self.confirm.assert_not_called()

                self.assertEqual(
                    item["variable"].get(), "ERR"
                )
                self.assertIsNone(item["baseline"])
                self.assertIsNone(item["baseline_raw"])
                self.assertFalse(self.editor._busy)
                self.assertFalse(self.tab._disconnected)

                other_after = {
                    code: (
                        row["variable"].get(),
                        row["baseline"],
                        row["baseline_raw"],
                    )
                    for code, row in self.editor._rows.items()
                    if code != cmd
                }
                self.assertEqual(other_after, other_before)
                self.assertEqual(
                    self.device.events, events_before
                )

                # A new edit must not reuse the old baseline.
                item["variable"].set("1.1000")
                with self.assertRaisesRegex(
                    ValueError,
                    "Read these registers",
                ):
                    self.editor.prepare_pending_changes()

        self.factory.assert_not_called()

    def setUp(self):
        fixture.VoutGuiTests.setUp(self)
        self._prepare_changes()
        self.editor = self.tab.channels[0].config_editor
        self.other = self.tab.channels[1].config_editor
        self.cmd = Cmd.VOUT_COMMAND

    def _make_control_editor(self, special_id):
        device = MemoryDevice(special_id=special_id)
        editor_class = type(self.editor)

        rows = []
        for cmd in (Cmd.OPERATION, Cmd.ON_OFF_CONFIG):
            name, size, fmt, paged = device._regmap[cmd]
            self.assertTrue(paged)
            rows.append(SimpleNamespace(
                cmd=cmd,
                name=name,
                size=size,
                fmt=fmt,
                writable=True,
            ))

        # Use an existing tab title, without assuming its position.
        tab_title = next(
            group.tab
            for group in self.editor.layout
            if any(
                row.cmd == Cmd.OPERATION
                for row in group.rows
            )
        )
        layout = [
            SimpleNamespace(
                tab=tab_title,
                title="Control test",
                rows=tuple(rows),
            )
        ]

        with patch(
            f"{editor_class.__module__}.build_config_layout",
            return_value=layout,
        ):
            editor = editor_class(
                self.root,
                device,
                None,
                page=0,
                paged=True,
            )

        self.addCleanup(editor.destroy)
        return device, editor

    def test_control_comboboxes_use_each_device_profile(self):
        expected_operations = {
            0x0236: {
                0x00, 0x40, 0x54, 0x58, 0x64, 0x68,
                0x80, 0x94, 0x98, 0xA4, 0xA8,
            },
            0x47B8: {
                0x00, 0x40, 0x80, 0x98, 0xA8,
            },
            0x4101: {
                0x00, 0x40, 0x80, 0x98, 0xA8,
            },
        }
        config_4673 = {
            0x02, 0x03, 0x06, 0x07,
            0x0A, 0x0B, 0x0E, 0x0F,
            0x12, 0x13, 0x16, 0x17,
            0x1A, 0x1B, 0x1E, 0x1F,
        }

        for sid in expected_operations:
            with self.subTest(special_id=hex(sid)):
                device, editor = self._make_control_editor(sid)
                events_before = list(device.events)

                expected = {
                    Cmd.OPERATION: expected_operations[sid],
                    Cmd.ON_OFF_CONFIG: (
                        config_4673
                        if sid == 0x0236
                        else {0x16, 0x17, 0x1E, 0x1F}
                    ),
                }

                for cmd, values in expected.items():
                    item = editor._rows[cmd]
                    field = item["field"]

                    self.assertEqual(
                        field.winfo_class(), "TCombobox"
                    )
                    self.assertTrue(
                        field.instate(["readonly"])
                    )
                    self.assertFalse(
                        field.instate(["disabled"])
                    )
                    self.assertEqual(
                        set(item["options"].values()), values
                    )
                    self.assertEqual(
                        len(item["options"]), len(values)
                    )
                    self.assertEqual(
                        tuple(field["values"]),
                        tuple(item["options"]),
                    )

                    name = item["row"].name
                    profile_options = device._metadata[
                        "control_options"
                    ][name]
                    expected_labels = tuple(
                        f"0x{raw:02X}  {description}"
                        for raw, description in profile_options
                    )
                    self.assertEqual(
                        tuple(field["values"]),
                        expected_labels,
                    )

                self.assertEqual(
                    device.events, events_before
                )

        self.factory.assert_not_called()

    def test_nonpreset_operation_preserves_raw_and_has_no_edit(self):
        device, editor = self._make_control_editor(0x0236)
        cmd = Cmd.OPERATION
        item = editor._rows[cmd]
        events_before = list(device.events)

        self.assertNotIn(0x84, item["options"].values())
        self.assertEqual(
            device.validate_register_raw(cmd, 0x84, "byte"),
            0x84,
        )

        with (
            patch.object(device, "read_register") as read,
            patch.object(device, "write_register") as write,
            patch.object(device, "write_val") as write_val,
        ):
            # Exercise display handling of an already-read value.
            editor._show(cmd, 0x84)

            self.assertEqual(
                item["variable"].get(),
                "0x84  Not in preset list",
            )
            self.assertEqual(
                item["baseline"],
                "0x84  Not in preset list",
            )
            self.assertEqual(item["baseline_raw"], 0x84)
            self.assertEqual(editor.get_pending_changes(), [])

            prepared, errors = editor.prepare_pending_changes()
            self.assertEqual(prepared, [])
            self.assertEqual(errors, [])

            read.assert_not_called()
            write.assert_not_called()
            write_val.assert_not_called()

        self.assertEqual(device.events, events_before)
        self.confirm.assert_not_called()
        self.error.assert_not_called()
        self.factory.assert_not_called()

    def test_4673_ignore_fault_modes_warn_before_cancel(self):
        device, editor = self._make_control_editor(0x0236)
        cmd = Cmd.OPERATION
        item = editor._rows[cmd]

        for raw in (0x94, 0xA4, 0x54, 0x64):
            with self.subTest(raw=hex(raw)):
                editor._show(cmd, 0x80)
                baseline = item["baseline"]
                selected = next(
                    text
                    for text, value in item["options"].items()
                    if value == raw
                )
                item["variable"].set(selected)
                events_before = list(device.events)

                self.confirm.reset_mock()
                observed_busy = []

                def cancel(*args, **kwargs):
                    observed_busy.append(editor._busy)
                    return False

                self.confirm.side_effect = cancel

                with (
                    patch.object(
                        device, "read_register"
                    ) as read,
                    patch.object(
                        device, "write_register"
                    ) as write,
                    patch.object(
                        device, "write_val"
                    ) as write_val,
                ):
                    editor._run_write(cmd)

                    read.assert_not_called()
                    write.assert_not_called()
                    write_val.assert_not_called()

                self.confirm.assert_called_once()
                message = self.confirm.call_args[0][1]

                self.assertIn("LTM4673", message)
                self.assertIn(selected, message)
                self.assertIn(
                    "The effect depends on ON_OFF_CONFIG",
                    message,
                )
                self.assertIn(
                    "WARNING: This mode ignores faults "
                    "and warnings as described in the datasheet.",
                    message,
                )

                self.assertEqual(observed_busy, [True])
                self.assertFalse(editor._busy)
                self.assertEqual(item["baseline"], baseline)
                self.assertEqual(item["baseline_raw"], 0x80)
                self.assertEqual(
                    item["variable"].get(), selected
                )
                self.assertEqual(
                    device.events, events_before
                )

        self.error.assert_not_called()
        self.warning.assert_not_called()
        self.info.assert_not_called()
        self.factory.assert_not_called()

    def test_tab_busy_blocks_individual_read_and_write(self):
        self.device.read_register = Mock()
        self.device.write_register = Mock()
        self.device.write_val = Mock()
        self.tab._action_busy = True

        try:
            for editor in (self.editor, self.other):
                editor._run_read(self.cmd)
                editor._run_write(self.cmd)
                self.assertFalse(editor._busy)

            self.assertTrue(self.tab._action_busy)
            self.device.read_register.assert_not_called()
            self.device.write_register.assert_not_called()
            self.device.write_val.assert_not_called()
            self.confirm.assert_not_called()
            self.error.assert_not_called()
        finally:
            self.tab._action_busy = False

        self.factory.assert_not_called()

    def test_config_confirmation_blocks_app_lifecycle(self):
        events_before = list(self.device.events)

        app = SimpleNamespace(
            _busy=False,
            _closing=False,
            tabs=[self.tab],
            _status=Mock(),
            _stop_monitoring=Mock(),
            _clear_tabs=Mock(),
            _refresh_buses_silent=Mock(),
            _get_bus_num=Mock(return_value=100),
            _selected_bus_label=Mock(return_value="Test bus"),
            addr_var=Mock(),
            nb=Mock(),
            destroy=Mock(),
        )
        app._operation_in_progress = (
            lambda: App._operation_in_progress(app)
        )

        self.device.write_register = Mock()
        self.device.write_val = Mock()
        self.device.read_register = Mock()

        observed = []

        def cancel_after_lifecycle_attempts(*args, **kwargs):
            observed.append(
                (
                    self.editor._busy,
                    self.tab._action_busy,
                    app._operation_in_progress(),
                )
            )

            App._refresh_buses(app)
            App.scan(app)
            App.connect_manual(app)
            App._quit(app)

            return False

        self.confirm.side_effect = cancel_after_lifecycle_attempts

        with (
            patch("gui.app.close_all_buses") as close_buses,
            patch("gui.app.find_buses") as find_buses,
            patch("gui.app.scan_bus") as scan_bus,
            patch("gui.app.PMBusDevice") as device_class,
        ):
            self.editor._run_write(self.cmd)

            self.confirm.assert_called_once()
            self.assertEqual(
                observed, [(True, False, True)]
            )

            self.assertFalse(app._closing)
            self.assertFalse(self.editor._busy)
            self.assertFalse(self.tab._action_busy)
            self.assertFalse(app._operation_in_progress())
            self.assertTrue(self.tab.winfo_exists())

            app._stop_monitoring.assert_not_called()
            app._clear_tabs.assert_not_called()
            app._refresh_buses_silent.assert_not_called()
            app._get_bus_num.assert_not_called()
            app.addr_var.get.assert_not_called()
            app.nb.forget.assert_not_called()
            app.destroy.assert_not_called()

            close_buses.assert_not_called()
            find_buses.assert_not_called()
            scan_bus.assert_not_called()
            device_class.assert_not_called()

            app._status.assert_called_once()
            self.assertIn(
                "Operation in progress",
                app._status.call_args[0][0],
            )

            # Cancellation releases the guard. A repeated Quit
            # now reaches the mocked cleanup operations.
            App._quit(app)

            self.assertTrue(app._closing)
            app._stop_monitoring.assert_called_once_with()
            close_buses.assert_called_once_with()
            app.destroy.assert_called_once_with()

        self.device.write_register.assert_not_called()
        self.device.write_val.assert_not_called()
        self.device.read_register.assert_not_called()
        self.assertEqual(self.device.events, events_before)

        self.error.assert_not_called()
        self.warning.assert_not_called()
        self.info.assert_not_called()
        self.factory.assert_not_called()

    def test_disconnect_during_config_confirmation_prevents_write(self):
        events_before = list(self.device.events)

        self.device.read_register = Mock()
        self.device.write_register = Mock()
        self.device.write_val = Mock()

        self.tab.monitoring = True
        self.tab.mon_btn.configure(text="Stop Monitor")
        monitor_token = self.tab.after(
            60_000, self.tab._mon_loop
        )
        self.tab._monitor_after_id = monitor_token

        def confirm_after_disconnect(*args, **kwargs):
            self.assertTrue(self.editor._busy)
            self.assertFalse(self.tab._action_busy)

            # Model a disconnect detected while the dialog is open.
            self.tab._handle_disconnect(
                TransportDisconnectedError(
                    "Injected disconnect during Config confirmation"
                )
            )
            return True

        self.confirm.side_effect = confirm_after_disconnect

        self.editor._run_write(self.cmd)

        self.confirm.assert_called_once()
        self.device.write_register.assert_not_called()
        self.device.write_val.assert_not_called()
        self.device.read_register.assert_not_called()

        self.assertFalse(self.editor._busy)
        self.assertFalse(self.tab._action_busy)
        self.assertTrue(self.tab._disconnected)
        self.assertFalse(self.tab.monitoring)
        self.assertIsNone(self.tab._monitor_after_id)
        self.assertEqual(self.device.events, events_before)

        pending = set(
            self.root.tk.splitlist(
                self.root.tk.call("after", "info")
            )
        )
        self.assertNotIn(monitor_token, pending)

        for editor in self.tab._config_editors():
            self.assertTrue(editor._stale)
            self.assertFalse(editor._busy)
            self.assertEqual(editor._feedback_after_ids, {})

            for row in editor._rows.values():
                self.assertEqual(
                    row["variable"].get(), "STALE"
                )
                self.assertIsNone(row["baseline"])
                self.assertIsNone(row["baseline_raw"])
                self.assertTrue(
                    row["field"].instate(["disabled"])
                )

                for name in ("read_button", "write_button"):
                    button = row.get(name)
                    if button is not None:
                        self.assertFalse(
                            button.bind("<Button-1>")
                        )

        self.error.assert_called_once()
        title, message = self.error.call_args[0]
        self.assertEqual(title, "Adapter disconnected")
        self.assertIn(
            "Injected disconnect during Config confirmation",
            message,
        )

        # Released busy flags must not bypass the stale state.
        self.editor._run_write(self.cmd)
        self.editor._run_read(self.cmd)
        self.other._run_write(self.cmd)
        self.other._run_read(self.cmd)

        callback = Mock()
        self.tab._run_guarded_action(callback)

        callback.assert_not_called()
        self.confirm.assert_called_once()
        self.error.assert_called_once()
        self.device.write_register.assert_not_called()
        self.device.write_val.assert_not_called()
        self.device.read_register.assert_not_called()

        self.info.assert_not_called()
        self.warning.assert_not_called()
        self.factory.assert_not_called()

    def test_config_confirmation_blocks_tab_and_other_editor(self):
        events_before = list(self.device.events)

        self.device.store_user_all = Mock()
        self.device.read_register = Mock()
        self.device.write_register = Mock()
        self.device.write_val = Mock()
        self.tab._show_changes_preview = Mock()
        self.tab._mon_loop = Mock()

        def cancel_after_attempts(*args, **kwargs):
            self.assertTrue(self.editor._busy)
            self.assertFalse(self.tab._action_busy)

            self.tab.do_store()
            self.tab.do_write_all()
            self.tab.toggle_monitor()

            self.other._run_read(self.cmd)
            self.other._run_write(self.cmd)

            self.assertFalse(self.tab._action_busy)
            self.assertFalse(self.other._busy)
            self.assertFalse(self.tab.monitoring)
            return False

        self.confirm.side_effect = cancel_after_attempts

        self.editor._run_write(self.cmd)

        self.confirm.assert_called_once()
        self.assertFalse(self.editor._busy)
        self.assertFalse(self.other._busy)
        self.assertFalse(self.tab._action_busy)

        self.device.store_user_all.assert_not_called()
        self.device.read_register.assert_not_called()
        self.device.write_register.assert_not_called()
        self.device.write_val.assert_not_called()
        self.tab._show_changes_preview.assert_not_called()
        self.tab._mon_loop.assert_not_called()

        self.assertEqual(self.device.events, events_before)
        self.error.assert_not_called()
        self.warning.assert_not_called()
        self.info.assert_not_called()
        self.factory.assert_not_called()

        # Cancellation must release the guard for the tab.
        callback = Mock(return_value="released")
        result = self.tab._run_guarded_action(callback)
        self.assertEqual(result, "released")
        callback.assert_called_once_with()
        self.assertFalse(self.tab._action_busy)

    def test_read_error_releases_editor_guard(self):
        self.device.read_register = Mock(
            side_effect=OSError("Injected Config read error")
        )

        self.editor._run_read(self.cmd)

        self.device.read_register.assert_called_once_with(
            self.editor.page, self.cmd
        )
        self.assertFalse(self.editor._busy)
        self.assertFalse(self.tab._action_busy)
        self.assertFalse(self.tab._disconnected)

        self.error.assert_called_once()
        self.assertIn(
            "Injected Config read error",
            self.error.call_args[0][1],
        )

        callback = Mock()
        self.tab._run_guarded_action(callback)
        callback.assert_called_once_with()

        # Another editor can read after the failed operation.
        raw = self.device.values[
            (self.other.page, self.cmd)
        ]
        self.device.read_register = Mock(return_value=raw)

        self.other._run_read(self.cmd)

        self.device.read_register.assert_called_once_with(
            self.other.page, self.cmd
        )
        self.assertFalse(self.other._busy)
        self.assertEqual(
            self.other._rows[self.cmd]["baseline_raw"],
            raw,
        )
        self.factory.assert_not_called()

    def _clear_editor_pending_input(self):
        for item in self.editor._rows.values():
            baseline = item["baseline"]
            item["variable"].set(
                baseline if baseline is not None else "---"
            )

    def test_unknown_control_text_blocks_preview_and_write(self):
        self._clear_editor_pending_input()
        cmd = Cmd.OPERATION
        item = self.editor._rows[cmd]

        self.assertTrue(item["options"])
        self.editor._show(cmd, 0x80)

        # A readonly Combobox still permits programmatic changes
        # to its StringVar. The handler must reject this text.
        item["variable"].set("0xFF  Injected unsupported value")
        events_before = list(self.device.events)

        with (
            patch.object(
                self.device, "read_register"
            ) as read,
            patch.object(
                self.device, "write_register"
            ) as write,
            patch.object(
                self.device, "write_val"
            ) as write_val,
        ):
            prepared, errors = (
                self.editor.prepare_pending_changes()
            )

            self.assertEqual(prepared, [])
            self.assertEqual(len(errors), 1)
            self.assertIn(
                "Select a supported control value",
                errors[0],
            )

            self.editor._run_write(cmd)

            read.assert_not_called()
            write.assert_not_called()
            write_val.assert_not_called()

        self.confirm.assert_not_called()
        self.error.assert_called_once()
        self.assertIn(
            "Select a supported control value",
            self.error.call_args[0][1],
        )
        self.assertFalse(self.editor._busy)
        self.assertEqual(item["baseline_raw"], 0x80)
        self.assertEqual(self.device.events, events_before)
        self.factory.assert_not_called()

    def test_profile_rejection_blocks_preview_and_confirmation(self):
        self._clear_editor_pending_input()
        cmd = Cmd.OPERATION
        item = self.editor._rows[cmd]

        self.editor._show(cmd, 0x80)
        selected = next(
            text
            for text, raw in item["options"].items()
            if raw == 0x98
        )
        item["variable"].set(selected)
        events_before = list(self.device.events)

        with (
            patch.object(
                self.device,
                "validate_register_raw",
                side_effect=ValueError(
                    "Injected profile rejection"
                ),
                create=True,
            ) as validator,
            patch.object(
                self.device, "read_register"
            ) as read,
            patch.object(
                self.device, "write_register"
            ) as write,
            patch.object(
                self.device, "write_val"
            ) as write_val,
        ):
            prepared, errors = (
                self.editor.prepare_pending_changes()
            )

            self.assertEqual(prepared, [])
            self.assertEqual(len(errors), 1)
            self.assertIn(
                "Injected profile rejection", errors[0]
            )
            validator.assert_called_once_with(
                cmd, 0x98, item["row"].size
            )

            validator.reset_mock()

            self.editor._run_write(cmd)

            validator.assert_called_once_with(
                cmd, 0x98, item["row"].size
            )
            read.assert_not_called()
            write.assert_not_called()
            write_val.assert_not_called()

        self.confirm.assert_not_called()
        self.error.assert_called_once()
        self.assertIn(
            "Injected profile rejection",
            self.error.call_args[0][1],
        )
        self.assertFalse(self.editor._busy)
        self.assertEqual(item["baseline_raw"], 0x80)
        self.assertEqual(item["variable"].get(), selected)
        self.assertEqual(self.device.events, events_before)
        self.factory.assert_not_called()

    def test_control_confirmation_cancel_preserves_input(self):
        self._clear_editor_pending_input()
        cmd = Cmd.OPERATION
        item = self.editor._rows[cmd]

        self.editor._show(cmd, 0x80)
        baseline = item["baseline"]
        selected = next(
            text
            for text, raw in item["options"].items()
            if raw == 0x98
        )
        item["variable"].set(selected)
        events_before = list(self.device.events)

        observed_busy = []

        def cancel(*args, **kwargs):
            observed_busy.append(self.editor._busy)
            return False

        self.confirm.side_effect = cancel

        with (
            patch.object(
                self.device, "read_register"
            ) as read,
            patch.object(
                self.device, "write_register"
            ) as write,
            patch.object(
                self.device, "write_val"
            ) as write_val,
        ):
            self.editor._run_write(cmd)

            read.assert_not_called()
            write.assert_not_called()
            write_val.assert_not_called()

        self.confirm.assert_called_once()
        message = self.confirm.call_args[0][1]
        self.assertIn(selected, message)
        self.assertIn(
            "The effect depends on ON_OFF_CONFIG",
            message,
        )
        self.assertIn(
            "This selection does not indicate output state.",
            message,
        )

        self.assertEqual(observed_busy, [True])
        self.assertFalse(self.editor._busy)
        self.assertEqual(item["baseline"], baseline)
        self.assertEqual(item["baseline_raw"], 0x80)
        self.assertEqual(item["variable"].get(), selected)
        self.assertEqual(self.device.events, events_before)

        self.error.assert_not_called()
        self.warning.assert_not_called()
        self.info.assert_not_called()
        self.factory.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
