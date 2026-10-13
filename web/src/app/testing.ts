import { Discrepancy, Summary } from './models';

export const row = (over: Partial<Discrepancy> = {}): Discrepancy => ({
  discrepancy_id: 'AMOUNT_MISMATCH:TXN1',
  type: 'AMOUNT_MISMATCH',
  txn_id: 'TXN1',
  merchant_id: 'M1',
  merchant_name: 'Shop',
  platform_amount: 100,
  bank_amount: 90,
  amount_delta: -10,
  detail: 'short',
  severity_hint: 'medium',
  triage_root_cause: null,
  triage_severity: null,
  triage_next_action: null,
  resolved: 0,
  resolution_notes: null,
  ...over,
});

export const summary = (over: Partial<Summary> = {}): Summary => ({
  total: 2, open: 2, resolved: 0, triaged: 0, by_type: {}, by_severity: {},
  triage_job: { running: false, done: 0, total: 0, errors: [] },
  ...over,
});
