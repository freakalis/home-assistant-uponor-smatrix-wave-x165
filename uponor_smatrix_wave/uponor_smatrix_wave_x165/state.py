"""Per-device state for the live receive-only proof of concept."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .protocol import ParsedThermostatFrame


def display_temperature(raw: int | None) -> str:
    """Truncate Celsius to one decimal using exact uint16 RF arithmetic.

    Matches the observed display pairs; the firmware rounding rule is not
    confirmed across all values. Keep decoded temperatures at full precision.
    """
    if raw is None:
        return "unknown"
    if not 0 <= raw <= 0xFFFF:
        raise ValueError("temperature raw value must be uint16")
    numerator = (raw - 320) * 5
    tenths = abs(numerator) // 9
    sign = "-" if numerator < 0 and tenths else ""
    return f"{sign}{tenths // 10}.{tenths % 10}"


@dataclass
class DeviceState:
    device_id: bytes
    temperature_c: float | None = None
    setpoint_c: float | None = None
    last_raw_temperature: int | None = None
    last_raw_setpoint: int | None = None
    bypass_enabled: bool | None = None
    actuator_open: bool | None = None
    last_seen: datetime | None = None
    valid_packet_count: int = 0

    def update(self, frame: ParsedThermostatFrame, observed_at: datetime) -> None:
        if frame.device_id != self.device_id:
            raise ValueError("frame device does not match state device")
        if frame.raw_temperature is not None:
            self.last_raw_temperature = frame.raw_temperature
            self.temperature_c = frame.temperature_c
        if frame.raw_setpoint is not None:
            self.last_raw_setpoint = frame.raw_setpoint
            self.setpoint_c = frame.setpoint_c
        if frame.bypass_enabled is not None:
            self.bypass_enabled = frame.bypass_enabled
        if frame.actuator_open is not None:
            self.actuator_open = frame.actuator_open
        self.last_seen = observed_at
        self.valid_packet_count += 1


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: dict[bytes, DeviceState] = {}

    def update(self, frame: ParsedThermostatFrame, observed_at: datetime) -> DeviceState:
        state = self.devices.setdefault(frame.device_id, DeviceState(frame.device_id))
        state.update(frame, observed_at)
        return state
