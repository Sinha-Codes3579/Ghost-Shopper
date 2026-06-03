"""GET /stores/{store_id}/heatmap — zone visit frequency + avg dwell, normalised 0–100."""

import logging
from fastapi import APIRouter, HTTPException
from database import get_conn

router = APIRouter()
logger = logging.getLogger(__name__)

MIN_SESSIONS_FOR_CONFIDENCE = 20


@router.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT zone_id,
                   COUNT(DISTINCT visitor_id) as visit_count,
                   AVG(dwell_ms) as avg_dwell_ms
            FROM events
            WHERE store_id = ? AND is_staff = 0
              AND zone_id IS NOT NULL
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
            GROUP BY zone_id
        """, (store_id,)).fetchall()

        if not rows:
            return {"store_id": store_id, "zones": {}, "data_confidence": "LOW"}

        # Normalise visit_count to 0–100
        max_visits = max(r["visit_count"] for r in rows) or 1
        total_sessions = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM sessions WHERE store_id = ?
        """, (store_id,)).fetchone()[0]

        zones = {}
        for row in rows:
            zones[row["zone_id"]] = {
                "visits": row["visit_count"],
                "avg_dwell_ms": round(row["avg_dwell_ms"] or 0),
                "score": round(row["visit_count"] / max_visits * 100),
            }

        confidence = "HIGH" if total_sessions >= MIN_SESSIONS_FOR_CONFIDENCE else "LOW"

        return {
            "store_id": store_id,
            "zones": zones,
            "data_confidence": confidence,
            "total_sessions": total_sessions,
        }

    except Exception as e:
        logger.error(f"heatmap error store={store_id} err={e}")
        raise HTTPException(status_code=503, detail={"error": "service_unavailable", "message": str(e)})
    finally:
        conn.close()
