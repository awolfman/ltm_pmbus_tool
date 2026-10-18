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

        if dec is None:
            ds = "N/A"
        elif r["format"] in ("L11", "L16"):
            ds = f"{dec:.6f}"
        elif isinstance(dec, float):
            ds = repr(dec)
        else:
            ds = str(dec)
        w.writerow([r['page'], f"0x{r['cmd']:02X}", r['cmd'], r['name'],
                     r['size'], r['format'], 'Y' if r.get('is_paged') else 'N',
                     rh, rd, ds, 'YES' if r['readonly'] else 'NO'])
    return add_dump_checksum(out.getvalue())


def _page_csv_string_to_dump(csv_text):
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

_FULL_HEADER = [
    "Page", "CmdHex", "CmdDec", "Name", "Size", "Format",
    "Paged", "RawHex", "RawDec", "Decoded", "ReadOnly",
]


def _dump_metadata_from_body(body):
    """Read normalized metadata without accepting duplicate fields."""
    metadata = {}

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#") or ":" not in stripped:
            continue

        key, value = stripped.lstrip("#").strip().split(":", 1)
        key = key.strip().lower().replace(" ", "_")

        if not key:
            raise ValueError("Empty CSV metadata key")
        if key in metadata:
            raise ValueError(
                f"Duplicate CSV metadata field: {key}"
            )

        metadata[key] = value.strip()

    return metadata


def _validate_full_dump_metadata(metadata):
    if metadata.get("dump_format_version") != "2":
        raise ValueError("Unsupported full dump format version")

    if metadata.get("dump_scope") != "full":
        raise ValueError("Full dump scope must be full")

    if not metadata.get("device"):
        raise ValueError("Full dump device model is missing")

    sid = metadata.get("special_id", "")
    if not re.fullmatch(r"0[xX][0-9a-fA-F]{1,4}", sid):
        raise ValueError("Invalid full dump Special ID")

    pages_text = metadata.get("pages", "")
    if not re.fullmatch(r"[0-9]+", pages_text):
        raise ValueError("Invalid full dump page count")

    pages = int(pages_text)
    if not 1 <= pages <= 255:
        raise ValueError("Full dump page count is outside 1..255")

    for page in range(pages):
        key = f"vout_mode_exp_page_{page}"
        value = metadata.get(key, "")
        if not re.fullmatch(r"-?[0-9]+", value):
            raise ValueError(
                f"Missing or invalid exponent for page {page}"
            )

        exponent = int(value)
        if not -16 <= exponent <= 15:
            raise ValueError(
                f"Exponent outside signed five-bit range "
                f"for page {page}"
            )

    if "page" in metadata or "vout_mode_exp" in metadata:
        raise ValueError(
            "Full dump contains conflicting page-dump metadata"
        )

    prefix = "vout_mode_exp_page_"
    expected = {
        f"{prefix}{page}" for page in range(pages)
    }
    supplied = {
        key for key in metadata if key.startswith(prefix)
    }
    if supplied != expected:
        raise ValueError(
            "Full dump exponent fields do not match page count"
        )

    return pages


