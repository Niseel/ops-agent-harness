import fixture from './trace.fixture.json';
import { TraceEvent } from './api';
import {
  TraceStore,
  attentionStyle,
  eventText,
  nodeOf,
  resultText,
  roleOf,
  runStatusStyle,
} from './trace';

let seq = 0;

/** An event shaped like the backend's, with increasing `seq` and `t_ms` (one second apart). */
function ev(kind: string, fields: Partial<TraceEvent> = {}): TraceEvent {
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
}

const guard = (steps: number, toolCalls: number) =>
  ev('stage', {
    node: 'guard',
    data: { steps, max_steps: 8, tool_calls: toolCalls, max_tool_calls: 12 },
  });
const llm = (calls: { id: string; name: string; args: unknown }[]) =>
  ev('llm', {
    node: 'agent',
    status: calls.length ? 'tool_calls' : 'final',
    data: { tool_calls: calls },
  });
const toolsStage = () => ev('stage', { node: 'tools', msg: '1 call(s)' });
const tool = (
  call: string,
  name: string,
  attempt: number,
  status: string,
  fields: Partial<TraceEvent> = {},
) =>
  ev('tool', {
    node: 'tools',
    tool: name,
    status,
    msg: `${name} attempt ${attempt}: ${status}`,
    data: {
      tool_call_id: call,
      attempt,
      args: { service_name: 'payments-api' },
      duration_ms: 100,
      result: null,
    },
    ...fields,
  });
const retry = (call: string, name: string, attempt: number) =>
  ev('retry', {
    node: 'tools',
    tool: name,
    status: 'retry',
    attention: 'warn',
    msg: `${name} attempt ${attempt} failed (timeout); retrying in 0.40 s`,
    data: { tool_call_id: call, attempt, delay_s: 0.4, reason: 'timeout' },
  });
const done = (status: string, attention: TraceEvent['attention']) =>
  ev('done', {
    status,
    attention,
    msg: `run ${status}`,
    data: { status, error: null, steps: 3, tool_calls: 2 },
  });

const STATUS_ARGS = { service_name: 'payments-api' };
const SEARCH_OK = {
  ok: true,
  data: { mode: 'sparse_only', results: [{ doc_id: 'a' }, { doc_id: 'b' }] },
};

function store(...events: TraceEvent[]): TraceStore {
  const s = new TraceStore();
  events.forEach((e) => s.add(e));
  return s;
}

