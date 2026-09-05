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
