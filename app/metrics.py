"""GET /stores/{store_id}/metrics — real-time store metrics."""

import logging
from fastapi import APIRouter, HTTPException
from database import get_conn

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str):
    conn = get_conn()
    try:
        # Unique customer visitors today (exclude staff)
        unique_visitors = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM sessions
            WHERE store_id = ?
        """, (store_id,)).fetchone()[0]

        # Only count sessions from events where is_staff = 0
        customer_visitors = conn.execute("""
            SELECT COUNT(DISTINCT e.visitor_id)
            FROM events e
            WHERE e.store_id = ? AND e.is_staff = 0 AND e.event_type = 'ENTRY'
        """, (store_id,)).fetchone()[0]

        # Converted sessions
        converted = conn.execute("""
            SELECT COUNT(*) FROM sessions
            WHERE store_id = ? AND converted = 1
        """, (store_id,)).fetchone()[0]

        conversion_rate = round(converted / customer_visitors, 4) if customer_visitors > 0 else 0.0

        # Avg dwell per zone (exclude staff)
        zone_rows = conn.execute("""
            SELECT zone_id, AVG(dwell_ms) as avg_dwell, COUNT(*) as visits
            FROM events
            WHERE store_id = ? AND is_staff = 0
              AND event_type IN ('ZONE_DWELL', 'ZONE_ENTER')
              AND zone_id IS NOT NULL
            GROUP BY zone_id
        """, (store_id,)).fetchall()
        avg_dwell_per_zone = {
            row["zone_id"]: {
                "avg_dwell_ms": round(row["avg_dwell"] or 0),
                "visits": row["visits"]
            }
            for row in zone_rows
        }

        # Current queue depth (most recent BILLING_QUEUE_JOIN)
        queue_row = conn.execute("""
            SELECT queue_depth FROM events
            WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
            ORDER BY timestamp DESC LIMIT 1
        """, (store_id,)).fetchone()
        queue_depth = queue_row["queue_depth"] if queue_row else 0

        # Abandonment rate
        abandoned = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id = ? AND event_type = 'BILLING_QUEUE_ABANDON'
        """, (store_id,)).fetchone()[0]
        abandonment_rate = round(abandoned / customer_visitors, 4) if customer_visitors > 0 else 0.0

        return {
            "store_id": store_id,
            "unique_visitors": customer_visitors,
            "converted_visitors": converted,
            "conversion_rate": conversion_rate,
            "avg_dwell_per_zone": avg_dwell_per_zone,
            "queue_depth": queue_depth or 0,
            "abandonment_rate": abandonment_rate,
        }

    except Exception as e:
        logger.error(f"metrics error store={store_id} err={e}")
        raise HTTPException(status_code=503, detail={"error": "service_unavailable", "message": str(e)})
    finally:
        conn.close()
