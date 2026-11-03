"""Flask blueprint:
    POST /api/webhooks/payments                  receiver (X-Razorpay-Signature, X-Razorpay-Event-Id)
    GET  /api/webhooks/events?status=failed      delivery log
    GET  /api/webhooks/events/<event_id>         one delivery with its raw body
    POST /api/webhooks/events/<event_id>/replay  re-apply a failed event
    GET  /api/webhooks/payments/<payment_id>     current payment state built from events
The read/replay endpoints require X-Admin-Token when WEBHOOK_ADMIN_TOKEN is set (open in a local sandbox)."""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request

from . import service

bp = Blueprint("webhooks", __name__)


def _admin_denied():
    token = os.environ.get("WEBHOOK_ADMIN_TOKEN")
    if token and not hmac.compare_digest(request.headers.get("X-Admin-Token", ""), token):
        return jsonify({"ok": False, "error": "admin token required"}), 401
    return None


@bp.post("/api/webhooks/payments")
def receive():
    body, code = service.receive(request.get_data(), request.headers)  # raw bytes, never re-serialised
    return jsonify(body), code


@bp.get("/api/webhooks/events")
def events():
    if (d := _admin_denied()):
        return d
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return jsonify({"ok": False, "error": "limit must be an integer"}), 400
    return jsonify(service.list_events(request.args.get("status"), limit))


@bp.get("/api/webhooks/events/<event_id>")
def event(event_id):
    if (d := _admin_denied()):
        return d
    ev = service.get_event(event_id)
    return (jsonify(ev), 200) if ev else (jsonify({"ok": False, "error": "unknown event id"}), 404)


@bp.post("/api/webhooks/events/<event_id>/replay")
def replay(event_id):
    if (d := _admin_denied()):
        return d
    body, code = service.replay(event_id)
    return jsonify(body), code


@bp.get("/api/webhooks/payments/<payment_id>")
def payment(payment_id):
    if (d := _admin_denied()):
        return d
    p = service.get_payment(payment_id)
    return (jsonify(p), 200) if p else (jsonify({"ok": False, "error": "unknown payment id"}), 404)
