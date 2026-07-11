#!/usr/bin/env python3
"""
Fetch and parse Yellow Brick (YB) Tracking race data.

Demonstrates the two endpoints this project uses:
  1. GET https://yb.tl/JSON/{race_key}/RaceSetup     → JSON metadata + boats
  2. GET https://yb.tl/BIN/{race_key}/AllPositions3 → binary GPS tracks

Usage:
  python examples/fetch_yb_race.py bayviewmack2024
  python examples/fetch_yb_race.py bayviewmack2024 --cache-only
  python examples/fetch_yb_race.py bayviewmack2024 --no-cache

Requires: requests (and this repo's src/ on PYTHONPATH, or run from repo root).

(Originally provided by Seb as fetch_yb_race_example.py; moved here so its
REPO_ROOT/src path resolution matches this repo's layout.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# Allow running as `python examples/fetch_yb_race.py` from repo root
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yb_binary_decoder import YBPositionDecoder  # noqa: E402

DEFAULT_RACE = "bayviewmack2024"
SERVER = "yb.tl"
CACHE_DIR = Path.home() / ".cache" / "yb_race_data"
# YB AllPositions3 DTF is stored as nautical-miles × 1000 (not SI meters).
DTF_SCALE = 1000.0


# ---------------------------------------------------------------------------
# HTTP helpers (mirrors the retry behavior in yb_race_data.py)
# ---------------------------------------------------------------------------

def cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return CACHE_DIR / f"{key}.cache"


def read_cache(url: str) -> Optional[bytes]:
    path = cache_path(url)
    if not path.exists():
        return None
    return path.read_bytes()


def write_cache(url: str, data: bytes) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(url).write_bytes(data)


def fetch_bytes(
    url: str,
    *,
    use_cache: bool = True,
    cache_only: bool = False,
    timeout: int = 60,
    max_retries: int = 5,
) -> bytes:
    """GET url with optional file cache and exponential-backoff retries."""
    if use_cache or cache_only:
        cached = read_cache(url)
        if cached is not None:
            print(f"  cache hit: {url}")
            return cached
        if cache_only:
            raise FileNotFoundError(
                f"No cache for {url}\n"
                f"Expected file: {cache_path(url)}\n"
                "Load the race once online, or omit --cache-only."
            )

    delay = 1.0
    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 2):
        try:
            print(f"  GET {url} (attempt {attempt})")
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                if use_cache:
                    write_cache(url, resp.content)
                return resp.content
            if 400 <= resp.status_code < 500:
                raise RuntimeError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_error = exc

        if attempt <= max_retries:
            jitter = delay * random.uniform(-0.25, 0.25)
            sleep_for = max(0.1, delay + jitter)
            print(f"  retry in {sleep_for:.1f}s ({last_error})")
            time.sleep(sleep_for)
            delay *= 2.0

    raise RuntimeError(f"Failed after retries: {url} ({last_error})")


def decode_json_bytes(data: bytes) -> Any:
    """YB RaceSetup is usually UTF-8 but may include latin-1 boat names."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_race_setup(race_key: str, **fetch_kwargs) -> Dict[str, Any]:
    url = f"https://{SERVER}/JSON/{race_key}/RaceSetup"
    return decode_json_bytes(fetch_bytes(url, **fetch_kwargs))


def load_positions(race_key: str, **fetch_kwargs) -> List[Dict[str, Any]]:
    url = f"https://{SERVER}/BIN/{race_key}/AllPositions3"
    raw = fetch_bytes(url, **fetch_kwargs)
    return YBPositionDecoder(raw).parse()


def fmt_ts(ts: int, tz_offset_seconds: int = 0) -> str:
    tz = timezone(timedelta(seconds=tz_offset_seconds))
    return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def pick_sample_tracker(
    trackers: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return the tracker with the most moments, moments sorted oldest→newest."""
    best = max(trackers, key=lambda t: len(t.get("moments", [])))
    moments = sorted(best.get("moments", []), key=lambda m: m["at"])
    return best, moments


def summarize(race_key: str, setup: Dict[str, Any], trackers: List[Dict[str, Any]]) -> None:
    title = (setup.get("pro") or {}).get("name") or setup.get("title") or race_key
    tz_name = setup.get("tz", "UTC")
    tz_offset = int(setup.get("tzOffset") or 0)
    teams = setup.get("teams") or []
    tags = setup.get("tags") or []
    course = setup.get("course") or {}

    print()
    print(f"Race: {title} ({race_key})")
    print(
        f"Window: {setup.get('start')} → {setup.get('stop')}  "
        f"tz={tz_name} offset={tz_offset}s"
    )
    if setup.get("start"):
        print(f"  local start: {fmt_ts(setup['start'], tz_offset)}")
    print(f"YB course distance: {course.get('distance')} nm")
    print(f"Boats: {len(teams)}   Tags: {len(tags)}   Trackers with positions: {len(trackers)}")

    tag_by_id = {t["id"]: t for t in tags}
    boat_by_id = {t["id"]: t for t in teams}

    tracker, moments = pick_sample_tracker(trackers)
    boat = boat_by_id.get(tracker["id"], {})
    name = boat.get("name", f"id={tracker['id']}")
    classes = [
        tag_by_id[i]["name"]
        for i in boat.get("tags", [])
        if i in tag_by_id
    ]

    # TCF fields arrive as strings
    tcf_vals = []
    for key in ("tcf1", "tcf2", "tcf3"):
        raw = boat.get(key)
        if raw is not None:
            tcf_vals.append(f"{key}={float(raw)}")

    print()
    print(f"Sample boat: {name} (id={tracker['id']})  positions={len(moments)}")
    print(f"  status={boat.get('status')}  sail={boat.get('sail')}  model={boat.get('model')}")
    print(f"  classes={classes}")
    print(f"  boat start={boat.get('start')}  finishedAt={boat.get('finishedAt')}")
    print(f"  {', '.join(tcf_vals) or 'no tcf fields'}")

    if not moments:
        print("  (no moments)")
        return

    first, last = moments[0], moments[-1]
    for label, m in (("First", first), ("Last", last)):
        dtf_raw = m.get("dtf")
        dtf_nm = f"{dtf_raw / DTF_SCALE:.1f} nm" if dtf_raw is not None else "n/a"
        print(
            f"  {label}: {fmt_ts(m['at'], tz_offset)}  "
            f"{m['lat']:.5f},{m['lon']:.5f}  DTF={dtf_nm}"
        )

    print()
    print("Join tip: teams[].id == tracker['id'] from AllPositions3.")
    print("Sort moments by 'at' before analysis (binary order is often newest-first).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "race_key",
        nargs="?",
        default=DEFAULT_RACE,
        help=f"YB race key (default: {DEFAULT_RACE})",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Only read ~/.cache/yb_race_data (no network)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cache; always hit the network",
    )
    args = parser.parse_args()

    fetch_kwargs = {
        "use_cache": not args.no_cache,
        "cache_only": args.cache_only,
    }

    print(f"Loading {args.race_key} from {SERVER} ...")
    print("1) RaceSetup JSON")
    setup = load_race_setup(args.race_key, **fetch_kwargs)
    print("2) AllPositions3 binary")
    trackers = load_positions(args.race_key, **fetch_kwargs)
    summarize(args.race_key, setup, trackers)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
