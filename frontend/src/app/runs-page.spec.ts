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
});
