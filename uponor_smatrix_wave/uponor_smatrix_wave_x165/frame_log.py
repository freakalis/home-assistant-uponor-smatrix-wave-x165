"""Read-only research journal for valid frames, including unknown message families."""
from datetime import datetime
import json
from pathlib import Path
import uuid

from .protocol import FrameError, validate_frame


class FrameJournal:
    def __init__(self, path: Path, *, trace_ids=(), metadata=None, log=print):
        self.path = Path(path)
        self.trace_ids = tuple(trace_ids)
        self.log = log
        self.session = uuid.uuid4().hex
        self.count = 0
        self._classes = set()
        self._failed = False
        self._file = self.path.open("a", encoding="utf-8", buffering=1)
        self._write({"type": "session", "started_at": datetime.now().astimezone().isoformat(),
                     "trace_ids": [value.hex().upper() for value in self.trace_ids],
                     "include_thermostat_context": True, **(metadata or {})})

    def _write(self, record):
        if self._failed:
            return False
        try:
            self._file.write(json.dumps({"schema_version": 1, "session": self.session, **record}, allow_nan=False) + "\n")
            self._file.flush()
            return True
        except OSError:
            self._failed = True
            self.log("Frame log write failed; RF/MQTT continue, restart with a writable log path")
            return False

    def observe(self, packet, observed_at, *, frame=None, result=None, sample_offset=0, sample_rate=0):
        try:
            validate_frame(packet)
        except FrameError:
            return False
        # Search only inside the frame body. A byte match is association evidence,
        # not proof that a field is an address or which endpoint transmitted.
        matches = {}
        for value in self.trace_ids:
            offsets = [i for i in range(9, len(packet) - 5) if packet[i:i+4] == value]
            if offsets:
                matches[value.hex().upper()] = offsets
        if self.trace_ids and not matches and frame is None:
            return False
        record = {"type": "frame", "observed_at": observed_at.isoformat(),
                  "processed_at": datetime.now().astimezone().isoformat(),
                  "sample_offset": sample_offset, "sample_rate": sample_rate,
                  "length": len(packet), "crc_valid": True,
                  "raw_hex": packet.hex(" ").upper(), "matched_id_offsets": matches,
                  "selection": "trace_id" if matches else "thermostat_context" if frame else "all_valid",
                  "bytes_09_12": packet[9:13].hex().upper(),
                  "bytes_13_16": packet[13:17].hex().upper()}
        if frame is not None:
            record["thermostat"] = {"device_id": frame.device_id.hex().upper(),
                "controller_id": frame.controller_id.hex().upper(),
                "raw_temperature": frame.raw_temperature, "temperature_c": frame.temperature_c,
                "raw_setpoint": frame.raw_setpoint, "setpoint_c": frame.setpoint_c,
                "tlvs": {f"{tag:02X}": value for tag, value in frame.tlv_items}}
        result = result or {}
        record["rf"] = {key: result[key] for key in (
            "tone_low_hz", "tone_high_hz", "estimated_bitrate", "confidence",
            "sync_errors", "bit_order", "inverted") if key in result}
        if not self._write(record):
            return False
        self.count += 1
        kind = (len(packet), tuple(matches))
        if matches and kind not in self._classes:
            self._classes.add(kind)
            self.log(f"[{observed_at:%H:%M:%S}] Trace: CRC-valid L{len(packet)}, ID {', '.join(matches)}; saved to {self.path}")
        return True

    def close(self):
        self._write({"type": "session_end", "ended_at": datetime.now().astimezone().isoformat(),
                     "frames_written": self.count, "write_failed": self._failed})
        try:
            self._file.close()
        except OSError:
            self.log("Frame log close failed")
        self.log(f"Frame log: {self.count} frames saved to {self.path}" + (" (write failure occurred)" if self._failed else ""))
