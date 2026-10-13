"""Case management: screening results that need a human decision, with an append-only audit trail."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS screening_cases (
    case_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    idem_key        TEXT UNIQUE,
    subject_name    TEXT NOT NULL,
    country         TEXT,
    subject_type    TEXT,
    risk_score      INTEGER NOT NULL,
    risk_level      TEXT NOT NULL,
    degraded        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',
    findings_json   TEXT NOT NULL,
    vendors_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER NOT NULL REFERENCES screening_cases(case_id),
    action     TEXT NOT NULL,
    actor      TEXT,
    notes      TEXT,
    at         TEXT NOT NULL
);
"""
# open -> cleared | escalated ; escalated -> cleared | open (re-review) ; cleared is terminal
_TRANSITIONS = {"open": {"cleared", "escalated"}, "escalated": {"cleared", "open"}, "cleared": set()}


class InvalidTransition(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    db = sqlite3.connect(store.DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(_SCHEMA)
    return db


def _row(r):
    d = dict(r)
    d["findings"] = json.loads(d.pop("findings_json"))
    d["vendors"] = json.loads(d.pop("vendors_json"))
    return d


def create(subject: dict, result: dict, idem_key: str | None = None) -> tuple[dict, bool]:
    """Returns (case, created). A repeated idempotency key returns the original case unchanged."""
    db = _db()
    try:
        if idem_key:
            ex = db.execute("SELECT * FROM screening_cases WHERE idem_key = ?", (idem_key,)).fetchone()
            if ex:
                return _row(ex), False
        now = _now()
        cur = db.execute(
            "INSERT INTO screening_cases (idem_key, subject_name, country, subject_type, risk_score, risk_level, "
            "degraded, findings_json, vendors_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (idem_key, subject["name"], subject.get("country"), subject.get("type"), result["risk_score"],
             result["risk_level"], int(result["degraded"]), json.dumps(result["findings"]),
             json.dumps(result["vendors"]), now, now))
        db.execute("INSERT INTO case_events (case_id, action, actor, notes, at) VALUES (?,?,?,?,?)",
                   (cur.lastrowid, "created", "system", f'risk {result["risk_score"]} ({result["risk_level"]})', now))
        db.commit()
        return _row(db.execute("SELECT * FROM screening_cases WHERE case_id = ?", (cur.lastrowid,)).fetchone()), True
    except sqlite3.IntegrityError:  # lost a race on the idempotency key
        db.rollback()
        return _row(db.execute("SELECT * FROM screening_cases WHERE idem_key = ?", (idem_key,)).fetchone()), False
    finally:
        db.close()


def by_key(idem_key: str):
    db = _db()
    try:
        r = db.execute("SELECT * FROM screening_cases WHERE idem_key = ?", (idem_key,)).fetchone()
        return _row(r) if r else None
    finally:
        db.close()


def get(case_id: int):
    db = _db()
    try:
        r = db.execute("SELECT * FROM screening_cases WHERE case_id = ?", (case_id,)).fetchone()
        if not r:
            return None
        c = _row(r)
        c["events"] = [dict(e) for e in db.execute(
            "SELECT action, actor, notes, at FROM case_events WHERE case_id = ? ORDER BY event_id", (case_id,))]
        return c
    finally:
        db.close()


def list_cases(status: str | None = None):
    db = _db()
    try:
        q, args = "SELECT * FROM screening_cases", ()
        if status:
            q, args = q + " WHERE status = ?", (status,)
        return [_row(r) for r in db.execute(q + " ORDER BY risk_score DESC, case_id", args)]
    finally:
        db.close()


def decide(case_id: int, decision: str, actor: str, notes: str):
    target = {"clear": "cleared", "escalate": "escalated", "reopen": "open"}.get(decision)
    if not target:
        raise ValueError("decision must be clear, escalate or reopen")
    if not notes.strip():
        raise ValueError("notes are required")
    db = _db()
    try:
        r = db.execute("SELECT status FROM screening_cases WHERE case_id = ?", (case_id,)).fetchone()
        if not r:
            return None
        if target not in _TRANSITIONS[r["status"]]:
            raise InvalidTransition(f'cannot go from {r["status"]} to {target}')
        now = _now()
        # Conditional update: only succeeds if the status is still what we validated, so two analysts
        # deciding at once cannot both win.
        cur = db.execute("UPDATE screening_cases SET status = ?, updated_at = ? WHERE case_id = ? AND status = ?",
                         (target, now, case_id, r["status"]))
        if cur.rowcount == 0:
            raise InvalidTransition("case changed while you were deciding; reload and retry")
        db.execute("INSERT INTO case_events (case_id, action, actor, notes, at) VALUES (?,?,?,?,?)",
                   (case_id, decision, actor or "analyst", notes.strip(), now))
        db.commit()
    finally:
        db.close()
    return get(case_id)
