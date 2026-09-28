import { TestBed } from '@angular/core/testing';
import { Approval } from './api';
import { ApprovalInbox } from './approval-inbox';

const NOW = Date.parse('2026-09-28T09:00:00.000Z');

const approval = (fields: Partial<Approval> = {}): Approval => ({
  id: 'ap1',
  run_id: 'run123456',
  tool_call_id: 'c1',
  tool: 'create_incident',
  args: { title: 'payments-api 5xx', severity: 'SEV2' },
  status: 'pending',
  decision: null,
  reason: null,
  decided_by: null,
  created_at: '2026-09-28T08:59:00.000Z',
  decided_at: null,
  expires_at: new Date(NOW + 300_000).toISOString(),
  ...fields,
});

/** Every POST answers with `status` and `detail`; the calls are kept for the asserts. */
function stubFetch(status = 200, detail: unknown = 'ok') {
  const fetch = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify(status === 200 ? { id: 'ap1' } : { detail }), { status }),
  );
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

describe('ApprovalInbox', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function render(approvals: Approval[] = [approval()]) {
    const fixture = TestBed.createComponent(ApprovalInbox);
    fixture.componentRef.setInput('approvals', approvals);
    const events = { decided: 0, openRun: [] as string[] };
    fixture.componentInstance.decided.subscribe(() => (events.decided += 1));
    fixture.componentInstance.openRun.subscribe((id) => events.openRun.push(id));
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    const button = (text: string) =>
      [...root.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
    const type = async (selector: string, value: string) => {
      const field = root.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement;
      field.value = value;
      field.dispatchEvent(new Event('input'));
      await fixture.whenStable();
    };
    const click = async (text: string) => {
      button(text).click();
      await fixture.whenStable();
    };
    return { fixture, root, events, button, type, click };
  }

  const body = (fetch: ReturnType<typeof stubFetch>) =>
    JSON.parse(fetch.mock.calls[0][1]!.body as string);

  it('lists a pending approval with its tool, run, args and countdown', async () => {
    const { root, events, click } = await render();
    expect(root.textContent).toContain('create_incident');
    expect(root.querySelector('pre')!.textContent).toContain('"severity": "SEV2"');
    expect(root.querySelector('.countdown')!.textContent).toBe('05:00');
    await click('run run123');
    expect(events.openRun).toEqual(['run123456']);
  });

  it('shows expired at zero and keeps its buttons', async () => {
    const { root, button } = await render([approval({ expires_at: new Date(NOW).toISOString() })]);
    expect(root.querySelector('.countdown')!.textContent).toBe('expired');
    expect(button('Approve').disabled).toBe(false);
  });

  it('approve posts {decision: approve} without a token header, then emits decided', async () => {
    const fetch = stubFetch();
    const { events, click } = await render();
    await click('Approve');
    expect(fetch.mock.calls[0][0]).toBe('/api/runs/run123456/approvals/ap1');
    expect(body(fetch)).toEqual({ decision: 'approve' });
    expect(fetch.mock.calls[0][1]!.headers).not.toHaveProperty('X-Approver-Token');
    expect(events.decided).toBe(1);
  });

  it('sends X-Approver-Token when the field is set', async () => {
    const fetch = stubFetch();
    const { type, click } = await render();
    await type('input[type="password"]', 's3cret');
    await click('Approve');
    expect(fetch.mock.calls[0][1]!.headers).toMatchObject({ 'X-Approver-Token': 's3cret' });
  });

  it('reject needs a reason that is not blank', async () => {
    const fetch = stubFetch();
    const { button, type, click } = await render();
    await click('Reject');
    expect(button('Send reject').disabled).toBe(true);
    await type('input[maxlength="500"]', '   ');
    expect(button('Send reject').disabled).toBe(true);
    await type('input[maxlength="500"]', 'not a SEV2');
    await click('Send reject');
    expect(body(fetch)).toEqual({ decision: 'reject', reason: 'not a SEV2' });
  });

  it('edit sends the parsed args; bad JSON sends nothing', async () => {
    const fetch = stubFetch();
    const { root, type, click } = await render();
    await click('Edit');
    const textarea = root.querySelector('textarea')!;
    expect(JSON.parse(textarea.value)).toEqual({ title: 'payments-api 5xx', severity: 'SEV2' });

    await type('textarea', '{"title": ');
    await click('Send edit');
    expect(fetch).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('JSON object');

    await type('textarea', '["SEV3"]');
    await click('Send edit');
    expect(fetch).not.toHaveBeenCalled();

    await type('textarea', '{"title": "payments-api 5xx", "severity": "SEV3"}');
    await click('Send edit');
    expect(body(fetch)).toEqual({
      decision: 'edit',
      args: { title: 'payments-api 5xx', severity: 'SEV3' },
    });
  });

  it('a 422 shows the detail and keeps the item', async () => {
    stubFetch(422, 'severity: Input should be SEV1, SEV2 or SEV3');
    const { root, events, type, click, button } = await render();
    await click('Edit');
    await type('textarea', '{"title": "x", "severity": "SEV9"}');
    await click('Send edit');
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('Input should be SEV1');
    expect(events.decided).toBe(0);
    expect(button('Send edit')).toBeDefined(); // the form stays open for another try
  });

  it('a 409 shows the detail and emits decided so the list reloads', async () => {
    stubFetch(409, 'approval is already approved');
    const { root, events, click } = await render();
    await click('Approve');
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('already approved');
    expect(events.decided).toBe(1);
  });

  it('a 404 shows the detail and emits decided so the list reloads', async () => {
    stubFetch(404, 'approval not found');
    const { root, events, click } = await render();
    await click('Approve');
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('not found');
    expect(events.decided).toBe(1);
  });

  it('a 401 shows the detail and keeps the item without emitting decided', async () => {
    stubFetch(401, 'invalid approver token');
    const { root, events, click, button } = await render();
    await click('Approve');
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('invalid approver token');
    expect(events.decided).toBe(0);
    expect(button('Approve')).toBeDefined(); // the item stays in the list
  });

  it('disables the buttons while a request is in flight', async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((r) => {
          resolve = r;
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const { button, click, fixture } = await render();
    const clickPromise = (async () => {
      button('Approve').click();
      await fixture.whenStable();
    })();
    await Promise.resolve();
    await fixture.whenStable();
    expect(button('Approve').disabled).toBe(true);
    expect(button('Edit').disabled).toBe(true);
    expect(button('Reject').disabled).toBe(true);
    resolve(new Response(JSON.stringify({ id: 'ap1' }), { status: 200 }));
    await clickPromise;
  });

  it('opens one form at a time: opening a second item closes the first', async () => {
    const { root, fixture } = await render([
      approval({ id: 'ap1' }),
      approval({ id: 'ap2', tool: 'get_service_status' }),
    ]);
    const editButtons = () =>
      [...root.querySelectorAll('button')].filter((b) => b.textContent?.trim() === 'Edit');
    editButtons()[0].click();
    await fixture.whenStable();
    expect(root.querySelectorAll('textarea').length).toBe(1);
    editButtons()[0].click(); // the remaining "Edit" button now belongs to the second item
    await fixture.whenStable();
    expect(root.querySelectorAll('textarea').length).toBe(1); // still only one form open
  });

  it("a decision on one item keeps another item's open form and draft", async () => {
    stubFetch();
    const { root, fixture, type } = await render([
      approval({ id: 'ap1' }),
      approval({ id: 'ap2', tool: 'get_service_status' }),
    ]);
    const buttons = (text: string) =>
      [...root.querySelectorAll('button')].filter((b) => b.textContent?.trim() === text);
    buttons('Edit')[0].click();
    await fixture.whenStable();
    await type('textarea', '{"severity": "SEV3"}');
    buttons('Approve')[0].click(); // the second item's: the first shows its form instead
    await fixture.whenStable();
    expect(root.querySelector('textarea')!.value).toBe('{"severity": "SEV3"}');
  });

  it('refuses a token an HTTP header cannot carry, with a local message', async () => {
    const fetch = stubFetch();
    const { root, type, click } = await render();
    await type('input[type="password"]', 'token\u201d');
    await click('Approve');
    expect(fetch).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('HTTP header');
  });

  it('ticks the countdown down with the 1 s timer', async () => {
    const { root } = await render([approval({ expires_at: new Date(NOW + 65_000).toISOString() })]);
    expect(root.querySelector('.countdown')!.textContent).toBe('01:05');
    await vi.advanceTimersByTimeAsync(5000);
    expect(root.querySelector('.countdown')!.textContent).toBe('01:00');
  });

  it('never writes the token to localStorage or sessionStorage', async () => {
    const { type } = await render();
    await type('input[type="password"]', 's3cret');
    expect(JSON.stringify(localStorage)).not.toContain('s3cret');
    expect(JSON.stringify(sessionStorage)).not.toContain('s3cret');
    for (let i = 0; i < localStorage.length; i++) {
      expect(localStorage.getItem(localStorage.key(i)!)).not.toContain('s3cret');
    }
  });
});
