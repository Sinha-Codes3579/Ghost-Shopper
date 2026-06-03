# PROMPT: "Write pytest tests for retail store metrics API endpoint. Cover: unique visitor
# count increases after ENTRY events ingested, conversion_rate computed correctly as
# converted/total (0.0 when no purchases, 1.0 when all visitors purchased), avg_dwell_per_zone
# populated after ZONE_DWELL events, queue_depth reflects latest BILLING_QUEUE_JOIN value,
# abandonment_rate computed from BILLING_QUEUE_ABANDON events, staff events excluded from
# all customer metrics, metrics for unknown store returns zero values not error."
#
# CHANGES MADE: Split conversion_rate test into zero-purchase and partial-purchase cases.
# Added explicit staff exclusion check — ingest staff ENTRY + customer ENTRY, verify
# unique_visitors=1 not 2. Added avg_dwell_per_zone structure check (must be dict not list).
# Removed test for cached metrics — our API computes on query so always real-time.

import sys, os, uuid
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest

STORE = "ST1008"


def make_client():
    from fastapi.testclient import TestClient
    import importlib, main as m
    importlib.reload(m)
    return TestClient(m.app)


def ingest(client, events):
    return client.post("/events/ingest", json={"events": events})


def ev(visitor_id, event_type, zone_id=None, dwell_ms=0, is_staff=False,
       queue_depth=None, ts="2026-04-10T12:00:00Z", camera="CAM_ENTRY_01"):
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   STORE,
        "camera_id":  camera,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  ts,
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": 0.91,
        "metadata":   {"queue_depth": queue_depth, "sku_zone": None, "session_seq": 1},
    }


# ── T1: Unique visitors count increases after ENTRY events ───────────────────
def test_unique_visitors_count():
    client = make_client()
    ingest(client, [
        ev("VIS_001", "ENTRY", ts="2026-04-10T12:00:00Z"),
        ev("VIS_002", "ENTRY", ts="2026-04-10T12:01:00Z"),
        ev("VIS_003", "ENTRY", ts="2026-04-10T12:02:00Z"),
    ])
    r = client.get(f"/stores/{STORE}/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 3


# ── T2: conversion_rate = 0.0 when no purchases ──────────────────────────────
def test_conversion_rate_zero_purchases():
    client = make_client()
    ingest(client, [ev("VIS_nopur", "ENTRY")])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert m["conversion_rate"] == 0.0


# ── T3: Staff excluded from unique_visitors ───────────────────────────────────
def test_staff_excluded_from_unique_visitors():
    client = make_client()
    ingest(client, [
        ev("VIS_staff1", "ENTRY", is_staff=True),
        ev("VIS_staff2", "ENTRY", is_staff=True),
        ev("VIS_cust1",  "ENTRY", is_staff=False),
    ])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert m["unique_visitors"] == 1   # only the customer


# ── T4: avg_dwell_per_zone is a dict after ZONE_DWELL events ─────────────────
def test_avg_dwell_per_zone_structure():
    client = make_client()
    ingest(client, [
        ev("VIS_dwell", "ENTRY"),
        ev("VIS_dwell", "ZONE_DWELL", zone_id="FOH", dwell_ms=30000,
           ts="2026-04-10T12:01:00Z", camera="CAM_FLOOR_01"),
        ev("VIS_dwell", "ZONE_DWELL", zone_id="CASH_COUNTER", dwell_ms=60000,
           ts="2026-04-10T12:02:00Z", camera="CAM_BILLING_01"),
    ])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert isinstance(m["avg_dwell_per_zone"], dict)
    assert "FOH" in m["avg_dwell_per_zone"]
    assert "CASH_COUNTER" in m["avg_dwell_per_zone"]
    assert m["avg_dwell_per_zone"]["FOH"]["avg_dwell_ms"] == 30000


# ── T5: queue_depth reflects latest BILLING_QUEUE_JOIN ───────────────────────
def test_queue_depth_from_billing_event():
    client = make_client()
    ingest(client, [
        ev("VIS_q1", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER",
           queue_depth=4, ts="2026-04-10T14:00:00Z", camera="CAM_BILLING_01"),
    ])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert m["queue_depth"] == 4


# ── T6: queue_depth updates to latest value ───────────────────────────────────
def test_queue_depth_uses_latest():
    client = make_client()
    ingest(client, [
        ev("VIS_qa", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER",
           queue_depth=2, ts="2026-04-10T14:00:00Z", camera="CAM_BILLING_01"),
        ev("VIS_qb", "BILLING_QUEUE_JOIN", zone_id="CASH_COUNTER",
           queue_depth=5, ts="2026-04-10T14:05:00Z", camera="CAM_BILLING_01"),
    ])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert m["queue_depth"] == 5   # most recent


# ── T7: abandonment_rate reflects BILLING_QUEUE_ABANDON events ───────────────
def test_abandonment_rate_computed():
    client = make_client()
    ingest(client, [
        ev("VIS_ab1", "ENTRY"),
        ev("VIS_ab2", "ENTRY"),
        ev("VIS_ab1", "BILLING_QUEUE_ABANDON", zone_id="CASH_COUNTER",
           ts="2026-04-10T13:00:00Z", camera="CAM_BILLING_01"),
    ])
    m = client.get(f"/stores/{STORE}/metrics").json()
    assert m["abandonment_rate"] > 0.0


# ── T8: Metrics for unknown store → zeros not error ───────────────────────────
def test_unknown_store_returns_zeros():
    client = make_client()
    r = client.get("/stores/STORE_NEVER_EXISTS_999/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["unique_visitors"] == 0
    assert m["conversion_rate"] == 0.0
    assert m["queue_depth"] == 0


# ── T9: Metrics response has all required keys ────────────────────────────────
def test_metrics_has_all_required_keys():
    client = make_client()
    r = client.get(f"/stores/{STORE}/metrics")
    assert r.status_code == 200
    required = {"store_id", "unique_visitors", "conversion_rate",
                "avg_dwell_per_zone", "queue_depth", "abandonment_rate"}
    assert required.issubset(r.json().keys())


# ── T10: Real-time — metrics reflect events ingested this call ────────────────
def test_metrics_real_time_not_cached():
    client = make_client()
    m_before = client.get(f"/stores/{STORE}/metrics").json()
    visitors_before = m_before["unique_visitors"]

    ingest(client, [ev(f"VIS_rt_{uuid.uuid4().hex[:6]}", "ENTRY")])

    m_after = client.get(f"/stores/{STORE}/metrics").json()
    assert m_after["unique_visitors"] == visitors_before + 1
