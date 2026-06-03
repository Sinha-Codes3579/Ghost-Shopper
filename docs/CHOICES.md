# CHOICES.md — Technical Decision Log

## Decision 1: Detection Model — YOLOv8n + ByteTrack

### Options considered
- **YOLOv9** — higher mAP than YOLOv8 on COCO benchmarks, but slower inference and less mature ByteTrack integration.
- **RT-DETR** — end-to-end transformer-based detector, no NMS step. Accurate but heavy (requires GPU, slower on CPU fallback).
- **MediaPipe Pose** — fast, designed for single-person tracking. Not suitable for multi-person retail CCTV.
- **YOLOv8n (chosen)** — smallest YOLOv8 variant, fastest inference, ByteTrack built directly into the `ultralytics` library (`.track()` method), massive community support, and sufficient accuracy for the 1080p retail footage in this challenge.

### What AI suggested
An LLM suggested RT-DETR for its "superior accuracy and end-to-end design." This is valid in principle but ignores practical constraints: this challenge runs on a laptop without guaranteed GPU access, and RT-DETR's inference time would make processing 6×20-minute clips feasible but slow. ByteTrack integration with RT-DETR also requires more custom code.

### What I chose and why
**YOLOv8n + ByteTrack.** The challenge scoring rewards system correctness and documentation over detection mAP. A system that reliably detects ~85% of people and correctly handles re-entry, group entry, and staff exclusion will outscore one that squeezes out 2% extra mAP but has brittle tracking logic. ByteTrack specifically was chosen because it handles partial occlusion by tracking low-confidence detections — critical for the billing queue scenario.

---

## Decision 2: Event Schema Design

### Options considered
- **Session snapshots** — emit a full session summary on EXIT (one document per visitor with all their zone visits). Simple to store, but cannot support real-time anomaly detection or live queue monitoring.
- **Event sourcing (chosen)** — emit one event per meaningful state change (ENTRY, ZONE_ENTER, ZONE_DWELL, etc.). More events, but fully replayable and supports streaming analytics.
- **Aggregated counters** — just emit zone visit counts and timestamps, no per-visitor identity. Simpler, but cannot support funnel analysis or re-entry detection.

### What AI suggested
The LLM initially suggested session snapshots as "simpler and more compact." Simpler to store, yes — but the challenge explicitly requires real-time anomaly detection (BILLING_QUEUE_SPIKE requires knowing current queue depth, not a post-session summary) and a conversion funnel (requires tracking each stage of each session in order). Session snapshots cannot support these.

### What I chose and why
**Event sourcing.** Every visitor state transition becomes an immutable event with a UUID, timestamp, and confidence score. The `sessions` table is a materialised view derived from these events — updated incrementally as events arrive. This gives both real-time metrics (query sessions table) and full auditability (replay from events table). The trade-off is storage volume, which is acceptable at single-store scale.

The event schema was designed to satisfy every analytics query in the challenge:
- `event_type` + `visitor_id` + `timestamp` → funnel reconstruction
- `zone_id` + `dwell_ms` → heatmap computation
- `queue_depth` in metadata → BILLING_QUEUE_SPIKE anomaly
- `is_staff` → staff exclusion from all customer metrics
- `confidence` → kept even for low-confidence detections (flagged, not dropped)

---

## Decision 3: API Architecture — Compute on Query, Not on Ingest

### Options considered
- **Compute all metrics on ingest** — pre-aggregate visitor counts, conversion rates, heatmap scores whenever events arrive. Very fast GET responses. But: wrong counts if events arrive out of order, hard to correct errors, and complex ingest logic.
- **Compute on query (chosen)** — store raw events and sessions, compute metrics at GET time via SQL. Slightly slower responses but always accurate and trivially correctable.
- **Hybrid: materialise sessions, compute metrics on query** — session state (billing_enter_time, converted flag) is updated incrementally on ingest. Metrics (conversion_rate, avg_dwell) are computed on query from the up-to-date sessions table.

### What AI suggested
The LLM suggested pure compute-on-ingest for "production performance." This optimises for the wrong thing at this stage: correctness and debuggability matter more than sub-millisecond GET responses for a single store. The LLM didn't account for out-of-order events (a known issue with CCTV pipelines where frames arrive slightly out of sequence).

### What I chose and why
**Hybrid approach.** The `sessions` table is materialised incrementally during ingest — this handles the stateful parts (billing enter/exit time, conversion flag) that are expensive to recompute from raw events. Metrics endpoints (conversion_rate, avg_dwell_per_zone, heatmap scores) are computed on query from the sessions and events tables.

**Why this survives the 40-store follow-up question:** At 40 stores with concurrent ingest, the first bottleneck is the `GET /stores/{id}/funnel` query joining events against sessions. The fix is pre-aggregated zone-level counts (a daily summary table populated by a background job) while keeping real-time data for the last 30-minute window. I would not change the event schema or session materialisation logic — those remain correct and unchanged.

**Why SQLite over PostgreSQL:**
SQLite with WAL mode handles this load comfortably for one store. The docker compose setup is simpler (no separate DB container, no connection strings to configure). At 40 stores with live ingest from all cameras simultaneously, I would migrate to PostgreSQL with per-store schema partitioning and read replicas for the metrics endpoints.