describe('TraceStore', () => {
  beforeEach(() => (seq = 0));

  it('ignores an event whose seq is not above the last one', () => {
    const first = guard(0, 0);
    const s = store(first, llm([]));
    s.add(first);
    s.add({ ...first, seq: 1 });
    expect(s.events().map((e) => e.seq)).toEqual([1, 2]);
  });

  describe('timeline', () => {
    it('puts call items right after their llm event, with tool events grouped', () => {
      const s = store(
        guard(0, 0),
        llm([
          { id: 'c1', name: 'get_service_status', args: STATUS_ARGS },
          { id: 'c2', name: 'search_knowledge_base', args: { query: 'latency' } },
        ]),
        toolsStage(),
        tool('c1', 'get_service_status', 1, 'timeout'),
        retry('c1', 'get_service_status', 1),
        tool('c1', 'get_service_status', 2, 'ok', {
          data: {
            tool_call_id: 'c1',
            attempt: 2,
            args: STATUS_ARGS,
            duration_ms: 250.5,
            result: { ok: true, data: { service: 'payments-api', status: 'degraded' } },
          },
        }),
      );
      // c2 runs once c1 is done; it counts as `not run` if the run ends before its first attempt.
      expect(s.timeline()[2]).toMatchObject({ tool: 'search_knowledge_base', status: 'running' });
      s.add(done('completed', 'success'));
      const items = s.timeline();
      expect(items.map((i) => i.type)).toEqual(['llm', 'call', 'call', 'done']);
      const [, status, search] = items;
      if (status.type !== 'call' || search.type !== 'call') throw new Error('call items expected');
      expect(status).toMatchObject({
        tool: 'get_service_status',
        attempts: 2,
        durationMs: 350.5,
        status: 'ok',
      });
      expect(resultText(status.result)).toBe('payments-api degraded');
      expect(search).toMatchObject({
        tool: 'search_knowledge_base',
        args: { query: 'latency' },
        status: 'not run',
      });
    });

    it('shows the approval status and the edited args of a call', () => {
      const proposed = { title: 'payments-api is degraded', severity: 'SEV2' };
      const edited = { title: 'payments-api p95 is high', severity: 'SEV3' };
      const reply = llm([{ id: 'c9', name: 'create_incident', args: proposed }]); // built first: seq order
      const pending = ev('approval', {
        node: 'approval',
        tool: 'create_incident',
        status: 'pending',
        attention: 'warn',
        data: { tool_call_id: 'c9', tool: 'create_incident', args: proposed },
      });
      const s = store(reply, pending);
      const waiting = s.timeline()[1];
      expect(waiting).toMatchObject({
        status: 'pending',
        args: proposed,
        approval: pending,
        event: pending,
      });
      s.add(
        ev('approval', {
          node: 'approval',
          tool: 'create_incident',
          status: 'edited',
          attention: 'info',
          data: { tool_call_id: 'c9', decision: { decision: 'edit', args: edited } },
        }),
      );
      expect(s.timeline()[1]).toMatchObject({ status: 'edited', args: edited });
    });

    it('puts eval badges on their target: a search call or the done item', () => {
      const s = store(
        llm([{ id: 'c1', name: 'search_knowledge_base', args: { query: 'q' } }]),
        toolsStage(),
        tool('c1', 'search_knowledge_base', 1, 'ok', {
          data: {
            tool_call_id: 'c1',
            attempt: 1,
            args: { query: 'q' },
            duration_ms: 5,
            result: SEARCH_OK,
          },
        }),
        llm([]),
        done('completed', 'success'),
        ev('eval', {
          node: 'eval',
          tool: 'search_knowledge_base',
          data: { target: 'search:c1', metric: 'context_relevance' },
        }),
        ev('eval', { node: 'eval', data: { target: 'answer', metric: 'faithfulness' } }),
        ev('eval', { node: 'eval', data: { target: 'answer', metric: 'answer_relevancy' } }),
      );
      const items = s.timeline();
      const call = items[1];
      const end = items[3];
      if (call.type !== 'call' || end.type !== 'done')
        throw new Error('call and done items expected');
      expect(call.badges.map((b) => b.data.metric)).toEqual(['context_relevance']);
      expect(end.badges.map((b) => b.data.metric)).toEqual(['faithfulness', 'answer_relevancy']);
      expect(s.expectedEvals(true)).toBe(3);
      expect(s.expectedEvals(false)).toBe(1);
    });
  });

  describe('now', () => {
    it('follows the running call with its args and attempt through two retries', () => {
      const s = store(
        guard(0, 0),
        llm([{ id: 'c1', name: 'get_service_status', args: STATUS_ARGS }]),
      );
      expect(s.now().tool).toBeUndefined(); // proposed, the tools step has not started
      s.add(toolsStage());
      expect(s.now()).toEqual({
        text: 'running',
        tool: 'get_service_status',
        args: STATUS_ARGS,
        attempt: 1,
      });
      s.add(tool('c1', 'get_service_status', 1, 'timeout'));
      s.add(retry('c1', 'get_service_status', 1));
      expect(s.now()).toMatchObject({ tool: 'get_service_status', attempt: 2 });
      expect(s.now().text).toContain('retrying in 0.40 s');
      s.add(tool('c1', 'get_service_status', 2, 'timeout'));
      s.add(retry('c1', 'get_service_status', 2));
      expect(s.now().attempt).toBe(3);
      s.add(tool('c1', 'get_service_status', 3, 'ok'));
      expect(s.now().tool).toBeUndefined();
    });

    it('shows a pending approval with its args, then the edited args while the call runs', () => {
      const proposed = { title: 'payments-api is degraded' };
      const edited = { title: 'payments-api p95 is high' };
      const s = store(
        llm([{ id: 'c9', name: 'create_incident', args: proposed }]),
        ev('approval', {
          node: 'approval',
          tool: 'create_incident',
          status: 'pending',
          msg: 'create_incident waits for a person',
          data: { tool_call_id: 'c9', tool: 'create_incident', args: proposed },
        }),
      );
      expect(s.now()).toEqual({
        text: 'create_incident waits for a person',
        tool: 'create_incident',
        args: proposed,
      });
      s.add(
        ev('approval', {
          node: 'approval',
          tool: 'create_incident',
          status: 'edited',
          data: { tool_call_id: 'c9', decision: { decision: 'edit', args: edited } },
        }),
      );
      s.add(toolsStage());
      expect(s.now()).toMatchObject({ tool: 'create_incident', args: edited, attempt: 1 });
    });

    it('shows the done message at the end', () => {
      expect(store(guard(0, 0), done('limit_exceeded', 'error')).now()).toEqual({
        text: 'run limit_exceeded',
      });
    });
  });

  describe('nodes', () => {
    it('lights the running tool, then a knowledge-base sub-step, then nothing after done', () => {
      const s = store(
        guard(0, 0),
        llm([{ id: 'c1', name: 'search_knowledge_base', args: { query: 'q' } }]),
      );
      expect(s.activeNode()).toBe('agent');
      s.add(toolsStage());
      expect(s.activeNode()).toBe('search_knowledge_base');
      const embed = ev('stage', {
        node: 'kb.embed',
        tool: 'search_knowledge_base',
        status: 'failed',
        data: { tool_call_id: 'c1' },
      });
      s.add(embed);
      expect(s.activeNode()).toBe('kb.embed');
      expect(s.nodes()['kb.embed']).toBe(embed);
      s.add(done('completed', 'success'));
      expect(s.activeNode()).toBeNull();
      expect(Object.keys(s.nodes())).toEqual(['guard', 'agent', 'tools', 'kb.embed', 'finalize']);
    });

    it('lights the next call of the same reply once the first one finished', () => {
      const s = store(
        llm([
          { id: 'c1', name: 'get_service_status', args: STATUS_ARGS },
          { id: 'c2', name: 'search_knowledge_base', args: { query: 'q' } },
        ]),
        toolsStage(),
        tool('c1', 'get_service_status', 1, 'ok'),
      );
      expect(s.activeNode()).toBe('search_knowledge_base');
      expect(s.now()).toMatchObject({
        tool: 'search_knowledge_base',
        args: { query: 'q' },
        attempt: 1,
      });
    });

    it('moves on after a refused or a rejected call', () => {
      for (const [status, attempt, attention] of [
        ['validation', 0, 'warn'],
        ['rejected', 0, 'info'],
      ] as const) {
        const s = store(
          llm([
            { id: 'c1', name: 'create_incident', args: {} },
            { id: 'c2', name: 'get_service_status', args: STATUS_ARGS },
          ]),
          toolsStage(),
          tool('c1', 'create_incident', attempt, status, { attention }),
        );
        expect(s.activeNode()).toBe('get_service_status');
        expect(s.now().tool).toBe('get_service_status');
      }
    });

    it('counts attempts since the latest tools step after a resume', () => {
      const s = store(
        llm([
          { id: 'c1', name: 'get_service_status', args: STATUS_ARGS },
          { id: 'c2', name: 'search_knowledge_base', args: { query: 'q' } },
        ]),
        toolsStage(),
        tool('c1', 'get_service_status', 1, 'ok'),
        ev('log', { data: { action: 'resume_run' } }), // the process died during c2; the step starts again
        toolsStage(),
      );
      expect(s.now()).toMatchObject({ tool: 'get_service_status', attempt: 1 });
    });

    it('maps each kind to its node', () => {
      expect(nodeOf(ev('llm'))).toBe('agent');
      expect(nodeOf(ev('retry', { node: 'agent' }))).toBe('agent');
      expect(nodeOf(ev('retry', { node: 'tools', tool: 'get_service_status' }))).toBe(
        'get_service_status',
      );
      expect(nodeOf(ev('tool', { node: 'tools', tool: 'create_incident' }))).toBe(
        'create_incident',
      );
      expect(nodeOf(ev('approval'))).toBe('approval');
      expect(nodeOf(ev('done'))).toBe('finalize');
      expect([
        nodeOf(ev('log')),
        nodeOf(ev('eval', { node: 'eval' })),
        nodeOf(ev('error')),
      ]).toEqual([null, null, null]);
    });
  });

  it('marks a call without an attempt as not run once the run ended', () => {
    const s = store(
      llm([{ id: 'c9', name: 'create_incident', args: {} }]),
      ev('approval', {
        node: 'approval',
        status: 'pending',
        data: { tool_call_id: 'c9', args: {} },
      }),
      done('cancelled', 'info'), // a cancel closes the approval without an approval event
    );
    expect(s.timeline()[1]).toMatchObject({ status: 'not run' });
  });

  it('lists attention events newest first', () => {
    const s = store(
      retry('c1', 'get_service_status', 1),
      guard(1, 1),
      done('completed', 'success'),
    );
    expect(s.attention().map((e) => e.kind)).toEqual(['done', 'retry']);
  });

  it('counts steps and tool calls, and seconds of the current segment only', () => {
    const s = store(
      ev('log', { data: { action: 'create_run' } }),
      guard(0, 0),
      guard(2, 1),
      ev('approval', { node: 'approval', status: 'pending' }),
    );
    expect(s.used()).toEqual({ steps: 2, toolCalls: 1, seconds: 3 });
    s.add(ev('approval', { node: 'approval', status: 'approved' })); // t 5 s
    s.add(ev('log', { data: { action: 'decide_approval' } })); // t 6 s: the second segment starts
    s.add(guard(3, 2)); // t 7 s
    s.add(done('completed', 'success')); // t 8 s
    s.add(ev('eval', { node: 'eval' })); // after done: not counted
    expect(s.used()).toEqual({ steps: 3, toolCalls: 2, seconds: 2 });
  });
});