def _parse_full_dump_body(body, metadata):
    """Parse v2 using the existing scalar-field parser."""
    pages = _validate_full_dump_metadata(metadata)

    table_lines = [
        line
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not table_lines:
        raise ValueError("CSV header is missing")

    try:
        rows = list(csv.reader(table_lines, strict=True))
    except csv.Error as exc:
        raise ValueError(f"Invalid full dump CSV: {exc}") from exc

    if [cell.strip() for cell in rows[0]] != _FULL_HEADER:
        raise ValueError("CSV header does not match the dump format")

    translated = []
    scopes = []
    seen = set()
    seen_pages = set()

    for index, source in enumerate(rows[1:], start=1):
        if len(source) != len(_FULL_HEADER):
            raise ValueError(
                f"Full dump record {index}: "
                f"Expected 11 columns, received {len(source)}"
            )

        row = [cell.strip() for cell in source]
        page_text = row[0]
        paged_text = row[6]

        if paged_text == "Y":
            if not re.fullmatch(r"[0-9]+", page_text):
                raise ValueError(
                    f"Full dump record {index}: "
                    "paged register requires a numeric page"
                )
            page = int(page_text)
            if not 0 <= page < pages:
                raise ValueError(
                    f"Full dump record {index}: page out of range"
                )
            seen_pages.add(page)

        elif paged_text == "N":
            if page_text != "GLOBAL":
                raise ValueError(
                    f"Full dump record {index}: "
                    "global register requires GLOBAL"
                )
            page = None
            # The legacy parser requires a numeric page.
            row[0] = "0"

        else:
            raise ValueError(
                f"Full dump record {index}: Paged must be Y or N"
            )

        scopes.append(page)
        translated.append(row)

    # Reuse all existing scalar syntax and range checks.
    temporary = StringIO()
    writer = csv.writer(temporary)
    writer.writerow(_FULL_HEADER)
    writer.writerows(translated)

    _, records = _page_csv_string_to_dump(
        add_dump_checksum(temporary.getvalue())
    )

    for record, page in zip(records, scopes):
        record["page"] = page
        identity = (page, record["cmd"])

        if identity in seen:
            raise ValueError(
                "Duplicate full dump target: "
                f"page={page}, command=0x{record['cmd']:02X}"
            )
        seen.add(identity)

        if record["cmd"] == 0x00:
            raise ValueError(
                "PAGE is excluded from full dump snapshots"
            )

    if seen_pages != set(range(pages)):
        raise ValueError(
            "Full dump does not contain records for every page"
        )

    return dict(metadata), records


def csv_string_to_dump(csv_text):
    """Dispatch legacy page CSV and versioned full-device CSV."""
    body = verify_dump_checksum(csv_text)
    metadata = _dump_metadata_from_body(body)

    version = metadata.get("dump_format_version")
    scope = metadata.get("dump_scope")

    if version is None and scope is None:
        return _page_csv_string_to_dump(csv_text)

    if version != "2":
        raise ValueError("Unsupported dump format version")

    if scope != "full":
        raise ValueError("Unsupported dump scope")

    return _parse_full_dump_body(body, metadata)


def device_dump_to_csv_string(device, dump_data, *, metadata=None):
    """Export a full scalar snapshot without device I/O.

    Imported metadata is preserved, never filled from the target device.
    Structural validation does not establish profile compatibility
    or suitability for writing.
    """
    records = [dict(record) for record in dump_data]

    if metadata is None:
        metadata = {
            "dump_format_version": "2",
            "dump_scope": "full",
            "device": device.name,
            "address": f"0x{device.address:02X}",
            "special_id": f"0x{device.special_id:04X}",
            "revision": str(device.revision),
            "pages": str(device.num_pages),
            "date": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        }

        for page in range(device.num_pages):
            exponent = device.vout_exp.get(page)
            if (
                isinstance(exponent, bool)
                or not isinstance(exponent, int)
                or not -16 <= exponent <= 15
            ):
                raise ValueError(
                    f"VOUT exponent unavailable for page {page}"
                )
            metadata[f"vout_mode_exp_page_{page}"] = str(exponent)

    elif not isinstance(metadata, dict):
        raise ValueError("CSV metadata must be a dictionary")

    else:
        metadata = dict(metadata)

    export_records = []

    for record in records:
        scope = record.get("is_paged")
        page = record.get("page")

        if not isinstance(scope, bool):
            raise ValueError("Full dump scope must be boolean")

        if scope:
            if (
                isinstance(page, bool)
                or not isinstance(page, int)
                or page < 0
            ):
                raise ValueError(
                    "Paged full dump record requires a numeric page"
                )
        else:
            if page is not None:
                raise ValueError(
                    "Global full dump record requires page=None"
                )
            record["page"] = "GLOBAL"

        export_records.append(record)

    text = dump_to_csv_string(
        device,
        export_records,
        0,
        metadata=metadata,
    )

    # Reject malformed snapshots before returning export text.
    csv_string_to_dump(text)
    return text
