import { TestBed } from '@angular/core/testing';
import { IncidentsTab } from './incidents-tab';

const incident = {
  id: 'INC-0A1B2C3D',
  run_id: 'run123456',
  title: 'payments-api 5xx',
  description: 'error rate 12%',
  severity: 'SEV2',
  status: 'open',
  created_at: '2026-09-28T09:00:00.000Z',
};

describe('IncidentsTab', () => {
  afterEach(() => vi.unstubAllGlobals());

  /** The read is a plain `fetch`, which `whenStable()` does not wait for. */
  const settle = (fixture: { whenStable(): Promise<unknown> }, check: () => void) =>
    vi.waitFor(async () => {
      await fixture.whenStable();
      check();
    });

  it('reads the incidents each time the tab is shown, not while hidden', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify([incident]), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const fixture = TestBed.createComponent(IncidentsTab);
    await fixture.whenStable();
    expect(fetch).not.toHaveBeenCalled();

    fixture.componentRef.setInput('active', true);
    const root = fixture.nativeElement as HTMLElement;
    await settle(fixture, () => expect(root.textContent).toContain('INC-0A1B2C3D'));
    expect(fetch).toHaveBeenCalledWith('/api/incidents', expect.anything());
    const cells = [...root.querySelectorAll('tbody td')].map((td) => td.textContent?.trim());
    expect(cells.slice(0, 5)).toEqual([
      'INC-0A1B2C3D',
      'run123',
      'payments-api 5xx',
      'SEV2',
      'open',
    ]);
    expect(root.querySelector('tbody tr')!.getAttribute('title')).toBe('error rate 12%');

    fixture.componentRef.setInput('active', false);
    await fixture.whenStable();
    fixture.componentRef.setInput('active', true);
    await fixture.whenStable();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('shows a failed read and keeps the last list', async () => {
    let fail = false;
    vi.stubGlobal('fetch', async () =>
      fail
        ? new Response(JSON.stringify({ detail: 'database is locked' }), { status: 500 })
        : new Response(JSON.stringify([incident]), { status: 200 }),
    );
    const fixture = TestBed.createComponent(IncidentsTab);
    fixture.componentRef.setInput('active', true);
    const root = fixture.nativeElement as HTMLElement;
    await settle(fixture, () => expect(root.textContent).toContain('INC-0A1B2C3D'));
    fail = true;
    fixture.componentRef.setInput('active', false);
    await fixture.whenStable();
    fixture.componentRef.setInput('active', true);
    await settle(fixture, () =>
      expect(root.querySelector('[role="alert"]')?.textContent).toContain('database is locked'),
    );
    expect(root.textContent).toContain('INC-0A1B2C3D');
  });
});
