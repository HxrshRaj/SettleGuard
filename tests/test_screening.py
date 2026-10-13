import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402
from screening import cases, matcher  # noqa: E402
from screening.orchestrator import CircuitBreaker, Orchestrator  # noqa: E402
from screening.vendors import (AdverseMediaVendor, IdentityVendor, SanctionsVendor, VendorRejected,  # noqa: E402
                               load_watchlist)

WL = load_watchlist()
no_sleep = lambda s: None  # noqa: E731


# ---- matcher ---------------------------------------------------------------- #
def test_normalise_strips_accents_punctuation_and_suffixes():
    assert matcher.normalise("Müller & Söhne GmbH, Ltd.") == ["muller", "sohne"]


def test_jaro_winkler_known_values():
    assert matcher.jaro_winkler("martha", "marhta") == pytest.approx(0.9611, abs=1e-3)
    assert matcher.jaro_winkler("abc", "abc") == 1.0
    assert matcher.jaro_winkler("abc", "xyz") == 0.0


def test_soundex_groups_spelling_variants():
    assert matcher.soundex("robert") == matcher.soundex("rupert") == "R163"
    assert matcher.soundex("mohammed") == matcher.soundex("muhammad")


@pytest.mark.parametrize("query", [
    "Viktor Aleksandrovich Morozov",   # exact
    "Morozov Viktor Aleksandrovich",   # reordered
    "Victor Morozoff",                 # alias
    "Muhammad Alrashidi",              # alias with spelling variant
    "NORTHGATE TRADING CO.",           # case, punctuation, suffix
])
def test_known_bad_names_are_flagged(query):
    assert matcher.screen(query, WL), query


@pytest.mark.parametrize("query", ["Priya Nair", "Oakwood Bakery Pvt Ltd", "John Smith", "Morozov"])
def test_unrelated_or_single_token_names_are_not_flagged(query):
    assert matcher.screen(query, WL) == [], query


def test_country_mismatch_lowers_score():
    same = matcher.screen("Li Wei Zhang", WL, country="CN")[0].score
    other = matcher.screen("Li Wei Zhang", WL, country="CA")[0].score
    assert other < same


# ---- orchestrator ----------------------------------------------------------- #
def orch(vendors, **kw):
    kw.setdefault("sleep", no_sleep)
    return Orchestrator(vendors, **kw)


def test_clean_subject_scores_low_and_is_not_degraded():
    r = orch([SanctionsVendor(WL), AdverseMediaVendor(), IdentityVendor()]).screen(
        {"name": "Priya Nair", "id_number": "ABCDE1234F"})
    assert (r.risk_level, r.degraded, r.findings) == ("low", False, [])


def test_sanctioned_name_is_high_risk():
    r = orch([SanctionsVendor(WL), AdverseMediaVendor(), IdentityVendor()]).screen(
        {"name": "Viktor Morozov", "country": "RU"})
    assert r.risk_level == "high" and r.risk_score >= 70
    assert {f.kind for f in r.findings} >= {"sanctions", "adverse_media"}


def test_transient_failures_are_retried_then_succeed():
    v = SanctionsVendor(WL, fail_first=2)
    r = orch([v]).screen({"name": "Li Wei Zhang"})
    assert r.vendors[0].status == "ok" and r.vendors[0].attempts == 3 and v.calls == 3


def test_exhausted_retries_degrade_but_other_vendors_still_count():
    bad = SanctionsVendor(WL, always_fail=True)
    r = orch([bad, AdverseMediaVendor()]).screen({"name": "Northgate Trading Company"})
    by = {o.vendor: o for o in r.vendors}
    assert by["sanctions-api"].status == "error" and by["sanctions-api"].attempts == 3
    assert by["adverse-media"].status == "ok" and r.degraded and r.findings


def test_backoff_grows_exponentially_with_jitter_bounds():
    waits = []
    orch([SanctionsVendor(WL, always_fail=True)], sleep=waits.append, base_backoff_s=1.0).screen({"name": "x"})
    assert len(waits) == 2
    assert 0.5 <= waits[0] <= 1.0 and 1.0 <= waits[1] <= 2.0


def test_slow_vendor_times_out_without_blocking_the_rest():
    slow = SanctionsVendor(WL, delay_s=0.5)
    t0 = time.perf_counter()
    r = orch([slow, IdentityVendor()], timeout_s=0.05, max_attempts=1).screen({"name": "a b", "id_number": "bad"})
    assert time.perf_counter() - t0 < 0.4
    assert {o.vendor: o.status for o in r.vendors} == {"sanctions-api": "timeout", "identity-check": "ok"}


def test_vendors_run_concurrently():
    vs = [SanctionsVendor(WL, delay_s=0.2), AdverseMediaVendor(delay_s=0.2), IdentityVendor(delay_s=0.2)]
    t0 = time.perf_counter()
    orch(vs, timeout_s=2).screen({"name": "a b"})
    assert time.perf_counter() - t0 < 0.5  # serial would be 0.6s+


def test_permanent_rejection_is_not_retried():
    class Rejecting(SanctionsVendor):
        def check(self, subject):
            self.calls += 1
            raise VendorRejected("bad request")
    v = Rejecting(WL)
    r = orch([v]).screen({"name": "x"})
    assert r.vendors[0].status == "rejected" and v.calls == 1


