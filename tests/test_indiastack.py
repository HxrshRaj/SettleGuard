import os
import sys
import time
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indiastack import identity, statement  # noqa: E402
from indiastack.enach import MandateError, MandateService  # noqa: E402
from indiastack.upi import CollectService, build_intent, parse_intent  # noqa: E402


def make_aadhaar(payload11: str) -> str:
    return payload11 + str(identity.verhoeff_check_digit(payload11))


# ---------- identifiers ----------
def test_verhoeff_known_vector():
    assert identity.verhoeff_check_digit("236") == 3 and identity.verhoeff_valid("2363")


def test_valid_aadhaar_accepted_and_masked():
    a = make_aadhaar("23456789012")
    r = identity.validate_aadhaar(a)
    assert r["valid"] and r["masked"] == "XXXX XXXX " + a[-4:] and a not in str(r)
    assert identity.validate_aadhaar(" ".join([a[:4], a[4:8], a[8:]]))["valid"]  # spaces allowed


def test_aadhaar_single_digit_error_and_adjacent_swap_are_caught():
    a = make_aadhaar("23456789012")
    for i in range(12):
        bad = a[:i] + str((int(a[i]) + 1) % 10) + a[i + 1:]
        if i == 0 and bad[0] in "01":
            continue
        assert not identity.validate_aadhaar(bad)["valid"]
    for i in range(11):
        if a[i] != a[i + 1] and not (i == 0 and a[1] in "01"):
            swapped = a[:i] + a[i + 1] + a[i] + a[i + 2:]
            assert not identity.validate_aadhaar(swapped)["valid"]


@pytest.mark.parametrize("bad", ["", "12345", "0" * 12, "1" + make_aadhaar("2345678901")[1:], "222222222222", "abcd efgh ijkl"])
def test_aadhaar_rejections(bad):
    assert not identity.validate_aadhaar(bad)["valid"]


def test_pan_ifsc_vpa():
    assert identity.validate_pan("abcpe1234f") == {"valid": True, "holder_type": "individual", "masked": "ABXXXXXXXF"}
    assert not identity.validate_pan("ABCDE1234")["valid"] and not identity.validate_pan("ABCXE1234F")["valid"]
    assert identity.validate_ifsc("hdfc0001234") == {"valid": True, "bank_code": "HDFC", "branch_code": "001234"}
    assert not identity.validate_ifsc("HDFC1001234")["valid"]
    assert identity.validate_vpa("harsh.raj@okbank")["handle"] == "okbank"
    assert not identity.validate_vpa("no-at-sign")["valid"] and not identity.validate_vpa("a@b")["valid"]


# ---------- UPI ----------
def test_intent_round_trips_with_special_characters():
    uri = build_intent("shop@upi", "Raj & Sons", "249.50", "Order #12", "TR1")
    q = parse_intent(uri)
    assert q == {"pa": "shop@upi", "pn": "Raj & Sons", "am": "249.50", "cu": "INR", "tn": "Order #12", "tr": "TR1"}


@pytest.mark.parametrize("amt", ["0", "-5", "100000.01", "abc", "10.999"])
def test_intent_rejects_bad_amounts(amt):
    with pytest.raises(ValueError):
        build_intent("shop@upi", "Shop", amt)


def test_collect_idempotency_and_sandbox_outcomes():
    s = CollectService()
    a, created = s.create("alice@upi", "bob@upi", "100.00", "k1")
    b, created2 = s.create("alice@upi", "bob@upi", "100.00", "k1")
    assert created and not created2 and a["collect_id"] == b["collect_id"]
    assert s.approve(a["collect_id"])["status"] == "SUCCESS"
    f, _ = s.create("alice@upi", "bob@upi", "50.02")
    out = s.approve(f["collect_id"])
    assert out["status"] == "FAILED" and out["failure_code"] == "U30_DEBIT_FAILED"


