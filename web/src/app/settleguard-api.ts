import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import { Discrepancy, ReconcileResult, Summary } from './models';

/** Thin typed wrapper over the Flask JSON API. URLs are relative so the app works behind any host. */
@Injectable({ providedIn: 'root' })
export class SettleGuardApi {
  private readonly http = inject(HttpClient);

  discrepancies(): Observable<Discrepancy[]> {
    return this.http.get<Discrepancy[]>('/api/discrepancies?all=1');
  }

  summary(): Observable<Summary> {
    return this.http.get<Summary>('/api/summary');
  }

  reconcile(): Observable<ReconcileResult> {
    return this.http.post<ReconcileResult>('/api/reconcile', {});
  }

  startTriage(): Observable<{ ok: boolean; pending?: number; already_running?: boolean }> {
    return this.http.post<{ ok: boolean; pending?: number; already_running?: boolean }>('/api/triage', {});
  }

  resolve(id: string, notes: string): Observable<Discrepancy> {
    return this.http
      .post<{ discrepancy: Discrepancy }>(`/api/resolve/${encodeURIComponent(id)}`, { notes })
      .pipe(map((r) => r.discrepancy));
  }

  reopen(id: string): Observable<Discrepancy> {
    return this.http
      .post<{ discrepancy: Discrepancy }>(`/api/reopen/${encodeURIComponent(id)}`, {})
      .pipe(map((r) => r.discrepancy));
  }
}
