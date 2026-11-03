import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402
from webhooks import signature  # noqa: E402

SECRET = "whsec_test_secret"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("WEBHOOK_ADMIN_TOKEN", raising=False)
    import app as app_module
    store.init()
    return app_module.app.test_client()


def payment_event(kind, pid="pay_1", amount=50000, ts=1_700_000_000, **extra):
    ent = {"id": pid, "order_id": "order_1", "amount": amount, "currency": "INR", "method": "upi", **extra}
    return {"entity": "event", "event": f"payment.{kind}", "created_at": ts, "payload": {"payment": {"entity": ent}}}


def refund_event(pid="pay_1", amount=10000, rid="rfnd_1", ts=1_700_000_100):
    return {"entity": "event", "event": "refund.processed", "created_at": ts,
            "payload": {"refund": {"entity": {"id": rid, "payment_id": pid, "amount": amount}}}}


def send(client, event, event_id, secret=SECRET, raw=None, sig=None, headers=None):
    body = raw if raw is not None else json.dumps(event).encode()
    h = {"X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"}
    if event_id is None:
        del h["X-Razorpay-Event-Id"]
    h["X-Razorpay-Signature"] = sig if sig is not None else signature.sign(body, secret)
    h.update(headers or {})
    return client.post("/api/webhooks/payments", data=body, headers=h)


# ---- signature ------------------------------------------------------------------------------ #
def test_sign_and_verify_round_trip_and_known_vector():
    body = b'{"a":1}'
    assert signature.verify(body, signature.sign(body, "k"), "k")
    # independently computed: echo -n '{"a":1}' | openssl dgst -sha256 -hmac k
    assert signature.sign(body, "k") == "c3a92ff9e274cdcce27a58c15a78ec6dcbbdbd0038a87e7a11baef2028fd8bff"
    assert not signature.verify(body, signature.sign(body, "k"), "other")
    assert not signature.verify(body, None, "k")
    assert not signature.verify(body, "", "k")


def test_whitespace_change_breaks_the_signature():
    body = b'{"a":1}'
    sig = signature.sign(body, "k")
    assert not signature.verify(b'{"a": 1}', sig, "k")  # re-serialised JSON no longer matches


