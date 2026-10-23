"""Flask blueprint (all JSON):
    POST /api/india/validate/<aadhaar|pan|ifsc|vpa>     {"value": "..."}
    POST /api/india/upi/intent                          {"vpa","name","amount","note"?,"txn_ref"?} -> upi:// link
    POST /api/india/upi/collect                         {"payer","payee","amount"}; Idempotency-Key supported
    GET  /api/india/upi/collect/<id>
    POST /api/india/upi/collect/<id>/approve            sandbox PSP: paise digits choose the outcome
    POST /api/india/enach/mandates                      {"ifsc","account_last4","max_amount","frequency"}
    POST /api/india/enach/mandates/<id>/<activate|pause|cancel>
    POST /api/india/enach/mandates/<id>/debits          {"amount","notified_at","debit_ref"}
    POST /api/india/statement/analyze                   raw CSV body (date,narration,debit,credit,balance)
Sandbox only: nothing here talks to UIDAI, NPCI, a PSP or a statement vendor."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import identity, statement
from .enach import MandateError, MandateService
from .upi import CollectService, build_intent

bp = Blueprint("indiastack", __name__)

_VALIDATORS = {"aadhaar": identity.validate_aadhaar, "pan": identity.validate_pan,
               "ifsc": identity.validate_ifsc, "vpa": identity.validate_vpa}


def _svc(name, factory):
    s = current_app.extensions.get(name)
    if s is None:
        s = current_app.extensions[name] = factory()
    return s


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


@bp.post("/api/india/validate/<kind>")
def validate(kind):
    fn = _VALIDATORS.get(kind)
    if fn is None:
        return _bad("unknown identifier type", 404)
    value = (request.get_json(silent=True) or {}).get("value")
    if not isinstance(value, str):
        return _bad("value is required")
    return jsonify({"ok": True, **fn(value)})  # Aadhaar is masked by the validator; never echoed in full


@bp.post("/api/india/upi/intent")
def upi_intent():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "uri": build_intent(b.get("vpa", ""), b.get("name", ""), b.get("amount", ""),
                                                        b.get("note", ""), b.get("txn_ref", ""))})
    except ValueError as e:
        return _bad(str(e))


@bp.post("/api/india/upi/collect")
def collect_create():
    b = request.get_json(silent=True) or {}
    try:
        rec, created = _svc("upi_collect", CollectService).create(
            b.get("payer", ""), b.get("payee", ""), b.get("amount", ""), request.headers.get("Idempotency-Key"))
    except ValueError as e:
        return _bad(str(e))
    return jsonify({"ok": True, "replayed": not created, **rec}), 201 if created else 200


@bp.get("/api/india/upi/collect/<cid>")
def collect_get(cid):
    rec = _svc("upi_collect", CollectService).get(cid)
    return (jsonify({"ok": True, **rec}) if rec else _bad("not found", 404))


@bp.post("/api/india/upi/collect/<cid>/approve")
def collect_approve(cid):
    try:
        return jsonify({"ok": True, **_svc("upi_collect", CollectService).approve(cid)})
    except KeyError:
        return _bad("not found", 404)
    except ValueError as e:
        return _bad(str(e), 409)


@bp.post("/api/india/enach/mandates")
def mandate_create():
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **_svc("enach", MandateService).create(
            b.get("ifsc", ""), str(b.get("account_last4", "")), str(b.get("max_amount", "")), b.get("frequency", ""))}), 201
    except (MandateError, ArithmeticError) as e:
        return _bad(str(e))


_ACTIONS = {"activate": "ACTIVE", "pause": "PAUSED", "cancel": "CANCELLED"}


@bp.post("/api/india/enach/mandates/<mid>/<action>")
def mandate_action(mid, action):
    if action not in _ACTIONS:
        return _bad("unknown action", 404)
    try:
        return jsonify({"ok": True, **_svc("enach", MandateService).transition(mid, _ACTIONS[action])})
    except KeyError:
        return _bad("not found", 404)
    except MandateError as e:
        return _bad(str(e), 409)


@bp.post("/api/india/enach/mandates/<mid>/debits")
def mandate_debit(mid):
    b = request.get_json(silent=True) or {}
    try:
        res = _svc("enach", MandateService).present_debit(mid, str(b["amount"]), float(b["notified_at"]), str(b["debit_ref"]))
    except KeyError as e:
        return _bad("not found" if e.args and e.args[0] == mid else f"missing field {e.args[0]}", 404 if e.args and e.args[0] == mid else 400)
    except (ValueError, ArithmeticError):
        return _bad("invalid debit request")
    return jsonify({"ok": True, **res})


@bp.post("/api/india/statement/analyze")
def statement_analyze():
    try:
        return jsonify({"ok": True, **statement.analyze(statement.parse_csv(request.get_data(as_text=True)))})
    except (ValueError, KeyError, ArithmeticError) as e:
        return _bad(f"could not parse statement: {e}")
