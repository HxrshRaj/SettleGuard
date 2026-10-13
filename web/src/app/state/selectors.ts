import { createSelector } from '@ngrx/store';
import { settleGuardFeature } from './reducer';

const { selectRows, selectStatusFilter, selectTypeFilter, selectSummary } = settleGuardFeature;

export const selectTypes = createSelector(selectRows, (rows) =>
  [...new Set(rows.map((r) => r.type))].sort(),
);

export const selectVisibleRows = createSelector(
  selectRows,
  selectStatusFilter,
  selectTypeFilter,
  (rows, status, type) =>
    rows.filter(
      (r) =>
        (status === 'all' || (status === 'open' ? !r.resolved : !!r.resolved)) &&
        (type === 'all' || r.type === type),
    ),
);

/** Net absolute amount at risk across open discrepancies that have a known delta. */
export const selectOpenExposure = createSelector(selectRows, (rows) =>
  rows
    .filter((r) => !r.resolved && r.amount_delta !== null)
    .reduce((sum, r) => sum + Math.abs(r.amount_delta ?? 0), 0),
);

export const selectTriageProgress = createSelector(selectSummary, (s) => {
  const job = s?.triage_job;
  if (!job || !job.running) return null;
  return { done: job.done, total: job.total, percent: job.total ? Math.round((100 * job.done) / job.total) : 0 };
});
