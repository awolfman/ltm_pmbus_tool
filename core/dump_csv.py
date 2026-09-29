"""CSV dump read/write for register snapshots."""

import os
import re
import csv
from io import StringIO
from datetime import datetime
import tempfile
import hashlib

def _normalize_csv_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _is_checksum_line(line):
    return bool(
        re.match(r"^\s*#\s*sha256\s*:", line, flags=re.IGNORECASE)
    )


def add_dump_checksum(body):
    """Append a SHA-256 footer to an unsigned dump."""
    body = _normalize_csv_newlines(body)

    if any(_is_checksum_line(line) for line in body.split("\n")):
        raise ValueError("Dump already contains a SHA256 footer")

    if not body.endswith("\n"):
        body += "\n"

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body + f"# SHA256: {digest}\n"


def verify_dump_checksum(text):
    """Verify the footer and return the normalized unsigned body."""
    text = _normalize_csv_newlines(text)
    lines = text.splitlines(keepends=True)

    positions = [
        index
        for index, line in enumerate(lines)
        if _is_checksum_line(line)
    ]

    if not positions:
        raise ValueError(
            "CSV checksum is missing. "
            "Only dumps with a SHA256 footer are accepted."
        )

    if len(positions) != 1:
        raise ValueError("CSV contains multiple SHA256 footers")

    index = positions[0]
    if index != len(lines) - 1:
        raise ValueError("SHA256 footer must be the last line")

    footer = lines[index].removesuffix("\n")
    match = re.fullmatch(
        r"# SHA256: ([0-9a-fA-F]{64})",
        footer,
    )
    if match is None:
        raise ValueError("Invalid SHA256 footer format")

    body = "".join(lines[:index])
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    expected = match.group(1).lower()

    if actual != expected:
        raise ValueError(
            "CSV checksum mismatch. "
            "The file was changed or damaged. "
            "The dump was not loaded."
        )

    return body

