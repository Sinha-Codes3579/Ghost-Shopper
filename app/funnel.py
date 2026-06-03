"""GET /stores/{store_id}/funnel — conversion funnel, session-based."""

import logging
from fastapi import APIRouter, HTTPException
from database import get_conn

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str):
    conn = get_conn()
    try:
        # Entry: unique customer sessions (exclude staff, count REENTRY as same visitor)
        entry = conn.execute("""
            SELECT COUNT(DISTINCT e.visitor_id)
            FROM events e
            WHERE e.store_id = ? AND e.is_staff = 0
              AND e.event_type IN ('ENTRY', 'REENTRY')
        """, (store_id,)).fetchone()[0]

        # Zone visit: visited any named zone (not entry/exit)
        zone_visit = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id)
            FROM events
            WHERE store_id = ? AND is_staff = 0
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
              AND zone_id NOT IN ('ENTRY_EXIT')
              AND zone_id IS NOT NULL
        """, (store_id,)).fetchone()[0]

        # Billing: entered CASH_COUNTER zone
        billing = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id)
            FROM events
            WHERE store_id = ? AND is_staff = 0
              AND event_type = 'ZONE_ENTER'
              AND zone_id = 'CASH_COUNTER'
        """, (store_id,)).fetchone()[0]

        # Purchase: converted sessions
        purchase = conn.execute("""
            SELECT COUNT(*) FROM sessions
            WHERE store_id = ? AND converted = 1
        """, (store_id,)).fetchone()[0]

        def drop_pct(a, b):
            if a == 0:
                return 0.0
            return round((a - b) / a * 100, 1)

        return {
            "store_id": store_id,
            "funnel": {
                "entry": {"count": entry, "drop_off_pct": 0.0},
                "zone_visit": {"count": zone_visit, "drop_off_pct": drop_pct(entry, zone_visit)},
                "billing": {"count": billing, "drop_off_pct": drop_pct(zone_visit, billing)},
                "purchase": {"count": purchase, "drop_off_pct": drop_pct(billing, purchase)},
            },
            "overall_conversion_pct": round(purchase / entry * 100, 1) if entry > 0 else 0.0,
        }

    except Exception as e:
        logger.error(f"funnel error store={store_id} err={e}")
        raise HTTPException(status_code=503, detail={"error": "service_unavailable", "message": str(e)})
    finally:
        conn.close()
