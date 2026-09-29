// TraceStore: a run's events turned into what the Runs tab shows (plan M4, "TraceStore").
// A plain class with signals: no DI, no I/O. Everything is computed from the event list.
// ponytail: every view is recomputed per event; fine for the hundreds of events a run has.
import { computed, signal } from '@angular/core';
import { Envelope, TraceEvent } from './api';
import { FLOW, Role } from './flow';

export interface ProposedCall {
  id: string;
  name: string;
  args?: unknown;
}

export interface CallItem {
  type: 'call';
  id: string;
  tool: string;
  args: unknown;
  attempts: number;
  durationMs: number;
  status: string;
  result: Envelope | null;
  event: TraceEvent | null;
  approval: TraceEvent | null;
  badges: TraceEvent[];
}

export type TimelineItem =
  | { type: 'llm'; event: TraceEvent }
  | CallItem
  | { type: 'done'; event: TraceEvent; badges: TraceEvent[] };

export interface Now {
  text: string;
  tool?: string;
  args?: unknown;
  attempt?: number;
}

/** Who is working now, and on what (plan M6, "Now panel: who is working"). */
export type Actor = 'llm' | 'harness' | 'person' | 'none';

export interface Activity {
  actor: Actor;
  text: string;
}

export interface Used {
  steps: number;
  toolCalls: number;
  seconds: number;
}

export interface AttentionStyle {
  colour: 'amber' | 'orange' | 'red' | 'blue' | 'green';
  icon: string;
  word: string;
}

const SEARCH = 'search_knowledge_base';
// The actions that start a segment: `max_run_seconds` counts each segment on its own.
const SEGMENT_STARTS = new Set(['create_run', 'decide_approval', 'expire_approval', 'resume_run']);

function findLast<T>(items: readonly T[], test: (item: T) => boolean): T | undefined {
  for (let i = items.length - 1; i >= 0; i--) if (test(items[i])) return items[i];
  return undefined;
}

/** A `tool` event after which the same call runs again: a transient failure with no attention. */
function retried(event: TraceEvent): boolean {
  return (event.status === 'timeout' || event.status === 'unavailable') && !event.attention;
}

/** The flow node an event lights; ids match `flow.ts`. */
export function nodeOf(event: TraceEvent): string | null {
  switch (event.kind) {
    case 'stage':
      return event.node;
    case 'llm':
      return 'agent';
    case 'retry':
      return event.tool ?? event.node;
    case 'tool':
      return event.tool;
    case 'approval':
      return 'approval';
    case 'done':
      return 'finalize';
    default:
      return null; // log, eval, error
  }
}

/** Colour, icon and word for an event's attention (spec: attention table). Never colour alone. */
export function attentionStyle(
  event: Pick<TraceEvent, 'kind' | 'attention'>,
): AttentionStyle | null {
  switch (event.attention) {
    case 'error':
      return { colour: 'red', icon: '✖', word: 'error' };
    case 'success':
      return { colour: 'green', icon: '✔', word: 'success' };
    case 'info':
      return { colour: 'blue', icon: 'ℹ', word: 'info' };
    case 'warn':
      return {
        colour: event.kind === 'approval' || event.kind === 'retry' ? 'amber' : 'orange',
        icon: '⚠',
        word: 'warn',
      };
    default:
      return null;
  }
}

const ROLES = new Map(FLOW.nodes.map((node) => [node.id, node.role]));

/** Who acts in an event: the role of its flow node, or the harness for events without one (log, eval, error). */
export function roleOf(event: TraceEvent): Role {
  const node = nodeOf(event);
  return (node && ROLES.get(node)) || 'harness';
}

