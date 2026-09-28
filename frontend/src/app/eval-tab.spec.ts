import { TestBed } from '@angular/core/testing';
import { EvalReport } from './api';
import { EvalTab } from './eval-tab';

const summary = (hit: number | null, judgeError: string | null = null) => ({
  questions: 15,
  errors: 0,
  'hit@3': hit,
  'mrr@10': 0.5,
  'recall@3': 0.8,
  context_precision: null,
  context_recall: null,
  judge_error: judgeError,
});

const report = (id: string): EvalReport => ({
  id,
  created_at: '2026-09-28T09:00:00.000Z',
  models: { embed_model: 'bge-m3', judge_model: 'judge-x' },
  config: { golden_set: 'golden.jsonl', modes: ['hybrid', 'sparse'] },
  summary: { hybrid: summary(0.9333, 'judge unreachable'), sparse: summary(null) },
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** An SSE answer whose body yields `text` in one chunk. */
const sse = (text: string) =>
  new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text));
        controller.close();
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  );

describe('EvalTab', () => {
  afterEach(() => vi.unstubAllGlobals());

  async function render(fetch: (url: string, init?: RequestInit) => Promise<Response>) {
    const stub = vi.fn(fetch);
    vi.stubGlobal('fetch', stub);
    const fixture = TestBed.createComponent(EvalTab);
    await fixture.whenStable();
    return { fixture, root: fixture.nativeElement as HTMLElement, stub };
  }

  it('shows the 404 detail when there is no report yet', async () => {
    const { root } = await render(async () => json({ detail: 'no evaluation report yet' }, 404));
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('no evaluation report yet');
    expect(root.querySelector('table')).toBeNull();
  });

  it('shows the latest report: one row per mode, 2 decimals, n/a for null, judge error below', async () => {
    const { root } = await render(async () => json(report('r1')));
    const rows = [...root.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.children].map((cell) => cell.textContent?.trim()),
    );
    expect(rows).toEqual([
      ['hybrid', '15', '0', '0.93', '0.50', '0.80', 'n/a', 'n/a'],
      ['sparse', '15', '0', 'n/a', '0.50', '0.80', 'n/a', 'n/a'],
    ]);
    expect(root.textContent).toContain('judge-x');
    expect(root.textContent).toContain('hybrid: judge error: judge unreachable');
  });

  it('runs with no body, shows progress, then the report', async () => {
    const { fixture, root, stub } = await render(async (url, init) => {
      if (init?.method === 'POST') {
        return sse(
          'event: progress\ndata: {"done":1,"total":2}\n\n: ping\n\n' +
            `event: report\ndata: ${JSON.stringify(report('r2'))}\n\n`,
        );
      }
      return json({ detail: 'no evaluation report yet' }, 404);
    });
    const button = root.querySelector('button')!;
    button.click();
    await fixture.whenStable();

    const post = stub.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(post[0]).toBe('/api/eval/kb');
    expect(post[1]!.body).toBeUndefined();
    expect(root.querySelector('progress')!.getAttribute('max')).toBe('2');
    expect(root.textContent).toContain('1/2 questions');
    expect(root.querySelectorAll('tbody tr').length).toBe(2);
    expect(button.disabled).toBe(false);
  });

  it('shows the 503 detail and an error event', async () => {
    let answer = json({ detail: 'knowledge base unavailable' }, 503);
    const { fixture, root } = await render(async (_url, init) =>
      init?.method === 'POST' ? answer : json(report('r1')),
    );
    root.querySelector('button')!.click();
    await fixture.whenStable();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain(
      'knowledge base unavailable',
    );
    expect(root.querySelectorAll('tbody tr').length).toBe(2); // the panel keeps its last data

    answer = sse('event: error\ndata: {"detail":"evaluation failed"}\n\n');
    root.querySelector('button')!.click();
    await fixture.whenStable();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('evaluation failed');
  });

  it('reads the latest report again after a run ends without an error', async () => {
    const { fixture, root, stub } = await render(async (url, init) => {
      if (init?.method === 'POST') {
        return sse(`event: report\ndata: ${JSON.stringify(report('during-run'))}\n\n`);
      }
      return json(report('after-run'));
    });
    root.querySelector('button')!.click();
    await fixture.whenStable();

    const gets = stub.mock.calls.filter(([, init]) => init?.method !== 'POST');
    expect(gets.length).toBe(2); // once at start, once after the run
    expect(root.textContent).toContain('judge-x'); // the re-read report is shown
  });

  it('does not overwrite a run error with the 404 of a re-read (no report yet)', async () => {
    const { fixture, root, stub } = await render(async (_url, init) =>
      init?.method === 'POST'
        ? sse('event: error\ndata: {"detail":"evaluation failed"}\n\n')
        : json({ detail: 'no evaluation report yet' }, 404),
    );
    root.querySelector('button')!.click();
    await fixture.whenStable();

    expect(root.querySelector('[role="alert"]')!.textContent).toContain('evaluation failed');
    const gets = stub.mock.calls.filter(([, init]) => init?.method !== 'POST');
    expect(gets.length).toBe(1); // only the load at start; not re-read after a failed run
  });

  it('shows a network failure on the run and re-enables the button', async () => {
    const { fixture, root } = await render(async (_url, init) => {
      if (init?.method === 'POST') throw new TypeError('Failed to fetch');
      return json({ detail: 'no evaluation report yet' }, 404);
    });
    const button = root.querySelector('button')!;
    button.click();
    await fixture.whenStable();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain(
      'network error: the API cannot be reached',
    );
    expect(button.disabled).toBe(false);
  });

  it('says so when the stream ends without a report or an error', async () => {
    const { fixture, root, stub } = await render(async (_url, init) =>
      init?.method === 'POST'
        ? sse('event: progress\ndata: {"done":12,"total":48}\n\n')
        : json(report('r1')),
    );
    const readsBefore = stub.mock.calls.length;
    root.querySelector('button')!.click();
    await fixture.whenStable();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain(
      'the evaluation stream ended early',
    );
    expect(stub.mock.calls.length).toBe(readsBefore + 1); // the POST only: no report re-read
    expect(root.querySelector('button')!.disabled).toBe(false);
  });

  it('shows the report created time', async () => {
    const { root } = await render(async () => json(report('r1')));
    expect(root.textContent).toContain(new Date('2026-09-28T09:00:00.000Z').toLocaleString());
  });

  it('disables the button while a run streams', async () => {
    let finish!: () => void;
    const { fixture, root } = await render(async (_url, init) => {
      if (init?.method !== 'POST') return json(report('r1'));
      return new Response(
        new ReadableStream({
          start(controller) {
            finish = () => controller.close();
          },
        }),
        { status: 200 },
      );
    });
    const button = root.querySelector('button')!;
    button.click();
    await fixture.whenStable();
    expect(button.disabled).toBe(true);
    finish();
    await vi.waitFor(async () => {
      await fixture.whenStable();
      expect(button.disabled).toBe(false);
    });
  });
});