def test_circuit_breaker_opens_skips_calls_then_half_opens():
    now = [0.0]
    v = SanctionsVendor(WL, always_fail=True)
    o = orch([v], max_attempts=1, breaker_threshold=2, breaker_cooldown_s=30, clock=lambda: now[0])
    o.screen({"name": "x"}); o.screen({"name": "x"})
    assert o.breakers["sanctions-api"].state == "open"
    calls = v.calls
    assert o.screen({"name": "x"}).vendors[0].status == "circuit_open" and v.calls == calls
    now[0] = 31
    assert o.breakers["sanctions-api"].state == "half-open"
    v.always_fail = False
    assert o.screen({"name": "x"}).vendors[0].status == "ok"
    assert o.breakers["sanctions-api"].state == "closed"


def test_risk_combination_is_bounded_and_monotonic():
    r1 = orch([SanctionsVendor(WL)]).screen({"name": "Li Wei Zhang"})
    r2 = orch([SanctionsVendor(WL), AdverseMediaVendor()]).screen({"name": "Li Wei Zhang"})
    assert 0 < r1.risk_score <= r2.risk_score <= 100


def test_vendor_response_shapes_normalise_to_one_model():
    s, a, i = SanctionsVendor(WL), AdverseMediaVendor(), IdentityVendor()
    sample = {"name": "Northgate Trading Company Limited", "id_number": "123"}
    raws = [s.raw(sample), a.raw(sample), i.raw(sample)]
    assert set(raws[0]) == {"hits"} and set(raws[1]) == {"articles"} and set(raws[2]) == {"verdict", "confidence"}
    for v, raw in zip((s, a, i), raws):
        assert all(hasattr(f, "severity") and f.vendor == v.name for f in v.normalise(raw))


# ---- cases + API ------------------------------------------------------------ #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    import app as app_module
    store.init()
    app_module.app.extensions.pop("screening_orchestrator", None)
    return app_module.app.test_client()


def test_screen_validates_input(client):
    assert client.post("/api/screen", json={}).status_code == 400
    assert client.post("/api/screen", json={"name": "x" * 201}).status_code == 400


def test_low_risk_creates_no_case_high_risk_does(client):
    low = client.post("/api/screen", json={"name": "Priya Nair", "id_number": "ABCDE1234F"}).get_json()
    assert low["risk_level"] == "low" and "case_id" not in low
    hi = client.post("/api/screen", json={"name": "Mohammed Al-Rashidi", "country": "AE"}).get_json()
    assert hi["risk_level"] in ("medium", "high") and hi["case_id"]
    assert client.get("/api/cases").get_json()[0]["case_id"] == hi["case_id"]


def test_idempotency_key_replays_without_a_second_case_or_vendor_call(client):
    h = {"Idempotency-Key": "abc-1"}
    a = client.post("/api/screen", json={"name": "Victor Morozoff"}, headers=h).get_json()
    sanctions = client.application.extensions["screening_orchestrator"].vendors[0]
    calls = sanctions.calls
    b = client.post("/api/screen", json={"name": "Victor Morozoff"}, headers=h).get_json()
    assert b["replayed"] and b["case_id"] == a["case_id"] and sanctions.calls == calls
    assert len(client.get("/api/cases").get_json()) == 1


def test_case_workflow_and_audit_trail(client):
    cid = client.post("/api/screen", json={"name": "Victor Morozoff"}).get_json()["case_id"]
    d = lambda body: client.post(f"/api/cases/{cid}/decision", json=body)  # noqa: E731
    assert d({"decision": "clear", "notes": " "}).status_code == 400
    assert d({"decision": "nonsense", "notes": "x"}).status_code == 400
    assert d({"decision": "escalate", "notes": "needs MLRO", "actor": "asha"}).status_code == 200
    assert d({"decision": "clear", "notes": "false positive, DOB differs"}).status_code == 200
    assert d({"decision": "reopen", "notes": "x"}).status_code == 409   # cleared is terminal
    case = client.get(f"/api/cases/{cid}").get_json()
    assert case["status"] == "cleared"
    assert [e["action"] for e in case["events"]] == ["created", "escalate", "clear"]
    assert client.get("/api/cases/9999").status_code == 404


def test_degraded_check_still_opens_a_case_for_review(client):
    o = Orchestrator([SanctionsVendor(WL, always_fail=True)], sleep=no_sleep)
    client.application.extensions["screening_orchestrator"] = o
    r = client.post("/api/screen", json={"name": "Priya Nair"}).get_json()
    assert r["degraded"] and r["case_id"]
    assert client.get("/api/screening/health").get_json()["sanctions-api"] in ("closed", "open")


def test_concurrent_decisions_leave_a_consistent_case(client):
    cid = client.post("/api/screen", json={"name": "Victor Morozoff"}).get_json()["case_id"]
    codes = []
    def go():
        codes.append(client.post(f"/api/cases/{cid}/decision", json={"decision": "clear", "notes": "n"}).status_code)
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes.count(200) == 1 and codes.count(409) == 3   # exactly one decision wins
    assert len([e for e in client.get(f"/api/cases/{cid}").get_json()["events"] if e["action"] == "clear"]) == codes.count(200)
