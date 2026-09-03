"""Persistent state for SettleGuard, backed by a single stdlib-sqlite3 file.

Holds the current set of discrepancies plus any AI triage notes and human
resolutions. Re-running reconciliation refreshes the detection fields but
never clobbers a triage note or a resolution that already exists.
"""
import os
import sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "state.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS discrepancies (
    discrepancy_id          TEXT PRIMARY KEY,
    type                    TEXT NOT NULL,
    txn_id                  TEXT,
    merchant_id             TEXT,
    merchant_name           TEXT,
    platform_amount         REAL,
    bank_amount             REAL,
    amount_delta            REAL,
    expected_settlement_at  TEXT,
    settled_at              TEXT,
    delay_hours             REAL,
    detail                  TEXT,
    severity_hint           TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    first_seen              TEXT,
    last_seen               TEXT,
    triage_root_cause       TEXT,
    triage_severity         TEXT,
    triage_next_action      TEXT,
    triage_model            TEXT,
    triage_generated_at     TEXT,
    resolved                INTEGER NOT NULL DEFAULT 0,
    resolution_notes        TEXT,
    resolved_at             TEXT
);
"""

_DETECTION_FIELDS = (
    "type", "txn_id", "merchant_id", "merchant_name", "platform_amount",
    "bank_amount", "amount_delta", "expected_settlement_at", "settled_at",
    "delay_hours", "detail", "severity_hint",
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(_SCHEMA)
    return db


def init():
    _connect().close()


def sync_discrepancies(found):
    """Upsert the freshly reconciled list; deactivate anything not seen this run.

    Returns (new_count, updated_count, deactivated_count).
    """
    now = _now()
    db = _connect()
    try:
        existing = {r["discrepancy_id"]: r for r in db.execute(
            "SELECT * FROM discrepancies")}
        seen = set()
        new_count = updated = 0
        for d in found:
            did = d["discrepancy_id"]
            seen.add(did)
            if did in existing:
                sets = ", ".join(f"{f} = :{f}" for f in _DETECTION_FIELDS)
                db.execute(
                    f"UPDATE discrepancies SET {sets}, active = 1, last_seen = :last_seen "
                    f"WHERE discrepancy_id = :discrepancy_id",
                    {**d, "last_seen": now},
                )
                updated += 1
            else:
                cols = ["discrepancy_id", *_DETECTION_FIELDS, "active",
                        "first_seen", "last_seen"]
                db.execute(
                    f"INSERT INTO discrepancies ({', '.join(cols)}) VALUES "
                    f"({', '.join(':' + c for c in cols)})",
                    {**d, "active": 1, "first_seen": now, "last_seen": now},
                )
                new_count += 1

        stale = [did for did in existing if did not in seen and existing[did]["active"]]
        if stale:
            db.executemany(
                "UPDATE discrepancies SET active = 0, last_seen = ? WHERE discrepancy_id = ?",
                [(now, did) for did in stale],
            )
        db.commit()
        return new_count, updated, len(stale)
    finally:
        db.close()


def list_discrepancies(active_only=True):
    db = _connect()
    try:
        q = "SELECT * FROM discrepancies"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY resolved ASC, type ASC, txn_id ASC"
        return [dict(r) for r in db.execute(q)]
    finally:
        db.close()


def get(discrepancy_id):
    db = _connect()
    try:
        row = db.execute(
            "SELECT * FROM discrepancies WHERE discrepancy_id = ?", (discrepancy_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def pending_triage(active_only=True):
    return [d for d in list_discrepancies(active_only)
            if not d.get("triage_root_cause")]


def save_triage(discrepancy_id, triage, model):
    db = _connect()
    try:
        db.execute(
            "UPDATE discrepancies SET triage_root_cause = ?, triage_severity = ?, "
            "triage_next_action = ?, triage_model = ?, triage_generated_at = ? "
            "WHERE discrepancy_id = ?",
            (triage.get("root_cause"), triage.get("severity"),
             triage.get("next_action"), model, _now(), discrepancy_id),
        )
        db.commit()
    finally:
        db.close()


def resolve(discrepancy_id, notes):
    db = _connect()
    try:
        cur = db.execute(
            "UPDATE discrepancies SET resolved = 1, resolution_notes = ?, resolved_at = ? "
            "WHERE discrepancy_id = ?",
            (notes, _now(), discrepancy_id),
        )
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def reopen(discrepancy_id):
    db = _connect()
    try:
        db.execute(
            "UPDATE discrepancies SET resolved = 0, resolution_notes = NULL, "
            "resolved_at = NULL WHERE discrepancy_id = ?", (discrepancy_id,))
        db.commit()
    finally:
        db.close()


def summary():
    rows = list_discrepancies(active_only=True)
    by_type, by_sev = {}, {}
    resolved = 0
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        sev = r.get("triage_severity") or r.get("severity_hint") or "unknown"
        by_sev[sev] = by_sev.get(sev, 0) + 1
        resolved += 1 if r["resolved"] else 0
    return {
        "total": len(rows),
        "resolved": resolved,
        "open": len(rows) - resolved,
        "by_type": by_type,
        "by_severity": by_sev,
        "triaged": sum(1 for r in rows if r.get("triage_root_cause")),
    }
