from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


DEVICE_IP = "192.168.1.41"
DEVICE_PORT = 1001
LEGACY_STATUS_SIZE = 1067
EXTENDED_STATUS_SIZE = 1112
EXTENDED_FIELD_SHIFT = 10
LEGACY_PROGRESS_TRAILER_SIZE = 34
EXTENDED_PROGRESS_TRAILER_SIZE = 26
MINIMUM_PLAUSIBLE_TEMPERATURE_C = -100.0
MAXIMUM_PLAUSIBLE_TEMPERATURE_C = 200.0

TEMPERATURE_FIELDS: tuple[tuple[str, int], ...] = (
    ("V1 Board 1", 197),
    ("V2 Board 1", 201),
    ("V1 Board 2", 205),
    ("V2 Board 2", 209),
    ("I1 Board 1", 213),
    ("I2 Board 1", 217),
    ("I1 Board 2", 221),
    ("I2 Board 2", 225),
    ("Main Board 1", 233),
    ("Main Board 2", 241),
)
VOLTAGE_SENSORS = tuple(name for name, _ in TEMPERATURE_FIELDS[:4])
CURRENT_SENSORS = tuple(name for name, _ in TEMPERATURE_FIELDS[4:8])
INTERNAL_SENSORS = tuple(name for name, _ in TEMPERATURE_FIELDS[8:])
CONTROL_SENSORS = VOLTAGE_SENSORS + CURRENT_SENSORS


def _i32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _status_layout(data: bytes) -> tuple[int, int, str] | None:
    """Return base size, confirmed field shift and layout name.

    Legacy packets leave bytes 6–7 at zero. The updated firmware inserts a
    length-bearing header and advertises the 1112-byte base response there.
    Refusing unknown advertised sizes prevents raw bytes from being displayed
    as implausible temperatures after a future protocol change.
    """
    if len(data) < LEGACY_STATUS_SIZE or data[:4] != b"\x04\x00\x00\x00":
        return None
    advertised_size = _u16(data, 6)
    if advertised_size == 0:
        return LEGACY_STATUS_SIZE, 0, "legacy_1067"
    if advertised_size in (EXTENDED_STATUS_SIZE, EXTENDED_STATUS_SIZE + EXTENDED_PROGRESS_TRAILER_SIZE) and len(data) >= advertised_size:
        return EXTENDED_STATUS_SIZE, EXTENDED_FIELD_SHIFT, "extended_1112"
    return None


@dataclass(slots=True)
class PacketRecord:
    frame: int
    epoch: float
    relative: float
    src: str
    src_port: int
    dst: str
    dst_port: int
    payload: bytes

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.epoch, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")


@dataclass(slots=True)
class StatusReading:
    frame: int
    epoch: float
    relative: float
    timestamp_iso: str
    temperatures: dict[str, float]
    hottest_control_name: str
    hottest_control_c: float
    hottest_internal_name: str
    hottest_internal_c: float
    executing: bool
    error_latched: bool
    error_code: int
    progress_status: int | None = None
    profile_elapsed_s: float | None = None


@dataclass(slots=True)
class ProtocolEvent:
    frame: int
    relative: float
    timestamp_iso: str
    kind: str
    severity: str
    message: str
    source: str = "protocol"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VectorCommand:
    state_index: int
    channels: list[dict[str, float | int | str]]


