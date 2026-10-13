export type Severity = 'low' | 'medium' | 'high' | 'critical' | 'unknown';
export type DiscrepancyType = string;

export interface Discrepancy {
  discrepancy_id: string;
  type: DiscrepancyType;
  txn_id: string | null;
  merchant_id: string | null;
  merchant_name: string | null;
  platform_amount: number | null;
  bank_amount: number | null;
  amount_delta: number | null;
  detail: string | null;
  severity_hint: string | null;
  triage_root_cause: string | null;
  triage_severity: string | null;
  triage_next_action: string | null;
  resolved: 0 | 1;
  resolution_notes: string | null;
}

export interface TriageJob {
  running: boolean;
  done: number;
  total: number;
  errors: { id: string; error: string }[];
}

export interface Summary {
  total: number;
  open: number;
  resolved: number;
  triaged: number;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
  triage_job: TriageJob;
}

export interface ReconcileResult {
  ok: boolean;
  found: number;
  new: number;
  updated: number;
  deactivated: number;
  triage_available: boolean;
  triage_started: number | null;
}

export type StatusFilter = 'open' | 'resolved' | 'all';
