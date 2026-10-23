"""eNACH mandate lifecycle and debit presentation (sandbox)."""
from __future__ import annotations

import threading
import time
import uuid
from decimal import Decimal

from .identity import validate_ifsc

PRE_DEBIT_NOTICE_SECONDS = 24 * 3600  # a customer must be told at least 24h before a debit
FREQUENCIES = {"MONTHLY", "QUARTERLY", "YEARLY", "AS_PRESENTED"}

_TRANSITIONS = {
    "INITIATED": {"ACTIVE", "REJECTED"},
    "ACTIVE": {"PAUSED", "CANCELLED", "EXPIRED"},
    "PAUSED": {"ACTIVE", "CANCELLED", "EXPIRED"},
    "REJECTED": set(), "CANCELLED": set(), "EXPIRED": set(),
}


class MandateError(ValueError):
    pass


class MandateService:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._m: dict[str, dict] = {}

    def create(self, ifsc: str, account_last4: str, max_amount: str, frequency: str, valid_days: int = 365) -> dict:
        if not validate_ifsc(ifsc)["valid"]:
            raise MandateError("invalid IFSC")
        if not (account_last4.isdigit() and len(account_last4) == 4):
            raise MandateError("account_last4 must be 4 digits")
        if frequency not in FREQUENCIES:
            raise MandateError(f"frequency must be one of {sorted(FREQUENCIES)}")
        cap = Decimal(str(max_amount))
        if cap <= 0:
            raise MandateError("max_amount must be positive")
        now = self._clock()
        mid = "MND" + uuid.uuid4().hex[:14].upper()
        rec = {"mandate_id": mid, "ifsc": ifsc.upper(), "account": "XXXXXX" + account_last4, "max_amount": str(cap),
               "frequency": frequency, "status": "INITIATED", "expires_at": now + valid_days * 86400, "debits": []}
        with self._lock:
            self._m[mid] = rec
            return _copy(rec)

    def get(self, mid: str) -> dict | None:
        with self._lock:
            rec = self._m.get(mid)
            if rec and rec["status"] in ("ACTIVE", "PAUSED") and self._clock() > rec["expires_at"]:
                rec["status"] = "EXPIRED"
            return _copy(rec) if rec else None

    def transition(self, mid: str, to: str) -> dict:
        self.get(mid)  # applies lazy expiry
        with self._lock:
            rec = self._m.get(mid)
            if rec is None:
                raise KeyError(mid)
            if to not in _TRANSITIONS[rec["status"]]:
                raise MandateError(f"illegal transition {rec['status']} -> {to}")
            rec["status"] = to
            return _copy(rec)

    def present_debit(self, mid: str, amount: str, notified_at: float, debit_ref: str) -> dict:
        """Present a debit. Enforces: ACTIVE mandate, amount <= cap, 24h pre-debit notice,
        and idempotency on debit_ref (re-presenting the same ref returns the original)."""
        self.get(mid)
        with self._lock:
            rec = self._m.get(mid)
            if rec is None:
                raise KeyError(mid)
            for d in rec["debits"]:
                if d["debit_ref"] == debit_ref:
                    return dict(d, replayed=True)
            now = self._clock()
            amt = Decimal(str(amount))
            reason = None
            if rec["status"] != "ACTIVE":
                reason = f"MANDATE_{rec['status']}"
            elif amt <= 0 or amt > Decimal(rec["max_amount"]):
                reason = "AMOUNT_EXCEEDS_MANDATE"
            elif now - notified_at < PRE_DEBIT_NOTICE_SECONDS:
                reason = "PRE_DEBIT_NOTICE_TOO_RECENT"
            debit = {"debit_ref": debit_ref, "amount": str(amt), "status": "REJECTED" if reason else "ACCEPTED",
                     "reason": reason, "presented_at": now}
            rec["debits"].append(debit)
            return dict(debit, replayed=False)


def _copy(rec: dict) -> dict:
    return dict(rec, debits=[dict(d) for d in rec["debits"]])
