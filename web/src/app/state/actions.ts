import { createActionGroup, emptyProps, props } from '@ngrx/store';
import { Discrepancy, ReconcileResult, StatusFilter, Summary } from '../models';

export const SettleGuardActions = createActionGroup({
  source: 'SettleGuard',
  events: {
    'Load': emptyProps(),
    'Load Success': props<{ rows: Discrepancy[]; summary: Summary }>(),
    'Load Failure': props<{ error: string }>(),

    'Reconcile': emptyProps(),
    'Reconcile Success': props<{ result: ReconcileResult }>(),
    'Reconcile Failure': props<{ error: string }>(),

    'Start Triage': emptyProps(),
    'Triage Started': emptyProps(),
    'Triage Failure': props<{ error: string }>(),
    'Summary Polled': props<{ summary: Summary }>(),
    'Triage Finished': emptyProps(),

    'Resolve': props<{ id: string; notes: string; previous: Discrepancy }>(),
    'Resolve Success': props<{ row: Discrepancy }>(),
    'Resolve Failure': props<{ id: string; error: string; previous: Discrepancy }>(),
    'Reopen': props<{ id: string; previous: Discrepancy }>(),
    'Reopen Success': props<{ row: Discrepancy }>(),
    'Reopen Failure': props<{ id: string; error: string; previous: Discrepancy }>(),

    'Set Status Filter': props<{ status: StatusFilter }>(),
    'Set Type Filter': props<{ discrepancyType: string }>(),
    'Dismiss Message': emptyProps(),
  },
});
