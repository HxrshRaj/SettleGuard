"""Flask blueprint:
    POST /api/screen                -> {"name", "country"?, "type"?, "id_number"?}; Idempotency-Key header supported
    GET  /api/cases[?status=open]   -> cases ordered by risk
    GET  /api/cases/<id>            -> case with audit trail
    POST /api/cases/<id>/decision   -> {"decision": "clear|escalate|reopen", "notes": "...", "actor": "..."}
    GET  /api/screening/health      -> vendor circuit-breaker states
"""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from . import cases
from .orchestrator import Orchestrator
from .vendors import AdverseMediaVendor, IdentityVendor, SanctionsVendor

bp = Blueprint("screening", __name__)
CASE_THRESHOLD = 35  # medium risk and above opens a case


def get_orchestrator() -> Orchestrator:
    orch = current_app.extensions.get("screening_orchestrator")
    if orch is None:
        orch = Orchestrator([SanctionsVendor(), AdverseMediaVendor(), IdentityVendor()])
        current_app.extensions["screening_orchestrator"] = orch
    return orch


@bp.post("/api/screen")
def screen():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    if len(name) > 200:
        return jsonify({"ok": False, "error": "name too long"}), 400
    idem = request.headers.get("Idempotency-Key")
    if idem:
        prior = cases.by_key(idem)
        if prior:  # replay: return the stored result without calling vendors again
            return jsonify({"ok": True, "risk_score": prior["risk_score"], "risk_level": prior["risk_level"],
                            "degraded": bool(prior["degraded"]), "findings": prior["findings"],
                            "vendors": prior["vendors"], "case_id": prior["case_id"], "replayed": True})
    subject = {"name": name, "country": body.get("country"), "type": body.get("type"),
               "id_number": body.get("id_number")}
    res = get_orchestrator().screen(subject)
    payload = {"risk_score": res.risk_score, "risk_level": res.risk_level, "degraded": res.degraded,
               "findings": [asdict(f) for f in res.findings], "vendors": [asdict(o) | {"findings": len(o.findings)} for o in res.vendors]}
    case = None
    if res.risk_score >= CASE_THRESHOLD or res.degraded:
        case, created = cases.create(subject, payload, idem)
        payload["case_id"] = case["case_id"]
        payload["replayed"] = not created
    return jsonify({"ok": True, **payload}), 200


@bp.get("/api/cases")
def list_cases():
    return jsonify(cases.list_cases(request.args.get("status")))


@bp.get("/api/cases/<int:case_id>")
def get_case(case_id):
    c = cases.get(case_id)
    return (jsonify(c), 200) if c else (jsonify({"ok": False, "error": "unknown case"}), 404)


@bp.post("/api/cases/<int:case_id>/decision")
def decide(case_id):
    body = request.get_json(silent=True) or {}
    try:
        c = cases.decide(case_id, body.get("decision", ""), body.get("actor", ""), body.get("notes", ""))
    except cases.InvalidTransition as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return (jsonify({"ok": True, "case": c}), 200) if c else (jsonify({"ok": False, "error": "unknown case"}), 404)


@bp.get("/api/screening/health")
def health():
    return jsonify({n: b.state for n, b in get_orchestrator().breakers.items()})
