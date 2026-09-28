// The API client (spec: API). Relative URLs: the dev proxy in development, the same origin in production.

export type Attention = 'info' | 'warn' | 'error' | 'success';

export interface TraceEvent {
  seq: number;
  run_id: string | null;
  t_ms: number;
  kind: string;
  node: string | null;
  tool: string | null;
  status: string | null;
  attention: Attention | null;
  msg: string | null;
  data: any;
  created_at: string;
}

/** A tool result (spec: tool envelope). A long result is cut and marked `truncated`. */
export type Envelope =
  | { ok: true; data: any; truncated?: true }
  | { ok: false; error: { type: string; message: string; retryable: boolean } };

export interface RunSummary {
  id: string;
  objective: string;
  status: string;
  llm_mode: string;
  steps: number;
  tool_calls: number;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_call_id: string;
  tool: string;
  args: Record<string, unknown>;
  status: string;
  decision: Record<string, unknown> | null;
  reason: string | null;
  decided_by: string | null;
  created_at: string;
  decided_at: string | null;
  expires_at: string;
}

export interface RunDetail extends RunSummary {
  model: string;
  options: { limits: Record<string, number>; faults: Record<string, unknown>; evaluate: boolean };
  final: string | null;
  error: string | null;
  finished_at: string | null;
  messages: Record<string, unknown>[];
  calls: Record<string, unknown>[];
  approvals: Approval[];
  evals: Record<string, unknown>[];
  usage: { prompt_tokens: number; completion_tokens: number };
}

export interface Incident {
  id: string;
  run_id: string | null;
  title: string;
  description: string;
  severity: string;
  status: string;
  created_at: string;
}

export interface EvalSummary {
  questions: number;
  errors: number;
  'hit@3': number | null;
  'mrr@10': number | null;
  'recall@3': number | null;
  context_precision: number | null;
  context_recall: number | null;
  judge_error: string | null;
}

export interface EvalReport {
  id: string;
  created_at: string;
  models: { embed_model: string; judge_model: string };
  config: { golden_set: string; modes: string[] };
  summary: Record<string, EvalSummary>;
  rows?: Record<string, unknown>[];
}

/** A non-2xx answer. `detail` is the body's `detail` (a string, or FastAPI's 422 list). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(detailText(detail));
  }
}

/** A string as is; FastAPI's validation list as `loc: msg` items joined by `; `. */
export function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const loc = (item?.loc ?? []).filter((part: unknown) => part !== 'body').join('.');
        return loc ? `${loc}: ${item?.msg ?? ''}` : String(item?.msg ?? '');
      })
      .join('; ');
  }
  return JSON.stringify(detail) ?? String(detail ?? '');
}

/**
 * One request. A body goes as JSON. Any failure throws `ApiError`: status 0 when the server cannot be reached,
 * the answer's status and `detail` otherwise. An empty 2xx body gives `undefined`.
 */
export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const init: RequestInit = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.headers = { ...headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  let response: Response;
  let text: string;
  try {
    response = await fetch(path, init);
    text = await response.text();
  } catch {
    throw new ApiError(0, 'network error: the API cannot be reached');
  }
  let parsed: any = undefined;
  let json = true;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    json = false;
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      parsed?.detail ?? (response.statusText || `HTTP ${response.status}`),
    );
  }
  if (!json) throw new ApiError(response.status, 'the answer is not JSON');
  return parsed as T;
}

/**
 * Live events of one run (`GET /api/runs/{id}/events`). Closed after `done`: otherwise the browser reconnects
 * every few seconds and gets empty streams. No `onerror`: after a network error, or a stream that ended without
 * `done`, the browser reconnects with `Last-Event-ID` by itself. Returns `close`.
 */
export function followRun(runId: string, onEvent: (event: TraceEvent) => void): () => void {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as TraceEvent;
    onEvent(event);
    if (event.kind === 'done') source.close();
  };
  return () => source.close();
}

/**
 * Server-sent events from a `fetch` body: the evaluation stream is a POST, which `EventSource` cannot send.
 * An event ends at a blank line, and an unfinished tail waits for the next chunk. `event:` names it (default
 * `message`); `data:` lines are joined by `\n` and parsed as JSON; `:` lines are comments (the `: ping` keep-alive).
 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (name: string, data: any) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      // On the whole buffer, so a `\r\n` split across two chunks is still found.
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n');
      let end: number;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let name = 'message';
        const data: string[] = [];
        for (const line of block.split('\n')) {
          if (line.startsWith(':')) continue;
          const colon = line.indexOf(':');
          const field = colon < 0 ? line : line.slice(0, colon);
          const text = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
          if (field === 'event') name = text;
          else if (field === 'data') data.push(text);
        }
        if (data.length) onEvent(name, JSON.parse(data.join('\n')));
      }
      if (done) return;
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined); // leaving closes the request, which stops the server's job
    throw error;
  }
}
