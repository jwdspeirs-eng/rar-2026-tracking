"""
Decoder for Yellow Brick (YB) Tracking's AllPositions3 binary feed.

Format reference: ../../YELLOW_BRICK_API.md (section 2), reverse-engineered
from YB's public viewer. All multi-byte integers are big-endian.

ASSUMPTIONS (not fully pinned down by the spec doc — verify against a real
race before trusting anything beyond DTF-only races like bayviewmack2024,
which is the doc's own worked example and uses flag byte 0x02, i.e. DTF
only, no altitude/lap/pc):

  - Absolute moments: optional fields (alt, dtf, lap, pc) are int32, mirroring
    the int32 lat/lon width used in absolute records.
  - Compressed/delta moments: optional delta fields (Dalt, Ddtf, Dpc) are
    int16, mirroring the int16 Dlat/Dlon width. `lap` is treated as a plain
    uint8 (lap counters are small; whether it's delta or absolute makes no
    practical difference at that size).
  - Field order within a moment follows flag bit order: alt (0x01), dtf
    (0x02), lap (0x04), pc (0x08).

If decoded coordinates come out nonsensical (e.g. not in the expected race
area) for a race using flags other than 0x02, these assumptions are the
first place to check.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

FLAG_ALT = 0x01
FLAG_DTF = 0x02
FLAG_LAP = 0x04
FLAG_PC = 0x08

COORD_SCALE = 100000.0  # raw integer -> decimal degrees


@dataclass
class Moment:
    at: int
    lat: float
    lon: float
    alt: Optional[int] = None
    dtf: Optional[int] = None
    lap: Optional[int] = None
    pc: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"at": self.at, "lat": self.lat, "lon": self.lon}
        if self.alt is not None:
            d["alt"] = self.alt
        if self.dtf is not None:
            d["dtf"] = self.dtf
        if self.lap is not None:
            d["lap"] = self.lap
        if self.pc is not None:
            d["pc"] = self.pc
        return d


class _Cursor:
    """Small helper to walk a bytes buffer and unpack big-endian fields."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self) -> int:
        v = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return v


class YBPositionDecoder:
    """Decodes an AllPositions3 binary payload into a list of tracker dicts:

        [{"id": <team_id>, "moments": [{"at": ..., "lat": ..., "lon": ..., "dtf": ...}, ...]}, ...]

    Moments are returned in file order (which YB often writes newest-first
    within a tracker, due to how delta compression walks backward in time).
    Sort by "at" before doing any time-series analysis.
    """

    def __init__(self, data: bytes):
        self.cur = _Cursor(data)

    def parse(self) -> List[Dict[str, Any]]:
        cur = self.cur
        if cur.remaining() < 5:
            return []

        flags = cur.u8()
        base_timestamp = cur.u32()

        has_alt = bool(flags & FLAG_ALT)
        has_dtf = bool(flags & FLAG_DTF)
        has_lap = bool(flags & FLAG_LAP)
        has_pc = bool(flags & FLAG_PC)

        trackers: List[Dict[str, Any]] = []

        while cur.remaining() >= 4:
            team_id = cur.u16()
            num_positions = cur.u16()

            moments: List[Dict[str, Any]] = []
            prev: Optional[Moment] = None

            for _ in range(num_positions):
                if cur.remaining() < 2:
                    break  # truncated/corrupt tail; stop gracefully

                # Peek high bit of the time field without consuming yet.
                first_byte = cur.data[cur.pos]
                compressed = bool(first_byte & 0x80)

                if compressed:
                    time_word = cur.u16()
                    delta_seconds = time_word & 0x7FFF
                    dlat = cur.i16()
                    dlon = cur.i16()

                    alt = dtf = lap = pc = None
                    if has_alt:
                        d_alt = cur.i16()
                        alt = (prev.alt if prev and prev.alt is not None else 0) + d_alt
                    if has_dtf:
                        d_dtf = cur.i16()
                        dtf = (prev.dtf if prev and prev.dtf is not None else 0) + d_dtf
                    if has_lap:
                        lap = cur.u8()
                    if has_pc:
                        d_pc = cur.i16()
                        pc = (prev.pc if prev and prev.pc is not None else 0) + d_pc

                    if prev is None:
                        # No prior absolute moment to delta against; skip
                        # rather than emit garbage. Shouldn't normally
                        # happen since files typically open with an
                        # absolute record.
                        continue

                    at = prev.at - delta_seconds
                    lat = prev.lat + (dlat / COORD_SCALE)
                    lon = prev.lon + (dlon / COORD_SCALE)
                    m = Moment(at=at, lat=lat, lon=lon, alt=alt, dtf=dtf, lap=lap, pc=pc)

                else:
                    offset = cur.u32()
                    lat_raw = cur.i32()
                    lon_raw = cur.i32()

                    alt = dtf = lap = pc = None
                    if has_alt:
                        alt = cur.i32()
                    if has_dtf:
                        dtf = cur.i32()
                    if has_lap:
                        lap = cur.u8()
                    if has_pc:
                        pc = cur.i32()

                    at = base_timestamp + offset
                    lat = lat_raw / COORD_SCALE
                    lon = lon_raw / COORD_SCALE
                    m = Moment(at=at, lat=lat, lon=lon, alt=alt, dtf=dtf, lap=lap, pc=pc)

                moments.append(m.as_dict())
                prev = m

            trackers.append({"id": team_id, "moments": moments})

        return trackers
