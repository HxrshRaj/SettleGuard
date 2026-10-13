import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Actions, createEffect, ofType } from '@ngrx/effects';
import { EMPTY, Observable, catchError, concatMap, exhaustMap, filter, forkJoin, map, merge, of, switchMap, takeWhile, timer } from 'rxjs';
import { Summary } from '../models';
import { SettleGuardApi } from '../settleguard-api';
import { SettleGuardActions as A } from './actions';

export const POLL_MS = 1500;

export const errText = (e: unknown): string =>
  e instanceof HttpErrorResponse
    ? ((e.error && e.error.error) as string) || e.message
    : e instanceof Error
      ? e.message
      : String(e);

@Injectable()
export class SettleGuardEffects {
  private readonly actions$ = inject(Actions);
  private readonly api = inject(SettleGuardApi);

  /** Rows and summary load together; a failure of either surfaces one error. */
  load$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.load, A.reconcileSuccess, A.triageStarted),
      switchMap(() =>
        forkJoin({ rows: this.api.discrepancies(), summary: this.api.summary() }).pipe(
          map(({ rows, summary }) => A.loadSuccess({ rows, summary })),
          catchError((e) => of(A.loadFailure({ error: errText(e) }))),
        ),
      ),
    ),
  );

  // exhaustMap: a double click on "Re-run" must not start two reconciliations.
  reconcile$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.reconcile),
      exhaustMap(() =>
        this.api.reconcile().pipe(
          map((result) => A.reconcileSuccess({ result })),
          catchError((e) => of(A.reconcileFailure({ error: errText(e) }))),
        ),
      ),
    ),
  );

  startTriage$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.startTriage),
      exhaustMap(() =>
        this.api.startTriage().pipe(
          map(() => A.triageStarted()),
          catchError((e) => of(A.triageFailure({ error: errText(e) }))),
        ),
      ),
    ),
  );

  /**
   * After a reconcile that began triage, or an explicit triage start, poll the summary until the
   * background job stops. switchMap cancels a previous poll loop if a new one starts.
   */
  pollTriage$ = createEffect(() =>
    merge(
      this.actions$.pipe(ofType(A.triageStarted)),
      this.actions$.pipe(
        ofType(A.reconcileSuccess),
        filter(({ result }) => !!result.triage_started),
      ),
    ).pipe(switchMap(() => this.poll())),
  );

  private poll(): Observable<ReturnType<typeof A.summaryPolled> | ReturnType<typeof A.triageFinished>> {
    return timer(0, POLL_MS).pipe(
      switchMap(() => this.api.summary().pipe(catchError(() => EMPTY))),
      takeWhile((s: Summary) => s.triage_job.running, true),
      concatMap((summary) =>
        summary.triage_job.running
          ? of(A.summaryPolled({ summary }))
          : of(A.summaryPolled({ summary }), A.triageFinished()),
      ),
    );
  }

  /** When triage finishes, reload so the new notes appear in the table. */
  reloadAfterTriage$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.triageFinished),
      map(() => A.load()),
    ),
  );

  resolve$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.resolve),
      concatMap(({ id, notes, previous }) =>
        this.api.resolve(id, notes).pipe(
          map((row) => A.resolveSuccess({ row })),
          catchError((e) => of(A.resolveFailure({ id, error: errText(e), previous }))),
        ),
      ),
    ),
  );

  reopen$ = createEffect(() =>
    this.actions$.pipe(
      ofType(A.reopen),
      concatMap(({ id, previous }) =>
        this.api.reopen(id).pipe(
          map((row) => A.reopenSuccess({ row })),
          catchError((e) => of(A.reopenFailure({ id, error: errText(e), previous }))),
        ),
      ),
    ),
  );
}
