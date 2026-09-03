"""SettleGuard API + dashboard host.

    GET  /                     -> dashboard (static/index.html)
    GET  /api/config           -> the reconciliation rules currently in force
    GET  /api/summary          -> counts by type / severity / resolved + triage job state
    GET  /api/discrepancies    -> full list with triage notes + resolution state
    POST /api/reconcile        -> re-read rules.yaml, re-run engine, kick off triage in the background
    POST /api/triage           -> start (or restart) AI triage for any rows still pending
    POST /api/resolve/<id>     -> {"notes": "..."} mark one resolved
    POST /api/reopen/<id>      -> undo a resolution

Triage runs in a background thread so the HTTP calls stay snappy even when the
Gemini free tier is slow. The dashboard polls /api/summary to watch notes land.
"""
import os
import threading
import traceback

from flask import Flask, jsonify, request, send_from_directory

import reconcile
import store
import triage

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="")

store.init()

# --- background triage job -------------------------------------------------- #
_triage_lock = threading.Lock()
_triage_state = {"running": False, "done": 0, "total": 0, "errors": []}


def _run_triage_job():
    cfg = reconcile.load_config()
    pending = store.pending_triage()
    with _triage_lock:
        _triage_state.update(running=True, done=0, total=len(pending), errors=[])
    try:
        for d in pending:
            try:
                res = triage.generate(d, cfg)
                store.save_triage(d["discrepancy_id"], res, res["_model"])
                with _triage_lock:
                    _triage_state["done"] += 1
            except Exception as e:  # noqa: BLE001 - record and keep going
                with _triage_lock:
                    _triage_state["errors"].append(
                        {"id": d["discrepancy_id"], "error": str(e)})
    finally:
        with _triage_lock:
            _triage_state["running"] = False


def _start_triage():
    """Start the triage job unless one is already running. Returns pending count."""
    with _triage_lock:
        if _triage_state["running"]:
            return None
    pending = len(store.pending_triage())
    if pending:
        threading.Thread(target=_run_triage_job, daemon=True).start()
    return pending


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def get_config():
    cfg = reconcile.load_config()
    return jsonify({
        "rules": cfg,
        "config_path": os.path.relpath(reconcile.CONFIG_PATH, HERE),
        "triage_available": triage.available(),
    })


@app.get("/api/summary")
def get_summary():
    s = store.summary()
    with _triage_lock:
        s["triage_job"] = dict(_triage_state)
    return jsonify(s)


@app.get("/api/discrepancies")
def get_discrepancies():
    active_only = request.args.get("all") != "1"
    return jsonify(store.list_discrepancies(active_only=active_only))


@app.post("/api/reconcile")
def post_reconcile():
    try:
        cfg = reconcile.load_config()
        found = reconcile.reconcile(cfg)
        new, updated, deactivated = store.sync_discrepancies(found)
        triage_started = _start_triage() if triage.available() else None
        return jsonify({
            "ok": True,
            "rules_in_force": cfg["matching"],
            "found": len(found),
            "new": new,
            "updated": updated,
            "deactivated": deactivated,
            "triage_available": triage.available(),
            "triage_started": triage_started,
        })
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/triage")
def post_triage():
    if not triage.available():
        return jsonify({"ok": False,
                        "error": "GEMINI_API_KEY is not set; cannot run triage."}), 400
    started = _start_triage()
    if started is None:
        return jsonify({"ok": True, "already_running": True})
    return jsonify({"ok": True, "pending": started})


@app.post("/api/resolve/<path:discrepancy_id>")
def post_resolve(discrepancy_id):
    notes = (request.get_json(silent=True) or {}).get("notes", "").strip()
    if not notes:
        return jsonify({"ok": False, "error": "resolution notes are required"}), 400
    if not store.resolve(discrepancy_id, notes):
        return jsonify({"ok": False, "error": "unknown discrepancy_id"}), 404
    return jsonify({"ok": True, "discrepancy": store.get(discrepancy_id)})


@app.post("/api/reopen/<path:discrepancy_id>")
def post_reopen(discrepancy_id):
    store.reopen(discrepancy_id)
    return jsonify({"ok": True, "discrepancy": store.get(discrepancy_id)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"SettleGuard on http://localhost:{port}  "
          f"(triage {'ENABLED' if triage.available() else 'DISABLED - set GEMINI_API_KEY'})")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
