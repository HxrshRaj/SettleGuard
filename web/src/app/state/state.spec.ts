import { row, summary } from '../testing';
import { SettleGuardActions as A } from './actions';
import { initialState, settleGuardFeature } from './reducer';
import { selectOpenExposure, selectTriageProgress, selectTypes, selectVisibleRows } from './selectors';

const reducer = settleGuardFeature.reducer;
const root = (s = initialState) => ({ settleGuard: s });

describe('reducer', () => {
  const a = row({ discrepancy_id: 'A', type: 'X' });
  const b = row({ discrepancy_id: 'B', type: 'Y', resolved: 1 });

  it('stores rows and summary on load success', () => {
    const s = reducer(initialState, A.loadSuccess({ rows: [a], summary: summary() }));
    expect(s.rows).toEqual([a]);
    expect(s.loading).toBeFalse();
  });

  it('resolves optimistically and rolls back on failure', () => {
    const s0 = reducer(initialState, A.loadSuccess({ rows: [a], summary: summary() }));
    const s1 = reducer(s0, A.resolve({ id: 'A', notes: 'ok', previous: a }));
    expect(s1.rows[0].resolved).toBe(1);
    expect(s1.rows[0].resolution_notes).toBe('ok');
    const s2 = reducer(s1, A.resolveFailure({ id: 'A', error: 'boom', previous: a }));
    expect(s2.rows[0]).toEqual(a);
    expect(s2.message).toEqual({ kind: 'error', text: 'boom' });
  });

  it('reopens optimistically and clears notes', () => {
    const s0 = reducer(initialState, A.loadSuccess({ rows: [row({ discrepancy_id: 'A', resolved: 1, resolution_notes: 'x' })], summary: summary() }));
    const s1 = reducer(s0, A.reopen({ id: 'A', previous: s0.rows[0] }));
    expect(s1.rows[0].resolved).toBe(0);
    expect(s1.rows[0].resolution_notes).toBeNull();
  });

  it('marks busy during reconcile and reports the result', () => {
    const s1 = reducer(initialState, A.reconcile());
    expect(s1.busy).toBeTrue();
    const s2 = reducer(s1, A.reconcileSuccess({ result: { ok: true, found: 3, new: 2, updated: 1, deactivated: 0, triage_available: false, triage_started: null } }));
    expect(s2.busy).toBeFalse();
    expect(s2.message?.text).toContain('3 discrepancies');
  });

  describe('selectors', () => {
    const base = { ...initialState, rows: [a, b] };
    it('filters by status and type', () => {
      expect(selectVisibleRows(root({ ...base, statusFilter: 'open' })).map((r) => r.discrepancy_id)).toEqual(['A']);
      expect(selectVisibleRows(root({ ...base, statusFilter: 'resolved' })).map((r) => r.discrepancy_id)).toEqual(['B']);
      expect(selectVisibleRows(root({ ...base, statusFilter: 'all', typeFilter: 'Y' })).map((r) => r.discrepancy_id)).toEqual(['B']);
    });
    it('lists distinct sorted types', () => {
      expect(selectTypes(root(base))).toEqual(['X', 'Y']);
    });
    it('sums absolute open exposure and ignores resolved or null deltas', () => {
      const rows = [row({ discrepancy_id: '1', amount_delta: -50 }), row({ discrepancy_id: '2', amount_delta: 25 }),
        row({ discrepancy_id: '3', amount_delta: null }), row({ discrepancy_id: '4', amount_delta: 999, resolved: 1 })];
      expect(selectOpenExposure(root({ ...initialState, rows }))).toBe(75);
    });
    it('reports triage progress only while running', () => {
      expect(selectTriageProgress(root({ ...base, summary: summary() }))).toBeNull();
      const running = summary({ triage_job: { running: true, done: 1, total: 4, errors: [] } });
      expect(selectTriageProgress(root({ ...base, summary: running }))).toEqual({ done: 1, total: 4, percent: 25 });
    });
  });
});
