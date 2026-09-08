"""Timestamp manual display observations in a separate JSONL file."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path


def finite_number(value):
    number = float(value.replace(",", "."))
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("Enter a finite temperature")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="Separate observation file, not the receiver frame log")
    parser.add_argument("--house-temperature", type=finite_number)
    parser.add_argument("--display-heating", choices=("on", "off", "unknown"), default="unknown",
                        help="What the display shows; does not assert actual pump/valve activity")
    parser.add_argument("--note", help="Room, original/new setpoint, channel LED observations, etc.")
    parser.add_argument("--at", help="Actual observation time as ISO-8601 with timezone; defaults to now")
    args = parser.parse_args()
    stamp = datetime.fromisoformat(args.at) if args.at else datetime.now().astimezone()
    if stamp.utcoffset() is None:
        parser.error("--at must include a timezone, e.g. 2026-09-05T15:30:00+02:00")
    record = {"schema_version": 1, "type": "manual_observation", "observed_at": stamp.isoformat(),
              "entered_at": datetime.now().astimezone().isoformat(),
              "house_temperature_c": args.house_temperature,
              "display_heating": args.display_heating, "note": args.note}
    with args.file.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"Observation saved: {stamp.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
