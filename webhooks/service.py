"""Webhook processing: verify, deduplicate by event id, apply to payment state, keep a delivery log.

Response contract (what a gateway sees, and why):
    2xx  processed, ignored (unhandled event type) or duplicate  -> gateway stops retrying
    400  malformed request (no event id, body is not JSON)       -> retrying will not help
    401  signature missing or wrong                              -> retrying will not help
    413  body too large
    500  we failed while applying the event                      -> gateway should retry
    503  receiver has no secret configured                       -> refuse rather than accept unsigned data
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

import store
from . import signature

MAX_BODY_BYTES = 1_000_000
HANDLED = {"payment.authorized", "payment.captured", "payment.failed", "refund.processed"}
# A late or repeated event may never move a payment backwards.
RANK = {"created": 0, "authorized": 1, "failed": 2, "captured": 3, "partially_refunded": 4, "refunded": 5}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT,
    received_at   TEXT NOT NULL,
    status        TEXT NOT NULL,          -- processed | ignored | failed
    reason        TEXT,
    attempts      INTEGER NOT NULL DEFAULT 1,
    raw_body      TEXT NOT NULL,
    processed_at  TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    payment_id      TEXT PRIMARY KEY,
    order_id        TEXT,
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL,
    method          TEXT,
    error_code      TEXT,
    error_description TEXT,
    refunded_paise  INTEGER NOT NULL DEFAULT 0,
    last_event_id   TEXT,
    last_event_at   INTEGER,
    updated_at      TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db() -> sqlite3.Connection:
    db = store._connect()          # honours store.DB_PATH, so tests can point it at a temp file
    db.executescript(_SCHEMA)
    return db


def secret() -> str:
    return os.environ.get("WEBHOOK_SECRET", "")


class ApplyError(Exception):
    pass


def receive(raw: bytes, headers) -> tuple[dict, int]:
    key = secret()
    if not key:
        return {"ok": False, "error": "WEBHOOK_SECRET is not configured on this server"}, 503
    if len(raw) > MAX_BODY_BYTES:
        return {"ok": False, "error": "body too large"}, 413
    if not signature.verify(raw, headers.get("X-Razorpay-Signature"), key):
        return {"ok": False, "error": "invalid or missing X-Razorpay-Signature"}, 401
    event_id = (headers.get("X-Razorpay-Event-Id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "missing X-Razorpay-Event-Id header"}, 400
    try:
        event = json.loads(raw)
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError
    except ValueError:
        return {"ok": False, "error": "body is not a JSON event object with an 'event' field"}, 400
    return _process(event_id, event, raw.decode("utf-8", "replace"))


def _process(event_id: str, event: dict, raw_text: str) -> tuple[dict, int]:
    db = _db()
    try:
        db.execute("BEGIN IMMEDIATE")  # one writer at a time: two simultaneous deliveries cannot both apply
        row = db.execute("SELECT status, attempts FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if row and row["status"] in ("processed", "ignored"):
            db.rollback()
            return {"ok": True, "duplicate": True, "event_id": event_id, "status": row["status"]}, 200
        etype = event["event"]
        if row is None:
            db.execute("INSERT INTO webhook_events(event_id,event_type,received_at,status,raw_body) VALUES (?,?,?,?,?)",
                       (event_id, etype, _now(), "failed", raw_text))
        else:  # a previous attempt failed; the gateway is retrying
            db.execute("UPDATE webhook_events SET attempts=attempts+1 WHERE event_id=?", (event_id,))
        try:
            status, reason = _apply(db, event_id, event)
        except ApplyError as e:
            db.execute("UPDATE webhook_events SET status='failed', reason=? WHERE event_id=?", (str(e), event_id))
            db.commit()
            return {"ok": False, "event_id": event_id, "error": str(e), "retry": True}, 500
        db.execute("UPDATE webhook_events SET status=?, reason=?, processed_at=? WHERE event_id=?",
                   (status, reason, _now(), event_id))
        db.commit()
        return {"ok": True, "duplicate": False, "event_id": event_id, "status": status, "reason": reason}, 200
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _entity(event: dict, key: str) -> dict:
    ent = ((event.get("payload") or {}).get(key) or {}).get("entity")
    if not isinstance(ent, dict):
        raise ApplyError(f"payload.{key}.entity is missing")
    return ent


def _apply(db, event_id: str, event: dict) -> tuple[str, str | None]:
    etype = event["event"]
    if etype not in HANDLED:
        return "ignored", f"event type '{etype}' is not handled"
    created_at = event.get("created_at") if isinstance(event.get("created_at"), int) else 0
    if etype == "refund.processed":
        return _apply_refund(db, event_id, created_at, _entity(event, "refund"))
    p = _entity(event, "payment")
    for f in ("id", "amount"):
        if f not in p:
            raise ApplyError(f"payment.{f} is missing")
    if not isinstance(p["amount"], int) or p["amount"] <= 0:
        raise ApplyError("payment.amount must be a positive integer in paise")
    new = {"payment.authorized": "authorized", "payment.captured": "captured", "payment.failed": "failed"}[etype]
    cur = db.execute("SELECT * FROM payments WHERE payment_id=?", (p["id"],)).fetchone()
    if cur is None:
        db.execute("""INSERT INTO payments(payment_id,order_id,amount_paise,currency,status,method,error_code,
                      error_description,last_event_id,last_event_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (p["id"], p.get("order_id"), p["amount"], p.get("currency", "INR"), new, p.get("method"),
                    p.get("error_code"), p.get("error_description"), event_id, created_at, _now()))
        return "processed", f"payment created as {new}"
    if cur["amount_paise"] != p["amount"]:
        raise ApplyError(f"amount mismatch for {p['id']}: stored {cur['amount_paise']}, event {p['amount']}")
    if RANK[new] < RANK[cur["status"]] or (RANK[new] == RANK[cur["status"]] and created_at <= (cur["last_event_at"] or 0)):
        return "processed", f"stale event ignored: payment is already {cur['status']}"
    db.execute("""UPDATE payments SET status=?, method=COALESCE(?,method), error_code=?, error_description=?,
                  last_event_id=?, last_event_at=?, updated_at=? WHERE payment_id=?""",
               (new, p.get("method"), p.get("error_code"), p.get("error_description"), event_id, created_at,
                _now(), p["id"]))
    return "processed", f"payment moved {cur['status']} -> {new}"


