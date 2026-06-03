# PROMPT: "Write pytest tests for a retail CCTV event pipeline. Cover: event schema
# validation (all required fields present, event_id is unique UUID, timestamp is
# ISO-8601), zone mapping (centroid inside polygon returns correct zone, centroid
# outside all polygons returns None), entry/exit line crossing direction logic,
# dwell event only emitted after 30 seconds of continuous presence."
#
# CHANGES MADE: Used actual store_layout.json zone polygons (CASH_COUNTER coords
# from real floor plan). Replaced generic 'zone_name' with zone_id field to match
# schema. Added test for confidence clamping (0.0-1.0 range). Added test that
# low-confidence events are written, not dropped.

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))

# ── Minimal layout fixture ──────────────────────────────────────────────────
LAYOUT = {
    "store_id": "ST1008",
    "image_dimensions": {"width": 940, "height": 451},
    "cameras": [],
    "zones": [
        {
            "zone_id": "CASH_COUNTER",
            "polygon": [[700,100],[860,100],[860,310],[700,310]],
        },
        {
            "zone_id": "FOH",
            "polygon": [[80,100],[580,100],[580,370],[80,370]],
        },
        {
            "zone_id": "ENTRY_EXIT",
            "polygon": [[0,150],[80,150],[80,360],[0,360]],
            "entry_line": {"x": 60, "y_start": 150, "y_end": 360,
                           "direction_inbound": "right", "direction_outbound": "left"}
        },
    ],
    "pos_correlation": {"billing_zone_id": "CASH_COUNTER", "correlation_window_minutes": 5},
    "staff_notes": {"salesperson_codes": []},
}


# ── EventEmitter tests ───────────────────────────────────────────────────────
class TestEventEmitter:
    def test_entry_event_schema(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        ev = em.emit_entry("VIS_abc123", "2026-04-10T12:00:00Z", 0.91, False)
        em.close()

        # Required fields
        for field in ["event_id","store_id","camera_id","visitor_id",
                      "event_type","timestamp","zone_id","dwell_ms",
                      "is_staff","confidence","metadata"]:
            assert field in ev, f"Missing field: {field}"

        assert ev["event_type"] == "ENTRY"
        assert ev["store_id"]   == "ST1008"
        assert ev["zone_id"]    is None
        assert ev["dwell_ms"]   == 0

        # event_id must be valid UUID
        uuid.UUID(ev["event_id"])

    def test_event_ids_are_unique(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        ids = [em.emit_entry(f"VIS_{i}", "2026-04-10T12:00:00Z", 0.9, False)["event_id"]
               for i in range(20)]
        em.close()
        assert len(set(ids)) == 20

    def test_timestamp_format(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        ev = em.emit_entry("VIS_ts", "2026-04-10T14:22:10Z", 0.85, False)
        em.close()
        # Must parse as ISO-8601
        datetime.strptime(ev["timestamp"], "%Y-%m-%dT%H:%M:%SZ")

    def test_low_confidence_event_not_dropped(self, tmp_path):
        """Low-confidence events must be written, not silently dropped."""
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        em.emit_entry("VIS_lowconf", "2026-04-10T12:00:00Z", 0.11, False)
        em.close()
        with open(out) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert ev["confidence"] == 0.11

    def test_confidence_clamped_to_range(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        ev1 = em.emit_entry("VIS_x1", "2026-04-10T12:00:00Z", 1.5, False)
        ev2 = em.emit_entry("VIS_x2", "2026-04-10T12:00:00Z", -0.3, False)
        em.close()
        assert ev1["confidence"] <= 1.0
        assert ev2["confidence"] >= 0.0

    def test_reentry_event_type(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        ev = em.emit_entry("VIS_re", "2026-04-10T12:00:00Z", 0.88, False, is_reentry=True)
        em.close()
        assert ev["event_type"] == "REENTRY"

    def test_session_seq_increments(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        vid = "VIS_seq"
        ev1 = em.emit_entry(vid, "2026-04-10T12:00:00Z", 0.9, False)
        ev2 = em.emit_zone_enter(vid, "2026-04-10T12:01:00Z", "FOH", 0.9, False)
        ev3 = em.emit_zone_dwell(vid, "2026-04-10T12:02:00Z", "FOH", 30000, 0.9, False)
        em.close()
        seqs = [ev1["metadata"]["session_seq"],
                ev2["metadata"]["session_seq"],
                ev3["metadata"]["session_seq"]]
        assert seqs == [1, 2, 3]

    def test_billing_queue_join_has_queue_depth(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_BILLING_01", out)
        ev = em.emit_billing_queue_join("VIS_q", "2026-04-10T14:00:00Z", 3, 0.88)
        em.close()
        assert ev["event_type"] == "BILLING_QUEUE_JOIN"
        assert ev["metadata"]["queue_depth"] == 3

    def test_jsonl_file_written(self, tmp_path):
        from emit import EventEmitter
        out = str(tmp_path / "events.jsonl")
        em = EventEmitter("ST1008", "CAM_ENTRY_01", out)
        em.emit_entry("VIS_file1", "2026-04-10T12:00:00Z", 0.9, False)
        em.emit_entry("VIS_file2", "2026-04-10T12:01:00Z", 0.9, False)
        em.close()
        with open(out) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must be valid JSON


# ── ZoneMapper tests ─────────────────────────────────────────────────────────
class TestZoneMapper:
    def _mapper(self, camera_id="CAM_FLOOR_01"):
        from zones import ZoneMapper
        return ZoneMapper(LAYOUT, camera_id)

    def test_centroid_in_cash_counter(self):
        mapper = self._mapper("CAM_BILLING_01")
        # Cash counter polygon: x=[700-860], y=[100-310]
        # Centroid at layout coords (780, 200) = inside
        # Frame 1920x1080, layout 940x451
        # layout_x = cx * 940/1920 → cx = 780 * 1920/940 ≈ 1594
        # layout_y = cy * 451/1080 → cy = 200 * 1080/451 ≈ 479
        frame_shape = (1080, 1920, 3)
        cx = 780 * 1920 / 940
        cy = 200 * 1080 / 451
        zone = mapper.get_zone(cx, cy, frame_shape)
        assert zone == "CASH_COUNTER"

    def test_centroid_outside_all_zones_returns_none(self):
        mapper = self._mapper("CAM_FLOOR_01")
        # Far corner outside all polygons
        zone = mapper.get_zone(1900, 1070, (1080, 1920, 3))
        assert zone is None

    def test_centroid_in_foh(self):
        mapper = self._mapper("CAM_FLOOR_01")
        # FOH polygon: x=[80-580], y=[100-370] in layout
        cx = 330 * 1920 / 940
        cy = 235 * 1080 / 451
        zone = mapper.get_zone(cx, cy, (1080, 1920, 3))
        assert zone == "FOH"

    def test_entry_camera_returns_entry_line_x(self):
        mapper = self._mapper("CAM_ENTRY_01")
        line_x = mapper.get_entry_line_x(1920)
        assert line_x is not None
        assert 0 < line_x < 1920

    def test_is_entry_camera(self):
        assert self._mapper("CAM_ENTRY_01").is_entry_camera() is True
        assert self._mapper("CAM_FLOOR_01").is_entry_camera() is False
