"""Run one subject through many vendors concurrently and merge the results.

Behaviours that matter in production and are covered by tests:
  * per-vendor timeout, so one slow vendor cannot stall the whole check
  * retries with exponential backoff and jitter for transient errors; permanent errors are not retried
  * a circuit breaker per vendor: after N consecutive failures it opens and calls are skipped until a
    cool-down passes, then one trial call is allowed (half-open)
  * partial results: the check still completes if some vendors fail, and says so (``degraded``)
  * a single normalised result model regardless of each vendor's native response shape
"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from dataclasses import dataclass, field
from typing import Callable

from .vendors import Finding, Vendor, VendorError, VendorRejected


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            if self.clock() - self.opened_at >= self.cooldown_s:
                return True  # half-open: let one trial through
            return False

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.failures, self.opened_at = 0, None
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = self.clock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self.clock() - self.opened_at >= self.cooldown_s else "open"


@dataclass
class VendorOutcome:
    vendor: str
    status: str                  # ok | error | timeout | circuit_open | rejected
    attempts: int = 0
    latency_ms: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None


@dataclass
class ScreeningResult:
    risk_score: int
    risk_level: str
    degraded: bool
    findings: list[Finding]
    vendors: list[VendorOutcome]


def risk_level(score: int) -> str:
    return "high" if score >= 70 else "medium" if score >= 35 else "low"


class Orchestrator:
    def __init__(self, vendors: list[Vendor], timeout_s: float = 2.0, max_attempts: int = 3,
                 base_backoff_s: float = 0.2, sleep: Callable[[float], None] = time.sleep,
                 breaker_threshold: int = 3, breaker_cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.vendors = vendors
        self.timeout_s, self.max_attempts, self.base = timeout_s, max_attempts, base_backoff_s
        self.sleep = sleep
        self.breakers = {v.name: CircuitBreaker(breaker_threshold, breaker_cooldown_s, clock) for v in vendors}
        self._pool = ThreadPoolExecutor(max_workers=max(4, len(vendors)))

    def _call(self, vendor: Vendor, subject: dict) -> VendorOutcome:
        br = self.breakers[vendor.name]
        if not br.allow():
            return VendorOutcome(vendor.name, "circuit_open", error="circuit breaker open")
        start = time.perf_counter()
        last_err = None
        for attempt in range(1, self.max_attempts + 1):
            fut = self._pool.submit(vendor.check, subject)
            try:
                findings = fut.result(timeout=self.timeout_s)
                br.record(True)
                return VendorOutcome(vendor.name, "ok", attempt, (time.perf_counter() - start) * 1000, findings)
            except FutTimeout:
                last_err, status = "timed out", "timeout"
            except VendorRejected as e:
                br.record(False)
                return VendorOutcome(vendor.name, "rejected", attempt, (time.perf_counter() - start) * 1000, error=str(e))
            except VendorError as e:
                last_err, status = str(e), "error"
            if attempt < self.max_attempts:
                self.sleep(self.base * (2 ** (attempt - 1)) * (0.5 + random.random() / 2))
        br.record(False)
        return VendorOutcome(vendor.name, status, self.max_attempts, (time.perf_counter() - start) * 1000, error=last_err)

    def screen(self, subject: dict) -> ScreeningResult:
        # Fan out: each vendor runs its own retry loop in its own thread.
        with ThreadPoolExecutor(max_workers=len(self.vendors)) as ex:
            outcomes = list(ex.map(lambda v: self._call(v, subject), self.vendors))
        weights = {v.name: v.weight for v in self.vendors}
        findings = [f for o in outcomes for f in o.findings]
        # Noisy-OR combination: independent signals raise risk without ever exceeding 100.
        p_clear = 1.0
        for f in findings:
            p_clear *= 1.0 - min(1.0, f.severity * weights[f.vendor])
        score = round((1.0 - p_clear) * 100)
        return ScreeningResult(score, risk_level(score), any(o.status != "ok" for o in outcomes), findings, outcomes)
