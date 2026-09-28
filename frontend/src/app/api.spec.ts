import { ApiError, api, detailText } from './api';

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
