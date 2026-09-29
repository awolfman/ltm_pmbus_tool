"""Offline export, parse and profile-validation integration tests."""

import copy
import unittest
from unittest.mock import patch

from core.dump_csv import dump_to_csv_string, csv_string_to_dump
from core.pmbus_constants import (
    Cmd,
    build_device_metadata,
)
from core.pmbus_device import PMBusDevice
from test_vout_write_plan import MemoryDevice

class DumpCsvRoundtripTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "core.pmbus_device.create_bus",
            side_effect=AssertionError(
                "Hardware bus creation is forbidden"
            ),
        )
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def test_loaded_records_can_be_reexported_without_decoded_field(self):
        device = MemoryDevice(special_id=0x4101)
        original_records = self.snapshot(device, 0)

        first_csv = dump_to_csv_string(
            device, original_records, 0
        )
        source_meta, imported = csv_string_to_dump(first_csv)

        self.assertTrue(imported)
        self.assertTrue(
            all("decoded" not in record for record in imported)
        )

        original_imported = copy.deepcopy(imported)
        original_meta = copy.deepcopy(source_meta)
        events_before = list(device.events)

        with patch.object(
            device,
            "_get_bus",
            side_effect=AssertionError("Unexpected bus access"),
        ):
            second_csv = dump_to_csv_string(
                device,
                imported,
                0,
                metadata=source_meta,
            )
            restored_meta, restored_records = csv_string_to_dump(
                second_csv
            )

        self.assertEqual(restored_meta, original_meta)
        self.assertEqual(restored_records, original_imported)
        self.assertEqual(imported, original_imported)
        self.assertEqual(source_meta, original_meta)
        self.assertEqual(device.events, events_before)

        # Parsed records preserve RAW, not a validated engineering
        # interpretation. Reexport marks Decoded as unavailable.
        import csv
        from io import StringIO

        table = "\n".join(
            line
            for line in second_csv.splitlines()
            if not line.startswith("#")
        )
        rows = list(csv.DictReader(StringIO(table)))

        self.assertEqual(len(rows), len(imported))
        self.assertTrue(
            all(row["Decoded"] == "N/A" for row in rows)
        )
        self.factory.assert_not_called()

    def test_core_rejects_control_values_before_page_or_transport(self):
        cases = (
            (0x0236, Cmd.OPERATION, 0xC0, "Reserved"),
            (0x0236, Cmd.OPERATION, 0x3C, "requires checking"),
            (0x0236, Cmd.ON_OFF_CONFIG, 0x00,
             "Application policy"),
            (0x47B8, Cmd.OPERATION, 0x94, "Unsupported"),
            (0x47B8, Cmd.ON_OFF_CONFIG, 0x12, "Unsupported"),
            (0x4101, Cmd.OPERATION, 0x94, "Unsupported"),
            (0x4101, Cmd.ON_OFF_CONFIG, 0x12, "Unsupported"),
        )

        for sid, cmd, raw, error_text in cases:
            for entry_point in (
                "write_register",
                "write_byte_data",
            ):
                with self.subTest(
                    special_id=hex(sid),
                    cmd=hex(cmd),
                    raw=hex(raw),
                    entry_point=entry_point,
                ):
                    device = MemoryDevice(special_id=sid)
                    device._page = 0
                    events_before = list(device.events)

                    with (
                        patch.object(
                            device, "set_page",
                            return_value=True,
                        ) as select_page,
                        patch.object(
                            device, "_wait_ready",
                            return_value=True,
                        ) as ready,
                        patch.object(
                            device, "_write_transport",
                            return_value=True,
                        ) as transport,
                        patch.object(
                            device, "_get_bus",
                            side_effect=AssertionError(
                                "Unexpected bus access"
                            ),
                        ) as get_bus,
                        patch.object(
                            device,
                            "validate_register_raw",
                            wraps=lambda *args:
                                PMBusDevice.validate_register_raw(
                                    device, *args
                                ),
                        ) as validator,
                        patch.object(
                            device,
                            "_write_checked",
                            wraps=lambda *args:
                                PMBusDevice._write_checked(
                                    device, *args
                                ),
                        ),
                        self.assertLogs(
                            "core.pmbus_device",
                            level="WARNING",
                        ) as logs,
                    ):
                        # Call the production entry point explicitly,
                        # bypassing possible MemoryDevice overrides.
                        if entry_point == "write_register":
                            result = PMBusDevice.write_register(
                                device, 0, cmd, raw, "byte"
                            )
                        else:
                            result = PMBusDevice.write_byte_data(
                                device, cmd, raw
                            )

                        self.assertIs(result, False)
                        validator.assert_called_once_with(
                            cmd, raw, "byte"
                        )
                        select_page.assert_not_called()
                        ready.assert_not_called()
                        transport.assert_not_called()
                        get_bus.assert_not_called()

                    self.assertIn(
                        error_text, device.last_error
                    )
                    self.assertTrue(
                        any(
                            error_text in line
                            for line in logs.output
                        )
                    )
                    self.assertEqual(
                        device.events, events_before
                    )

        self.factory.assert_not_called()

    def test_reexport_preserves_imported_metadata(self):
        device = MemoryDevice(special_id=0x4101)
        records = self.snapshot(device, 0)
        source_metadata = {
            "device": "LTM4673",
            "special_id": "0x0236",
            "address": "0x40",
            "vout_mode_exp": "-13",
            "date": "source date",
        }
        original = copy.deepcopy(source_metadata)

        text = dump_to_csv_string(
            device,
            records,
            0,
            metadata=source_metadata,
        )
        restored, parsed = csv_string_to_dump(text)

        self.assertEqual(restored, original)
        self.assertEqual(source_metadata, original)
        self.assertEqual(len(parsed), len(records))
        self.factory.assert_not_called()

    def test_reexport_does_not_fill_missing_import_metadata(self):
        device = MemoryDevice(special_id=0x4101)

        text = dump_to_csv_string(
            device,
            self.snapshot(device, 0),
            0,
            metadata={},
        )
        restored, parsed = csv_string_to_dump(text)

        self.assertEqual(restored, {})
        self.assertTrue(parsed)
        self.factory.assert_not_called()

    def snapshot(self, device, page):
        records = []

        # Explicit fixture values supported by all tested models.
        # These are test data, not hardware write recommendations.
        control_raw = {
            Cmd.OPERATION: 0x80,
            Cmd.ON_OFF_CONFIG: 0x1E,
        }

        for cmd, info in sorted(device._regmap.items()):
            if not device.can_read_register(cmd):
                continue

            name, size, fmt, paged = info
            raw = control_raw.get(cmd, 0)

            records.append({
                "page": page,
                "cmd": cmd,
                "name": name,
                "size": size,
                "format": fmt,
                "is_paged": bool(paged),
                "raw": raw,
                "decoded": raw if cmd in control_raw else 0,
                "readonly": not device.can_write_register(
                    cmd, size
                ),
            })

        return records

    def test_exported_snapshots_parse_and_validate_for_all_profiles(self):
        for special_id in (0x0236, 0x47B8, 0x4101):
            device = MemoryDevice(special_id=special_id)

            for page in range(device.num_pages):
                with self.subTest(
                    special_id=hex(special_id),
                    page=page,
                ):
                    records = self.snapshot(device, page)
                    original = copy.deepcopy(records)
                    events_before = list(device.events)

                    self.assertTrue(records)

                    with patch.object(
                        device,
                        "_get_bus",
                        side_effect=AssertionError(
                            "Unexpected bus access"
                        ),
                    ):
                        text = dump_to_csv_string(
                            device, records, page
                        )
                        meta, parsed = csv_string_to_dump(text)

                        selected = [
                            record
                            for record in parsed
                            if (
                                not record["readonly"]
                                and record["raw"] is not None
                            )
                        ]
                        self.assertTrue(selected)

                        validated = device.validate_dump_write_records(
                            selected,
                            default_page=page,
                        )

                    self.assertEqual(records, original)
                    self.assertEqual(len(parsed), len(records))
                    self.assertEqual(validated, selected)
                    self.assertEqual(device.events, events_before)

                    self.assertEqual(meta["device"], device.name)
                    self.assertEqual(
                        int(meta["special_id"], 16),
                        device.special_id,
                    )
                    self.assertEqual(int(meta["page"]), page)
                    self.assertEqual(
                        int(meta["vout_mode_exp"]),
                        device.vout_exp[page],
                    )

                    for source, restored in zip(records, parsed):
                        for field in (
                            "page", "cmd", "name", "size",
                            "format", "is_paged", "raw", "readonly",
                        ):
                            self.assertEqual(
                                restored[field], source[field]
                            )

        self.factory.assert_not_called()

    def test_exported_missing_raw_remains_missing_after_parse(self):
        device = MemoryDevice(special_id=0x4101)
        records = self.snapshot(device, 0)

        target = next(
            record
            for record in records
            if record["cmd"] == Cmd.VOUT_COMMAND
        )
        target["raw"] = None
        target["decoded"] = None

        text = dump_to_csv_string(device, records, 0)
        _, parsed = csv_string_to_dump(text)

        restored = next(
            record
            for record in parsed
            if record["cmd"] == Cmd.VOUT_COMMAND
        )

        self.assertIsNone(restored["raw"])
        self.assertFalse(restored["readonly"])

        selected = [
            record
            for record in parsed
            if (
                not record["readonly"]
                and record["raw"] is not None
            )
        ]
        self.assertNotIn(
            Cmd.VOUT_COMMAND,
            [record["cmd"] for record in selected],
        )
        self.factory.assert_not_called()

    def test_wrong_scope_survives_parse_and_is_rejected_by_profile(self):
        device = MemoryDevice(special_id=0x4101)
        record = next(
            record
            for record in self.snapshot(device, 0)
            if record["cmd"] == Cmd.VOUT_COMMAND
        )

        # Syntactically valid CSV, but incompatible with the profile.
        record["is_paged"] = False

        text = dump_to_csv_string(device, [record], 0)
        _, parsed = csv_string_to_dump(text)

        self.assertFalse(parsed[0]["is_paged"])

        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            device.validate_dump_write_records(parsed)

        self.factory.assert_not_called()

    def test_control_presets_match_profiles_and_validate(self):
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

        expected_configs_4673 = {
            0x02, 0x03, 0x06, 0x07,
            0x0A, 0x0B, 0x0E, 0x0F,
            0x12, 0x13, 0x16, 0x17,
            0x1A, 0x1B, 0x1E, 0x1F,
        }

        for sid in expected_operations:
            with self.subTest(special_id=hex(sid)):
                device = MemoryDevice(special_id=sid)
                metadata = build_device_metadata(sid)
                options = metadata["control_options"]
                events_before = list(device.events)

                expected = {
                    "OPERATION": expected_operations[sid],
                    "ON_OFF_CONFIG": (
                        expected_configs_4673
                        if sid == 0x0236
                        else {0x16, 0x17, 0x1E, 0x1F}
                    ),
                }
                commands = {
                    "OPERATION": Cmd.OPERATION,
                    "ON_OFF_CONFIG": Cmd.ON_OFF_CONFIG,
                }

                with patch.object(
                    device,
                    "_get_bus",
                    side_effect=AssertionError(
                        "Unexpected bus access"
                    ),
                ):
                    for name, expected_values in expected.items():
                        presets = options[name]
                        values = [
                            raw for raw, label in presets
                        ]

                        self.assertEqual(
                            set(values), expected_values
                        )
                        self.assertEqual(
                            len(values), len(set(values))
                        )

                        for raw, label in presets:
                            self.assertIsInstance(label, str)
                            self.assertTrue(label.strip())
                            self.assertEqual(
                                device.validate_register_raw(
                                    commands[name],
                                    raw,
                                    "byte",
                                ),
                                raw,
                            )

                self.assertEqual(
                    device.events, events_before
                )

        self.factory.assert_not_called()

    def test_invalid_control_raw_survives_csv_and_blocks_write(self):
        cases = (
            (0x0236, Cmd.ON_OFF_CONFIG, 0x00,
             "Application policy"),
            (0x0236, Cmd.OPERATION, 0xC0,
             "Reserved OPERATION"),
            (0x0236, Cmd.OPERATION, 0x3C,
             "requires checking"),
            (0x47B8, Cmd.ON_OFF_CONFIG, 0x00,
             "Unsupported ON_OFF_CONFIG"),
            (0x47B8, Cmd.OPERATION, 0x94,
             "Unsupported OPERATION"),
            (0x4101, Cmd.ON_OFF_CONFIG, 0x00,
             "Unsupported ON_OFF_CONFIG"),
            (0x4101, Cmd.OPERATION, 0x94,
             "Unsupported OPERATION"),
        )

        for sid, cmd, raw, error_text in cases:
            with self.subTest(
                special_id=hex(sid),
                cmd=hex(cmd),
                raw=hex(raw),
            ):
                device = MemoryDevice(special_id=sid)
                record = next(
                    item
                    for item in self.snapshot(device, 0)
                    if item["cmd"] == cmd
                )
                record["raw"] = raw
                record["decoded"] = raw

                original = copy.deepcopy(record)
                events_before = list(device.events)

                with patch.object(
                    device,
                    "_get_bus",
                    side_effect=AssertionError(
                        "Unexpected bus access"
                    ),
                ):
                    text = dump_to_csv_string(
                        device, [record], 0
                    )
                    _, parsed = csv_string_to_dump(text)

                    self.assertEqual(len(parsed), 1)
                    self.assertEqual(parsed[0]["raw"], raw)
                    self.assertFalse(parsed[0]["readonly"])
                    parsed_before = copy.deepcopy(parsed)

                    with self.assertRaisesRegex(
                        ValueError, error_text
                    ):
                        device.validate_dump_write_records(
                            parsed,
                            default_page=0,
                        )

                    self.assertEqual(
                        parsed, parsed_before
                    )

                self.assertEqual(record, original)
                self.assertEqual(
                    device.events, events_before
                )

        self.factory.assert_not_called()

if __name__ == "__main__":
    unittest.main(verbosity=2)
