"""AI triage layer.

For each flagged discrepancy, ask Gemini to draft a short triage finding in the
voice of a payments support engineer: likely root cause, a severity call, and a
concrete next action. Real HTTP call to the Gemini API via the standard library
-- no SDK, no native dependency.

Requires the GEMINI_API_KEY environment variable. If it is not set, triage is
skipped (the discrepancy simply stays "triage pending") -- nothing is faked.
"""
import json
import os
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_STYLE = (
    "You are a senior payments support engineer working a merchant settlement "
    "reconciliation queue. You write terse, concrete findings for the ops team. "
    "No hedging, no AI disclaimers, no restating the question."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {
            "type": "string",
            "description": "1-2 sentences naming the most likely concrete cause, "
                           "specific to these numbers and merchant.",
        },
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "next_action": {
            "type": "string",
            "description": "One imperative sentence: the single next step for the "
                           "ops team (who does what).",
        },
    },
    "required": ["root_cause", "severity", "next_action"],
}


class TriageUnavailable(RuntimeError):
    """Raised when triage cannot run for real (e.g. no API key)."""


def api_key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def available():
    return bool(api_key())


def _prompt(d, config):
    m = config.get("matching", {})
    hints = config.get("severity_hints", {})
    facts = {
        "discrepancy_type": d["type"],
        "transaction_id": d["txn_id"],
        "merchant": f'{d.get("merchant_name")} ({d.get("merchant_id")})',
        "platform_says_paid_inr": d.get("platform_amount"),
        "bank_actually_shows_inr": d.get("bank_amount"),
        "amount_delta_inr": d.get("amount_delta"),
        "platform_expected_settlement_at": d.get("expected_settlement_at"),
        "hours_late": d.get("delay_hours"),
        "engine_detail": d.get("detail"),
        "engine_severity_hint": d.get("severity_hint"),
    }
    rules = {
        "amount_tolerance_inr": m.get("amount_tolerance_inr"),
        "late_threshold_hours": m.get("late_threshold_hours"),
        "severity_hints": hints,
    }
    return (
        f"{SYSTEM_STYLE}\n\n"
        "Reconciliation rules currently in force:\n"
        f"{json.dumps(rules, indent=2)}\n\n"
        "Flagged discrepancy:\n"
        f"{json.dumps(facts, indent=2)}\n\n"
        "Write the triage finding. Consider the engine's severity hint but make "
        "your own call. Ground the root cause in the specific amounts, timing, "
        "and discrepancy type above."
    )


def generate(d, config, *, model=None, timeout=30):
    """Return {'root_cause','severity','next_action'} for one discrepancy."""
    key = api_key()
    if not key:
        raise TriageUnavailable("GEMINI_API_KEY is not set")

    model = model or config.get("triage", {}).get("model", "gemini-2.5-flash")
    gen_cfg = {
        "temperature": config.get("triage", {}).get("temperature", 0.2),
        "maxOutputTokens": config.get("triage", {}).get("max_output_tokens", 500),
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA,
    }
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": _prompt(d, config)}]}],
        "generationConfig": gen_cfg,
    }).encode("utf-8")

    url = f"{API_ROOT}/{model}:generateContent?key={key}"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise TriageUnavailable(f"Gemini API HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise TriageUnavailable(f"Gemini API unreachable: {e.reason}") from e

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise TriageUnavailable(f"Unexpected Gemini response shape: {e}") from e

    sev = str(parsed.get("severity", "")).lower().strip()
    if sev not in ("low", "medium", "high"):
        sev = d.get("severity_hint", "medium")
    return {
        "root_cause": (parsed.get("root_cause") or "").strip(),
        "severity": sev,
        "next_action": (parsed.get("next_action") or "").strip(),
        "_model": model,
    }


def run_batch(discrepancies, config, *, max_workers=5):
    """Triage many discrepancies concurrently. Returns (ok, [(id, error), ...])."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import store

    if not available():
        return 0, [("*", "GEMINI_API_KEY is not set")]

    ok, errors = 0, []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(generate, d, config): d for d in discrepancies}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                res = fut.result()
                store.save_triage(d["discrepancy_id"], res, res["_model"])
                ok += 1
            except Exception as e:  # noqa: BLE001 - surface every failure verbatim
                errors.append((d["discrepancy_id"], str(e)))
    return ok, errors


if __name__ == "__main__":
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import reconcile

    cfg = reconcile.load_config()
    ds = reconcile.reconcile(cfg)
    if not available():
        print("GEMINI_API_KEY not set - cannot run triage for real. Set it and retry.")
        raise SystemExit(1)
    sample = ds[0]
    print(f"Triaging {sample['type']} {sample['txn_id']} ...\n")
    out = generate(sample, cfg)
    print(json.dumps(out, indent=2, ensure_ascii=False))