def _apply_refund(db, event_id: str, created_at: int, r: dict) -> tuple[str, str | None]:
    for f in ("id", "payment_id", "amount"):
        if f not in r:
            raise ApplyError(f"refund.{f} is missing")
    cur = db.execute("SELECT * FROM payments WHERE payment_id=?", (r["payment_id"],)).fetchone()
    if cur is None:
        # Arrived before the payment event. Fail so the gateway retries; also replayable from the log.
        raise ApplyError(f"unknown payment {r['payment_id']}: refund arrived before the payment event")
    total = cur["refunded_paise"] + int(r["amount"])
    if total > cur["amount_paise"]:
        raise ApplyError("refunds exceed the captured amount")
    status = "refunded" if total == cur["amount_paise"] else "partially_refunded"
    db.execute("UPDATE payments SET refunded_paise=?, status=?, last_event_id=?, last_event_at=?, updated_at=? "
               "WHERE payment_id=?", (total, status, event_id, created_at, _now(), r["payment_id"]))
    return "processed", f"refund applied, payment is {status}"


def list_events(status: str | None = None, limit: int = 50) -> list[dict]:
    db = _db()
    try:
        q, a = "SELECT event_id,event_type,received_at,status,reason,attempts,processed_at FROM webhook_events", []
        if status:
            q += " WHERE status=?"
            a.append(status)
        q += " ORDER BY received_at DESC, rowid DESC LIMIT ?"
        a.append(max(1, min(limit, 200)))
        return [dict(r) for r in db.execute(q, a)]
    finally:
        db.close()


def get_event(event_id: str) -> dict | None:
    db = _db()
    try:
        r = db.execute("SELECT * FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def get_payment(payment_id: str) -> dict | None:
    db = _db()
    try:
        r = db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def replay(event_id: str) -> tuple[dict, int]:
    """Re-apply a stored event (for example one that failed because it arrived out of order)."""
    ev = get_event(event_id)
    if ev is None:
        return {"ok": False, "error": "unknown event id"}, 404
    if ev["status"] != "failed":
        return {"ok": False, "error": f"event is '{ev['status']}', only failed events can be replayed"}, 409
    return _process(event_id, json.loads(ev["raw_body"]), ev["raw_body"])