# ---- receiver: rejects --------------------------------------------------------------------- #
def test_missing_secret_config_returns_503(client, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET")
    assert send(client, payment_event("captured"), "evt_1").status_code == 503


def test_bad_signature_is_401_and_nothing_is_stored(client):
    r = send(client, payment_event("captured"), "evt_1", sig="deadbeef")
    assert r.status_code == 401
    assert client.get("/api/webhooks/events").get_json() == []


def test_wrong_secret_is_401(client):
    assert send(client, payment_event("captured"), "evt_1", secret="not-the-secret").status_code == 401


def test_tampered_body_is_401(client):
    body = json.dumps(payment_event("captured", amount=50000)).encode()
    sig = signature.sign(body, SECRET)
    tampered = body.replace(b"50000", b"90000")
    assert send(client, None, "evt_1", raw=tampered, sig=sig).status_code == 401


def test_missing_event_id_is_400(client):
    assert send(client, payment_event("captured"), None).status_code == 400


def test_non_json_body_with_valid_signature_is_400(client):
    assert send(client, None, "evt_1", raw=b"not json").status_code == 400
    assert send(client, None, "evt_2", raw=b'["a list"]').status_code == 400


def test_oversized_body_is_413(client):
    assert send(client, None, "evt_1", raw=b"x" * 1_000_001).status_code == 413


# ---- receiver: processing ------------------------------------------------------------------ #
def test_payment_lifecycle_updates_state(client):
    assert send(client, payment_event("authorized", ts=1), "e1").get_json()["status"] == "processed"
    assert send(client, payment_event("captured", ts=2), "e2").status_code == 200
    p = client.get("/api/webhooks/payments/pay_1").get_json()
    assert p["status"] == "captured" and p["amount_paise"] == 50000 and p["last_event_id"] == "e2"


def test_duplicate_event_id_is_not_applied_twice(client):
    send(client, payment_event("captured"), "e1")
    send(client, refund_event(amount=20000), "r1")
    again = send(client, refund_event(amount=20000), "r1")  # gateway redelivers the same event
    assert again.status_code == 200 and again.get_json()["duplicate"] is True
    assert client.get("/api/webhooks/payments/pay_1").get_json()["refunded_paise"] == 20000


def test_late_authorized_after_captured_does_not_move_payment_back(client):
    send(client, payment_event("captured", ts=20), "e2")
    r = send(client, payment_event("authorized", ts=10), "e1")  # arrives late
    assert r.status_code == 200 and "stale" in r.get_json()["reason"]
    assert client.get("/api/webhooks/payments/pay_1").get_json()["status"] == "captured"


def test_failed_event_records_error_details(client):
    send(client, payment_event("failed", error_code="BAD_REQUEST_ERROR", error_description="Card declined"), "e1")
    p = client.get("/api/webhooks/payments/pay_1").get_json()
    assert p["status"] == "failed" and p["error_code"] == "BAD_REQUEST_ERROR"


def test_unhandled_event_type_is_acknowledged_and_ignored(client):
    ev = {"event": "order.paid", "created_at": 1, "payload": {}}
    r = send(client, ev, "e1")
    assert r.status_code == 200 and r.get_json()["status"] == "ignored"


def test_amount_mismatch_fails_and_can_be_seen_in_the_log(client):
    send(client, payment_event("authorized", amount=50000), "e1")
    r = send(client, payment_event("captured", amount=99999), "e2")
    assert r.status_code == 500 and "amount mismatch" in r.get_json()["error"]
    failed = client.get("/api/webhooks/events?status=failed").get_json()
    assert [e["event_id"] for e in failed] == ["e2"]


def test_invalid_amount_fails_with_500(client):
    assert send(client, payment_event("captured", amount=-5), "e1").status_code == 500
    assert send(client, payment_event("captured", amount="500"), "e2").status_code == 500


def test_partial_then_full_refund(client):
    send(client, payment_event("captured"), "e1")
    send(client, refund_event(amount=20000, rid="r1"), "r1")
    assert client.get("/api/webhooks/payments/pay_1").get_json()["status"] == "partially_refunded"
    send(client, refund_event(amount=30000, rid="r2"), "r2")
    p = client.get("/api/webhooks/payments/pay_1").get_json()
    assert p["status"] == "refunded" and p["refunded_paise"] == 50000


def test_over_refund_is_rejected(client):
    send(client, payment_event("captured"), "e1")
    r = send(client, refund_event(amount=60000), "r1")
    assert r.status_code == 500 and "exceed" in r.get_json()["error"]


# ---- failure, retry and replay ------------------------------------------------------------- #
def test_refund_before_payment_fails_then_replay_succeeds(client):
    r = send(client, refund_event(), "r1")
    assert r.status_code == 500 and r.get_json()["retry"] is True
    send(client, payment_event("captured"), "e1")
    rep = client.post("/api/webhooks/events/r1/replay")
    assert rep.status_code == 200 and rep.get_json()["status"] == "processed"
    ev = client.get("/api/webhooks/events/r1").get_json()
    assert ev["status"] == "processed" and ev["attempts"] == 2
    assert client.get("/api/webhooks/payments/pay_1").get_json()["refunded_paise"] == 10000


def test_gateway_retry_of_a_failed_event_is_reprocessed_not_treated_as_duplicate(client):
    assert send(client, refund_event(), "r1").status_code == 500
    send(client, payment_event("captured"), "e1")
    again = send(client, refund_event(), "r1")  # same event id, gateway retry
    assert again.status_code == 200 and again.get_json()["duplicate"] is False


def test_replay_rejects_unknown_and_already_processed_events(client):
    assert client.post("/api/webhooks/events/nope/replay").status_code == 404
    send(client, payment_event("captured"), "e1")
    assert client.post("/api/webhooks/events/e1/replay").status_code == 409


def test_concurrent_deliveries_of_one_event_apply_once(client):
    send(client, payment_event("captured", amount=50000), "e1")
    results = []

    def go():
        results.append(send(client, refund_event(amount=10000), "r1").get_json())

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if r.get("duplicate") is False) == 1
    assert client.get("/api/webhooks/payments/pay_1").get_json()["refunded_paise"] == 10000


# ---- admin endpoints ----------------------------------------------------------------------- #
def test_admin_token_protects_read_and_replay_when_set(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_ADMIN_TOKEN", "s3cret")
    send(client, payment_event("captured"), "e1")
    assert client.get("/api/webhooks/events").status_code == 401
    assert client.get("/api/webhooks/events", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/webhooks/events", headers={"X-Admin-Token": "s3cret"}).status_code == 200
    assert client.post("/api/webhooks/events/e1/replay").status_code == 401


def test_unknown_ids_are_404_and_bad_limit_is_400(client):
    assert client.get("/api/webhooks/events/nope").status_code == 404
    assert client.get("/api/webhooks/payments/nope").status_code == 404
    assert client.get("/api/webhooks/events?limit=abc").status_code == 400
