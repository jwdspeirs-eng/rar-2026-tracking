# Yellow Brick (YB) Tracking API Guide

Agent-oriented reference for fetching and parsing YB Tracking race data. This is the same API used by this project (`src/yb_race_data.py`, `src/yb_binary_decoder.py`).

**Base host:** `https://yb.tl`  
**Auth:** none (public race viewer endpoints)  
**Example race key used below:** `bayviewmack2024`

---

## Important: race list is local, not from the API

The Streamlit app does **not** download a catalog of races on startup. The dropdown is a hardcoded map in `src/ui_components.py`:

```python
KNOWN_RACES = {
    "Bayview Mac 2025": "bayviewmack2025",
    "Bayview Mac 2024": "bayviewmack2024",
    "Bayview Mac 2023": "bayviewmack2023",
    # ...
    "Chicago Mac 2025": "chicagomac2025",
    "Chicago Mac 2024": "chicagomac2024",
    "Chicago Mac 2023": "chicagomac2023",
}
```

Users can also type any race key manually. There is no “list all races” endpoint used by this tool.

### How to find a race key

From a YB viewer URL:

```
https://yb.tl/bayviewmack2024
            ^^^^^^^^^^^^^^^^
            race_key
```

Also available as the `url` field inside `RaceSetup` JSON.

---

## Endpoints

For a race key `{race}` (e.g. `bayviewmack2024`):

| Purpose | Method | URL | Response |
|---------|--------|-----|----------|
| Race metadata + boats | `GET` | `https://yb.tl/JSON/{race}/RaceSetup` | JSON |
| All position tracks | `GET` | `https://yb.tl/BIN/{race}/AllPositions3` | Binary (custom format) |

Optional / related (not required by this app):

| Purpose | URL |
|---------|-----|
| Human viewer | `https://yb.tl/{race}` |
| Latest positions only | `https://yb.tl/BIN/{race}/LatestPositions3` (same binary format family) |

### Reliability note

`yb.tl` frequently returns intermittent **HTTP 503**. Always retry with exponential backoff (this project uses up to 5 retries, ~1s → ~16s delays). Finished-race data can be cached indefinitely under `~/.cache/yb_race_data/`.

---

## Quick start (use this repo)

```bash
cd yb-data-tactical-analysis
pip install -r requirements.txt

# High-level helper (recommended)
python -c "
import sys; sys.path.insert(0, 'src')
from yb_race_data import load_race
race = load_race('bayviewmack2024')
print(race.summary())
"

# Standalone walkthrough (raw HTTP + parsing)
python examples/fetch_yb_race.py bayviewmack2024
```

`load_race()` wraps both endpoints, caching, retries, and decoding.

---

## 1. RaceSetup JSON

### Request

```bash
curl -fL "https://yb.tl/JSON/bayviewmack2024/RaceSetup" -o RaceSetup.json
```

### Top-level fields used by this project

| Field | Type | Meaning |
|-------|------|---------|
| `title` | string | Display name (e.g. `"100th Bayview Mackinac 2024"`) |
| `url` | string | Race key |
| `start` | int | Tracking window start (Unix seconds) |
| `stop` | int | Tracking window end (Unix seconds) |
| `tz` | string | Timezone label (e.g. `"EDT"`) |
| `tzOffset` | int | Offset from UTC **in seconds** (e.g. `-14400` for EDT) |
| `viewerMode` | string | e.g. `"YACHT-OFFSHORE"` |
| `course` | object | YB course polyline + `distance` (nm) |
| `tags` | array | Classes / divisions |
| `teams` | array | Boats |

`pro` may be `null`. Prefer `title` (or `pro.name` when present) for the race name.

### `course` object

```json
{
  "distance": 384.74,
  "nodes": [
    {"name": "Start / Port Huron[no-timing]", "lat": 43.066, "lon": -82.421},
    {"lat": 43.251, "lon": -82.406}
  ]
}
```

YB’s `course.distance` / node path is a viewer polyline. This app often replaces DTF with its own mark-based course geometry (see `src/utils/course.py`).

### `tags[]` (classes / divisions)

