import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideEffects } from '@ngrx/effects';
import { provideStore } from '@ngrx/store';
import { App } from './app';
import { row, summary } from './testing';
import { SettleGuardEffects } from './state/effects';
import { settleGuardFeature } from './state/reducer';

describe('App (store + effects + http, end to end)', () => {
  let http: HttpTestingController;

  const create = () => {
    const f = TestBed.createComponent(App);
    f.detectChanges();
    return f;
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        provideStore({ [settleGuardFeature.name]: settleGuardFeature.reducer }),
        provideEffects(SettleGuardEffects),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify({ ignoreCancelled: true }));

  const flushLoad = (rows = [row({ discrepancy_id: 'A' }), row({ discrepancy_id: 'B', txn_id: 'TXN2' })]) => {
    http.expectOne('/api/discrepancies?all=1').flush(rows);
    http.expectOne('/api/summary').flush(summary({ total: rows.length, open: rows.length }));
  };

  it('loads and renders rows, cards and exposure', async () => {
    const f = create();
    flushLoad();
    await f.whenStable();
    f.detectChanges();
    const el: HTMLElement = f.nativeElement;
    expect(el.querySelectorAll('tbody tr').length).toBe(2);
    expect(el.querySelector('.cards')?.textContent).toContain('Open exposure');
    expect(el.textContent).toContain('₹20.00'); // |-10| * 2 open rows
  });

  it('resolving a row posts the notes and moves it out of the open filter', async () => {
    const f = create();
    flushLoad();
    await f.whenStable();
    f.detectChanges();
    const el: HTMLElement = f.nativeElement;
    (Array.from(el.querySelectorAll('tbody button')).find((b) => b.textContent?.trim() === 'Resolve') as HTMLButtonElement).click();
    f.detectChanges();
    const input = el.querySelector('input[aria-label="Resolution notes"]') as HTMLInputElement;
    input.value = 'confirmed with bank';
    input.dispatchEvent(new Event('input'));
    f.detectChanges();
    (el.querySelector('form button[type=submit]') as HTMLButtonElement).click();
    const req = http.expectOne('/api/resolve/A');
    expect(req.request.body).toEqual({ notes: 'confirmed with bank' });
    f.detectChanges();
    expect(el.querySelectorAll('tbody tr').length).toBe(1); // optimistic: gone from "open" immediately
    req.flush({ ok: true, discrepancy: row({ discrepancy_id: 'A', resolved: 1, resolution_notes: 'confirmed with bank' }) });
  });

  it('a failed resolve rolls the row back and shows the error', async () => {
    const f = create();
    flushLoad();
    await f.whenStable();
    f.detectChanges();
    const el: HTMLElement = f.nativeElement;
    (Array.from(el.querySelectorAll('tbody button')).find((b) => b.textContent?.trim() === 'Resolve') as HTMLButtonElement).click();
    f.detectChanges();
    const input = el.querySelector('input[aria-label="Resolution notes"]') as HTMLInputElement;
    input.value = 'x';
    input.dispatchEvent(new Event('input'));
    f.detectChanges();
    (el.querySelector('form button[type=submit]') as HTMLButtonElement).click();
    http.expectOne('/api/resolve/A').flush({ ok: false, error: 'unknown discrepancy_id' }, { status: 404, statusText: 'Not Found' });
    f.detectChanges();
    expect(el.querySelectorAll('tbody tr').length).toBe(2);
    expect(el.querySelector('.msg.error')?.textContent).toContain('unknown discrepancy_id');
  });

  it('shows an error banner when loading fails', async () => {
    const f = create();
    http.expectOne('/api/discrepancies?all=1').flush({ error: 'db down' }, { status: 500, statusText: 'x' });
    await f.whenStable();
    f.detectChanges();
    expect((f.nativeElement as HTMLElement).querySelector('.msg.error')?.textContent).toContain('db down');
  });

  it('empty-note submit is blocked by form validation', async () => {
    const f = create();
    flushLoad();
    await f.whenStable();
    f.detectChanges();
    const el: HTMLElement = f.nativeElement;
    (Array.from(el.querySelectorAll('tbody button')).find((b) => b.textContent?.trim() === 'Resolve') as HTMLButtonElement).click();
    f.detectChanges();
    expect((el.querySelector('form button[type=submit]') as HTMLButtonElement).disabled).toBeTrue();
  });
});