def test_collect_cannot_be_approved_twice_and_expires():
    now = [1000.0]
    s = CollectService(clock=lambda: now[0])
    c, _ = s.create("alice@upi", "bob@upi", "10.00")
    s.approve(c["collect_id"])
    with pytest.raises(ValueError):
        s.approve(c["collect_id"])
    e, _ = s.create("alice@upi", "bob@upi", "10.00")
    now[0] += 601
    assert s.get(e["collect_id"])["status"] == "EXPIRED"
    with pytest.raises(ValueError):
        s.approve(e["collect_id"])


def test_collect_validation():
    s = CollectService()
    with pytest.raises(ValueError):
        s.create("alice@upi", "alice@upi", "10.00")
    with pytest.raises(ValueError):
        s.create("bad", "bob@upi", "10.00")


# ---------- eNACH ----------
def test_mandate_lifecycle_and_illegal_transitions():
    m = MandateService()
    rec = m.create("HDFC0001234", "4321", "5000", "MONTHLY")
    mid = rec["mandate_id"]
    assert rec["status"] == "INITIATED" and rec["account"] == "XXXXXX4321"
    with pytest.raises(MandateError):
        m.transition(mid, "PAUSED")          # cannot pause before activation
    assert m.transition(mid, "ACTIVE")["status"] == "ACTIVE"
    assert m.transition(mid, "PAUSED")["status"] == "PAUSED"
    assert m.transition(mid, "ACTIVE")["status"] == "ACTIVE"
    m.transition(mid, "CANCELLED")
    with pytest.raises(MandateError):
        m.transition(mid, "ACTIVE")          # terminal


def test_mandate_creation_validation():
    m = MandateService()
    for args in [("BAD", "1234", "10", "MONTHLY"), ("HDFC0001234", "12", "10", "MONTHLY"),
                 ("HDFC0001234", "1234", "-1", "MONTHLY"), ("HDFC0001234", "1234", "10", "DAILY")]:
        with pytest.raises(MandateError):
            m.create(*args)


def test_debit_rules_cap_notice_state_and_idempotency():
    now = [10_000_000.0]
    m = MandateService(clock=lambda: now[0])
    mid = m.create("HDFC0001234", "4321", "5000", "MONTHLY")["mandate_id"]
    early = m.present_debit(mid, "100", now[0] - 3 * 86400, "D0")
    assert early["reason"] == "MANDATE_INITIATED"
    m.transition(mid, "ACTIVE")
    assert m.present_debit(mid, "100", now[0] - 3600, "D1")["reason"] == "PRE_DEBIT_NOTICE_TOO_RECENT"
    assert m.present_debit(mid, "5000.01", now[0] - 2 * 86400, "D2")["reason"] == "AMOUNT_EXCEEDS_MANDATE"
    ok = m.present_debit(mid, "5000", now[0] - 2 * 86400, "D3")
    assert ok["status"] == "ACCEPTED" and ok["reason"] is None and not ok["replayed"]
    again = m.present_debit(mid, "5000", now[0] - 2 * 86400, "D3")
    assert again["replayed"] and len(m.get(mid)["debits"]) == 4


def test_mandate_expires():
    now = [0.0]
    m = MandateService(clock=lambda: now[0])
    mid = m.create("HDFC0001234", "4321", "100", "MONTHLY", valid_days=1)["mandate_id"]
    m.transition(mid, "ACTIVE")
    now[0] += 2 * 86400
    assert m.get(mid)["status"] == "EXPIRED"
    assert m.present_debit(mid, "10", now[0] - 2 * 86400, "X")["reason"] == "MANDATE_EXPIRED"


# ---------- statement analysis ----------
CSV = """date,narration,debit,credit,balance
2026-07-01,SALARY JUL ACME,0,60000,70000
2026-07-05,HDFC LOAN EMI NACH,12000,0,58000
2026-07-20,RENT,15000,0,43000
2026-08-01,SALARY AUG ACME,0,60000,103000
2026-08-05,HDFC LOAN EMI NACH,12000,0,91000
2026-08-12,NACH RETURN INSUFFICIENT FUNDS,0,0,91000
2026-09-01,SALARY SEP ACME,0,60000,151000
2026-09-05,HDFC LOAN EMI NACH,12000,0,139000
"""


