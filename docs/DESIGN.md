# DESIGN.md — Store Intelligence System

## Overview

This system converts raw CCTV footage from Brigade Bangalore (ST1008) into live retail analytics. The architecture follows an event-sourcing pattern: every meaningful customer behaviour becomes a structured event, and all analytics are derived from that event stream.

## System Architecture

```
CCTV Clips (6 clips, 3 camera angles)
         │
         ▼
┌─────────────────────────────────┐
│      Detection Layer            │
│  YOLOv8n → ByteTrack → Shapely  │
│  OSNet Re-ID → EventEmitter     │
│  Output: events.jsonl           │
└──────────────┬──────────────────┘
               │ POST /events/ingest
               ▼
┌─────────────────────────────────┐
│      Intelligence API           │
│  FastAPI + SQLite               │
│  ├── Ingestion (dedup, validate)│
│  ├── Sessions engine            │
│  ├── POS correlation            │
│  └── Metrics / Funnel / Heatmap │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│      Live Dashboard             │
│  Rich terminal — polls API      │
│  Simulated real-time replay     │
└─────────────────────────────────┘
```

## Detection Layer

**Camera mapping (Brigade Road store):**
- `CAM_ENTRY_01` — left wall curved entrance. Entry/exit line at x=60px in frame.
- `CAM_FLOOR_01` — main floor, covers FOH, Makeup Unit, Nail Unit, Fragrance zones.
- `CAM_BILLING_01` — right side, covers Cash Counter and PMU.

**Zone mapping:** Zone polygons derived from the Brigade Road floor plan. Each zone is a pixel-coordinate polygon. At runtime, a visitor's bounding box centroid is tested against each polygon using `shapely.geometry.Point.within(Polygon)`.

**Visitor identity:** ByteTrack assigns `track_id` per frame. On each ENTRY event, a new UUID `visitor_id` is generated and `track_id → visitor_id` is recorded. The track_id is ephemeral; visitor_id persists for the session.

**Re-entry detection:** On EXIT, the visitor's appearance embedding (OSNet feature vector) is saved with a 10-minute TTL. On the next ENTRY from the same camera direction, cosine similarity is computed against the buffer. If similarity > 0.75 within the TTL, a REENTRY event is emitted reusing the original visitor_id.

**Staff detection:** A `staff_embedding_bank` is built from manually labelled staff frames (5–10 examples per person). Any detected person with cosine similarity > 0.80 to any staff embedding is flagged `is_staff=True` and excluded from all customer metrics.

## Event Stream

Events are emitted as JSONL (one JSON object per line) conforming to the schema in `pipeline/emit.py`. Event sourcing was chosen over snapshot-based logging because:
- Events are replayable — the full session can be reconstructed from scratch
- Anomaly detection requires temporal ordering of events
- Debugging is tractable — you can trace exactly what happened and when

## Intelligence API

**Storage:** SQLite with WAL mode. Sufficient for single-store analytics; would need PostgreSQL at 40+ stores with concurrent writes.

**Session table:** The `sessions` table is a materialised view of each visitor's journey, updated incrementally on every ENTRY/EXIT/ZONE_ENTER event. This avoids recomputing session state from raw events on every metrics request.

**POS correlation:** At startup, all 24 POS transactions from `pos_transactions.csv` are loaded. Conversion is detected when a visitor enters `CASH_COUNTER` and a POS transaction timestamp falls within the following 5-minute window for the same store.

**Conversion rate formula:**
```
conversion_rate = converted_sessions / unique_customer_sessions
```
Staff sessions are excluded. Re-entries are counted as one visitor.

## AI-Assisted Decisions

### 1. Zone polygon coordinate strategy
When I asked an LLM whether to use normalised (0–1) or pixel coordinates for zone polygons, it suggested normalised coordinates for resolution independence. I overrode this: pixel coordinates tied to the actual floor plan image (940×451px) are more debuggable. When something goes wrong with zone detection, I can open the image and visually verify which polygon a centroid falls into. Normalised coordinates add an indirection step that makes debugging harder at development time.

### 2. Staff detection approach
I asked an LLM to compare: (a) training a custom classifier, (b) using a VLM like GPT-4V for zero-shot classification, (c) embedding-based similarity. The LLM initially recommended the VLM approach as "most flexible." I disagreed: VLMs are slow (not suitable for per-frame inference), expensive in production, and non-deterministic. Embedding-based similarity using OSNet is fast, free, deterministic, and explainable in follow-up questions. I chose (c).

### 3. SQLite vs PostgreSQL
The LLM suggested PostgreSQL for "production readiness." I chose SQLite for this challenge because: docker compose up is simpler with no separate database container, SQLite in WAL mode handles the read-heavy analytics workload well for a single store, and the challenge evaluators run this on a laptop — SQLite has zero setup friction. At 40 stores, I would migrate to PostgreSQL with per-store partitioning. I documented this trade-off in CHOICES.md.