describe('attentionStyle', () => {
  // Every row of the spec's attention table: colour, icon and word.
  const rows: [string, TraceEvent['attention'], string, string][] = [
    ['approval', 'warn', 'amber', '⚠'], // approval waiting
    ['retry', 'warn', 'amber', '⚠'], // retry scheduled
    ['tool', 'warn', 'orange', '⚠'], // invalid input, bad output
    ['llm', 'warn', 'orange', '⚠'], // malformed reply repaired
    ['eval', 'warn', 'orange', '⚠'], // metric below threshold
    ['tool', 'error', 'red', '✖'], // tool failed, call blocked
    ['llm', 'error', 'red', '✖'], // LLM call failed
    ['done', 'error', 'red', '✖'], // failed, limit_exceeded, timed_out
    ['tool', 'info', 'blue', 'ℹ'], // sparse_only search, rejected call
    ['approval', 'info', 'blue', 'ℹ'], // edited, rejected or expired
    ['done', 'info', 'blue', 'ℹ'], // cancelled
    ['eval', 'info', 'blue', 'ℹ'], // metric null
    ['tool', 'success', 'green', '✔'], // incident created
    ['done', 'success', 'green', '✔'], // completed
  ];

  it.each(rows)('%s %s is %s with an icon and a word', (kind, attention, colour, icon) => {
    expect(attentionStyle({ kind, attention })).toEqual({ colour, icon, word: attention });
  });

  it('gives nothing without attention', () => {
    expect(attentionStyle({ kind: 'tool', attention: null })).toBeNull();
  });
});

