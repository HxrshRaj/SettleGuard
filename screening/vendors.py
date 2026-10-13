"""Simulated third-party compliance vendors with *different* response schemas.

Real vendors (sanctions, identity, adverse media) each return their own shape. The orchestrator must map
them onto one internal Finding model; that mapping lives in each adapter's ``normalise``. These vendors are
local simulations (no network calls) with controllable faults so failure handling can be tested.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from . import matcher

HERE = os.path.dirname(os.path.abspath(__file__))


def load_watchlist(path: str | None = None) -> list[dict]:
    with open(path or os.path.join(HERE, "watchlist.json"), encoding="utf-8") as f:
        return json.load(f)["entries"]


@dataclass
class Finding:
    vendor: str
    kind: str          # sanctions | pep | adverse_media | identity
    severity: float    # 0..1 contribution before weighting
    summary: str
    reference: str | None = None


class VendorError(Exception):
    """Transient vendor failure (HTTP 5xx / timeout equivalent). Retried by the orchestrator."""


class VendorRejected(Exception):
    """Permanent failure (bad request). Not retried."""


@dataclass
class Vendor:
    name: str
    weight: float
    fail_first: int = 0          # raise VendorError this many times, then succeed
    always_fail: bool = False
    delay_s: float = 0.0
    calls: int = field(default=0, init=False)

    def _maybe_fail(self):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.always_fail or self.calls <= self.fail_first:
            raise VendorError(f"{self.name}: upstream unavailable")

    def check(self, subject: dict) -> list[Finding]:  # pragma: no cover - interface
        raise NotImplementedError


class SanctionsVendor(Vendor):
    """Returns {"hits": [{"list_id", "list", "matched", "match_pct"}]}: percentages, not 0-1 scores."""

    def __init__(self, watchlist: list[dict] | None = None, **kw):
        super().__init__(name="sanctions-api", weight=1.0, **kw)
        self.watchlist = watchlist if watchlist is not None else load_watchlist()

    def raw(self, subject: dict) -> dict:
        self._maybe_fail()
        ms = matcher.screen(subject["name"], self.watchlist, threshold=0.88, country=subject.get("country"))
        return {"hits": [{"list_id": m.entry_id, "list": m.list_name, "matched": m.matched_name,
                          "match_pct": round(m.score * 100, 1)} for m in ms]}

    def normalise(self, raw: dict) -> list[Finding]:
        out = []
        for h in raw["hits"]:
            kind = "pep" if "PEP" in h["list"] else "adverse_media" if "MEDIA" in h["list"] else "sanctions"
            sev = {"sanctions": 1.0, "pep": 0.6, "adverse_media": 0.5}[kind] * (h["match_pct"] / 100)
            out.append(Finding(self.name, kind, round(sev, 3),
                               f'{h["match_pct"]}% name match to "{h["matched"]}" on {h["list"]}', h["list_id"]))
        return out

    def check(self, subject: dict) -> list[Finding]:
        return self.normalise(self.raw(subject))


_MEDIA = {
    "northgate": ["Customs probe into shell importers named in port records"],
    "morozov": ["Regional paper links director to unlicensed fund transfers"],
}


class AdverseMediaVendor(Vendor):
    """Returns {"articles": [{"headline", "tone"}]} where tone is -1..1 (negative = adverse)."""

    def __init__(self, **kw):
        super().__init__(name="adverse-media", weight=0.7, **kw)

    def raw(self, subject: dict) -> dict:
        self._maybe_fail()
        toks = set(matcher.normalise(subject["name"]))
        arts = [{"headline": h, "tone": -0.8} for k, hs in _MEDIA.items() if k in toks for h in hs]
        return {"articles": arts}

    def normalise(self, raw: dict) -> list[Finding]:
        return [Finding(self.name, "adverse_media", round(min(1.0, -a["tone"]) * 0.6, 3), a["headline"])
                for a in raw["articles"] if a["tone"] < -0.3]

    def check(self, subject: dict) -> list[Finding]:
        return self.normalise(self.raw(subject))


_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class IdentityVendor(Vendor):
    """Returns {"verdict": "verified|mismatch|invalid_format", "confidence": 0..1} for a tax-ID style number."""

    def __init__(self, **kw):
        super().__init__(name="identity-check", weight=0.5, **kw)

    def raw(self, subject: dict) -> dict:
        self._maybe_fail()
        ident = (subject.get("id_number") or "").strip().upper()
        if not ident:
            return {"verdict": "not_provided", "confidence": 0.0}
        if not _PAN.match(ident):
            return {"verdict": "invalid_format", "confidence": 0.99}
        return {"verdict": "verified", "confidence": 0.9}

    def normalise(self, raw: dict) -> list[Finding]:
        if raw["verdict"] == "invalid_format":
            return [Finding(self.name, "identity", 0.5, "ID number format is invalid")]
        if raw["verdict"] == "mismatch":
            return [Finding(self.name, "identity", 0.8, "ID does not match the subject")]
        return []

    def check(self, subject: dict) -> list[Finding]:
        return self.normalise(self.raw(subject))
