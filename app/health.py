"""GET /health — service status, last event timestamp, STALE_FEED detection."""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from database import get_conn

router = APIRouter()
logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 10


@router.get("/health")
def health():
    now = datetime.now(timezone.utc)
    warnings = []

    try:
        conn = get_conn()
        stores = conn.execute("""
            SELECT store_id, MAX(ingested_at) as last_event
            FROM events GROUP BY store_id
        """).fetchall()
        conn.close()

        store_status = {}
        for row in stores:
            last_event_str = row["last_event"]
            if last_event_str:
                last_event = datetime.fromisoformat(last_event_str.replace("Z", "+00:00"))
                lag_minutes = (now - last_event).total_seconds() / 60
                stale = lag_minutes > STALE_THRESHOLD_MINUTES
                if stale:
                    warnings.append(f"STALE_FEED: {row['store_id']} — {round(lag_minutes)}m since last event")
                store_status[row["store_id"]] = {
                    "last_event": last_event_str,
                    "lag_minutes": round(lag_minutes, 1),
                    "status": "STALE_FEED" if stale else "OK",
                }

        status = "WARN" if warnings else "OK"
        return {
            "status": status,
            "checked_at": now.isoformat(),
            "stores": store_status,
            "warnings": warnings,
        }

    except Exception as e:
        logger.error(f"health check failed: {e}")
        return {
            "status": "ERROR",
            "checked_at": now.isoformat(),
            "error": str(e),
            "stores": {},
            "warnings": [],
        }
