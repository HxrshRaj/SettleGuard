import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Store } from '@ngrx/store';
import { StatusFilter } from '../models';
import { SettleGuardActions as A } from '../state/actions';
import { settleGuardFeature } from '../state/reducer';
import { selectTypes } from '../state/selectors';

@Component({
  selector: 'sg-filters',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="filters">
      <div class="group" role="group" aria-label="Status">
        @for (s of statuses; track s) {
          <button type="button" [class.active]="status() === s" (click)="setStatus(s)">{{ s }}</button>
        }
      </div>
      <div class="group" role="group" aria-label="Type">
        <button type="button" [class.active]="type() === 'all'" (click)="setType('all')">all types</button>
        @for (t of types(); track t) {
          <button type="button" [class.active]="type() === t" (click)="setType(t)">{{ t }}</button>
        }
      </div>
    </section>
  `,
})
export class Filters {
  private readonly store = inject(Store);
  readonly statuses: StatusFilter[] = ['open', 'resolved', 'all'];
  readonly status = this.store.selectSignal(settleGuardFeature.selectStatusFilter);
  readonly type = this.store.selectSignal(settleGuardFeature.selectTypeFilter);
  readonly types = this.store.selectSignal(selectTypes);

  setStatus(status: StatusFilter) {
    this.store.dispatch(A.setStatusFilter({ status }));
  }
  setType(type: string) {
    this.store.dispatch(A.setTypeFilter({ discrepancyType: type }));
  }
}
