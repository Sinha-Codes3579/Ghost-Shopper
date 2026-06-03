"""SQLite setup and POS data loader.WAL mode for concurrent reads. Graceful degradation if DB unreachable."""

import logging
import os
import sqlite3
from datetime import datetime

import pandas as pd

logger = logging.getLogger("api.database")

DB_PATH = os.getenv("DB_PATH", "./store_intelligence.db")
POS_CSV = os.getenv("POS_CSV_PATH", "./data/pos_transactions.csv")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            event_id     TEXT PRIMARY KEY,
            store_id     TEXT NOT NULL,
            camera_id    TEXT NOT NULL,
            visitor_id   TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            timestamp    TEXT NOT NULL,
            zone_id      TEXT,
            dwell_ms     INTEGER DEFAULT 0,
            is_staff     INTEGER DEFAULT 0,
            confidence   REAL,
            queue_depth  INTEGER,
            sku_zone     TEXT,
            session_seq  INTEGER DEFAULT 0,
            ingested_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            visitor_id          TEXT NOT NULL,
            store_id            TEXT NOT NULL,
            entry_time          TEXT,
            exit_time           TEXT,
            billing_enter_time  TEXT,
            billing_exit_time   TEXT,
            converted           INTEGER DEFAULT 0,
            is_reentry          INTEGER DEFAULT 0,
            PRIMARY KEY (visitor_id, store_id)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id  TEXT PRIMARY KEY,
            store_id        TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            basket_value    REAL DEFAULT 0,
            invoice_number  TEXT,
            customer_number TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_ev_store_ts   ON events(store_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_ev_visitor    ON events(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_ev_type       ON events(store_id, event_type);
        CREATE INDEX IF NOT EXISTS idx_sess_store    ON sessions(store_id);
        CREATE INDEX IF NOT EXISTS idx_txn_store_ts  ON transactions(store_id, timestamp);
    """)
    conn.commit()
    conn.close()
    logger.info(f"DB initialised at {DB_PATH}")


def load_pos_data():
    if not os.path.exists(POS_CSV):
        logger.warning(f"POS CSV not found at {POS_CSV} — skipping")
        return

    df = pd.read_csv(POS_CSV)
    required = {"invoice_number", "order_time", "order_date", "total_amount", "store_id"}
    if not required.issubset(df.columns):
        logger.warning(f"POS CSV missing columns: {required - set(df.columns)}")
        return

    txn_df = df.groupby("invoice_number").agg(
        order_time      = ("order_time", "first"),
        order_date      = ("order_date", "first"),
        total_amount    = ("total_amount", "sum"),
        store_id        = ("store_id", "first"),
        customer_number = ("customer_number", "first"),
        order_id        = ("order_id", "first"),
    ).reset_index()

    conn = get_conn()
    inserted = 0
    for _, row in txn_df.iterrows():
        try:
            ts = datetime.strptime(
                f"{row['order_date']} {row['order_time']}", "%d-%m-%Y %H:%M:%S"
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            ts = str(row["order_time"])

        conn.execute("""
            INSERT OR IGNORE INTO transactions
                (transaction_id, store_id, timestamp, basket_value, invoice_number, customer_number)
            VALUES (?,?,?,?,?,?)
        """, (
            str(row["order_id"]), str(row["store_id"]), ts,
            float(row["total_amount"]), str(row["invoice_number"]),
            str(row.get("customer_number", "")),
        ))
        inserted += 1

    conn.commit()
    conn.close()
    logger.info(f"Loaded {inserted} POS transactions from {POS_CSV}")
