#!/usr/bin/env bash
# Cross-language check: Ruby SDK drives the Go ledger, Scala settles the exported events,
# and the Scala totals must agree with the Go ledger's own balances for every merchant.
set -euo pipefail
cd "$(dirname "$0")"
WORK="$(mktemp -d)"; trap 'kill "${PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT
SCALA_HOME="${SCALA_HOME:-/tmp/scala/scala3-3.3.4}"
PORT="${PORT:-18090}"

(cd ledger && go build -o "$WORK/ledgerd" ./cmd/ledgerd)
"$WORK/ledgerd" -addr "127.0.0.1:$PORT" >"$WORK/server.log" 2>&1 & PID=$!
for _ in $(seq 50); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.1; done

ruby -I ruby-sdk/lib - "$PORT" <<'RUBY'
require "ledger_sdk"
c = LedgerSDK::Client.new(base_url: "http://127.0.0.1:#{ARGV[0]}")
srand(7)
%w[acme globex initech].each do |m|
  charges = Array.new(12) { c.create_charge(merchant_id: m, amount: 1000 + rand(90_000)) }
  charges.sample(4).each { |ch| c.refund_charge(ch["id"], amount: rand(1..ch["amount"] / 2)) }
  c.create_charge(merchant_id: m, amount: 4242, idempotency_key: "dup-#{m}")
  c.create_charge(merchant_id: m, amount: 4242, idempotency_key: "dup-#{m}") # replay: must not double count
end
RUBY

curl -sf "http://127.0.0.1:$PORT/v1/events.csv" >"$WORK/events.csv"
mkdir -p "$WORK/classes"
"$SCALA_HOME/bin/scalac" -d "$WORK/classes" settlement/src/*.scala 2>&1 | grep -v JAVA_TOOL_OPTIONS || true
LIB="$SCALA_HOME/lib"
java -cp "$LIB/scala-library-2.13.14.jar:$LIB/scala3-library_3-3.3.4.jar:$WORK/classes" \
  settlement.settle "$WORK/events.csv" --totals 2>/dev/null >"$WORK/totals.csv"

fail=0
while IFS=, read -r m gross refunds; do
  avail=$(curl -sf "http://127.0.0.1:$PORT/v1/balances/$m" | sed -E 's/.*"available":(-?[0-9]+).*/\1/')
  want=$((gross - refunds))
  if [ "$avail" = "$want" ]; then echo "OK   $m: go balance=$avail  scala gross-refunds=$want"
  else echo "FAIL $m: go balance=$avail  scala gross-refunds=$want"; fail=1; fi
done <"$WORK/totals.csv"
[ "$(wc -l <"$WORK/totals.csv")" -eq 3 ] || { echo "FAIL expected 3 merchants"; fail=1; }
exit $fail
