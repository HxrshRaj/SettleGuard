#!/usr/bin/env python3
"""Send a signed test webhook to a SettleGuard server, or break one on purpose to see how it fails.

  WEBHOOK_SECRET=whsec_demo python scripts/send_webhook.py --event payment.captured
  python scripts/send_webhook.py --event payment.captured --event-id evt_1 --repeat 2     # duplicate delivery
  python scripts/send_webhook.py --event payment.captured --tamper                        # body changed after signing -> 401
  python scripts/send_webhook.py --event payment.captured --show-curl                     # print the equivalent cURL
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def build(event: str, payment_id: str, amount: int, ts: int) -> dict:
    if event == "refund.processed":
        ent = {"id": "rfnd_" + uuid.uuid4().hex[:8], "payment_id": payment_id, "amount": amount}
        return {"entity": "event", "event": event, "created_at": ts, "payload": {"refund": {"entity": ent}}}
    ent = {"id": payment_id, "order_id": "order_demo", "amount": amount, "currency": "INR", "method": "upi"}
    if event == "payment.failed":
        ent.update(error_code="BAD_REQUEST_ERROR", error_description="Payment failed at the bank")
    return {"entity": "event", "event": event, "created_at": ts, "payload": {"payment": {"entity": ent}}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:5000/api/webhooks/payments")
    ap.add_argument("--secret", default=os.environ.get("WEBHOOK_SECRET", ""))
    ap.add_argument("--event", default="payment.captured",
                    choices=["payment.authorized", "payment.captured", "payment.failed", "refund.processed", "order.paid"])
    ap.add_argument("--payment-id", default="pay_demo")
    ap.add_argument("--amount", type=int, default=50000, help="in paise")
    ap.add_argument("--event-id", default=None)
    ap.add_argument("--repeat", type=int, default=1, help="send the same event this many times")
    ap.add_argument("--tamper", action="store_true", help="change the body after signing")
    ap.add_argument("--show-curl", action="store_true")
    a = ap.parse_args()
    if not a.secret:
        print("set WEBHOOK_SECRET or pass --secret", file=sys.stderr)
        return 2
    body = json.dumps(build(a.event, a.payment_id, a.amount, int(time.time())), separators=(",", ":")).encode()
    sig = hmac.new(a.secret.encode(), body, hashlib.sha256).hexdigest()
    if a.tamper:
        body = body.replace(str(a.amount).encode(), str(a.amount + 1).encode(), 1)
    event_id = a.event_id or "evt_" + uuid.uuid4().hex[:12]
    if a.show_curl:
        print(f"curl -i -X POST '{a.url}' \\\n  -H 'Content-Type: application/json' \\\n"
              f"  -H 'X-Razorpay-Event-Id: {event_id}' \\\n  -H 'X-Razorpay-Signature: {sig}' \\\n"
              f"  --data-binary '{body.decode()}'")
    code = 0
    for i in range(a.repeat):
        req = urllib.request.Request(a.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Razorpay-Event-Id": event_id, "X-Razorpay-Signature": sig})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                status, text = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode()
        print(f"[{i + 1}/{a.repeat}] {event_id} -> HTTP {status} {text.strip()}")
        code = code or (0 if status < 400 else 1)
    return code


if __name__ == "__main__":
    sys.exit(main())