def test_statement_analysis_numbers():
    r = statement.analyze(statement.parse_csv(CSV))
    assert r["months"] == 3 and r["avg_monthly_credits"] == "60000.00"
    assert r["bounce_count"] == 1 and r["salary_detected"] and r["salary_months"] == ["2026-07", "2026-08", "2026-09"]
    assert r["recurring_obligations_total"] == "36000"          # three EMIs; the NACH return is not counted
    assert Decimal(r["obligation_to_income"]) == Decimal("0.2000")
    assert r["min_balance"] == "43000"
    assert r["avg_month_end_balance"] == str(((Decimal(43000) + 91000 + 139000) / 3).quantize(Decimal("0.01")))


def test_statement_errors():
    with pytest.raises(ValueError):
        statement.analyze([])
    with pytest.raises(Exception):
        statement.parse_csv("date,narration\n2026-01-01,x")


# ---------- HTTP layer ----------
@pytest.fixture()
def client():
    import app as app_module
    app_module.app.extensions.pop("upi_collect", None)
    app_module.app.extensions.pop("enach", None)
    return app_module.app.test_client()


def test_http_validate_never_echoes_full_aadhaar(client):
    a = make_aadhaar("23456789012")
    body = client.post("/api/india/validate/aadhaar", json={"value": a})
    assert body.get_json()["valid"] and a not in body.get_data(as_text=True)
    assert client.post("/api/india/validate/zzz", json={"value": "x"}).status_code == 404
    assert client.post("/api/india/validate/pan", json={}).status_code == 400


def test_http_upi_flow_with_idempotency(client):
    h = {"Idempotency-Key": "abc"}
    r1 = client.post("/api/india/upi/collect", json={"payer": "alice@upi", "payee": "bob@upi", "amount": "75.00"}, headers=h)
    r2 = client.post("/api/india/upi/collect", json={"payer": "alice@upi", "payee": "bob@upi", "amount": "75.00"}, headers=h)
    assert r1.status_code == 201 and r2.status_code == 200 and r2.get_json()["replayed"]
    cid = r1.get_json()["collect_id"]
    assert client.post(f"/api/india/upi/collect/{cid}/approve").get_json()["status"] == "SUCCESS"
    assert client.post(f"/api/india/upi/collect/{cid}/approve").status_code == 409
    assert client.get("/api/india/upi/collect/NOPE").status_code == 404
    assert client.post("/api/india/upi/collect", json={"payer": "alice@upi", "payee": "bob@upi", "amount": "0"}).status_code == 400


def test_http_enach_flow(client):
    m = client.post("/api/india/enach/mandates", json={"ifsc": "HDFC0001234", "account_last4": "4321", "max_amount": "5000", "frequency": "MONTHLY"})
    assert m.status_code == 201
    mid = m.get_json()["mandate_id"]
    assert client.post(f"/api/india/enach/mandates/{mid}/pause").status_code == 409
    assert client.post(f"/api/india/enach/mandates/{mid}/activate").get_json()["status"] == "ACTIVE"
    d = client.post(f"/api/india/enach/mandates/{mid}/debits", json={"amount": "100", "notified_at": time.time() - 2 * 86400, "debit_ref": "R1"})
    assert d.get_json()["status"] == "ACCEPTED"
    assert client.post("/api/india/enach/mandates/NOPE/activate").status_code == 404
    assert client.post(f"/api/india/enach/mandates/{mid}/debits", json={"amount": "1"}).status_code == 400


def test_http_statement(client):
    r = client.post("/api/india/statement/analyze", data=CSV, content_type="text/csv")
    assert r.get_json()["bounce_count"] == 1
    assert client.post("/api/india/statement/analyze", data="garbage", content_type="text/csv").status_code == 400


def test_verhoeff_round_trip_over_many_payloads():
    import random
    rng = random.Random(7)
    for _ in range(500):
        payload = "".join(rng.choice("0123456789") for _ in range(11))
        full = payload + str(identity.verhoeff_check_digit(payload))
        assert identity.verhoeff_valid(full)
        wrong = payload + str((identity.verhoeff_check_digit(payload) + 1) % 10)
        assert not identity.verhoeff_valid(wrong)
