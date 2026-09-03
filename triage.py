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
    "No hedging, no AI disclaimers, no restating the question. "
    "Write money amounts as plain ASCII, e.g. 'INR 22,111.20' -- never use the "
    "rupee sign or other non-ASCII symbols."
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


def _post_with_retry(url, body, timeout, *, max_tries=4):
    """POST JSON with bounded resilience against a busy free tier:
      * 429 WITH a "retry in Ns" hint (per-minute limit) -> wait it out, retry
      * 429 without a hint (quota exhausted) -> fail fast, no point retrying now
      * 500/503 -> short exponential backoff, then retry
      * 400 mentioning thinkingConfig -> drop that field and retry once
    """
    import re
    import time

    payload = json.loads(body.decode("utf-8"))
    last = None
    for attempt in range(max_tries):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            last = f"Gemini API HTTP {e.code}: {detail[:300]}"
            gc = payload.get("generationConfig", {})
            final = attempt == max_tries - 1
            m = re.search(r"retry in ([\d.]+)s", detail)
            if e.code == 429 and m and not final:
                time.sleep(min(float(m.group(1)) + 1, 40))
                continue
            if e.code in (500, 503) and not final:
                time.sleep(min(2 * (2 ** attempt), 12))
                continue
            # Some models (3.x, *-lite-latest) reject thinkingConfig with a
            # generic 400. Drop it and retry once before giving up.
            if e.code == 400 and gc.pop("thinkingConfig", None) is not None:
                continue
            raise TriageUnavailable(last) from e
        except urllib.error.URLError as e:
            if attempt < max_tries - 1:
                time.sleep(2 * (2 ** attempt))
                continue
            raise TriageUnavailable(f"Gemini API unreachable: {e.reason}") from e
    raise TriageUnavailable(last or "Gemini API: exhausted retries")


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

    model = model or config.get("triage", {}).get("model", "gemini-flash-latest")
    gen_cfg = {
        "temperature": config.get("triage", {}).get("temperature", 0.2),
        "maxOutputTokens": config.get("triage", {}).get("max_output_tokens", 2048),
        "responseMimeType": "application/json",
        "responseSchema": RESPONSE_SCHEMA,
        # Modern Gemini models spend output tokens on a hidden "thinking" phase,
        # which can truncate short structured replies. This is a fact-shaped
        # extraction task, so ask for thinking off. Some models reject the field;
        # _post_with_retry strips it and retries if so.
        "thinkingConfig": {"thinkingBudget": 0},
    }
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": _prompt(d, config)}]}],
        "generationConfig": gen_cfg,
    }).encode("utf-8")
    url = f"{API_ROOT}/{model}:generateContent?key={key}"

    payload = _post_with_retry(url, body, timeout)

    try:
        cand = payload["candidates"][0]
        text = cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        finish = (payload.get("candidates") or [{}])[0].get("finishReason")
        raise TriageUnavailable(
            f"Unexpected Gemini response shape: {e} (finishReason={finish})") from e

    parsed = _parse_json(text)
    if parsed is None:
        raise TriageUnavailable(
            f"Could not parse JSON from Gemini reply: {text[:200]!r}")

    sev = str(parsed.get("severity", "")).lower().strip()
    if sev not in ("low", "medium", "high"):
        sev = d.get("severity_hint", "medium")
    return {
        "root_cause": (parsed.get("root_cause") or "").strip(),
        "severity": sev,
        "next_action": (parsed.get("next_action") or "").strip(),
        "_model": model,
    }


_CTRL = {c: None for c in range(0x20) if c not in (0x09, 0x0a, 0x0d)}


def _parse_json(text):
    """Best-effort parse of a model JSON reply: strip fences + stray control chars."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    for candidate in (text, t, t.translate(_CTRL)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def run_batch(discrepancies, config, *, max_workers=2):
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