def dump_to_csv_string(device, dump_data, page, *, metadata=None):
    out = StringIO()

    if metadata is None:
        out.write(f"# Device: {device.name}\n")
        out.write(f"# Address: 0x{device.address:02X}\n")
        out.write(f"# Special ID: 0x{device.special_id:04X}\n")
        out.write(f"# Revision: {device.revision}\n")
        out.write(f"# Page: {page}\n")
        out.write(
            f"# VOUT_MODE exp: {device.vout_exp.get(page, -13)}\n"
        )
        out.write(
            f"# Date: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        )
    else:
        if not isinstance(metadata, dict):
            raise ValueError("CSV metadata must be a dictionary")

        # Preserve imported metadata, including missing fields.
        # Never replace source identity with the current device.
        for key, value in metadata.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("CSV metadata keys and values must be text")

            if (
                not key
                or any(character in key for character in "\r\n:")
                or any(character in value for character in "\r\n")
            ):
                raise ValueError("Invalid CSV metadata entry")

            out.write(f"# {key}: {value}\n")

    out.write("#\n")
    w = csv.writer(out)
    w.writerow(['Page','CmdHex','CmdDec','Name','Size','Format','Paged',
                'RawHex','RawDec','Decoded','ReadOnly'])
    for r in dump_data:
        raw = r['raw']
        if raw is not None:
            rh = f"0x{raw:02X}" if r['size']=='byte' else f"0x{raw:04X}"
            rd = str(raw)
        else:
            rh = rd = "N/A"
        dec = r.get("decoded")
        ds = f"{dec:.6f}" if dec is not None and r['format'] in ('L11','L16') else (str(int(dec)) if dec is not None else "N/A")
        w.writerow([r['page'], f"0x{r['cmd']:02X}", r['cmd'], r['name'],
                     r['size'], r['format'], 'Y' if r.get('is_paged') else 'N',
                     rh, rd, ds, 'YES' if r['readonly'] else 'NO'])
    return add_dump_checksum(out.getvalue())


def csv_string_to_dump(csv_text):
    """Parse an entire dump or reject it without returning partial data."""
    csv_text = verify_dump_checksum(csv_text)
    expected_header = [
        "Page", "CmdHex", "CmdDec", "Name", "Size", "Format",
        "Paged", "RawHex", "RawDec", "Decoded", "ReadOnly",
    ]
    missing = {"N/A", "", "None"}
    meta = {}
    data_lines = []
    source_lines = []

    for number, line in enumerate(csv_text.splitlines(), start=1):
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            if ":" in stripped:
                key, value = stripped.lstrip("#").strip().split(":", 1)
                key = key.strip().lower().replace(" ", "_")
                meta[key] = value.strip()
            continue

        data_lines.append(line)
        source_lines.append(number)

    if not data_lines:
        raise ValueError("CSV header is missing")

    reader = csv.reader(data_lines, strict=True)

    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValueError("Invalid CSV header") from exc

    if [cell.strip() for cell in header] != expected_header:
        raise ValueError("CSV header does not match the dump format")

    records = []

    def hexadecimal(text, field):
        if not text.lower().startswith("0x"):
            raise ValueError(f"{field} must start with 0x")
        return int(text, 16)

    try:
        for row in reader:
            line_number = source_lines[reader.line_num - 1]

            try:
                if len(row) != len(expected_header):
                    raise ValueError(
                        f"Expected 11 columns, received {len(row)}"
                    )

                (
                    page_text, cmd_hex, cmd_dec, name, size, fmt,
                    paged_text, raw_hex, raw_dec, decoded, readonly_text,
                ) = [cell.strip() for cell in row]

                page = int(page_text, 10)
                if page < 0:
                    raise ValueError("Page must be nonnegative")

                cmd = hexadecimal(cmd_hex, "CmdHex")
                if not 0 <= cmd <= 0xFF:
                    raise ValueError("Command is outside 0x00..0xFF")
                if int(cmd_dec, 10) != cmd:
                    raise ValueError("CmdHex and CmdDec disagree")

                if not name or not fmt:
                    raise ValueError("Name and Format must not be empty")

                if size not in {"byte", "word"}:
                    raise ValueError("Size must be byte or word")

                if paged_text not in {"Y", "N"}:
                    raise ValueError("Paged must be Y or N")

                if readonly_text not in {"YES", "NO"}:
                    raise ValueError("ReadOnly must be YES or NO")

                hex_missing = raw_hex in missing
                dec_missing = raw_dec in missing

                if hex_missing != dec_missing:
                    raise ValueError(
                        "RawHex and RawDec must both contain a value "
                        "or both indicate missing data"
                    )

                if hex_missing:
                    raw = None
                else:
                    raw = hexadecimal(raw_hex, "RawHex")
                    if int(raw_dec, 10) != raw:
                        raise ValueError("RawHex and RawDec disagree")

                    maximum = 0xFF if size == "byte" else 0xFFFF
                    if not 0 <= raw <= maximum:
                        raise ValueError("Raw value is outside the size range")

                # Decoded is informational, not an input to writes.
                # Do not treat it as a validated engineering value.
                records.append({
                    "page": page,
                    "cmd": cmd,
                    "name": name,
                    "size": size,
                    "format": fmt,
                    "is_paged": paged_text == "Y",
                    "raw": raw,
                    "readonly": readonly_text == "YES",
                })

            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"CSV line {line_number}: {exc}"
                ) from exc

    except csv.Error as exc:
        index = min(
            max(reader.line_num - 1, 0),
            len(source_lines) - 1,
        )
        raise ValueError(
            f"CSV line {source_lines[index]}: {exc}"
        ) from exc

    return meta, records

def save_csv_atomic(path, text):
    """Replace a CSV only after its temporary copy is fully written.

    The temporary file is created in the destination directory.
    This does not guarantee directory durability after power loss.
    """
    destination = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(destination)
    temporary = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=directory,
            prefix=".pmbus-csv-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

        # Close the temporary file before replacing the destination.
        os.replace(temporary, destination)
        temporary = None

    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
