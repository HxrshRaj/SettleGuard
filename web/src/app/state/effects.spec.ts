import { TestBed, fakeAsync, tick, discardPeriodicTasks } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { provideMockActions } from '@ngrx/effects/testing';
import { Action } from '@ngrx/store';
import { Observable, of, throwError } from 'rxjs';
import { SettleGuardApi } from '../settleguard-api';
import { row, summary } from '../testing';
import { SettleGuardActions as A } from './actions';
import { POLL_MS, SettleGuardEffects, errText } from './effects';

describe('SettleGuardEffects', () => {
  let actions$: Observable<Action>;
  let api: jasmine.SpyObj<SettleGuardApi>;

  const setup = (a: Observable<Action>) => {
    actions$ = a;
    return TestBed.inject(SettleGuardEffects);
  };

  beforeEach(() => {
    api = jasmine.createSpyObj('SettleGuardApi', ['discrepancies', 'summary', 'reconcile', 'startTriage', 'resolve', 'reopen']);
    TestBed.configureTestingModule({
      providers: [SettleGuardEffects, provideMockActions(() => actions$), { provide: SettleGuardApi, useValue: api }],
    });
  });

  it('load fetches rows and summary together', (done) => {
    api.discrepancies.and.returnValue(of([row()]));
    api.summary.and.returnValue(of(summary()));
    setup(of(A.load())).load$.subscribe((a) => {
      expect(a).toEqual(A.loadSuccess({ rows: [row()], summary: summary() }));
      done();
    });
  });

  it('load maps an HTTP error to a failure action using the server message', (done) => {
    api.discrepancies.and.returnValue(throwError(() => new HttpErrorResponse({ status: 500, error: { error: 'db down' } })));
    api.summary.and.returnValue(of(summary()));
    setup(of(A.load())).load$.subscribe((a) => {
      expect(a).toEqual(A.loadFailure({ error: 'db down' }));
      done();
    });
  });

  it('resolve success and failure (with rollback payload)', (done) => {
    const previous = row();
    api.resolve.and.returnValue(of(row({ resolved: 1 })));
    setup(of(A.resolve({ id: 'x', notes: 'n', previous }))).resolve$.subscribe((a) => {
      expect(a).toEqual(A.resolveSuccess({ row: row({ resolved: 1 }) }));
      api.resolve.and.returnValue(throwError(() => new Error('nope')));
      TestBed.inject(SettleGuardEffects).resolve$.subscribe((b) => {
        expect(b).toEqual(A.resolveFailure({ id: 'x', error: 'nope', previous }));
        done();
      });
    });
  });

  it('reconcile ignores a second click while one is in flight (exhaustMap)', fakeAsync(() => {
    let calls = 0;
    api.reconcile.and.callFake(() => { calls++; return new Observable((s) => { setTimeout(() => { s.next({ ok: true, found: 0, new: 0, updated: 0, deactivated: 0, triage_available: false, triage_started: null }); s.complete(); }, 100); }); });
    const out: Action[] = [];
    setup(new Observable<Action>((s) => { s.next(A.reconcile()); s.next(A.reconcile()); })).reconcile$.subscribe((a) => out.push(a));
    tick(100);
    expect(calls).toBe(1);
    expect(out.length).toBe(1);
  }));

  it('polls the summary until the triage job stops, then signals completion', fakeAsync(() => {
    const running = summary({ triage_job: { running: true, done: 1, total: 3, errors: [] } });
    const done = summary({ triage_job: { running: false, done: 3, total: 3, errors: [] } });
    const seq = [running, running, done];
    api.summary.and.callFake(() => of(seq.shift() ?? done));
    const out: Action[] = [];
    setup(of(A.triageStarted())).pollTriage$.subscribe((a) => out.push(a));
    tick(POLL_MS * 3);
    discardPeriodicTasks();
    expect(out.map((a) => a.type)).toEqual([
      A.summaryPolled.type, A.summaryPolled.type, A.summaryPolled.type, A.triageFinished.type,
    ]);
    expect(api.summary).toHaveBeenCalledTimes(3);
  }));

  it('does not poll after a reconcile that started no triage', fakeAsync(() => {
    const result = { ok: true, found: 1, new: 1, updated: 0, deactivated: 0, triage_available: false, triage_started: null };
    const out: Action[] = [];
    setup(of(A.reconcileSuccess({ result }))).pollTriage$.subscribe((a) => out.push(a));
    tick(POLL_MS * 2);
    expect(api.summary).not.toHaveBeenCalled();
    expect(out).toEqual([]);
  }));

  it('reloads after triage finishes', (done) => {
    setup(of(A.triageFinished())).reloadAfterTriage$.subscribe((a) => {
      expect(a).toEqual(A.load());
      done();
    });
  });

  it('errText handles http, Error and unknown values', () => {
    expect(errText(new Error('a'))).toBe('a');
    expect(errText('plain')).toBe('plain');
    expect(errText(new HttpErrorResponse({ status: 404, error: { error: 'unknown id' } }))).toBe('unknown id');
  });
});
