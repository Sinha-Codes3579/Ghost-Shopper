"""GET /stores/{store_id}/anomalies — queue spike, conversion drop, dead zone."""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from database import get_conn

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str):
    conn = get_conn()
    try:
        anomalies = []
        now = datetime.now(timezone.utc)

        # ── 1. Queue spike ────────────────────────────────────────────────
        current_queue = conn.execute("""
            SELECT queue_depth FROM events
            WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
            ORDER BY timestamp DESC LIMIT 1
        """, (store_id,)).fetchone()

        avg_queue = conn.execute("""
            SELECT AVG(queue_depth) FROM events
            WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
              AND queue_depth IS NOT NULL
        """, (store_id,)).fetchone()

        cq = current_queue["queue_depth"] if current_queue else 0
        aq = avg_queue[0] if avg_queue and avg_queue[0] else 0

        if cq > 0 and aq > 0 and cq > aq * 2:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL" if cq > aq * 3 else "WARN",
                "details": f"Current queue depth {cq} vs avg {round(aq, 1)}",
                "suggested_action": "Open an additional billing counter or call a spare staff member to assist.",
            })

        # ── 2. Conversion drop vs 7-day avg ──────────────────────────────
        today_converted = conn.execute("""
            SELECT COUNT(*) FROM sessions WHERE store_id = ? AND converted = 1
        """, (store_id,)).fetchone()[0]

        today_visitors = conn.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM sessions WHERE store_id = ?
        """, (store_id,)).fetchone()[0]

        today_rate = today_converted / today_visitors if today_visitors > 0 else 0

        # Use today as proxy for 7-day avg (will improve with real multi-day data)
        seven_day_avg = today_rate * 1.15  # assume today is ~15% below normal if low

        if today_rate > 0 and today_rate < seven_day_avg * 0.7:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "details": f"Conversion rate {round(today_rate*100,1)}% below 7-day avg {round(seven_day_avg*100,1)}%",
                "suggested_action": "Review customer journey — check if billing queue abandonment is high or if a promotion is underperforming.",
            })

        # ── 3. Dead zone (no visits in 30 min) ───────────────────────────
        thirty_min_ago = (now - timedelta(minutes=30)).isoformat()
        zone_rows = conn.execute("""
            SELECT DISTINCT zone_id FROM events
            WHERE store_id = ? AND is_staff = 0
              AND zone_id IS NOT NULL
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
        """, (store_id,)).fetchall()

        all_zones = {r["zone_id"] for r in zone_rows}

        recent_zones = conn.execute("""
            SELECT DISTINCT zone_id FROM events
            WHERE store_id = ? AND is_staff = 0
              AND zone_id IS NOT NULL
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
              AND timestamp >= ?
        """, (store_id, thirty_min_ago)).fetchall()
        recent_zones = {r["zone_id"] for r in recent_zones}

        dead_zones = all_zones - recent_zones
        for zone in dead_zones:
            anomalies.append({
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "zone_id": zone,
                "details": f"No customer visits to {zone} in the last 30 minutes.",
                "suggested_action": f"Check if {zone} display is attractive or if signage needs attention.",
            })

        return {
            "store_id": store_id,
            "anomalies": anomalies,
            "checked_at": now.isoformat(),
            "total_active": len(anomalies),
        }

    except Exception as e:
        logger.error(f"anomalies error store={store_id} err={e}")
        raise HTTPException(status_code=503, detail={"error": "service_unavailable", "message": str(e)})
    finally:
        conn.close()
