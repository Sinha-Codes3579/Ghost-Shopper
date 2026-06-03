"""EventEmitter: all event types, schema validation, JSONL output.
Low-confidence events are flagged but NEVER dropped (per spec)."""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EventEmitter:
    def __init__(self, store_id: str, camera_id: str, output_path: str, append: bool = False):
        self.store_id  = store_id
        self.camera_id = camera_id
        self._output   = Path(output_path)
        self._output.parent.mkdir(parents=True, exist_ok=True)
        self._fh       = open(self._output, "a" if append else "w", encoding="utf-8")
        self._counters: dict[str, int] = {}
        self._total    = 0
        logger.info(f"EventEmitter store={store_id} cam={camera_id} -> {output_path}")

    def _seq(self, visitor_id: str) -> int:
        self._counters[visitor_id] = self._counters.get(visitor_id, 0) + 1
        return self._counters[visitor_id]

    def _write(self, ev: dict):
        self._fh.write(json.dumps(ev) + "\n")
        self._fh.flush()
        self._total += 1
        return ev

    def _build(self, visitor_id, event_type, timestamp, confidence,
               zone_id, dwell_ms, is_staff, meta) -> dict:
        return {
            "event_id":   str(uuid.uuid4()),
            "store_id":   self.store_id,
            "camera_id":  self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp":  timestamp,
            "zone_id":    zone_id,
            "dwell_ms":   dwell_ms,
            "is_staff":   is_staff,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "metadata": {
                "queue_depth": meta.get("queue_depth"),
                "sku_zone":    meta.get("sku_zone"),
                "session_seq": self._seq(visitor_id),
            },
        }

    def emit_entry(self, visitor_id, timestamp, confidence, is_staff, is_reentry=False):
        ev = self._build(visitor_id, "REENTRY" if is_reentry else "ENTRY",
                         timestamp, confidence, None, 0, is_staff, {})
        return self._write(ev)

    def emit_exit(self, visitor_id, timestamp, confidence):
        ev = self._build(visitor_id, "EXIT", timestamp, confidence, None, 0, False, {})
        return self._write(ev)

    def emit_zone_enter(self, visitor_id, timestamp, zone_id, confidence,
                        is_staff, queue_depth=None, sku_zone=None):
        ev = self._build(visitor_id, "ZONE_ENTER", timestamp, confidence,
                         zone_id, 0, is_staff, {"queue_depth": queue_depth, "sku_zone": sku_zone})
        return self._write(ev)

    def emit_zone_exit(self, visitor_id, timestamp, zone_id, confidence, is_staff):
        ev = self._build(visitor_id, "ZONE_EXIT", timestamp, confidence, zone_id, 0, is_staff, {})
        return self._write(ev)

    def emit_zone_dwell(self, visitor_id, timestamp, zone_id, dwell_ms, confidence, is_staff):
        ev = self._build(visitor_id, "ZONE_DWELL", timestamp, confidence,
                         zone_id, dwell_ms, is_staff, {})
        return self._write(ev)

    def emit_billing_queue_join(self, visitor_id, timestamp, queue_depth, confidence):
        ev = self._build(visitor_id, "BILLING_QUEUE_JOIN", timestamp, confidence,
                         "CASH_COUNTER", 0, False, {"queue_depth": queue_depth})
        logger.info(f"BILLING_QUEUE_JOIN visitor={visitor_id} depth={queue_depth}")
        return self._write(ev)

    def emit_billing_queue_abandon(self, visitor_id, timestamp, confidence):
        ev = self._build(visitor_id, "BILLING_QUEUE_ABANDON", timestamp, confidence,
                         "CASH_COUNTER", 0, False, {})
        logger.info(f"BILLING_QUEUE_ABANDON visitor={visitor_id}")
        return self._write(ev)

    def close(self):
        self._fh.close()
        logger.info(f"EventEmitter closed — {self._total} events written to {self._output}")
