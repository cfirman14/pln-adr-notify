"""SQLite storage shared by the operator dashboard and the customer page."""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    substation    TEXT,
    feeder        TEXT,
    detected_at   TEXT,      -- AMR interval that triggered the event
    loading       REAL,
    feeder_target_kw REAL,
    event_start   TEXT,
    duration_h    REAL,
    status        TEXT       -- ITER1_OPEN, ITER2_OPEN, CONFIRMED, SETTLED
);
CREATE TABLE IF NOT EXISTS requests (
    token         TEXT PRIMARY KEY,
    event_id      TEXT REFERENCES events(event_id),
    iteration     INTEGER,
    customer_id   TEXT,
    customer_name TEXT,
    email         TEXT,
    target_kw     REAL,
    duration_h    REAL,
    est_incentive_idr REAL,
    max_penalty_idr   REAL,
    sent_at       TEXT,
    deadline      TEXT,
    response      TEXT DEFAULT 'PENDING',  -- PENDING, ACCEPT_100, ACCEPT_80, DECLINED, AUTO_APPROVED (iter 1), NO_RESPONSE (iter 2)
    responded_at  TEXT,
    delivered_kw  REAL,
    incentive_idr REAL,
    penalty_idr   REAL
);
"""


@contextmanager
def connect(path=None):
    con = sqlite3.connect(path or config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]
