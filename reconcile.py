"""Reconciliation engine.

Loads both datasets into an in-memory SQLite database and uses SQL joins to
match platform settlements against bank ledger credits, flagging every
discrepancy. All thresholds come from config/rules.yaml (read fresh on each
call) -- nothing about tolerance or timing is hardcoded here.
"""
import csv
import os
import sqlite3

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
CONFIG_PATH = os.path.join(HERE, "config", "rules.yaml")

DISCREPANCY_TYPES = (
    "AMOUNT_MISMATCH",
    "MISSING_IN_BANK",
    "MISSING_IN_PLATFORM",
    "DUPLICATE_IN_BANK",
    "TIMING_MISMATCH",
)


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _build_db(platform_rows, bank_rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE platform_settlements (
            txn_id TEXT, merchant_id TEXT, merchant_name TEXT,
            amount_inr REAL, expected_settlement_at TEXT, status TEXT
        );
        CREATE TABLE bank_ledger (
            ledger_id TEXT, txn_id TEXT, amount_inr REAL, settled_at TEXT
        );
        """
    )
    db.executemany(
        "INSERT INTO platform_settlements VALUES (:txn_id,:merchant_id,:merchant_name,"
        ":amount_inr,:expected_settlement_at,:status)",
        platform_rows,
    )
    db.executemany(
        "INSERT INTO bank_ledger VALUES (:ledger_id,:txn_id,:amount_inr,:settled_at)",
        bank_rows,
    )
    db.commit()
    return db


def _hours_between(later_iso, earlier_iso):
    """Signed hours: (later - earlier). SQLite julianday handles the parsing."""
    return round((_jd(later_iso) - _jd(earlier_iso)) * 24.0, 2)


def _jd(iso_str):
    # local helper mirrored by SQL below; kept for clarity in Python-side maths
    from datetime import datetime

    return datetime.fromisoformat(iso_str).timestamp() / 86400.0


def reconcile(config=None):
    """Return a list of discrepancy dicts. Pure function of the CSVs + config."""
    config = config or load_config()
    m = config["matching"]
    tol = float(m["amount_tolerance_inr"])
    late_h = float(m["late_threshold_hours"])
    hints = config.get("severity_hints", {})

    platform_rows = _load_csv(os.path.join(DATA_DIR, "platform_settlements.csv"))
    bank_rows = _load_csv(os.path.join(DATA_DIR, "bank_ledger.csv"))
    db = _build_db(platform_rows, bank_rows)

    out = []

    # --- duplicates: same txn_id credited more than once in the bank ledger ---
    dup_txns = set()
    for r in db.execute(
        """
        SELECT txn_id, COUNT(*) AS n, SUM(amount_inr) AS total,
               GROUP_CONCAT(ledger_id) AS ledger_ids
        FROM bank_ledger
        GROUP BY txn_id HAVING COUNT(*) > 1
        """
    ):
        dup_txns.add(r["txn_id"])
        p = db.execute(
            "SELECT * FROM platform_settlements WHERE txn_id = ?", (r["txn_id"],)
        ).fetchone()
        out.append(_mk(
            "DUPLICATE_IN_BANK", r["txn_id"], p,
            platform_amount=p["amount_inr"] if p else None,
            bank_amount=r["total"],
            amount_delta=round((r["total"] - (p["amount_inr"] if p else 0)), 2),
            detail=(f"{r['n']} bank credits for one settlement "
                    f"({r['ledger_ids']}); bank total {_inr(r['total'])} vs "
                    f"platform {_inr(p['amount_inr']) if p else 'n/a'}"),
            hints=hints,
        ))

    # --- missing in bank: platform settled it, nothing landed ---
    for r in db.execute(
        """
        SELECT p.* FROM platform_settlements p
        LEFT JOIN bank_ledger b ON b.txn_id = p.txn_id
        WHERE b.ledger_id IS NULL
        """
    ):
        out.append(_mk(
            "MISSING_IN_BANK", r["txn_id"], r,
            platform_amount=r["amount_inr"], bank_amount=None,
            amount_delta=-r["amount_inr"],
            detail=(f"Platform reports {_inr(r['amount_inr'])} settled to "
                    f"{r['merchant_name']} on {r['expected_settlement_at']}, "
                    f"but no bank credit exists."),
            hints=hints,
        ))

    # --- missing in platform: money landed with no settlement record ---
    for r in db.execute(
        """
        SELECT b.* FROM bank_ledger b
        LEFT JOIN platform_settlements p ON p.txn_id = b.txn_id
        WHERE p.txn_id IS NULL
        """
    ):
        out.append(_mk(
            "MISSING_IN_PLATFORM", r["txn_id"], None,
            platform_amount=None, bank_amount=r["amount_inr"],
            amount_delta=r["amount_inr"],
            detail=(f"Bank credit {r['ledger_id']} for {_inr(r['amount_inr'])} on "
                    f"{r['settled_at']} has no matching platform settlement."),
            hints=hints,
        ))

    # --- 1:1 matches: check amount tolerance and timing (skip duplicates) ---
    for r in db.execute(
        """
        SELECT p.txn_id, p.merchant_id, p.merchant_name,
               p.amount_inr  AS platform_amount,
               b.amount_inr  AS bank_amount,
               p.expected_settlement_at, b.settled_at,
               (julianday(b.settled_at) - julianday(p.expected_settlement_at)) * 24.0
                   AS delay_hours
        FROM platform_settlements p
        JOIN bank_ledger b ON b.txn_id = p.txn_id
        """
    ):
        if r["txn_id"] in dup_txns:
            continue
        delta = round(r["bank_amount"] - r["platform_amount"], 2)
        delay = round(r["delay_hours"], 2)

        if abs(delta) > tol:
            out.append(_mk(
                "AMOUNT_MISMATCH", r["txn_id"], r,
                platform_amount=r["platform_amount"], bank_amount=r["bank_amount"],
                amount_delta=delta, delay_hours=delay,
                detail=(f"Platform {_inr(r['platform_amount'])} vs bank "
                        f"{_inr(r['bank_amount'])} -> {_signed_inr(delta)} "
                        f"({'short' if delta < 0 else 'over'}payment)."),
                hints=hints,
            ))
        elif delay > late_h:
            out.append(_mk(
                "TIMING_MISMATCH", r["txn_id"], r,
                platform_amount=r["platform_amount"], bank_amount=r["bank_amount"],
                amount_delta=delta, delay_hours=delay,
                detail=(f"Amount matches, but settled {delay:.1f}h after the "
                        f"expected time (threshold {late_h:.0f}h)."),
                hints=hints,
            ))

    db.close()
    out.sort(key=lambda d: (d["type"], d["txn_id"]))
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _inr(x):
    return "n/a" if x is None else f"₹{x:,.2f}"


def _signed_inr(x):
    return ("+" if x >= 0 else "-") + _inr(abs(x))


def _severity_hint(dtype, amount_delta, delay_hours, hints):
    hi = float(hints.get("amount_delta_high_inr", 1e12))
    med = float(hints.get("amount_delta_medium_inr", 1e12))
    delay_hi = float(hints.get("delay_hours_high", 1e12))
    ad = abs(amount_delta or 0)
    if dtype in ("MISSING_IN_BANK", "MISSING_IN_PLATFORM", "DUPLICATE_IN_BANK"):
        return "high" if ad >= hi else "medium"
    if dtype == "AMOUNT_MISMATCH":
        if ad >= hi:
            return "high"
        return "medium" if ad >= med else "low"
    if dtype == "TIMING_MISMATCH":
        return "high" if (delay_hours or 0) >= delay_hi else "medium"
    return "medium"


def _mk(dtype, txn_id, prow, *, platform_amount, bank_amount, amount_delta,
        detail, hints, delay_hours=None):
    merchant_id = prow["merchant_id"] if prow and "merchant_id" in prow.keys() else None
    merchant_name = prow["merchant_name"] if prow and "merchant_name" in prow.keys() else None
    expected = prow["expected_settlement_at"] if prow and "expected_settlement_at" in prow.keys() else None
    return {
        "discrepancy_id": f"{dtype}:{txn_id}",
        "type": dtype,
        "txn_id": txn_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "platform_amount": _round(platform_amount),
        "bank_amount": _round(bank_amount),
        "amount_delta": _round(amount_delta),
        "expected_settlement_at": expected,
        "settled_at": None,
        "delay_hours": _round(delay_hours),
        "detail": detail,
        "severity_hint": _severity_hint(dtype, amount_delta, delay_hours, hints),
    }


def _round(x):
    return None if x is None else round(float(x), 2)


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = load_config()
    results = reconcile(cfg)
    print(f"config: tolerance ₹{cfg['matching']['amount_tolerance_inr']}, "
          f"late threshold {cfg['matching']['late_threshold_hours']}h")
    print(f"{len(results)} discrepancies:\n")
    for d in results:
        print(f"  [{d['severity_hint']:>6}] {d['type']:<20} {d['txn_id']:<9} "
              f"{d['merchant_name'] or '-'}")
        print(f"           {d['detail']}")