describe('runStatusStyle', () => {
  const rows: [string, string, string][] = [
    ['completed', 'green', '✔'],
    ['failed', 'red', '✖'],
    ['limit_exceeded', 'red', '✖'],
    ['timed_out', 'red', '✖'],
    ['cancelled', 'blue', 'ℹ'],
    ['awaiting_approval', 'amber', '⚠'],
    ['interrupted', 'amber', '⚠'],
  ];

  it.each(rows)('%s is %s with an icon and its own word', (status, colour, icon) => {
    expect(runStatusStyle(status)).toEqual({ colour, icon, word: status });
  });

  it('gives nothing while the run is running', () => {
    expect(runStatusStyle('running')).toBeNull();
  });
});

describe('roleOf', () => {
  it("gives the flow role of the event's node, and the harness for events without a node", () => {
    const roles = [
      ev('llm'),
      ev('stage', { node: 'agent' }),
      ev('stage', { node: 'guard' }),
      ev('stage', { node: 'tools' }),
      ev('tool', { tool: 'get_service_status' }),
      ev('stage', { node: 'kb.bm25', tool: 'search_knowledge_base' }),
      ev('retry', { node: 'tools', tool: 'get_service_status' }),
      ev('retry', { node: 'agent' }),
      ev('approval', { tool: 'create_incident' }),
      ev('done'),
      ev('log'),
      ev('eval'),
      ev('error'),
    ].map(roleOf);
    expect(roles).toEqual([
      'llm',
      'llm',
      'harness',
      'harness',
      'tool',
      'tool',
      'tool',
      'llm',
      'person',
      'harness',
      'harness',
      'harness',
      'harness',
    ]);
  });
});