| Field | Type | Meaning |
|-------|------|---------|
| `id` | int | Tag ID referenced by boats |
| `name` | string | e.g. `"Class A"`, `"Fleet Overall"` |
| `handicap` | string | e.g. `"LEVEL"`, rating system hint |
| `start` | int? | Class start time (Unix), if present |
| `sort` | int | Display order |

### `teams[]` (boats)

| Field | Type | Meaning |
|-------|------|---------|
| `id` | int | **Tracker / boat ID** — joins to binary positions |
| `name` | string | Boat name |
| `sail` | string | Sail number |
| `model` | string | Design |
| `owner`, `captain`, `country`, `flag`, `colour` | string | Metadata |
| `status` | string | `RACING`, `RETIRED`, `FINISHED`, `DNS`, … |
| `tags` | int[] | Tag IDs |
| `start` | int | **Boat/class start time** (Unix) — use this, not race `start` |
| `started` | bool | Whether tracking thinks they started |
| `finishedAt` | int? | Finish timestamp (Unix), if finished |
| `tcf1`, `tcf2`, `tcf3` | **string** | Time correction factors — cast to `float` |

#### Quirks

1. **TCF values are strings** (`"1.0334"`), not numbers.
2. **`race.start` is tracking start**, not necessarily the gun. Multi-class races stagger starts; use each boat’s `start` (and/or tag `start`).
3. **`tzOffset` is seconds**, not minutes.
4. **`status` can stay `RACING` even when `finishedAt` is set** (observed on Bayview Mac 2024). Prefer `finishedAt is not None` to detect finishers.
5. JSON may contain **ISO-8859-1** bytes in names (e.g. `Le Rêve`). Decode as UTF-8 first, fall back to `latin-1`.
6. YB DTF in positions is often **straight-line / course-polyline based**, not always mark-rounding accurate.

### Minimal parse example

```python
import json
import requests

race_key = "bayviewmack2024"
url = f"https://yb.tl/JSON/{race_key}/RaceSetup"
raw = requests.get(url, timeout=30).content
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("latin-1")

setup = json.loads(text)
boats = setup["teams"]
tags = {t["id"]: t for t in setup.get("tags", [])}

for boat in boats[:3]:
    tcf = float(boat.get("tcf3") or boat.get("tcf2") or boat.get("tcf1") or 1.0)
    classes = [tags[i]["name"] for i in boat.get("tags", []) if i in tags]
    print(boat["id"], boat["name"], boat.get("status"), tcf, classes)
```

---

## 2. AllPositions3 binary

### Request

```bash
curl -fL "https://yb.tl/BIN/bayviewmack2024/AllPositions3" -o AllPositions3.bin
```

Response is a compact, delta-compressed binary stream. **Do not treat it as JSON.**

This repo’s decoder: `src/yb_binary_decoder.py` (`YBPositionDecoder`), ported from YB’s viewer JS.

### Binary layout (AllPositions3)

All multi-byte integers are **big-endian**.

#### File header

| Offset | Size | Field |
|--------|------|-------|
| 0 | 1 | Flags |
| 1 | 4 | `base_timestamp` (Unix seconds) |

Flag bits:

| Bit | Meaning |
|-----|---------|
| 0 (`0x01`) | Records include altitude |
| 1 (`0x02`) | Records include DTF (distance to finish, nm × 1000) |
| 2 (`0x04`) | Records include lap |
| 3 (`0x08`) | Records include “PC” field |

For Bayview Mac 2024 the flag byte is typically `0x02` (DTF only).

#### Per-tracker block (repeated to EOF)

| Size | Field |
|------|-------|
| 2 | `team_id` (uint16) — matches `teams[].id` |
| 2 | `num_positions` (uint16) |
| … | `num_positions` moment records |

#### Moment records

Each moment starts with a time field whose **high bit of the first byte** indicates encoding:

- **Compressed / delta** if `(first_byte & 0x80) != 0`
  - `uint16` time word (mask with `0x7FFF` for delta seconds)
  - `int16` Δlat, `int16` Δlon (integer units)
  - optional Δalt / Δdtf / lap / Δpc depending on flags
  - Apply deltas to previous moment; **time goes backward**: `at = prev.at - time_delta`
- **Absolute** otherwise
  - `uint32` time offset → `at = base_timestamp + offset`
  - `int32` lat, `int32` lon (integer units)
  - optional absolute alt / dtf / lap / pc

