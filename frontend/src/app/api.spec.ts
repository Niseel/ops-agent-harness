import { ApiError, TraceEvent, api, detailText, followRun, readSse } from './api';

/** The error a request failed with; fails the test when it did not fail. */
const failure = (request: Promise<unknown>): Promise<ApiError> =>
  request.then(
    () => {
      throw new Error('the request did not fail');
    },
    (error: ApiError) => error,
  );

describe('api', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends a JSON body and parses the answer', async () => {
    const fetch = vi.fn(
      async () => new Response(JSON.stringify({ run_id: 'r1' }), { status: 202 }),
    );
    vi.stubGlobal('fetch', fetch);
    expect(await api('POST', '/api/runs', { objective: 'x' }, { 'X-Approver-Token': 't' })).toEqual(
      { run_id: 'r1' },
    );
    expect(fetch).toHaveBeenCalledWith('/api/runs', {
      method: 'POST',
      headers: { 'X-Approver-Token': 't', 'Content-Type': 'application/json' },
      body: '{"objective":"x"}',
    });
  });

  it('throws ApiError with the detail of a non-2xx answer', async () => {
    vi.stubGlobal(
      'fetch',
      async () => new Response('{"detail": "run r9 not found"}', { status: 404 }),
    );
    const error = await failure(api('GET', '/api/runs/r9'));
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.message]).toEqual([404, 'run r9 not found']);
  });

  it('falls back to the status text without a JSON body', async () => {
    vi.stubGlobal(
      'fetch',
      async () => new Response('oops', { status: 502, statusText: 'Bad Gateway' }),
    );
    expect((await failure(api('GET', '/api/runs'))).detail).toBe('Bad Gateway');
  });

  it('gives undefined for an empty 2xx body', async () => {
    vi.stubGlobal('fetch', async () => new Response(null, { status: 204 }));
    expect(await api('POST', '/api/x')).toBeUndefined();
  });

  it('turns a network failure and a non-JSON 2xx answer into ApiError', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('Failed to fetch');
    });
    const down = await failure(api('GET', '/api/runs'));
    expect([down.status, down.message]).toEqual([0, 'network error: the API cannot be reached']);
    vi.stubGlobal('fetch', async () => new Response('<html>', { status: 200 }));
    expect((await failure(api('GET', '/api/runs'))).message).toBe('the answer is not JSON');
  });

  it('names the status when there is no status text', async () => {
    vi.stubGlobal('fetch', async () => new Response('', { status: 500 }));
    expect((await failure(api('GET', '/api/runs'))).detail).toBe('HTTP 500');
  });

  it('formats FastAPI validation lists', () => {
    const detail = [
      { loc: ['body', 'objective'], msg: 'Field required' },
      {
        loc: ['body', 'options', 'limits', 'max_steps'],
        msg: 'Input should be greater than or equal to 1',
      },
    ];
    expect(detailText(detail)).toBe(
      'objective: Field required; options.limits.max_steps: Input should be greater than or equal to 1',
    );
    expect(detailText('knowledge base unavailable')).toBe('knowledge base unavailable');
    expect(detailText([{ loc: ['body'], msg: 'Field required' }])).toBe('Field required');
    expect(detailText(undefined)).toBe('');
  });
});

/** jsdom has no EventSource: a fake that records what the code does with it. */
class FakeEventSource {
  static last: FakeEventSource;
  onmessage: ((message: { data: string }) => void) | null = null;
  onerror: unknown = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }
  close(): void {
    this.closed = true;
  }
  send(event: Partial<TraceEvent>): void {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

describe('followRun', () => {
  beforeEach(() => vi.stubGlobal('EventSource', FakeEventSource));
  afterEach(() => vi.unstubAllGlobals());

  it('passes each event on and closes after done', () => {
    const seen: string[] = [];
    followRun('r 1', (event) => seen.push(event.kind));
    const source = FakeEventSource.last;
    expect(source.url).toBe('/api/runs/r%201/events');
    source.send({ seq: 1, kind: 'stage' });
    expect(source.closed).toBe(false);
    source.send({ seq: 2, kind: 'done' });
    expect(seen).toEqual(['stage', 'done']);
    expect(source.closed).toBe(true);
  });

  it('leaves reconnecting to the browser after an error, and close() stops it', () => {
    const close = followRun('r1', () => undefined);
    const source = FakeEventSource.last;
    expect(source.onerror).toBeNull();
    expect(source.closed).toBe(false);
    close();
    expect(source.closed).toBe(true);
  });
});

/** A body that yields these byte chunks, one per read. */
const streamOf = (chunks: Uint8Array[]): ReadableStream<Uint8Array> =>
  new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  });
const bytes = (text: string) => new TextEncoder().encode(text);

describe('readSse', () => {
  async function read(chunks: Uint8Array[]) {
    const seen: [string, unknown][] = [];
    await readSse(streamOf(chunks), (name, data) => seen.push([name, data]));
    return seen;
  }

  it('reads event names and data, and skips `: ping` comments', async () => {
    const seen = await read([
      bytes(': ping\n\nevent: progress\ndata: {"done":1,"total":2}\nid: 1\n\n'),
      bytes('data: {"plain":true}\n\n'),
    ]);
    expect(seen).toEqual([
      ['progress', { done: 1, total: 2 }],
      ['message', { plain: true }],
    ]);
  });

  it('joins an event split across two chunks, even inside a character', async () => {
    const all = bytes('event: report\ndata: {"judge_model":"modèle"}\n\n');
    const cut = all.indexOf(0xc3) + 1; // between the two bytes of "è"
    expect(await read([all.slice(0, cut), all.slice(cut)])).toEqual([
      ['report', { judge_model: 'modèle' }],
    ]);
  });

  it('accepts \\r\\n line ends and joins several data lines with \\n', async () => {
    const seen = await read([
      bytes('event: error\r\ndata: {"detail":\r'),
      bytes('\ndata: "x"}\r\n\r\n'),
    ]);
    expect(seen).toEqual([['error', { detail: 'x' }]]);
  });

  it('drops an unfinished event at the end of the stream', async () => {
    expect(await read([bytes('event: progress\ndata: {"done":1}\n')])).toEqual([]);
  });

  it('cancels the body when an event cannot be read, so the server stops its job', async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes('event: progress\ndata: {not json\n\n'));
      },
      cancel() {
        cancelled = true;
      },
    });
    await expect(readSse(body, () => undefined)).rejects.toThrow(SyntaxError);
    expect(cancelled).toBe(true);
  });

  it('accepts a data line with no space after the colon', async () => {
    expect(await read([bytes('event: progress\ndata:{"done":1,"total":2}\n\n')])).toEqual([
      ['progress', { done: 1, total: 2 }],
    ]);
  });

  it('ignores an event with no data line', async () => {
    expect(
      await read([bytes('event: progress\nid: 1\n\nevent: report\ndata: {"ok":true}\n\n')]),
    ).toEqual([['report', { ok: true }]]);
  });

  it('reads several events delivered in a single chunk', async () => {
    const seen = await read([
      bytes(
        'event: progress\ndata: {"done":1,"total":3}\n\n' +
          'event: progress\ndata: {"done":2,"total":3}\n\n' +
          'event: progress\ndata: {"done":3,"total":3}\n\n',
      ),
    ]);
    expect(seen).toEqual([
      ['progress', { done: 1, total: 3 }],
      ['progress', { done: 2, total: 3 }],
      ['progress', { done: 3, total: 3 }],
    ]);
  });
});