describe('TraceStore against a real run (fixture from the backend, Approve scenario)', () => {
  // Captured from a live uvicorn run (`llm: fake`, get_service_status faulted to time out twice)
  // through the actual API: POST /api/runs, approve the pending approval, GET the trace.
  // trace.fixture.json keeps the shape but trims the knowledge-base rankings and snippets.
  const events = fixture.events as unknown as TraceEvent[];
  const bySeq = (n: number) => events.find((e) => e.seq === n)!;
  const replayUpTo = (seq: number): TraceStore => {
    const s = new TraceStore();
    events.filter((e) => e.seq <= seq).forEach((e) => s.add(e));
    return s;
  };

  it('has the shape this store expects: a search, a retried status check, an approved incident, done', () => {
    expect(fixture.status).toBe('completed');
    expect(fixture.events.length).toBe(32);
  });

  it('follows the running get_service_status call through its two real retries (attempt 1, 2, 3)', () => {
    expect(replayUpTo(14).now()).toMatchObject({
      tool: 'get_service_status',
      args: { service_name: 'payments-api' },
      attempt: 1,
    });
    expect(replayUpTo(16).now()).toMatchObject({ tool: 'get_service_status', attempt: 2 });
    expect(replayUpTo(16).now().text).toContain('retrying in 0.10 s');
    expect(replayUpTo(18).now()).toMatchObject({ tool: 'get_service_status', attempt: 3 });
    expect(replayUpTo(18).now().text).toContain('retrying in 0.33 s');
    expect(replayUpTo(19).now().tool).toBeUndefined(); // attempt 3 was ok: nothing left running
  });

  it('shows the pending approval, then the running call once approved', () => {
    const pending = replayUpTo(23).now();
    expect(pending).toEqual({
      text: bySeq(23).msg,
      tool: 'create_incident',
      args: bySeq(23).data.args,
    });
    // approved but the tools step has not restarted yet: no running call
    expect(replayUpTo(24).now().tool).toBeUndefined();
    // the tools step restarts: the call runs with the approved (unedited) args
    expect(replayUpTo(26).now()).toMatchObject({
      tool: 'create_incident',
      args: bySeq(23).data.args,
      attempt: 1,
    });
  });

  it('lights the running tool, then a kb.* sub-step, then nothing once the run is done', () => {
    expect(replayUpTo(5).activeNode()).toBe('search_knowledge_base');
    expect(replayUpTo(6).activeNode()).toBe('kb.embed');
    expect(replayUpTo(fixture.events.length).activeNode()).toBeNull();
  });

  it('builds a timeline with a grouped, retried call and an approved, badge-free incident call', () => {
    const s = replayUpTo(fixture.events.length);
    const items = s.timeline();
    expect(items.map((i) => i.type)).toEqual([
      'llm',
      'call',
      'llm',
      'call',
      'llm',
      'call',
      'llm',
      'done',
    ]);
    const status = items[3];
    if (status.type !== 'call') throw new Error('call item expected');
    expect(status).toMatchObject({ tool: 'get_service_status', attempts: 3, status: 'ok' });
    expect(status.durationMs).toBeCloseTo(2000.9 + 2000.0 + 4.3, 1);
    expect(resultText(status.result)).toBe('payments-api degraded');
    const incident = items[5];
    if (incident.type !== 'call') throw new Error('call item expected');
    expect(incident).toMatchObject({ tool: 'create_incident', status: 'ok', badges: [] });
    expect(incident.approval?.status).toBe('approved');
    expect(resultText(incident.result)).toMatch(/^INC-\w+ open$/);
    expect(eventText(bySeq(10))).toContain('hybrid, 3 results'); // the trimmed search still has its mode and hits
  });

  it('ends with the done message, no active node, and used() from the real segments', () => {
    const s = replayUpTo(fixture.events.length);
    expect(s.now()).toEqual({ text: 'run completed' });
    expect(s.used().steps).toBe(4);
    expect(s.used().toolCalls).toBe(3);
    // the second segment starts at the decide_approval log event (seq 25), ends at done (seq 32)
    const start = bySeq(25).t_ms;
    const end = bySeq(32).t_ms;
    expect(s.used().seconds).toBeCloseTo((end - start) / 1000, 3);
  });
});

describe('resultText', () => {
  it('says the search mode in words', () => {
    const search = tool('c1', 'search_knowledge_base', 1, 'ok', {
      attention: 'info',
      data: { tool_call_id: 'c1', attempt: 1, args: {}, duration_ms: 1, result: SEARCH_OK },
    });
    expect(eventText(search)).toContain('sparse_only, 2 results');
  });

  it('covers each tool and the error and truncated forms', () => {
    expect(resultText({ ok: true, data: { incident_id: 'INC-26F42A3B', status: 'open' } })).toBe(
      'INC-26F42A3B open',
    );
    expect(resultText({ ok: true, data: 'long text', truncated: true })).toBe('truncated');
    expect(resultText({ ok: true, data: { other: 1 } })).toBe('ok');
    expect(
      resultText({
        ok: false,
        error: { type: 'timeout', message: 'no answer in 2 s', retryable: true },
      }),
    ).toBe('timeout: no answer in 2 s');
  });
});