#### Coordinate scale

After parsing a tracker’s moments:

```text
lat_degrees = lat_raw / 100000.0
lon_degrees = lon_raw / 100000.0
```

**DTF units (important):** the integer is *not* SI meters. On Bayview Mac 2024,
start-of-race DTF is ~`384712` while `course.distance` is ~`384.74` nm, i.e.
**DTF ≈ nautical miles × 1000**. Convert with `dtf / 1000.0` for nm.

This repo’s `Position.dtf` docstring historically says “meters” and some paths
divide by `1852`; for tactical charts the app prefers **course-based DTF**
(`utils/course.py`) over YB’s value. If you use raw YB DTF, prefer `/ 1000`.

### Decoded shape

```python
[
  {
    "id": 3484,
    "moments": [
      {"at": 1721476801, "lat": 42.97470, "lon": -82.42050, "dtf": 384712},
      ...
    ]
  },
  ...
]
```

| Field | Meaning |
|-------|---------|
| `id` | Boat / team ID |
| `at` | Unix timestamp |
| `lat`, `lon` | Decimal degrees |
| `dtf` | Distance to finish (**nm × 1000**), if present |
| `alt`, `lap`, `pc` | Optional |

**Moment order in the file is often newest-first** within a tracker (because compressed deltas walk time backward). Sort by `at` before analysis.

### Join setup ↔ positions

```python
boat_by_id = {t["id"]: t for t in setup["teams"]}
for tracker in decoded_positions:
    boat = boat_by_id.get(tracker["id"])
    if not boat:
        continue
    moments = sorted(tracker["moments"], key=lambda m: m["at"])
    # boat["name"], moments[0], moments[-1], ...
```

---

## 3. End-to-end with this library

```python
import sys
sys.path.insert(0, "src")

from yb_race_data import load_race

race = load_race("bayviewmack2024")  # uses cache + retries

print(race.info.title, race.info.distance_nm)
boat = race.get_boat_by_name("Trixie")
positions = sorted(boat.positions, key=lambda p: p.timestamp)
print(len(positions), positions[0].lat, positions[0].dtf)
```

Cache directory: `~/.cache/yb_race_data/`  
- RaceSetup: cached indefinitely  
- AllPositions3: 5 minutes while race `stop` is in the future, else indefinitely  

---

## 4. Working example script

See [`examples/fetch_yb_race.py`](../examples/fetch_yb_race.py).

```bash
# Against live API (retries on 503)
python examples/fetch_yb_race.py bayviewmack2024

# Force using this project's cache only (useful when yb.tl is down)
python examples/fetch_yb_race.py bayviewmack2024 --cache-only
```

Expected output shape (values vary):

```text
Race: 100th Bayview Mackinac 2024 (bayviewmack2024)
Window: 1721473200 → 1721779200  tz=EDT offset=-14400s
Boats: 322   Tags: 21   Trackers with positions: 322
Sample boat: Trixie (id=3484)  positions=3436
  First: 2024-07-20 ...  42.97470,-82.42050  DTF=384.7 nm
  Last:  2024-07-23 ...  45.85134,-84.60531  DTF=0.0 nm
```

---

## 5. Units cheat sheet

| Quantity | YB units | Display in this app |
|----------|----------|---------------------|
| Lat/lon | degrees | degrees |
| DTF (YB raw) | nm × 1000 (integer) | nautical miles (`/ 1000`) |
| Speed (derived) | m/s if computed from Δs | knots (`* 1.94384`) |
| Time | Unix seconds | race-local via `tz` / `tzOffset` |
| TCF | dimensionless float (from string) | same |

---

## 6. Implementation map in this repo

| Concern | File |
|---------|------|
| HTTP + cache + RaceSetup/boat models | `src/yb_race_data.py` |
| AllPositions3 decoder | `src/yb_binary_decoder.py` |
| Hardcoded race dropdown | `src/ui_components.py` → `KNOWN_RACES` |
| Course-based DTF (not YB DTF) | `src/utils/course.py` |
| Tactical metrics on top of tracks | `src/tactical_analysis.py` |

For product-level analysis APIs (ahead/behind, corrected time), see [PYTHON_API.md](PYTHON_API.md).
