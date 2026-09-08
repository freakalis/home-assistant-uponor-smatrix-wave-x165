"""Read-only candidate identification for the observed X-165 SD file layouts.

No checksum for the backup/header is known. Results always require RF validation.
"""
import argparse
import json
from pathlib import Path


class SDFormatError(ValueError):
    """Input does not match the investigated layout."""


def parse_backup(data: bytes) -> dict:
    if len(data) != 77 or data[76] != 1 or data[4:12] != bytes(8):
        raise SDFormatError("Unsupported backup layout (expected observed 77-byte layout)")
    display = data[:4].hex().upper()
    if display in ("00000000", "FFFFFFFF"):
        raise SDFormatError("Missing display candidate")
    slots = []
    for offset in range(12, 76, 4):
        value = data[offset:offset + 4].hex().upper()
        if value == "FFFFFFFF":
            raise SDFormatError("Unsupported all-ones slot")
        if value != "00000000":
            slots.append({"offset": offset, "device_id": value})
    ids = [slot["device_id"] for slot in slots]
    if len(set(ids)) != len(ids) or display in ids:
        raise SDFormatError("Ambiguous duplicate IDs in backup")
    return {"interface_id_candidate": display, "thermostat_candidates": slots,
            "warnings": ["Layout based on one sample; integrity checksum unknown.",
                         "Slot order is not a verified room/channel mapping.",
                         "This backup layout does not identify the controller."]}


def _nibbles(data: bytes) -> str:
    if not data or any(value < 0x30 or value > 0x3F for value in data):
        raise SDFormatError("Invalid SD nibble encoding")
    return "".join(format(value - 0x30, "X") for value in data)


def parse_log(data: bytes) -> dict:
    records = data.split(b"\r\n")
    if records and records[-1] == b"":
        records.pop()
    if not records or not records[0]:
        raise SDFormatError("Empty log")
    header = records[0]
    if len(header) != 24 or header[8:11] != b"00|":
        raise SDFormatError("Unsupported log header layout")
    _nibbles(header[:10])
    _nibbles(header[19:24])
    controller = _nibbles(header[11:19])
    if controller in ("00000000", "FFFFFFFF"):
        raise SDFormatError("Missing controller candidate")
    valid = 0
    invalid = []
    for number, record in enumerate(records[1:], 2):
        try:
            if len(record) < 11:
                raise SDFormatError("Short record")
            _nibbles(record[:10])
            if sum(record[:-1]) & 255 != record[-1]:
                raise SDFormatError("Checksum mismatch")
        except SDFormatError:
            invalid.append(number)
        else:
            valid += 1
    warnings = ["Controller is a header candidate; header integrity is unverified."]
    if invalid:
        warnings.append("Some body records failed validation; log may be damaged or unsupported.")
    return {"controller_id_candidate": controller, "valid_body_records": valid,
            "invalid_body_records": invalid, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args(argv)
    if args.backup is None and args.log is None:
        parser.error("Provide --backup and/or --log")
    result = {"schema_version": 1, "requires_rf_verification": True,
              "pair_association_verified": False}
    failed = False
    for name, path, parse in (("backup", args.backup, parse_backup),
                              ("log", args.log, parse_log)):
        if path is None:
            continue
        try:
            result[name] = parse(path.read_bytes())
        except (OSError, SDFormatError) as exc:
            result[name] = {"error": str(exc)}
            failed = True
    print(json.dumps(result, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