/** Colour, icon and word for a run's status in the runs list; none while it runs. The word is the status. */
export function runStatusStyle(status: string): AttentionStyle | null {
  switch (status) {
    case 'completed':
      return { colour: 'green', icon: '✔', word: status };
    case 'failed':
    case 'limit_exceeded':
    case 'timed_out':
      return { colour: 'red', icon: '✖', word: status };
    case 'cancelled':
      return { colour: 'blue', icon: 'ℹ', word: status };
    case 'awaiting_approval':
    case 'interrupted':
      return { colour: 'amber', icon: '⚠', word: status };
    default:
      return null; // running
  }
}

/** A tool result envelope in a few words. */
export function resultText(envelope: Envelope | null | undefined): string {
  if (!envelope) return '';
  if (!envelope.ok) return `${envelope.error?.type}: ${envelope.error?.message}`;
  if (envelope.truncated) return 'truncated';
  const data = envelope.data;
  if (data && typeof data === 'object') {
    if (Array.isArray(data.results) && data.mode)
      return `${data.mode}, ${data.results.length} results`;
    if (data.service && data.status) return `${data.service} ${data.status}`;
    if (data.incident_id) return `${data.incident_id} ${data.status}`;
  }
  return 'ok';
}

/** An event's message, plus the result in words for an ok `tool` event (so `sparse_only` is in the text). */
export function eventText(event: TraceEvent): string {
  const msg = event.msg ?? event.kind;
  return event.kind === 'tool' && event.status === 'ok'
    ? `${msg} · ${resultText(event.data?.result)}`
    : msg;
}

export class TraceStore {
  private readonly list = signal<TraceEvent[]>([]);
  readonly events = this.list.asReadonly();

  /** Events arrive in `seq` order; a repeated or older one (a `/trace` read after `done`) is ignored. */
  add(event: TraceEvent): void {
    const events = this.list();
    if (events.length && event.seq <= events[events.length - 1].seq) return;
    this.list.set([...events, event]);
  }

  private readonly done = computed(() => findLast(this.list(), (e) => e.kind === 'done') ?? null);

  /** Per call id: its `tool` events, its latest `approval` event; per eval target: its `eval` events. */
  private readonly index = computed(() => {
    const tools = new Map<string, TraceEvent[]>();
    const approvals = new Map<string, TraceEvent>();
    const evals = new Map<string, TraceEvent[]>();
    for (const e of this.list()) {
      const callId = e.data?.tool_call_id;
      if (e.kind === 'tool' && callId) tools.set(callId, [...(tools.get(callId) ?? []), e]);
      else if (e.kind === 'approval' && callId) approvals.set(callId, e);
      else if (e.kind === 'eval' && e.data?.target) {
        evals.set(e.data.target, [...(evals.get(e.data.target) ?? []), e]);
      }
    }
    return { tools, approvals, evals };
  });

  /** The call the tools step is running: the first call of the latest reply whose last attempt is not final. */
  private readonly running = computed(() => {
    const events = this.list();
    if (this.done()) return null;
    const reply = findLast(events, (e) => e.kind === 'llm' && e.data?.tool_calls?.length > 0);
    // Nothing runs until the tools step starts after that reply (approvals come first). After a crash and a
    // resume the step starts again from its first call, so only attempts since its latest start count.
    const start = reply && findLast(events, (e) => e.kind === 'stage' && e.node === 'tools');
    if (!reply || !start || start.seq < reply.seq) return null;
    const { tools } = this.index();
    for (const call of reply.data.tool_calls as ProposedCall[]) {
      const last = tools
        .get(call.id)
        ?.filter((e) => e.seq > start.seq)
        .at(-1);
      if (!last || retried(last)) return { call, last: last ?? null };
    }
    return null;
  });

  /** What ran (the last attempt's args), else what the operator edited, else what the LLM proposed. */
  private argsOf(call: ProposedCall): unknown {
    const { tools, approvals } = this.index();
    const last = tools.get(call.id)?.at(-1);
    if (last) return last.data.args;
    const approval = approvals.get(call.id);
    if (approval?.status === 'edited' && approval.data?.decision?.args)
      return approval.data.decision.args;
    return call.args;
  }

