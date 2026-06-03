"""POST /events/ingestIdempotent by event_id. Partial success on malformed events. Updates sessions table incrementally on every ingest."""

import json
import logging
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from database import get_conn
from models import IngestRequest, IngestResponse, StoreEvent

router = APIRouter()
logger = logging.getLogger("api.ingestion")

BILLING_ZONE = "CASH_COUNTER"


@router.post("/events/ingest", response_model=IngestResponse)
def ingest_events(payload: IngestRequest, response: Response):
    conn = get_conn()
    accepted = rejected = duplicates = 0
    errors = []

    try:
        for i, event in enumerate(payload.events):
            try:
                _ingest_one(conn, event)
                accepted += 1
            except DuplicateEvent:
                duplicates += 1
            except Exception as e:
                rejected += 1
                errors.append({
                    "index":    i,
                    "event_id": getattr(event, "event_id", None),
                    "error":    str(e),
                })
                logger.warning(f"Rejected event index={i} err={e}")

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"Ingest transaction failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"error": "service_unavailable", "message": str(e)},
        )
    finally:
        conn.close()

    # Expose event_count in header for middleware logging
    response.headers["X-Event-Count"] = str(accepted)

    logger.info(f"ingest accepted={accepted} rejected={rejected} duplicates={duplicates}")
    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        errors=errors,
    )


class DuplicateEvent(Exception):
    pass


def _ingest_one(conn, event: StoreEvent):
    # Idempotency check
    existing = conn.execute(
        "SELECT 1 FROM events WHERE event_id=?", (event.event_id,)
    ).fetchone()
    if existing:
        raise DuplicateEvent(event.event_id)

    conn.execute("""
        INSERT INTO events
            (event_id, store_id, camera_id, visitor_id, event_type,
             timestamp, zone_id, dwell_ms, is_staff, confidence,
             queue_depth, sku_zone, session_seq)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        event.event_id,
        event.store_id,
        event.camera_id,
        event.visitor_id,
        event.event_type,
        event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        event.zone_id,
        event.dwell_ms,
        int(event.is_staff),
        event.confidence,
        event.metadata.queue_depth,
        event.metadata.sku_zone,
        event.metadata.session_seq,
    ))

    _update_session(conn, event)
    _check_conversion(conn, event)


def _update_session(conn, event: StoreEvent):
    vid = event.visitor_id
    sid = event.store_id
    ts  = event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    if event.event_type in ("ENTRY", "REENTRY"):
        conn.execute("""
            INSERT INTO sessions (visitor_id, store_id, entry_time, is_reentry)
            VALUES (?,?,?,?)
            ON CONFLICT(visitor_id, store_id) DO UPDATE SET
                entry_time = excluded.entry_time,
                is_reentry = excluded.is_reentry
        """, (vid, sid, ts, 1 if event.event_type == "REENTRY" else 0))

    elif event.event_type == "EXIT":
        conn.execute("""
            INSERT INTO sessions (visitor_id, store_id, exit_time)
            VALUES (?,?,?)
            ON CONFLICT(visitor_id, store_id) DO UPDATE SET exit_time=excluded.exit_time
        """, (vid, sid, ts))

    elif event.event_type == "ZONE_ENTER" and event.zone_id == BILLING_ZONE:
        conn.execute("""
            INSERT INTO sessions (visitor_id, store_id, billing_enter_time)
            VALUES (?,?,?)
            ON CONFLICT(visitor_id, store_id) DO UPDATE SET billing_enter_time=excluded.billing_enter_time
        """, (vid, sid, ts))

    elif event.event_type == "ZONE_EXIT" and event.zone_id == BILLING_ZONE:
        conn.execute("""
            INSERT INTO sessions (visitor_id, store_id, billing_exit_time)
            VALUES (?,?,?)
            ON CONFLICT(visitor_id, store_id) DO UPDATE SET billing_exit_time=excluded.billing_exit_time
        """, (vid, sid, ts))

    elif event.event_type == "BILLING_QUEUE_ABANDON":
        # Mark session as abandoned — not converted
        conn.execute("""
            UPDATE sessions SET converted=0 WHERE visitor_id=? AND store_id=?
        """, (vid, sid))


def _check_conversion(conn, event: StoreEvent):
    """Mark session converted if a POS txn exists within 5 min after billing entry."""
    if event.event_type not in ("ZONE_ENTER", "ZONE_DWELL"):
        return
    if event.zone_id != BILLING_ZONE:
        return
    if event.is_staff:
        return

    ts = event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    hit = conn.execute("""
        SELECT 1 FROM transactions
        WHERE store_id = ?
          AND timestamp >= ?
          AND timestamp <= datetime(?, '+5 minutes')
        LIMIT 1
    """, (event.store_id, ts, ts)).fetchone()

    if hit:
        conn.execute("""
            UPDATE sessions SET converted=1
            WHERE visitor_id=? AND store_id=?
        """, (event.visitor_id, event.store_id))
