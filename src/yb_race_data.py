"""
High-level Yellow Brick (YB) race data client.

Wraps the two yb.tl endpoints (RaceSetup JSON + AllPositions3 binary),
handles HTTP retries and on-disk caching, and joins boats to their decoded
position tracks. See ../../YELLOW_BRICK_API.md for the underlying API.

Usage:
    import sys; sys.path.insert(0, "src")
    from yb_race_data import load_race

    race = load_race("bayviewmack2024")
    print(race.summary())
    boat = race.get_boat_by_name("Trixie")
    positions = sorted(boat.positions, key=lambda p: p.timestamp)
    print(len(positions), positions[0].lat, positions[0].dtf)
"""

from __future__ import annotations

import hashlib
import json
import time
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from yb_binary_decoder import YBPositionDecoder

SERVER = "yb.tl"
CACHE_DIR = Path.home() / ".cache" / "yb_race_data"
DTF_SCALE = 1000.0  # YB raw DTF is nautical miles x 1000


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return CACHE_DIR / f"{key}.cache"


def _read_cache(url: str) -> Optional[bytes]:
    path = _cache_path(url)
    return path.read_bytes() if path.exists() else None


def _write_cache(url: str, data: bytes) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_bytes(data)


def fetch_bytes(
    url: str,
    *,
    use_cache: bool = True,
    cache_only: bool = False,
    timeout: int = 60,
    max_retries: int = 5,
) -> bytes:
    """GET url with optional file cache and exponential-backoff retries.

    yb.tl frequently returns intermittent HTTP 503s; retry with backoff
    (~1s -> ~16s across 5 attempts) before giving up.
    """
    if use_cache or cache_only:
        cached = _read_cache(url)
        if cached is not None:
            return cached
        if cache_only:
            raise FileNotFoundError(
                f"No cache for {url} (expected {_cache_path(url)}). "
                "Load the race once online, or omit cache_only."
            )

    delay = 1.0
    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 2):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                if use_cache:
                    _write_cache(url, resp.content)
                return resp.content
            if 400 <= resp.status_code < 500:
                raise RuntimeError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_error = exc

        if attempt <= max_retries:
            jitter = delay * random.uniform(-0.25, 0.25)
            time.sleep(max(0.1, delay + jitter))
            delay *= 2.0

    raise RuntimeError(f"Failed after retries: {url} ({last_error})")


def _decode_json_bytes(data: bytes) -> Any:
    """YB RaceSetup is usually UTF-8 but may contain latin-1 boat names."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class Position:
    timestamp: int
    lat: float
    lon: float
    dtf: Optional[float] = None  # nautical miles (already / 1000 from raw)
    alt: Optional[int] = None
    lap: Optional[int] = None
    pc: Optional[int] = None


@dataclass
class Boat:
    id: int
    name: str
    sail: Optional[str] = None
    model: Optional[str] = None
    owner: Optional[str] = None
    captain: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None
    class_names: List[str] = field(default_factory=list)
    start: Optional[int] = None
    started: Optional[bool] = None
    finished_at: Optional[int] = None
    tcf1: Optional[float] = None
    tcf2: Optional[float] = None
    tcf3: Optional[float] = None
    positions: List[Position] = field(default_factory=list)

    @property
    def is_finished(self) -> bool:
        # status can stay "RACING" even after finishing; finishedAt is the
        # reliable signal (observed on Bayview Mac 2024).
        return self.finished_at is not None


@dataclass
class RaceInfo:
    race_key: str
    title: str
    start: Optional[int]
    stop: Optional[int]
    tz: str
    tz_offset: int
    viewer_mode: Optional[str]
    distance_nm: Optional[float]


@dataclass
class Race:
    info: RaceInfo
    boats: List[Boat]

    def get_boat_by_name(self, name: str) -> Optional[Boat]:
        for b in self.boats:
            if b.name == name:
                return b
        return None

    def get_boat_by_id(self, team_id: int) -> Optional[Boat]:
        for b in self.boats:
            if b.id == team_id:
                return b
        return None

    def summary(self) -> str:
        with_positions = sum(1 for b in self.boats if b.positions)
        lines = [
            f"Race: {self.info.title} ({self.info.race_key})",
            f"Window: {self.info.start} -> {self.info.stop}  "
            f"tz={self.info.tz} offset={self.info.tz_offset}s",
            f"Distance: {self.info.distance_nm} nm",
            f"Boats: {len(self.boats)}   Trackers with positions: {with_positions}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _to_float(val: Any) -> Optional[float]:
    """TCF fields arrive as strings (e.g. '1.0334'); cast defensively."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def load_race(
    race_key: str,
    *,
    use_cache: bool = True,
    cache_only: bool = False,
    no_cache: bool = False,
) -> Race:
    fetch_kwargs = {"use_cache": use_cache and not no_cache, "cache_only": cache_only}

    setup_url = f"https://{SERVER}/JSON/{race_key}/RaceSetup"
    positions_url = f"https://{SERVER}/BIN/{race_key}/AllPositions3"

    setup = _decode_json_bytes(fetch_bytes(setup_url, **fetch_kwargs))
    raw_positions = fetch_bytes(positions_url, **fetch_kwargs)
    trackers = YBPositionDecoder(raw_positions).parse()
    positions_by_id: Dict[int, List[Dict[str, Any]]] = {
        t["id"]: t["moments"] for t in trackers
    }

    tags_by_id = {t["id"]: t for t in setup.get("tags", [])}

    boats: List[Boat] = []
    for team in setup.get("teams", []):
        tag_ids = team.get("tags", []) or []
        class_names = [tags_by_id[i]["name"] for i in tag_ids if i in tags_by_id]

        moments = positions_by_id.get(team["id"], [])
        positions = [
            Position(
                timestamp=m["at"],
                lat=m["lat"],
                lon=m["lon"],
                dtf=(m["dtf"] / DTF_SCALE) if m.get("dtf") is not None else None,
                alt=m.get("alt"),
                lap=m.get("lap"),
                pc=m.get("pc"),
            )
            for m in moments
        ]
        positions.sort(key=lambda p: p.timestamp)

        boats.append(
            Boat(
                id=team["id"],
                name=team.get("name", f"id={team['id']}"),
                sail=team.get("sail"),
                model=team.get("model"),
                owner=team.get("owner"),
                captain=team.get("captain"),
                country=team.get("country"),
                status=team.get("status"),
                class_names=class_names,
                start=team.get("start"),
                started=team.get("started"),
                finished_at=team.get("finishedAt"),
                tcf1=_to_float(team.get("tcf1")),
                tcf2=_to_float(team.get("tcf2")),
                tcf3=_to_float(team.get("tcf3")),
                positions=positions,
            )
        )

    course = setup.get("course") or {}
    info = RaceInfo(
        race_key=race_key,
        title=(setup.get("pro") or {}).get("name") or setup.get("title") or race_key,
        start=setup.get("start"),
        stop=setup.get("stop"),
        tz=setup.get("tz", "UTC"),
        tz_offset=int(setup.get("tzOffset") or 0),
        viewer_mode=setup.get("viewerMode"),
        distance_nm=course.get("distance"),
    )

    return Race(info=info, boats=boats)
