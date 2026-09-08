"""Observed I-167 frames. House-temperature interpretation is provisional."""
from dataclasses import dataclass

from .crc import crc16_cms, crc16_modbus
from .protocol import PREAMBLE_SYNC, FrameError, uponor_temperature, validate_frame


REMOTE_FIXED_17_19 = bytes.fromhex("00 0B 00")
REMOTE_FIXED_21_23 = bytes.fromhex("00 08 10")
REMOTE_FIXED_25_35 = bytes.fromhex("00 05 64 01 9A 03 B6 02 A8 03 14")
REMOTE_FIXED_38_39 = bytes.fromhex("00 12")


def validate_i167_frame(raw: bytes, *, interface_id: bytes) -> bytes:
    """Validate the shared outer and inner CRCs and selected I-167 endpoint."""
    validate_frame(raw)
    if len(interface_id) != 4:
        raise ValueError("interface_id must contain exactly 4 bytes")
    if raw[9:13] != interface_id:
        raise FrameError(f"unexpected I-167 endpoint {raw[9:13].hex().upper()}")
    received_inner = int.from_bytes(raw[-4:-2], "little")
    calculated_inner = crc16_modbus(raw[9:-4])
    if received_inner != calculated_inner:
        raise FrameError(
            f"inner CRC mismatch: received {received_inner:04X}, calculated {calculated_inner:04X}"
        )
    return raw


def _finish_i167_frame(raw: bytearray) -> bytes:
    raw[-4:-2] = crc16_modbus(raw[9:-4]).to_bytes(2, "little")
    raw[-2:] = crc16_cms(raw[8:-2]).to_bytes(2, "big")
    return bytes(raw)


@dataclass(frozen=True)
class RemoteSetpointFrame:
    raw: bytes
    interface_id: bytes
    room_primary: int
    room_secondary: int
    status_byte: int
    raw_setpoint: int

    @property
    def remote_enabled(self) -> bool:
        return bool(self.status_byte & 0x08)

    @property
    def setpoint_c(self) -> float:
        return uponor_temperature(self.raw_setpoint)


def parse_remote_setpoint(raw: bytes, *, interface_id: bytes) -> RemoteSetpointFrame:
    """Parse the observed wireless I-167 L44 remote-setpoint command."""
    validate_i167_frame(raw, interface_id=interface_id)
    if len(raw) != 44 or raw[9:13] != interface_id or raw[13:16] != bytes.fromhex("01 17 00"):
        raise FrameError("not a supported remote-setpoint frame for this interface")
    if (raw[17:20] != REMOTE_FIXED_17_19 or raw[21:24] != REMOTE_FIXED_21_23
            or raw[25:36] != REMOTE_FIXED_25_35 or raw[38:40] != REMOTE_FIXED_38_39):
        raise FrameError("unexpected remote-setpoint frame structure")
    if raw[24] not in (0x80, 0x88):
        raise FrameError(f"unexpected remote-control status {raw[24]:02X}")
    if (raw[16] - raw[20]) & 0xFF != 8:
        raise FrameError("inconsistent remote-setpoint room codes")
    return RemoteSetpointFrame(
        raw=raw,
        interface_id=interface_id,
        room_primary=raw[16],
        room_secondary=raw[20],
        status_byte=raw[24],
        raw_setpoint=int.from_bytes(raw[36:38], "big"),
    )


def build_remote_setpoint(
    *, interface_id: bytes, room_primary: int, room_secondary: int,
    remote_enabled: bool, raw_setpoint: int,
) -> bytes:
    """Build an L44 frame as bytes only; this function performs no RF transmission."""
    if len(interface_id) != 4:
        raise ValueError("interface_id must contain exactly 4 bytes")
    if not all(0 <= value <= 0xFF for value in (room_primary, room_secondary)):
        raise ValueError("room codes must be uint8")
    if (room_primary - room_secondary) & 0xFF != 8:
        raise ValueError("room_primary must be eight greater than room_secondary")
    if not 0 <= raw_setpoint <= 0xFFFF:
        raise ValueError("raw_setpoint must be uint16")
    raw = bytearray(PREAMBLE_SYNC)
    raw.extend(b"\x21")
    raw.extend(interface_id)
    raw.extend(bytes.fromhex("01 17 00"))
    raw.extend((room_primary,))
    raw.extend(REMOTE_FIXED_17_19)
    raw.extend((room_secondary,))
    raw.extend(REMOTE_FIXED_21_23)
    raw.extend((0x88 if remote_enabled else 0x80,))
    raw.extend(REMOTE_FIXED_25_35)
    raw.extend(raw_setpoint.to_bytes(2, "big"))
    raw.extend(REMOTE_FIXED_38_39)
    raw.extend(bytes(4))
    return _finish_i167_frame(raw)


@dataclass(frozen=True)
class SystemModeFrame:
    raw: bytes
    interface_id: bytes
    status_byte: int

    @property
    def mode(self):
        return "away" if self.status_byte & 0x08 else "home"


def parse_system_mode(raw: bytes, *, interface_id: bytes) -> SystemModeFrame:
    """Observed forced-away bit; scheduled ECO semantics remain unverified."""
    validate_i167_frame(raw, interface_id=interface_id)
    if len(raw) != 56 or raw[9:13] != interface_id or raw[13:16] != bytes.fromhex("FF1700"):
        raise FrameError("not a supported system-mode frame for this interface")
    return SystemModeFrame(raw, interface_id, raw[24])


def build_system_mode(template: bytes, *, interface_id: bytes, mode: str) -> bytes:
    """Change home/away in a captured L56 template; performs no RF transmission."""
    parse_system_mode(template, interface_id=interface_id)
    if mode not in ("home", "away"):
        raise ValueError("mode must be 'home' or 'away'")
    raw = bytearray(template)
    if mode == "away":
        raw[24] |= 0x08
    else:
        raw[24] &= ~0x08
    return _finish_i167_frame(raw)


@dataclass(frozen=True)
class HouseTemperatureFrame:
    raw: bytes
    interface_id: bytes
    raw_temperature: int

    @property
    def temperature_c(self):
        return uponor_temperature(self.raw_temperature)


def parse_house_temperature(raw: bytes, *, interface_id: bytes) -> HouseTemperatureFrame:
    """Require the selected I-167 and observed L50 / 01 17 1E family.

    The frame does not establish an X-165 association or RF direction. Selecting
    an interface ID is independent of selecting the thermostat controller ID.
    """
    validate_i167_frame(raw, interface_id=interface_id)
    if len(raw) != 50 or raw[9:13] != interface_id or raw[13:16] != bytes.fromhex("01 17 1E"):
        raise FrameError("not a supported house-temperature frame for this interface")
    value = int.from_bytes(raw[26:28], "big")
    if value in (0x7FFF, 0xFFFF):
        raise FrameError("house temperature unavailable (sentinel)")
    return HouseTemperatureFrame(raw, interface_id, value)
