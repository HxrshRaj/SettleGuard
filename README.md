# SettleGuard

Merchant settlement reconciliation and AI-powered triage.

## The Problem

Whenever a platform pays out settlements — to merchants, partners, or vendors — there are always small mismatches between what the platform *says* it paid and what actually shows up in the bank ledger. A missing entry, a duplicate charge, a delayed settlement, an amount that's off by a few cents. Individually these look trivial. At scale, across thousands of transactions, they're real, unrecovered revenue — and they're genuinely tedious to catch by manually diffing spreadsheets.

SettleGuard automates that reconciliation and adds an AI layer on top that doesn't just flag a discrepancy, but explains *why* it's likely happening and what to do about it.

## What It Does

1. **Reconciliation engine** — matches platform settlement records against bank ledger records using SQL joins, detecting **5 distinct discrepancy types**:
   - Amount mismatches (platform and bank amounts don't agree)
   - Missing entries — on the platform side (bank shows it, platform doesn't)
   - Missing entries — on the bank side (platform shows it, bank doesn't)
   - Duplicate charges
   - Timing mismatches (settlement recorded outside an expected window)

2. **AI triage layer** — for every flagged discrepancy, generates:
   - A root-cause hypothesis (plain-language explanation of the likely cause)
   - A severity rating
   - A recommended next action

3. **Operational dashboard** — a live view of all discrepancies, with the ability to review, resolve, and reopen issues, keeping a clear audit trail of what was caught and what was done about it.

4. **Config-driven business rules** — reconciliation tolerances and timing windows live in a YAML config file, not hardcoded in the engine. The config is re-read on every run, so tolerance thresholds or business rules can change without redeploying — the same way real reconciliation pipelines are configured in production.

## Live Demo

🔗 **[settleguard.onrender.com](https://settleguard.onrender.com)**

> Note: hosted on Render's free tier — if the app has been inactive, the first request may take 30–50 seconds to wake up. Please wait for the initial load before assuming it's down.

## Tech Stack

- **Backend**: Python, Flask
- **Database**: SQLite
- **Configuration**: YAML (business rules, tolerances, timing windows)
- **AI**: LLM-powered triage generation for root-cause analysis
- **Frontend**: Server-rendered dashboard for reviewing and resolving discrepancies

## Architecture

platform_records.csv ─┐
├──▶ Reconciliation Engine ──▶ Discrepancy Table ──▶ AI Triage Layer ──▶ Dashboard
bank_ledger.csv ─┘ ▲
│
config/rules.yaml
(tolerances, timing windows —
re-read on every run)


## Design Decisions Worth Knowing

**Why config-driven rules, not hardcoded logic?**
Reconciliation tolerances change — a business might decide a ₹1 rounding difference is acceptable, or that a 24-hour settlement delay is fine but 48 hours isn't. Hardcoding these into the engine means every small policy change requires a code change and a redeploy. Externalizing them into a YAML file means a non-engineer can adjust the business logic directly, and the engine picks it up on the next run with no deployment needed.

**Why separate the AI triage layer from the detection logic?**
Discrepancy *detection* is deterministic — a SQL join either finds a mismatch or it doesn't. But *explaining* a discrepancy (why did this likely happen, how severe is it, what should be done) benefits from a more flexible, language-based layer. Keeping these separate means the deterministic detection logic stays simple, testable, and trustworthy, while the AI layer adds interpretive value on top without being relied on for the actual pass/fail decision.

## Angular + NgRx dashboard (`web/`)

A second front end built with **Angular 20** (standalone components, signals, `OnPush`) and **NgRx** (store, effects, selectors) on top of the same Flask JSON API. It is served by Flask at `/ng/` once built; the original dashboard at `/` is unchanged.

- **State:** one NgRx feature slice (`web/src/app/state/`): typed actions via `createActionGroup`, a `createFeature` reducer, memoised selectors (status/type filters, open exposure, triage progress).
- **RxJS in effects:** `exhaustMap` so a double-click cannot start two reconciliations; `concatMap` to keep resolve/reopen ordered; `forkJoin` for the combined load; a `timer` + `switchMap` + `takeWhile` loop that polls `/api/summary` while the AI triage job runs and stops when it finishes.
- **Optimistic updates:** resolve/reopen change the row immediately and roll back (with an error banner) if the API call fails.
- **Forms:** reactive form with validation (notes are required, matching the API rule).
- **Tests:** 21 Jasmine/Karma tests in headless Chrome: reducer and selectors, effects (including the polling loop under `fakeAsync` and the `exhaustMap` behaviour), and component tests that run store + effects + `HttpTestingController` together. The Flask side has 4 pytest tests (`tests/`).

```bash
cd web && npm ci
CHROME_BIN=/path/to/chrome npx ng test --watch=false --karma-config=karma.conf.js
npx ng build --base-href /ng/      # then run `python app.py` and open http://localhost:5000/ng/
```

**Packaging and CI**
- `Dockerfile`: multi-stage OCI image (Node build, then Python/gunicorn); `.github/workflows/ci.yml` runs backend tests, Angular tests, the production build, then builds the image and smoke-tests it.
- `azure-pipelines.yml`: the same stages as an Azure DevOps YAML pipeline. It follows the Azure Pipelines schema but has **not** been run on an Azure DevOps organisation.

## Running Locally

```bash
git clone https://github.com/HxrshRaj/SettleGuard.git
cd SettleGuard
pip install -r requirements.txt --break-system-packages
python app.py
```

Then open `http://localhost:5000` (or whichever port the app binds to).

## What I'd Build Next

- Multi-currency reconciliation support
- Bulk resolution actions for near-identical discrepancies
- Configurable notification hooks (Slack/email) when a high-severity discrepancy is flagged
- Historical trend view — is a particular discrepancy type increasing over time?

## Author

Built independently by Harsh Raj — [GitHub](https://github.com/HxrshRaj) · [LinkedIn](https://www.linkedin.com/in/harsh-raj-7a26ab314)
