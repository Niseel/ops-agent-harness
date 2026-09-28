import { TestBed } from '@angular/core/testing';
import { RunsPage } from './runs-page';
import { TraceEvent } from './api';

/** jsdom has no EventSource: a fake that records what the code does with it (mirrors api.spec.ts). */
class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: ((message: { data: string }) => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  close(): void {
    this.closed = true;
  }
  send(event: Partial<TraceEvent>): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

let seq = 0;
const ev = (kind: string, fields: Partial<TraceEvent> = {}): TraceEvent => {
  seq += 1;
  return {
    seq,
    run_id: 'r1',
    t_ms: seq * 1000,
    kind,
    node: null,
    tool: null,
    status: null,
    attention: null,
    msg: `${kind} ${seq}`,
    data: null,
    created_at: '2026-09-28T09:00:00.000Z',
    ...fields,
  };
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

/** Routes fetch by URL; `detail` and `trace` are read fresh on every call so a test can mutate them. */
function stubFetch(opts: {
  runs?: unknown[];
  detail?: () => unknown;
  trace?: () => { events: TraceEvent[] };
  onCall?: (url: string) => void;
}) {
  const fetch = vi.fn(async (url: string) => {
    opts.onCall?.(url);
    if (url === '/api/runs') return jsonResponse(opts.runs ?? []);
    if (/\/api\/runs\/[^/]+\/trace$/.test(url))
      return jsonResponse(opts.trace?.() ?? { events: [] });
    if (/\/api\/runs\/[^/]+$/.test(url)) return jsonResponse(opts.detail?.() ?? { id: 'r1' });
    return jsonResponse({}, 404);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

describe('RunsPage', () => {
  beforeEach(() => {
    seq = 0;
    FakeEventSource.all = [];
    vi.stubGlobal('EventSource', FakeEventSource);
    // `shouldAdvanceTime`: TestBed's zoneless `whenStable()` schedules its own real timers; without this,
    // faking `setTimeout` globally blocks it forever.
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opening a run subscribes and renders timeline items as events arrive', async () => {
    stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    await fixture.whenStable();

    const source = FakeEventSource.all.at(-1)!;
    expect(source.url).toBe('/api/runs/r1/events');
    source.send(ev('stage', { node: 'guard', data: { steps: 1, tool_calls: 0 } }));
    await fixture.whenStable();

    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('.timeline li').length).toBe(1);
  });

  it('closes the stream and refreshes on done', async () => {
    let calls = 0;
    stubFetch({
      detail: () => ({ id: 'r1', status: 'ok', options: { limits: {}, evaluate: false } }),
      onCall: (url) => {
        if (url === '/api/runs') calls += 1;
      },
    });
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    await fixture.whenStable();
    const callsBeforeDone = calls;

    const source = FakeEventSource.all.at(-1)!;
    source.send(ev('done', { status: 'ok', data: { status: 'ok' } }));
    await fixture.whenStable();

    expect(source.closed).toBe(true);
    expect(calls).toBeGreaterThan(callsBeforeDone);
  });

  it('reads /trace after done when evaluate is true, and stops once the expected evals are in', async () => {
    let traceReads = 0;
    const fetch = stubFetch({
      detail: () => ({
        id: 'r1',
        status: 'ok',
        final: 'done',
        options: { limits: {}, evaluate: true },
      }),
      trace: () => {
        traceReads += 1;
        // the first read carries none of the two expected `eval` events (a final answer expects 2); the
        // second read carries both, so the poller must stop instead of scheduling a third read.
        const events =
          traceReads >= 2 ? [ev('eval', { kind: 'eval' }), ev('eval', { kind: 'eval' })] : [];
        return { events };
      },
    });
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    await fixture.whenStable();

    const source = FakeEventSource.all.at(-1)!;
    source.send(ev('done', { status: 'ok', data: { status: 'ok' } }));
    await fixture.whenStable();
    expect(traceReads).toBe(1);

    await vi.advanceTimersByTimeAsync(3000);
    await fixture.whenStable();
    expect(traceReads).toBe(2);

    const readsAtStop = fetch.mock.calls.filter((c) => /\/trace$/.test(c[0] as string)).length;
    await vi.advanceTimersByTimeAsync(3000);
    await fixture.whenStable();
    expect(fetch.mock.calls.filter((c) => /\/trace$/.test(c[0] as string)).length).toBe(
      readsAtStop,
    );
  });

  it('never reads /trace when evaluate is false or the run is cancelled', async () => {
    const detailFn = vi.fn(() => ({
      id: 'r1',
      status: 'cancelled',
      options: { limits: {}, evaluate: true },
    }));
    const fetch = stubFetch({ detail: detailFn });
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    await fixture.whenStable();
    FakeEventSource.all
      .at(-1)!
      .send(ev('done', { status: 'cancelled', data: { status: 'cancelled' } }));
    await fixture.whenStable();
    await vi.advanceTimersByTimeAsync(4000);
    await fixture.whenStable();
    expect(fetch.mock.calls.some((c) => /\/trace$/.test(c[0] as string))).toBe(false);
  });

  it('opening another run closes the previous stream and stops its eval reads', async () => {
    let traceReads = 0;
    stubFetch({
      detail: () => ({
        id: 'x',
        status: 'ok',
        final: 'done',
        options: { limits: {}, evaluate: true },
      }),
      trace: () => {
        traceReads += 1;
        return { events: [] }; // never reaches the expected count, so it would keep polling forever
      },
    });
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    await fixture.whenStable();
    const firstSource = FakeEventSource.all.at(-1)!;
    firstSource.send(ev('done', { status: 'ok', data: { status: 'ok' } }));
    await fixture.whenStable();
    expect(traceReads).toBe(1);

    fixture.componentInstance.open('r2');
    await fixture.whenStable();
    expect(firstSource.closed).toBe(true);

    const readsAfterSwitch = traceReads;
    await vi.advanceTimersByTimeAsync(9000);
    await fixture.whenStable();
    expect(traceReads).toBe(readsAfterSwitch);
  });

  it('shows the error in the Runs panel when the runs list request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail": "database down"}', { status: 500 })),
    );
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('database down');
  });

  it('keeps the newest refresh when an older answer arrives later', async () => {
    const answers: ((detail: unknown) => void)[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url === '/api/runs'
          ? Promise.resolve(jsonResponse([]))
          : new Promise<Response>((resolve) =>
              answers.push((detail) => resolve(jsonResponse(detail))),
            ),
      ),
    );
    const fixture = TestBed.createComponent(RunsPage);
    const page = fixture.componentInstance;
    page.runId.set('r1'); // without open(): no stream, only the two refreshes below
    const older = page.refresh();
    const newer = page.refresh();
    answers[1]({ id: 'r1', status: 'completed', final: 'done' });
    await newer;
    answers[0]({ id: 'r1', status: 'running', final: null });
    await older;
    expect(page.detail()?.status).toBe('completed');
  });

  it('ends the /trace reads when the same run is opened again during a read', async () => {
    let traceCalls = 0;
    let release: () => void = () => undefined;
    const detail = {
      id: 'r1',
      status: 'completed',
      final: 'ok',
      options: { evaluate: true, limits: {} },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url === '/api/runs') return Promise.resolve(jsonResponse([]));
        if (!url.endsWith('/trace')) return Promise.resolve(jsonResponse(detail));
        traceCalls += 1;
        // The first read hangs until released; later ones answer at once (never the expected evals).
        if (traceCalls > 1) return Promise.resolve(jsonResponse({ events: [] }));
        return new Promise<Response>(
          (resolve) => (release = () => resolve(jsonResponse({ events: [] }))),
        );
      }),
    );
    const fixture = TestBed.createComponent(RunsPage);
    await fixture.whenStable();
    fixture.componentInstance.open('r1');
    FakeEventSource.all.at(-1)!.send(ev('done', { status: 'completed' }));
    await vi.advanceTimersByTimeAsync(100);
    expect(traceCalls).toBe(1); // in flight
    fixture.componentInstance.open('r1'); // the same run again: a new store
    release();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(traceCalls).toBe(1); // the old chain did not go on
  });

  describe('NOW bar', () => {
    it('shows the tool, args and attempt while a call runs', async () => {
      stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(
        ev('llm', {
          node: 'agent',
          data: {
            tool_calls: [
              { id: 'c1', name: 'get_service_status', args: { service: 'payments-api' } },
            ],
          },
        }),
      );
      source.send(ev('stage', { node: 'tools' }));
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      const now = root.querySelector('#now')!.parentElement!;
      expect(now.textContent).toContain(
        'NOW get_service_status({"service":"payments-api"}) attempt 1',
      );
    });

    it('shows the status word, not a running call, for an interrupted run', async () => {
      stubFetch({ detail: () => ({ id: 'r1', status: 'interrupted', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(
        ev('llm', {
          node: 'agent',
          data: { tool_calls: [{ id: 'c1', name: 'get_service_status', args: {} }] },
        }),
      );
      source.send(ev('stage', { node: 'tools' }));
      await fixture.whenStable();

      expect(fixture.componentInstance.store().now().tool).toBe('get_service_status');
      const root = fixture.nativeElement as HTMLElement;
      const now = root.querySelector('#now')!.parentElement!;
      expect(now.textContent).not.toContain('NOW');
      expect(now.textContent).toContain('interrupted');
    });

    it('shows the text alone when nothing is running', async () => {
      stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(ev('stage', { node: 'guard', msg: 'guard checked the budget' }));
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      const now = root.querySelector('#now')!.parentElement!;
      expect(now.textContent).toContain('guard checked the budget');
      expect(now.textContent).not.toContain('NOW');
    });

    it('shows no attempt number while an approval is pending', async () => {
      stubFetch({
        detail: () => ({ id: 'r1', status: 'awaiting_approval', options: { limits: {} } }),
      });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(
        ev('approval', {
          node: 'approval',
          tool: 'create_incident',
          status: 'pending',
          msg: 'awaiting approval',
          data: { tool: 'create_incident', args: { title: 'payments-api down' } },
        }),
      );
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      const now = root.querySelector('#now')!.parentElement!;
      expect(now.textContent).toContain(
        'NOW create_incident({"title":"payments-api down"}) · awaiting approval',
      );
      expect(now.textContent).not.toContain('attempt');
    });
  });

  describe('console', () => {
    async function openWithEvents() {
      stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(ev('stage', { node: 'guard' }));
      source.send(
        ev('tool', {
          node: 'tools',
          tool: 'get_service_status',
          status: 'ok',
          data: { result: { ok: true, data: { service: 'payments-api', status: 'degraded' } } },
        }),
      );
      source.send(
        ev('tool', {
          node: 'tools',
          tool: 'search_knowledge_base',
          status: 'timeout',
          attention: 'error',
          msg: 'search timed out',
        }),
      );
      await fixture.whenStable();
      return fixture;
    }

    it('shows one line per event with offset seconds, kind, node or tool, and the result text', async () => {
      const fixture = await openWithEvents();
      const root = fixture.nativeElement as HTMLElement;
      const lines = [...root.querySelectorAll('.console-lines li')];
      expect(lines.length).toBe(3);
      expect(lines[0].textContent).toContain('0.00');
      expect(lines[0].textContent).toContain('stage');
      expect(lines[0].textContent).toContain('guard');
      expect(lines[1].textContent).toContain('1.00');
      expect(lines[1].textContent).toContain('get_service_status');
      expect(lines[1].textContent).toContain('payments-api degraded');
      expect(lines[2].textContent).toContain('search_knowledge_base');
      expect(lines[2].textContent).toContain('error');
    });

    it('filters to events with a tool', async () => {
      const fixture = await openWithEvents();
      const root = fixture.nativeElement as HTMLElement;
      const toolsButton = [...root.querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Tools',
      )!;
      toolsButton.click();
      await fixture.whenStable();
      const lines = [...root.querySelectorAll('.console-lines li')];
      expect(lines.length).toBe(2);
      expect(lines.some((line) => line.textContent?.includes('stage'))).toBe(false);
      expect(toolsButton.getAttribute('aria-pressed')).toBe('true');
    });

    it('filters to events with attention', async () => {
      const fixture = await openWithEvents();
      const root = fixture.nativeElement as HTMLElement;
      const attentionButton = [...root.querySelectorAll('button')].find(
        (b) => b.textContent?.trim() === 'Attention',
      )!;
      attentionButton.click();
      await fixture.whenStable();
      const lines = [...root.querySelectorAll('.console-lines li')];
      expect(lines.length).toBe(1);
      expect(lines[0].textContent).toContain('search_knowledge_base');
      expect(lines[0].textContent).toContain('error');
    });
  });

  describe('budget meters', () => {
    it('shows a meter with used and max for steps, tool calls and seconds', async () => {
      stubFetch({
        detail: () => ({
          id: 'r1',
          status: 'running',
          options: { limits: { max_steps: 10, max_tool_calls: 5, max_run_seconds: 60 } },
        }),
      });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(ev('stage', { node: 'guard', data: { steps: 3, tool_calls: 2 } }));
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      const meters = [...root.querySelectorAll('meter')];
      expect(meters.length).toBe(3);
      expect(root.textContent).toContain('steps 3 / 10');
      expect(root.textContent).toContain('tool calls 2 / 5');
      expect(meters[0].getAttribute('max')).toBe('10');
      expect(meters[0].getAttribute('value')).toBe('3');
    });

    it('shows no meter when the run detail has no limits', async () => {
      stubFetch({
        detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }),
      });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      expect(root.querySelectorAll('meter').length).toBe(0);
      expect(root.textContent).toContain('Open a run to see its budget.');
    });
  });

  describe('attention list', () => {
    it('lists icon, word, msg and local time, newest first', async () => {
      stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      await fixture.whenStable();
      fixture.componentInstance.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(
        ev('retry', {
          node: 'tools',
          tool: 'get_service_status',
          attention: 'warn',
          msg: 'retrying after a timeout',
        }),
      );
      source.send(
        ev('tool', {
          node: 'tools',
          tool: 'search_knowledge_base',
          status: 'unavailable',
          attention: 'error',
          msg: 'search unavailable',
        }),
      );
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      const items = [...root.querySelectorAll('.attention li')];
      expect(items.length).toBe(2);
      // newest first: the error event was sent last
      expect(items[0].textContent).toContain('error');
      expect(items[0].textContent).toContain('search unavailable');
      expect(items[0].querySelector('[aria-hidden="true"]')?.textContent).toBe('✖');
      expect(items[1].textContent).toContain('warn');
      expect(items[1].textContent).toContain('retrying after a timeout');
      expect(items[0].textContent).toMatch(/\d{1,2}:\d{2}:\d{2}/); // local time
    });
  });

  describe('active node', () => {
    it('lights no node for a final run even if the store still shows one running', async () => {
      stubFetch({ detail: () => ({ id: 'r1', status: 'running', options: { limits: {} } }) });
      const fixture = TestBed.createComponent(RunsPage);
      const page = fixture.componentInstance;
      await fixture.whenStable();
      page.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(ev('stage', { node: 'tools' }));
      source.send(ev('tool', { node: 'tools', tool: 'get_service_status', status: 'timeout' }));
      await fixture.whenStable();
      expect(page.store().activeNode()).not.toBeNull(); // still running, per the store alone

      page.detail.set({ ...page.detail()!, status: 'completed' });
      await fixture.whenStable();
      expect(page.activeNode()).toBeNull();
    });

    it('lights no node for an interrupted run', async () => {
      stubFetch({
        detail: () => ({ id: 'r1', status: 'interrupted', options: { limits: {} } }),
      });
      const fixture = TestBed.createComponent(RunsPage);
      const page = fixture.componentInstance;
      await fixture.whenStable();
      page.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(ev('stage', { node: 'tools' }));
      source.send(ev('tool', { node: 'tools', tool: 'get_service_status', status: 'timeout' }));
      await fixture.whenStable();
      expect(page.store().activeNode()).not.toBeNull();
      expect(page.activeNode()).toBeNull();
    });

    it('lights approval for a paused (awaiting_approval) run', async () => {
      stubFetch({
        detail: () => ({ id: 'r1', status: 'awaiting_approval', options: { limits: {} } }),
      });
      const fixture = TestBed.createComponent(RunsPage);
      const page = fixture.componentInstance;
      await fixture.whenStable();
      page.open('r1');
      await fixture.whenStable();
      const source = FakeEventSource.all.at(-1)!;
      source.send(
        ev('approval', {
          node: 'approval',
          tool: 'create_incident',
          status: 'pending',
          data: { tool: 'create_incident', args: {} },
        }),
      );
      await fixture.whenStable();
      expect(page.activeNode()).toBe('approval');
    });
  });
});
