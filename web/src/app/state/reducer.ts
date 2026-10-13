import { createFeature, createReducer, on } from '@ngrx/store';
import { Discrepancy, StatusFilter, Summary } from '../models';
import { SettleGuardActions as A } from './actions';

export interface State {
  rows: Discrepancy[];
  summary: Summary | null;
  statusFilter: StatusFilter;
  typeFilter: string; // 'all' or a discrepancy type
  loading: boolean;
  busy: boolean; // reconcile / triage kicked off
  message: { kind: 'info' | 'error'; text: string } | null;
}

export const initialState: State = {
  rows: [],
  summary: null,
  statusFilter: 'open',
  typeFilter: 'all',
  loading: false,
  busy: false,
  message: null,
};

const replaceRow = (rows: Discrepancy[], row: Discrepancy) =>
  rows.map((r) => (r.discrepancy_id === row.discrepancy_id ? row : r));

export const settleGuardFeature = createFeature({
  name: 'settleGuard',
  reducer: createReducer(
    initialState,
    on(A.load, (s) => ({ ...s, loading: true, message: null })),
    on(A.loadSuccess, (s, { rows, summary }) => ({ ...s, rows, summary, loading: false })),
    on(A.loadFailure, (s, { error }) => ({ ...s, loading: false, message: { kind: 'error', text: error } })),

    on(A.reconcile, (s) => ({ ...s, busy: true, message: null })),
    on(A.reconcileSuccess, (s, { result }) => ({
      ...s,
      busy: false,
      message: {
        kind: 'info',
        text: `Reconciliation found ${result.found} discrepancies (${result.new} new, ${result.deactivated} cleared).`,
      },
    })),
    on(A.reconcileFailure, (s, { error }) => ({ ...s, busy: false, message: { kind: 'error', text: error } })),

    on(A.startTriage, (s) => ({ ...s, busy: true })),
    on(A.triageStarted, (s) => ({ ...s, busy: false })),
    on(A.triageFailure, (s, { error }) => ({ ...s, busy: false, message: { kind: 'error', text: error } })),
    on(A.summaryPolled, (s, { summary }) => ({ ...s, summary })),

    // Optimistic update: flip the row immediately, roll back on failure.
    on(A.resolve, (s, { id, notes }) => ({
      ...s,
      rows: s.rows.map((r) =>
        r.discrepancy_id === id ? { ...r, resolved: 1 as const, resolution_notes: notes } : r,
      ),
    })),
    on(A.resolveSuccess, A.reopenSuccess, (s, { row }) => ({ ...s, rows: replaceRow(s.rows, row) })),
    on(A.resolveFailure, A.reopenFailure, (s, { previous, error }) => ({
      ...s,
      rows: replaceRow(s.rows, previous),
      message: { kind: 'error', text: error },
    })),
    on(A.reopen, (s, { id }) => ({
      ...s,
      rows: s.rows.map((r) =>
        r.discrepancy_id === id ? { ...r, resolved: 0 as const, resolution_notes: null } : r,
      ),
    })),

    on(A.setStatusFilter, (s, { status }) => ({ ...s, statusFilter: status })),
    on(A.setTypeFilter, (s, { discrepancyType }) => ({ ...s, typeFilter: discrepancyType })),
    on(A.dismissMessage, (s) => ({ ...s, message: null })),
  ),
});
