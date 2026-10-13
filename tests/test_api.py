import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    import app as app_module
    store.init()
    return app_module.app.test_client()


def test_reconcile_finds_the_five_discrepancy_types(client):
    r = client.post("/api/reconcile").get_json()
    assert r["ok"] and r["found"] == 10
    types = {d["type"] for d in client.get("/api/discrepancies").get_json()}
    assert types == {"AMOUNT_MISMATCH", "DUPLICATE_IN_BANK", "MISSING_IN_BANK",
                     "MISSING_IN_PLATFORM", "TIMING_MISMATCH"}


def test_resolve_requires_notes_and_reopen_round_trips(client):
    client.post("/api/reconcile")
    did = client.get("/api/discrepancies").get_json()[0]["discrepancy_id"]
    assert client.post(f"/api/resolve/{did}", json={"notes": "  "}).status_code == 400
    assert client.post("/api/resolve/NOPE", json={"notes": "x"}).status_code == 404
    ok = client.post(f"/api/resolve/{did}", json={"notes": "checked"}).get_json()
    assert ok["discrepancy"]["resolved"] == 1 and ok["discrepancy"]["resolution_notes"] == "checked"
    back = client.post(f"/api/reopen/{did}").get_json()
    assert back["discrepancy"]["resolved"] == 0 and back["discrepancy"]["resolution_notes"] is None


def test_summary_counts_match_list(client):
    client.post("/api/reconcile")
    s = client.get("/api/summary").get_json()
    assert s["total"] == len(client.get("/api/discrepancies").get_json()) == 10
    assert s["open"] + s["resolved"] == s["total"]


def test_angular_route_falls_back_or_reports_missing_build(client, tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "NG_DIST", str(tmp_path / "missing"))
    assert client.get("/ng/").status_code == 404
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<app-root></app-root>")
    monkeypatch.setattr(app_module, "NG_DIST", str(dist))
    assert b"app-root" in client.get("/ng/").data
    assert b"app-root" in client.get("/ng/some/client/route").data
