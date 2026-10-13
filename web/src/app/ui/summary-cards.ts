import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CurrencyPipe, DecimalPipe } from '@angular/common';
import { Store } from '@ngrx/store';
import { settleGuardFeature } from '../state/reducer';
import { selectOpenExposure, selectTriageProgress } from '../state/selectors';

@Component({
  selector: 'sg-summary-cards',
  imports: [CurrencyPipe, DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (summary(); as s) {
      <section class="cards" aria-label="Summary">
        <div class="card"><span class="n">{{ s.total }}</span><span class="l">Discrepancies</span></div>
        <div class="card"><span class="n">{{ s.open }}</span><span class="l">Open</span></div>
        <div class="card"><span class="n">{{ s.resolved }}</span><span class="l">Resolved</span></div>
        <div class="card"><span class="n">{{ s.triaged }}/{{ s.total }}</span><span class="l">AI triaged</span></div>
        <div class="card">
          <span class="n">{{ exposure() | currency: 'INR' : 'symbol' : '1.2-2' }}</span>
          <span class="l">Open exposure</span>
        </div>
      </section>
      @if (progress(); as p) {
        <div class="progress" role="progressbar" [attr.aria-valuenow]="p.percent" aria-valuemin="0" aria-valuemax="100">
          <div class="bar" [style.width.%]="p.percent"></div>
          <span>AI triage {{ p.done | number }} / {{ p.total | number }}</span>
        </div>
      }
    }
  `,
})
export class SummaryCards {
  private readonly store = inject(Store);
  readonly summary = this.store.selectSignal(settleGuardFeature.selectSummary);
  readonly exposure = this.store.selectSignal(selectOpenExposure);
  readonly progress = this.store.selectSignal(selectTriageProgress);
}