  readonly timeline = computed<TimelineItem[]>(() => {
    const { tools, approvals, evals } = this.index();
    const running = this.running();
    const items: TimelineItem[] = [];
    for (const e of this.list()) {
      if (e.kind === 'done') {
        items.push({ type: 'done', event: e, badges: evals.get('answer') ?? [] });
        continue;
      }
      if (e.kind !== 'llm') continue;
      items.push({ type: 'llm', event: e });
      for (const call of (e.data?.tool_calls ?? []) as ProposedCall[]) {
        const attempts = tools.get(call.id) ?? [];
        const last = attempts.at(-1) ?? null;
        const approval = approvals.get(call.id) ?? null;
        const latest = [last, approval]
          .filter((x): x is TraceEvent => !!x)
          .sort((a, b) => b.seq - a.seq)[0];
        items.push({
          type: 'call',
          id: call.id,
          tool: call.name,
          args: this.argsOf(call),
          attempts: Math.max(0, ...attempts.map((t) => t.data.attempt)),
          durationMs:
            Math.round(attempts.reduce((sum, t) => sum + (t.data.duration_ms ?? 0), 0) * 10) / 10,
          // No attempt when the run ended (cancelled during its approval or before its turn): not run.
          status:
            running?.call.id === call.id
              ? 'running'
              : (last?.status ?? (this.done() ? 'not run' : (approval?.status ?? 'waiting'))),
          result: last?.data?.result ?? null,
          event: latest ?? null,
          approval,
          badges: evals.get(`search:${call.id}`) ?? [],
        });
      }
    }
    return items;
  });

  readonly now = computed<Now>(() => {
    const events = this.list();
    const done = this.done();
    if (done) return { text: done.msg ?? 'done' };
    const approval = findLast(events, (e) => e.kind === 'approval');
    if (approval?.status === 'pending') {
      return {
        text: approval.msg ?? '',
        tool: approval.tool ?? approval.data?.tool,
        args: approval.data?.args,
      };
    }
    const running = this.running();
    if (running) {
      const about = findLast(events, (e) => e.data?.tool_call_id === running.call.id);
      return {
        text: about?.msg ?? 'running',
        tool: running.call.name,
        args: this.argsOf(running.call),
        attempt: (running.last?.data?.attempt ?? 0) + 1,
      };
    }
    return { text: findLast(events, (e) => e.kind !== 'log' && e.kind !== 'eval')?.msg ?? '' };
  });

  /**
   * Who is working, inferred from the events the UI already gets: no event marks a start, so each rule reads the
   * latest events. First rule that holds wins.
   */
  readonly activity = computed<Activity>(() => {
    const events = this.list();
    if (!events.length) return { actor: 'none', text: 'Waiting for the first event' };
    const done = this.done();
    if (done) return { actor: 'none', text: done.msg ?? 'done' };
    const approval = findLast(events, (e) => e.kind === 'approval');
    if (approval?.status === 'pending') {
      const tool = approval.tool ?? approval.data?.tool;
      return { actor: 'person', text: `must approve, edit or reject ${tool} in Approvals` };
    }
    const running = this.running();
    if (running) {
      const tool = running.call.name;
      const attempt = (running.last?.data?.attempt ?? 0) + 1;
      const about = findLast(events, (e) => e.data?.tool_call_id === running.call.id);
      // A retry waits only after an attempt since the latest start; an older one belongs to a segment before a resume.
      if (running.last && about?.kind === 'retry') {
        const wait = Number(about.data?.delay_s ?? 0).toFixed(2);
        return {
          actor: 'harness',
          text: `waits ${wait} s, then runs ${tool} again (attempt ${attempt})`,
        };
      }
      return { actor: 'harness', text: `runs ${tool} (attempt ${attempt})` };
    }
    const latest = findLast(events, (e) => nodeOf(e) !== null);
    const turn =
      (findLast(events, (e) => e.kind === 'stage' && e.node === 'guard')?.data?.steps ?? 0) + 1;
    if (latest?.kind === 'stage' && latest.node === 'agent')
      return { actor: 'llm', text: `is thinking (turn ${turn})` };
    if (latest?.kind === 'retry' && latest.node === 'agent')
      return {
        actor: 'llm',
        text: `is thinking (turn ${turn}, attempt ${(latest.data?.attempt ?? 0) + 1})`,
      };
    if (latest?.kind === 'llm') return { actor: 'harness', text: "checks the LLM's reply" };
    if (latest?.kind === 'stage' && latest.node === 'guard')
      return { actor: 'harness', text: `checks the limits: ${latest.msg ?? ''}` };
    if (latest?.kind === 'stage' && latest.node === 'finalize')
      return { actor: 'harness', text: 'finishes the run' };
    if (latest?.kind === 'approval')
      return {
        actor: 'harness',
        text: `continues after the decision on ${latest.tool ?? latest.data?.tool}`,
      };
    if (latest?.kind === 'tool')
      return { actor: 'harness', text: `has the result of ${latest.tool}` };
    if (!latest) return { actor: 'harness', text: 'starts the run' }; // only the audit log so far
    return { actor: 'harness', text: `is working: ${latest.msg ?? latest.kind}` };
  });

