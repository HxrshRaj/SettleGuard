# SettleGuard

Merchant settlement reconciliation & triage. Matches **what the platform says it
paid** each merchant against **what actually landed in the bank**, flags every
discrepancy with SQL joins, drafts an AI triage note per discrepancy, and lets an
operator resolve them from a web dashboard.

## Run it (Windows PowerShell)

```powershell
$env:GEMINI_API_KEY = "your-gemini-api-key"   # enables the AI triage layer
./run.ps1
```

`run.ps1` installs deps (`flask`, `pyyaml` — both pure Python, no native build),
regenerates the synthetic data, and starts the server on
<http://localhost:5000>.

Manual equivalent:

```powershell
python -m pip install -r requirements.txt
python seed.py
python app.py
```

Without `GEMINI_API_KEY` everything still runs; discrepancies just show
"triage pending" instead of an AI note. Nothing is faked.

## What you should see

1. The dashboard opens empty. Click **Re-run reconciliation** — the engine runs
   against the seed data and, if `GEMINI_API_KEY` is set, auto-triages every
   discrepancy it finds. You get **10 discrepancies**:
   - 2 amount mismatches (a ₹250 shortpayment, a ₹1,200 overpayment)
   - 3 settlements missing from the bank ledger
   - 2 duplicated bank credits
   - 2 late settlements (40h and 90h after expected)
   - 1 bank credit with no matching platform settlement
   - (a ₹0.50 rounding diff and a 20h-late settlement are correctly **not** flagged)
2. Each row shows an AI triage note: root cause, severity (low/medium/high), and a
   concrete next action, written in a support-engineer voice by Gemini.
   If the free tier throttles the batch (HTTP 429), click **Run AI triage** again —
   it only re-runs rows that still lack a note.
3. Edit `config/rules.yaml` (e.g. raise `amount_tolerance_inr` or
   `late_threshold_hours`), click **Re-run reconciliation** → the affected rows
   disappear or reappear. The YAML is re-read on every run.
4. Click **resolve…** on a row, enter notes, Save → it moves to Resolved and the
   notes (and the AI note) persist across re-runs.

## Architecture

```
seed.py        deterministic synthetic data -> data/*.csv
config/rules.yaml   tolerances + matching rules (read fresh every reconcile)
reconcile.py   loads both CSVs into sqlite3 :memory:, SQL joins -> discrepancy list
store.py       sqlite3 file (state.db): upserts discrepancies, keeps triage + resolutions
triage.py      per-discrepancy Gemini REST call (stdlib urllib) -> {root_cause, severity, next_action}
app.py         Flask API + serves the dashboard
static/        vanilla HTML/CSS/JS single-page dashboard
```

Data flow: `POST /api/reconcile` re-reads the YAML, rebuilds the in-memory DB from
the CSVs, runs the join queries, upserts results into `state.db` (preserving any
existing triage note or resolution), then auto-runs triage for rows that don't
have a note yet. The dashboard reads `GET /api/discrepancies`.

### Discrepancy types (all detected in SQL)

| Type | Rule |
|---|---|
| `AMOUNT_MISMATCH` | same `txn_id`, `abs(platform - bank) > amount_tolerance_inr` |
| `MISSING_IN_BANK` | `txn_id` in platform, no bank row |
| `MISSING_IN_PLATFORM` | bank row whose `txn_id` has no platform settlement |
| `DUPLICATE_IN_BANK` | `txn_id` credited more than once in the bank ledger |
| `TIMING_MISMATCH` | amount matches, but `settled_at - expected > late_threshold_hours` |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | current rules + whether triage is available |
| GET | `/api/summary` | counts by type / severity / resolved |
| GET | `/api/discrepancies` | full list (`?all=1` includes deactivated) |
| POST | `/api/reconcile` | re-read YAML, re-run engine, auto-triage new rows |
| POST | `/api/triage` | run triage for rows still pending |
| POST | `/api/resolve/<id>` | `{"notes": "..."}` mark resolved |
| POST | `/api/reopen/<id>` | undo a resolution |
