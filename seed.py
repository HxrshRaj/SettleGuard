"""Generate two synthetic datasets for SettleGuard.

  data/platform_settlements.csv  -- what the payment platform says it paid each merchant
  data/bank_ledger.csv           -- what actually landed in the bank

The data is deterministic (fixed seed) and contains a deliberate, known set of
discrepancies so the reconciliation engine has real problems to find:

  - 2 amount mismatches (one short payout, one overpayment)
  - 1 sub-tolerance rounding difference  -> must NOT be flagged
  - 3 settlements missing from the bank ledger
  - 2 duplicated bank credits
  - 2 late settlements (40h and 90h late)  -> flagged
  - 1 near-miss late settlement (20h late)  -> must NOT be flagged
  - 1 bank credit with no matching platform settlement
"""
import csv
import os
import random
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

MERCHANTS = [
    ("MER01", "Chai Point Foods"),
    ("MER02", "Blinkit Sellers Co"),
    ("MER03", "Urban Threads Retail"),
    ("MER04", "Nimbus Books"),
    ("MER05", "Kettle & Co"),
    ("MER06", "Peak Cycles"),
]

BASE = datetime(2026, 8, 25, 9, 0, 0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def build():
    rng = random.Random(42)
    platform_rows = []
    bank_rows = []
    ledger_seq = 1

    def add_ledger(txn_id, amount, settled_at):
        nonlocal ledger_seq
        bank_rows.append({
            "ledger_id": f"LDG{ledger_seq:04d}",
            "txn_id": txn_id,
            "amount_inr": f"{amount:.2f}",
            "settled_at": iso(settled_at),
        })
        ledger_seq += 1

    # 40 platform settlements, TXN0001..TXN0040
    for i in range(1, 41):
        txn_id = f"TXN{i:04d}"
        merchant_id, merchant_name = MERCHANTS[i % len(MERCHANTS)]
        amount = round(rng.uniform(1500, 95000), 2)
        expected = BASE + timedelta(hours=i * 6 + rng.randint(0, 5))
        platform_rows.append({
            "txn_id": txn_id,
            "merchant_id": merchant_id,
            "merchant_name": merchant_name,
            "amount_inr": f"{amount:.2f}",
            "expected_settlement_at": iso(expected),
            "status": "settled",
        })

        # Default: bank credit matches exactly, lands a few hours after expected.
        bank_amount = amount
        bank_time = expected + timedelta(hours=rng.randint(1, 8))
        emit = True
        duplicate = False

        if txn_id == "TXN0007":                 # amount mismatch: platform paid more than landed
            bank_amount = amount - 250.00
        elif txn_id == "TXN0014":               # amount mismatch: bank shows more than platform claims
            bank_amount = amount + 1200.00
        elif txn_id == "TXN0021":               # sub-tolerance rounding diff -> not a discrepancy
            bank_amount = amount - 0.50
        elif txn_id in ("TXN0009", "TXN0018", "TXN0032"):   # never arrived
            emit = False
        elif txn_id in ("TXN0025", "TXN0036"):  # duplicate credit
            duplicate = True
        elif txn_id == "TXN0011":               # 40h late
            bank_time = expected + timedelta(hours=40)
        elif txn_id == "TXN0028":               # 90h late
            bank_time = expected + timedelta(hours=90)
        elif txn_id == "TXN0004":               # 20h late -> within threshold, not flagged
            bank_time = expected + timedelta(hours=20)

        if emit:
            add_ledger(txn_id, bank_amount, bank_time)
            if duplicate:
                add_ledger(txn_id, bank_amount, bank_time + timedelta(minutes=90))

    # Bank credit with no matching platform settlement (unexpected money).
    add_ledger("TXN9001", 4820.00, BASE + timedelta(hours=52))

    os.makedirs(DATA_DIR, exist_ok=True)
    _write_csv(os.path.join(DATA_DIR, "platform_settlements.csv"), platform_rows,
               ["txn_id", "merchant_id", "merchant_name", "amount_inr",
                "expected_settlement_at", "status"])
    _write_csv(os.path.join(DATA_DIR, "bank_ledger.csv"), bank_rows,
               ["ledger_id", "txn_id", "amount_inr", "settled_at"])
    return len(platform_rows), len(bank_rows)


def _write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    p, b = build()
    print(f"wrote {p} platform settlements and {b} bank ledger rows to {DATA_DIR}")
