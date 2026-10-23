"""Bank-statement analysis for underwriting-style signals (the kind of summary a statement-
analysis vendor returns). Pure functions over parsed rows; no vendor connection."""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

_BOUNCE = re.compile(r"\b(bounce|returned|insufficient funds|nach return|ecs return|cheque return)\b", re.I)
_SALARY = re.compile(r"\b(salary|payroll|sal cr)\b", re.I)
_EMI = re.compile(r"\b(emi|loan|nach|ecs)\b", re.I)


def parse_csv(text: str) -> list[dict]:
    """Columns: date (YYYY-MM-DD), narration, debit, credit, balance."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        rows.append({
            "date": date.fromisoformat(r["date"].strip()),
            "narration": r["narration"].strip(),
            "debit": Decimal(r["debit"] or "0"), "credit": Decimal(r["credit"] or "0"),
            "balance": Decimal(r["balance"]),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def analyze(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("empty statement")
    monthly = defaultdict(lambda: {"credits": Decimal(0), "debits": Decimal(0), "closing": Decimal(0)})
    for r in rows:
        m = monthly[r["date"].strftime("%Y-%m")]
        m["credits"] += r["credit"]
        m["debits"] += r["debit"]
        m["closing"] = r["balance"]
    months = sorted(monthly)
    salary_months = {r["date"].strftime("%Y-%m") for r in rows if r["credit"] > 0 and _SALARY.search(r["narration"])}
    emi_total = sum((r["debit"] for r in rows if r["debit"] > 0 and _EMI.search(r["narration"]) and not _BOUNCE.search(r["narration"])), Decimal(0))
    avg_credit = sum((monthly[m]["credits"] for m in months), Decimal(0)) / len(months)
    return {
        "months": len(months),
        "avg_monthly_credits": str(avg_credit.quantize(Decimal("0.01"))),
        "avg_month_end_balance": str((sum((monthly[m]["closing"] for m in months), Decimal(0)) / len(months)).quantize(Decimal("0.01"))),
        "min_balance": str(min(r["balance"] for r in rows)),
        "bounce_count": sum(1 for r in rows if _BOUNCE.search(r["narration"])),
        "salary_detected": len(salary_months) >= max(2, len(months) - 1) if len(months) >= 2 else bool(salary_months),
        "salary_months": sorted(salary_months),
        "recurring_obligations_total": str(emi_total),
        "obligation_to_income": str((emi_total / (avg_credit * len(months))).quantize(Decimal("0.0001"))) if avg_credit else None,
    }
