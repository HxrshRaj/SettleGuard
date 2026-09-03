"""SettleGuard API + dashboard host.

    GET  /                     -> dashboard (static/index.html)
    GET  /api/config           -> the reconciliation rules currently in force
    GET  /api/summary          -> counts by type / severity / resolved
    GET  /api/discrepancies    -> full list with triage notes + resolution state
    POST /api/reconcile        -> re-read rules.yaml, re-run engine, auto-triage new rows
    POST /api/triage           -> (re)run AI triage for any rows still pending
    POST /api/resolve/<id>     -> {"notes": "..."} mark one resolved
    POST /api/reopen/<id>      -> undo a resolution
"""
import os
import traceback

from flask import Flask, jsonify, request, send_from_directory

import reconcile
import store
import triage

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="")

store.init()


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
    return jsonify(store.summary())


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

        triaged, triage_errors = 0, []
        if triage.available():
            pending = store.pending_triage()
            triaged, triage_errors = triage.run_batch(pending, cfg)

        return jsonify({
            "ok": True,
            "rules_in_force": cfg["matching"],
            "found": len(found),
            "new": new,
            "updated": updated,
            "deactivated": deactivated,
            "triaged": triaged,
            "triage_available": triage.available(),
            "triage_errors": [{"id": i, "error": e} for i, e in triage_errors],
        })
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/triage")
def post_triage():
    if not triage.available():
        return jsonify({"ok": False,
                        "error": "GEMINI_API_KEY is not set; cannot run triage."}), 400
    cfg = reconcile.load_config()
    pending = store.pending_triage()
    triaged, errors = triage.run_batch(pending, cfg)
    return jsonify({
        "ok": True,
        "pending": len(pending),
        "triaged": triaged,
        "errors": [{"id": i, "error": e} for i, e in errors],
    })


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
    app.run(host="127.0.0.1", port=port, debug=False)
