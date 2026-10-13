import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { Store } from '@ngrx/store';
import { SettleGuardActions as A } from './state/actions';
import { settleGuardFeature } from './state/reducer';
import { DiscrepancyTable } from './ui/discrepancy-table';
import { Filters } from './ui/filters';
import { SummaryCards } from './ui/summary-cards';

@Component({
  selector: 'app-root',
  imports: [SummaryCards, Filters, DiscrepancyTable],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header>
      <div>
        <h1>SettleGuard</h1>
        <span class="tag">Angular + NgRx edition · settlement reconciliation &amp; triage</span>
      </div>
      <div class="actions">
        <button type="button" class="primary" [disabled]="busy()" (click)="reconcile()">Re-run reconciliation</button>
        <button type="button" [disabled]="busy()" (click)="triage()">Run AI triage</button>
      </div>
    </header>
    @if (message(); as m) {
      <div class="msg" [class.error]="m.kind === 'error'" role="status">
        {{ m.text }} <button type="button" class="link" (click)="dismiss()" aria-label="Dismiss">×</button>
      </div>
    }
    <sg-summary-cards />
    <sg-filters />
    <main><sg-discrepancy-table /></main>
  `,
})
export class App implements OnInit {
  private readonly store = inject(Store);
  readonly busy = this.store.selectSignal(settleGuardFeature.selectBusy);
  readonly message = this.store.selectSignal(settleGuardFeature.selectMessage);

  ngOnInit() {
    this.store.dispatch(A.load());
  }
  reconcile() {
    this.store.dispatch(A.reconcile());
  }
  triage() {
    this.store.dispatch(A.startTriage());
  }
  dismiss() {
    this.store.dispatch(A.dismissMessage());
  }
}
