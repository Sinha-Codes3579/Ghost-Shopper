"""
assertions.py — Self-check script to validate your API before submission.
Run this after feeding events.jsonl to verify all acceptance gate criteria.

Usage:
    python assertions.py --api http://localhost:8000 --store ST1008

All 10 assertions must pass before you submit.
"""

import sys
import json
import uuid
import argparse
from datetime import datetime

import httpx

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []


def check(name: str, condition: bool, detail: str = ""):
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    results.append(condition)
    return condition


def run(api: str, store: str):
    print(f"\n=== Store Intelligence — Assertions ===")
    print(f"API: {api}  Store: {store}")
    print(f"(Also try: python assertions.py --store STORE_BLR_002 for acceptance gate check)\n")

    # ── A1: Health endpoint responds ────────────────────────────────────────
    try:
        r = httpx.get(f"{api}/health", timeout=5)
        data = r.json()
        check("A1: /health returns 200", r.status_code == 200)
        check("A2: health.status is OK/WARN/ERROR", data.get("status") in ("OK","WARN","ERROR"))
        check("A3: health.checked_at is present", "checked_at" in data)
    except Exception as e:
        check("A1: /health returns 200", False, str(e))
        check("A2: health.status is OK/WARN/ERROR", False)
        check("A3: health.checked_at is present", False)

    # ── A4: Ingest a valid event ─────────────────────────────────────────────
    test_event = {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store,
        "camera_id":  "CAM_ENTRY_01",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:8]}",
        "event_type": "ENTRY",
        "timestamp":  "2026-04-10T12:00:00Z",
        "zone_id":    None,
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.92,
        "metadata":   {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    try:
        r = httpx.post(f"{api}/events/ingest", json={"events": [test_event]}, timeout=5)
        data = r.json()
        check("A4: POST /events/ingest returns 200", r.status_code == 200)
        check("A5: ingest response has accepted/rejected/duplicates",
              all(k in data for k in ("accepted","rejected","duplicates")))
        check("A6: valid event is accepted", data.get("accepted", 0) >= 1)
    except Exception as e:
        check("A4: POST /events/ingest returns 200", False, str(e))
        check("A5: ingest response has accepted/rejected/duplicates", False)
        check("A6: valid event is accepted", False)

    # ── A7: Idempotency — same event ingested twice ──────────────────────────
    try:
        r2 = httpx.post(f"{api}/events/ingest", json={"events": [test_event]}, timeout=5)
        d2 = r2.json()
        check("A7: duplicate event → duplicates=1 not error",
              r2.status_code == 200 and d2.get("duplicates", 0) == 1)
    except Exception as e:
        check("A7: duplicate event → duplicates=1 not error", False, str(e))

    # ── A8: Metrics endpoint ─────────────────────────────────────────────────
    try:
        r = httpx.get(f"{api}/stores/{store}/metrics", timeout=5)
        data = r.json()
        check("A8: GET /stores/{id}/metrics returns 200", r.status_code == 200)
        required = {"store_id","unique_visitors","conversion_rate","avg_dwell_per_zone",
                    "queue_depth","abandonment_rate"}
        missing = required - set(data.keys())
        check("A9: metrics has all required fields", not missing,
              f"missing: {missing}" if missing else "")
        check("A10: conversion_rate is 0.0–1.0 float",
              isinstance(data.get("conversion_rate"), (int, float))
              and 0.0 <= data.get("conversion_rate", -1) <= 1.0)
    except Exception as e:
        check("A8: GET /stores/{id}/metrics returns 200", False, str(e))
        check("A9: metrics has all required fields", False)
        check("A10: conversion_rate is 0.0–1.0 float", False)

    # ── Bonus checks ─────────────────────────────────────────────────────────
    print("\n--- Bonus checks ---")
    try:
        r = httpx.get(f"{api}/stores/{store}/funnel", timeout=5)
        data = r.json()
        check("B1: /funnel returns 200", r.status_code == 200)
        stages = data.get("funnel", {})
        check("B2: funnel has entry/zone_visit/billing/purchase",
              all(s in stages for s in ("entry","zone_visit","billing","purchase")))
    except Exception as e:
        check("B1: /funnel returns 200", False, str(e))

    try:
        r = httpx.get(f"{api}/stores/{store}/heatmap", timeout=5)
        data = r.json()
        check("B3: /heatmap returns 200 with data_confidence",
              r.status_code == 200 and "data_confidence" in data)
    except Exception as e:
        check("B3: /heatmap returns 200 with data_confidence", False, str(e))

    try:
        r = httpx.get(f"{api}/stores/{store}/anomalies", timeout=5)
        data = r.json()
        check("B4: /anomalies returns 200 with anomalies list",
              r.status_code == 200 and isinstance(data.get("anomalies"), list))
    except Exception as e:
        check("B4: /anomalies returns 200 with anomalies list", False, str(e))

    # ── Summary ──────────────────────────────────────────────────────────────
    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*40}")
    print(f"Result: {passed}/{total} checks passed")
    if passed == total:
        print("\033[92mAll assertions passed — ready to submit!\033[0m")
    else:
        failed = total - passed
        print(f"\033[91m{failed} assertion(s) failed — fix before submitting.\033[0m")
    print()
    return passed == total


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api",   default="http://localhost:8000")
    p.add_argument("--store", default="ST1008")
    args = p.parse_args()
    ok = run(args.api, args.store)
    sys.exit(0 if ok else 1)
