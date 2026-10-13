# payments-core

A small, polyglot payments backend built to practise the way a payments platform is usually split up. Each language does the job it is commonly used for. It lives inside SettleGuard so its settlement output can feed the reconciliation tool.

| Part | Language | What it does | Tests |
|---|---|---|---|
| `ledger/` | Go (stdlib only) | Double-entry ledger HTTP API: charges, refunds, balances, event CSV export, signed webhooks with retries | `go test -race`: invariants, concurrency, httptest |
| `ruby-sdk/` | Ruby (stdlib only) | Client library: typed errors, retries that reuse the idempotency key, webhook signature verification | minitest: stub TCP server + real-server integration |
| `settlement/` | Scala 3 | Pure-functional settlement: fees, refunds, reserve and payout per merchant per UTC day | Own harness: unit + randomized invariants |
| `e2e.sh` | bash | Ruby drives Go, Scala settles the exported events, totals must equal Go's balances | run in CI |

## Design notes

**Money.** Amounts are `int64` minor units everywhere (Go `int64`, Ruby `Integer`, Scala `Long`). There is no floating point. Scala uses `Math.multiplyExact`, so an overflow throws instead of wrapping.

**Ledger.** Every charge posts two entries (`customer_funds` debit, `payable:<merchant>` credit) in one transaction. `Verify()` checks that each transaction and the whole journal sum to zero. Refunds are checked against the remaining refundable amount under one mutex, and a 50-goroutine test asserts the cap holds.

**Idempotency.** `POST` endpoints require an `Idempotency-Key`. The same key and body replays the stored response with `Idempotent-Replayed: true`. The same key with a different body returns `422`. Concurrent duplicates wait for the first request instead of executing twice, which a 20-goroutine test checks.

**Webhooks.** The `Ledger-Signature: t=<unix>,v1=<hex>` header is HMAC-SHA256 over `"<t>.<body>"`. The signature is recomputed for each retry. Delivery retries with exponential backoff, does not retry 4xx (except 429) and gives up after `MaxAttempts`. The Go and Ruby tests assert the same HMAC test vector, computed separately with Python.

**Ruby SDK.** A POST is retried only if it carries an idempotency key, and the key is generated once and reused across retries, so a lost response cannot double-charge. `Webhook.construct_event` compares signatures in constant time and rejects stale timestamps (replay protection). Several `v1` values are accepted so secrets can be rotated.

**Scala settlement.** `Engine.settle` is a pure fold. Fee = `round_half_up(amount × bps / 10000) + fixed` per charge. Fees are not refunded. The reserve is held only from positive net. Property tests (fixed seed) check that the result does not depend on event order, that money is conserved, and that splitting the stream and merging gives the same answer as one pass. A deliberate rounding mutation is caught by the tests.

## Run it

```bash
cd ledger && go test -race ./... && go run ./cmd/ledgerd -addr :8090
cd ruby-sdk && LEDGERD_BIN=/path/to/built/ledgerd rake test
./e2e.sh        # needs go, ruby, java 17+, and a Scala 3.3 compiler (SCALA_HOME)
```

## Honest limits

- The ledger is **in-memory**: data is lost on restart. This is a design exercise, not a production store. There is no Postgres layer.
- There is no real card network, no authentication on the API and no real Stripe integration. The fee numbers are illustrative.
- Webhook endpoints are accepted for any http(s) URL, so a production system would need SSRF protection.
- The Scala part compiles with plain `scalac` and a hand-written test harness, because Maven Central was not reachable from the build environment where I wrote it. Moving it to sbt with munit would be the natural next step.
- The Ruby SDK uses only the standard library, and there is no Rails or Sorbet.