def decode_status(packet: PacketRecord) -> StatusReading | None:
    data = packet.payload
    if packet.src != DEVICE_IP or packet.src_port != DEVICE_PORT:
        return None
    layout = _status_layout(data)
    if layout is None:
        return None

    status_size, field_shift, layout_name = layout
    header = data[:status_size]
    temperatures = {name: _i32(header, offset + field_shift) / 10.0 for name, offset in TEMPERATURE_FIELDS}
    if any(
        value < MINIMUM_PLAUSIBLE_TEMPERATURE_C or value > MAXIMUM_PLAUSIBLE_TEMPERATURE_C
        for value in temperatures.values()
    ):
        return None
    control_name = max(CONTROL_SENSORS, key=temperatures.__getitem__)
    internal_name = max(INTERNAL_SENSORS, key=temperatures.__getitem__)
    executing = all(_i32(header, offset + field_shift) == 1_000_000_000 for offset in range(329, 366, 4))
    error_latched = _u32(header, 4 + field_shift) == 1
    error_code = _u32(header, 8 + field_shift)

    progress_status: int | None = None
    elapsed_s: float | None = None
    trailer = data[status_size:]
    # Both layouts repeat elapsed microseconds at offsets 4 and 12. Updated
    # firmware removes four bytes before and after the progress-status byte.
    trailer_size = LEGACY_PROGRESS_TRAILER_SIZE if layout_name == "legacy_1067" else EXTENDED_PROGRESS_TRAILER_SIZE
    status_offset = 29 if layout_name == "legacy_1067" else 25
    if len(trailer) >= trailer_size:
        elapsed_us = struct.unpack_from("<Q", trailer, 4)[0]
        duplicate_us = struct.unpack_from("<Q", trailer, 12)[0]
        if elapsed_us == duplicate_us:
            elapsed_s = elapsed_us / 1_000_000.0
            progress_status = trailer[status_offset]

    return StatusReading(
        frame=packet.frame,
        epoch=packet.epoch,
        relative=packet.relative,
        timestamp_iso=packet.timestamp_iso,
        temperatures=temperatures,
        hottest_control_name=control_name,
        hottest_control_c=temperatures[control_name],
        hottest_internal_name=internal_name,
        hottest_internal_c=temperatures[internal_name],
        executing=executing,
        error_latched=error_latched,
        error_code=error_code,
        progress_status=progress_status,
        profile_elapsed_s=elapsed_s,
    )


def decode_fan_mode(packet: PacketRecord) -> str | None:
    data = packet.payload
    if packet.dst != DEVICE_IP or packet.dst_port != DEVICE_PORT:
        return None
    if len(data) == 110:
        flag = data[10]
    elif (
        len(data) in (124, 129)
        and data[:4] == b"\xFF\xFF\xFF\x00"
        and data[4] == len(data) - 6
        and data[6:10] == b"\x04\x00\x00\x00"
        and data[11] == len(data) - 6
    ):
        # Updated AMPro requests use either 124 or 129 bytes. The additional
        # five bytes in the 129-byte format are appended later in the message;
        # the confirmed Fan Mode flag remains at offset 18.
        flag = data[18]
    else:
        return None
    if flag == 0x3F:
        return "MAX"
    if flag == 0x00:
        return "NORMAL"
    return f"UNKNOWN_0x{flag:02X}"


def decode_vector_command(packet: PacketRecord) -> VectorCommand | None:
    data = packet.payload
    if packet.dst != DEVICE_IP or packet.dst_port != DEVICE_PORT:
        return None
    if len(data) not in (95, 167) or _u32(data, 0) != 16:
        return None
    count = 6 if len(data) == 95 else 12
    channels: list[dict[str, float | int | str]] = []
    for channel in range(count):
        offset = 13 + channel * 12
        amplitude, phase, frequency = struct.unpack_from("<iiI", data, offset)
        signal = "voltage" if count == 12 and channel < 6 else "current" if count == 12 else "unknown"
        channels.append(
            {
                "channel": channel + 1,
                "signal": signal,
                "amplitude_raw": amplitude,
                "amplitude": amplitude / 1_000_000.0,
                "phase_deg": phase / 1_000_000.0,
                "frequency_hz": frequency / 1_000_000.0,
            }
        )
    return VectorCommand(state_index=data[9], channels=channels)


def decode_state_duration(packet: PacketRecord) -> dict[str, float | int | bool] | None:
    data = packet.payload
    if packet.dst != DEVICE_IP or packet.dst_port != DEVICE_PORT:
        return None
    if len(data) != 253 or _u32(data, 0) != 16:
        return None
    return {
        "state_index": data[69],
        "enabled": bool(data[12]),
        "duration_s": _u32(data, 13) / 1_000_000.0,
    }


def is_clear_error_command(packet: PacketRecord) -> bool:
    return packet.dst == DEVICE_IP and packet.dst_port == DEVICE_PORT and packet.payload == b"\x00\x08\x00\x00"
