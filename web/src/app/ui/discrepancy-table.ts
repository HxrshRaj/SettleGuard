import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { CurrencyPipe } from '@angular/common';
import { Store } from '@ngrx/store';
import { Discrepancy } from '../models';
import { SettleGuardActions as A } from '../state/actions';
import { selectVisibleRows } from '../state/selectors';

@Component({
  selector: 'sg-discrepancy-table',
  imports: [ReactiveFormsModule, CurrencyPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <table>
      <thead>
        <tr>
          <th>Type</th><th>Txn / Merchant</th><th class="num">Platform</th><th class="num">Bank</th>
          <th class="num">Delta</th><th>Severity</th><th>AI triage note</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        @for (r of rows(); track r.discrepancy_id) {
          <tr [class.resolved]="r.resolved">
            <td><span class="chip">{{ r.type }}</span></td>
            <td>{{ r.txn_id }}<br /><small>{{ r.merchant_name }}</small></td>
            <td class="num">{{ r.platform_amount === null ? '—' : (r.platform_amount | currency: 'INR') }}</td>
            <td class="num">{{ r.bank_amount === null ? '—' : (r.bank_amount | currency: 'INR') }}</td>
            <td class="num">{{ r.amount_delta === null ? '—' : (r.amount_delta | currency: 'INR') }}</td>
            <td><span class="sev" [class]="'sev ' + severity(r)">{{ severity(r) }}</span></td>
            <td class="note">
              @if (r.triage_root_cause) {
                <strong>{{ r.triage_root_cause }}</strong><br /><small>{{ r.triage_next_action }}</small>
              } @else {
                <small>{{ r.detail }}</small>
              }
            </td>
            <td>
              @if (r.resolved) {
                <small>{{ r.resolution_notes }}</small><br />
                <button type="button" (click)="reopen(r)">Reopen</button>
              } @else if (editing === r.discrepancy_id) {
                <form [formGroup]="form" (ngSubmit)="submit(r)">
                  <input formControlName="notes" placeholder="Resolution notes" aria-label="Resolution notes" />
                  <button type="submit" [disabled]="form.invalid">Save</button>
                  <button type="button" (click)="editing = null">Cancel</button>
                </form>
              } @else {
                <button type="button" (click)="startEdit(r)">Resolve</button>
              }
            </td>
          </tr>
        } @empty {
          <tr><td colspan="8" class="empty">No discrepancies match this filter.</td></tr>
        }
      </tbody>
    </table>
  `,
})
export class DiscrepancyTable {
  private readonly store = inject(Store);
  private readonly fb = inject(FormBuilder).nonNullable;
  readonly rows = this.store.selectSignal(selectVisibleRows);
  editing: string | null = null;
  readonly form = this.fb.group({ notes: ['', [Validators.required, Validators.pattern(/\S/)]] });

  severity(r: Discrepancy): string {
    return r.triage_severity || r.severity_hint || 'unknown';
  }
  startEdit(r: Discrepancy) {
    this.editing = r.discrepancy_id;
    this.form.reset();
  }
  submit(r: Discrepancy) {
    if (this.form.invalid) return;
    this.store.dispatch(A.resolve({ id: r.discrepancy_id, notes: this.form.value.notes!.trim(), previous: r }));
    this.editing = null;
  }
  reopen(r: Discrepancy) {
    this.store.dispatch(A.reopen({ id: r.discrepancy_id, previous: r }));
  }
}
