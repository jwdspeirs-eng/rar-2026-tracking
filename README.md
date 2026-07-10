# Race Around the Rock 2026 — Race Control & Tracking

Tooling for RAR 2026: an ~88km clockwise coastal rowing circumnavigation of Salt Spring Island, run by Salt Spring Island Rowing.

## Contents

- `RAR_2026_Race_Control_Dashboard_v2.html` — Race control dashboard (Leaflet map, fleet quartile view, SOS/alert workflow, AIS overlay). Currently runs on **simulated fleet data** seeded along the real course GPX — layout and UX are real, but not yet wired to live tracking.
- `RAR_2026_Race_Control_Dashboard.html` — earlier version, kept for reference.
- `YELLOW_BRICK_API.md` — reference doc for the Yellow Brick (YB) tracker API: `RaceSetup` JSON endpoint and the `AllPositions3` binary position feed (format, flags, delta encoding, units).
- `RAR_clean.GPX` — actual race course.
- `nmea-sample` — sample AIS NMEA sentences (Gulf Islands / ferry traffic) used for the dashboard's AIS overlay layer.
- `RAR_2026_YB_Tracking_Meeting_Summary_2026-07-09.md` / `..._Transcript_2026-07-09.md` — notes from the July 9 sync with YB Tracking (Lee Gallacher) and Sebastien Gouin-Davis on tracker setup and data feed integration.
- `RAR_2026_Safety_Boat_Deployment_Concept_v0.3.docx`, `RAR_2026_Safety_Boat_Review.docx` — safety boat planning docs.

## Status

- Dashboard UI/UX: built, using simulated fleet data.
- YB API integration: not yet connected. Plan is to test against YB's public sample race data (`bayviewmack2024`) to prove the fetch/decode pipeline before pointing it at the live RAR feed once trackers are active.
- AIS overlay: built against a static sample feed; live source (MarineTraffic API, AISHub, etc.) still to be decided — ferries (Fulford, Vesuvius, Long Harbour) cross parts of the course.

## Next steps

- Wire up `RaceSetup` + `AllPositions3` fetch/decode per `YELLOW_BRICK_API.md`, replacing the dashboard's simulated data.
- Decide on a live AIS data source.
- Fleet sheet and tracker logistics tracked separately (see meeting notes).
