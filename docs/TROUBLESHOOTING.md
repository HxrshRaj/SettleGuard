# Webhook troubleshooting playbook

Follow the same loop every time: **listen** (what exactly did the merchant see?), **diagnose** (find the root cause from the
response, the delivery log and the raw body), **solutionise** (fix it and prove it), **close the loop** (tell them what happened and write it down).

## First three checks for any report

1. Look at the delivery log: `GET /api/webhooks/events?status=failed` and `GET /api/webhooks/events/<id>`. Is the event there at all?
2. Read the HTTP status the gateway received (see the table in [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md)).
3. Reproduce with `python scripts/send_webhook.py ...` or the Postman collection against the same environment.

## Symptom -> cause -> fix

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| `401 invalid or missing X-Razorpay-Signature` on every delivery | Wrong secret, or the secret in the dashboard was rotated | Sign a test body locally with the secret you think is configured and compare with the header | Make the server's `WEBHOOK_SECRET` equal the dashboard secret; restart; redeliver |
| `401` only on some deliveries | Body modified before verification (framework parsed and re-serialised it, proxy changed whitespace, `echo` added a newline in a test) | Sign the stored raw body; if it matches only the original bytes, the body is being altered | Verify against the raw request bytes, before any JSON parsing |
| `401`, but the same body verifies in your own test | Header name or case, or a CDN/proxy stripping custom headers | Print the headers the server actually received | Read `X-Razorpay-Signature` exactly; allow the header through the proxy |
| `503 WEBHOOK_SECRET is not configured` | Environment variable missing in this deployment | `printenv WEBHOOK_SECRET` on the server | Set it in the host's environment settings |
| `400 missing X-Razorpay-Event-Id` | Test client or proxy dropped the header | Look at the request headers | Send the header; the id is what makes retries safe |
| `400 body is not a JSON event object` | Wrong content, form-encoded body, or double-encoded JSON | Inspect the first bytes of the body | Send the JSON event object as the raw body |
| `500 amount mismatch` | The event amount differs from the amount stored for that payment | Compare `GET /api/webhooks/payments/<id>` with the event body | Check the amount unit (paise, not rupees); investigate before replaying |
| `500 unknown payment ...: refund arrived before the payment event` | Out-of-order delivery | The log shows the refund event failed; the payment event arrives afterwards | Nothing to fix: the gateway retries, or run `POST /api/webhooks/events/<id>/replay` once the payment exists |
| `500 refunds exceed the captured amount` | Duplicate refund recorded under a different event id, or wrong amount | List events for the payment and add up refunds | Confirm the real refund total with the gateway before replaying |
| Payment shows `captured` but a `payment.failed` event is in the log | A late event for an earlier attempt | The log reason says `stale event ignored` | Expected behaviour; the state machine never moves backwards |
| Customer charged twice | Two different payment ids, or a retried API call without an idempotency key | Check whether the two events carry the same payment id | Use `Idempotency-Key` on create calls; this is not a duplicate delivery |
| No events arrive at all | Webhook not enabled for the event type, wrong URL, endpoint not reachable from the internet | Gateway dashboard delivery attempts; `curl` the URL from outside | Fix the URL or event selection; free hosting may sleep, so the first request after idle can time out |
| Events arrive late | Sleeping free-tier instance, or the gateway retry schedule | Compare `created_at` in the event with `received_at` in the log | Keep the endpoint warm or accept retries; never rely on delivery time |

## Close the loop

Reply to the merchant with: what you saw (status code, event id), the root cause in one sentence, what changed, how to verify
(a replay or a test event), and what to do if it happens again. Then add a row to the table above if the case was new.

## FAQ

**Why return `200` for a duplicate?** The gateway only needs to know the event is safely handled. A `4xx` would make it keep retrying.

**Why return `500` when I cannot apply an event?** So the gateway retries. The raw body is stored, so you can also replay it yourself.

**Can I verify the signature after parsing the JSON?** No. Verify first, on the raw bytes.

**Does the receiver trust the amount in the event?** It checks that it is a positive integer and equals the stored amount for that payment.

**Is this production ready?** No. It is a sandbox on SQLite with a single shared secret, no rate limiting, and no connection to a real gateway account.
