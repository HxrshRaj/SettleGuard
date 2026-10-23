"""UPI payment intent links and a collect-request state machine (sandbox PSP)."""
from __future__ import annotations

import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, quote, urlparse

from .identity import validate_vpa

MAX_AMOUNT = Decimal("100000")  # per-transaction cap
COLLECT_TTL_SECONDS = 600


def build_intent(payee_vpa: str, payee_name: str, amount: str, note: str = "", txn_ref: str = "") -> str:
    if not validate_vpa(payee_vpa)["valid"]:
        raise ValueError("invalid payee VPA")
    amt = _amount(amount)
    q = f"pa={quote(payee_vpa, safe='@')}&pn={quote(payee_name)}&am={amt}&cu=INR"
    if note:
        q += f"&tn={quote(note)}"
    if txn_ref:
        q += f"&tr={quote(txn_ref)}"
    return "upi://pay?" + q


def parse_intent(uri: str) -> dict:
    u = urlparse(uri)
    if u.scheme != "upi" or u.netloc != "pay":
        raise ValueError("not a upi://pay link")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if "pa" not in q or "am" not in q:
        raise ValueError("missing pa or am")
    return q


def _amount(value) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("amount is not a number")
    if d <= 0 or d > MAX_AMOUNT or d != d.quantize(Decimal("0.01")):
        raise ValueError("amount must be > 0, <= 100000 and have at most 2 decimals")
    return d.quantize(Decimal("0.01"))


# Sandbox PSP rules: the paise value decides the outcome, so tests can force each path.
_FAIL_BY_PAISE = {"01": "U16_RISK_REJECT", "02": "U30_DEBIT_FAILED", "03": "ZM_INVALID_PIN"}

_TRANSITIONS = {"PENDING": {"SUCCESS", "FAILED", "EXPIRED"}, "SUCCESS": set(), "FAILED": set(), "EXPIRED": set()}


class CollectService:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}
        self._by_key: dict[str, str] = {}

    def create(self, payer_vpa: str, payee_vpa: str, amount: str, idem_key: str | None = None) -> tuple[dict, bool]:
        for vpa in (payer_vpa, payee_vpa):
            if not validate_vpa(vpa)["valid"]:
                raise ValueError(f"invalid VPA: {vpa}")
        if payer_vpa.lower() == payee_vpa.lower():
            raise ValueError("payer and payee must differ")
        amt = _amount(amount)
        with self._lock:
            if idem_key and idem_key in self._by_key:
                return dict(self._by_id[self._by_key[idem_key]]), False
            cid = "UPI" + uuid.uuid4().hex[:16].upper()
            rec = {"collect_id": cid, "payer": payer_vpa, "payee": payee_vpa, "amount": str(amt),
                   "status": "PENDING", "failure_code": None, "created_at": self._clock()}
            self._by_id[cid] = rec
            if idem_key:
                self._by_key[idem_key] = cid
            return dict(rec), True

    def get(self, cid: str) -> dict | None:
        with self._lock:
            rec = self._by_id.get(cid)
            if rec and rec["status"] == "PENDING" and self._clock() - rec["created_at"] > COLLECT_TTL_SECONDS:
                rec["status"] = "EXPIRED"
            return dict(rec) if rec else None

    def approve(self, cid: str) -> dict:
        """Payer approves in the sandbox PSP; the paise digits pick success or a failure code."""
        rec = self.get(cid)
        if rec is None:
            raise KeyError(cid)
        paise = rec["amount"].split(".")[1]
        if paise in _FAIL_BY_PAISE:
            return self._move(cid, "FAILED", _FAIL_BY_PAISE[paise])
        return self._move(cid, "SUCCESS")

    def _move(self, cid: str, to: str, failure_code: str | None = None) -> dict:
        with self._lock:
            rec = self._by_id[cid]
            if to not in _TRANSITIONS[rec["status"]]:
                raise ValueError(f"illegal transition {rec['status']} -> {to}")
            rec["status"], rec["failure_code"] = to, failure_code
            return dict(rec)