  /** The LLM's latest decision: a reply with tool calls, a final answer, or a malformed reply. */
  readonly lastReply = computed(
    () =>
      findLast(
        this.list(),
        (e) => e.kind === 'llm' && ['tool_calls', 'final', 'malformed'].includes(e.status ?? ''),
      ) ?? null,
  );

  /** The latest event per flow node. */
  readonly nodes = computed<Record<string, TraceEvent>>(() => {
    const nodes: Record<string, TraceEvent> = {};
    for (const e of this.list()) {
      const node = nodeOf(e);
      if (node) nodes[node] = e;
    }
    return nodes;
  });

  readonly activeNode = computed<string | null>(() => {
    if (this.done()) return null;
    const latest = findLast(this.list(), (e) => nodeOf(e) !== null);
    if (!latest) return null;
    // Inside the tools step the running call's tool is lit, also right after an earlier call of the same
    // reply finished: the next call emits nothing until its first attempt ends.
    const inTools =
      (latest.kind === 'stage' && latest.node === 'tools') ||
      (latest.kind === 'tool' && (retried(latest) || this.running() !== null)) ||
      (latest.kind === 'retry' && !!latest.tool);
    return inTools ? (this.running()?.call.name ?? 'tools') : nodeOf(latest);
  });

  /** Events a person should notice, newest first. */
  readonly attention = computed(() =>
    this.list()
      .filter((e) => e.attention)
      .reverse(),
  );

  readonly used = computed<Used>(() => {
    const events = this.list();
    const counter = findLast(
      events,
      (e) => e.kind === 'done' || (e.kind === 'stage' && e.node === 'guard'),
    );
    const start =
      findLast(events, (e) => e.kind === 'log' && SEGMENT_STARTS.has(e.data?.action)) ?? events[0];
    const end = findLast(events, (e) => e.kind !== 'eval');
    return {
      steps: counter?.data?.steps ?? 0,
      toolCalls: counter?.data?.tool_calls ?? 0,
      seconds: start && end ? Math.max(0, (end.t_ms - start.t_ms) / 1000) : 0,
    };
  });

  /** How many `eval` events online evaluation writes: one per search with results, two for a final answer. */
  expectedEvals(hasFinal: boolean): number {
    const searches = this.timeline().filter(
      (item) =>
        item.type === 'call' &&
        item.tool === SEARCH &&
        item.status === 'ok' &&
        item.result?.ok === true &&
        (item.result.data?.results?.length ?? 0) > 0,
    );
    return searches.length + (hasFinal ? 2 : 0);
  }
}
