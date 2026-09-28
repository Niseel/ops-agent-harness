import { JsonPipe } from '@angular/common';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { RunDetail, RunSummary, TraceEvent, api, followRun } from './api';
import { RunForm } from './run-form';
import { TraceStore, attentionStyle, resultText } from './trace';

export const REFRESH_MS = 5000; // runs list, open run summary and (T5) approvals
export const EVAL_POLL_MS = 3000; // `/trace` reads after `done`, for the evaluation badges
export const EVAL_POLL_READS = 40; // 2 min

/** The Runs tab: new run form and runs list (left), timeline (center), detail panel (bottom). */
@Component({
  selector: 'app-runs-page',
  imports: [RunForm, JsonPipe],
  templateUrl: './runs-page.html',
})
export class RunsPage {
  readonly runs = signal<RunSummary[]>([]);
  readonly runId = signal<string | null>(null);
  readonly detail = signal<RunDetail | null>(null);
  readonly store = signal(new TraceStore());
  readonly selected = signal<TraceEvent | null>(null);
  readonly listError = signal('');
  readonly runError = signal('');
  protected readonly attentionStyle = attentionStyle;
  protected readonly resultText = resultText;
  private closeStream: (() => void) | null = null;
  private evalTimer: ReturnType<typeof setTimeout> | undefined;
  private refreshSeq = 0; // only the latest refresh writes: an older answer must not overwrite a newer one

  constructor() {
    const timer = setInterval(() => void this.refresh(), REFRESH_MS);
    inject(DestroyRef).onDestroy(() => {
      clearInterval(timer);
      clearTimeout(this.evalTimer);
      this.closeStream?.();
      this.store.set(new TraceStore()); // stops a `/trace` read chain that is still in flight
    });
    void this.refresh();
  }

  /** Show one run: a new store fed by its live events. */
  open(runId: string): void {
    this.closeStream?.();
    clearTimeout(this.evalTimer);
    const store = new TraceStore();
    this.store.set(store);
    this.runId.set(runId);
    this.detail.set(null);
    this.selected.set(null);
    this.closeStream = followRun(runId, (event) => {
      store.add(event);
      if (event.kind === 'approval' || event.kind === 'done') void this.refresh();
      if (event.kind === 'done') void this.readEvals(runId, store);
    });
    void this.refresh();
  }

  async refresh(): Promise<void> {
    const seq = ++this.refreshSeq;
    const runId = this.runId();
    const [runs, detail] = await Promise.allSettled([
      api<RunSummary[]>('GET', '/api/runs'),
      runId
        ? api<RunDetail>('GET', `/api/runs/${encodeURIComponent(runId)}`)
        : Promise.resolve(null),
    ]);
    if (seq !== this.refreshSeq) return; // a newer refresh has started
    if (runs.status === 'fulfilled') this.runs.set(runs.value);
    this.listError.set(runs.status === 'rejected' ? message(runs.reason) : '');
    if (!runId || this.runId() !== runId) return;
    if (detail.status === 'fulfilled') this.detail.set(detail.value);
    this.runError.set(detail.status === 'rejected' ? message(detail.reason) : '');
  }

  /**
   * Online evaluation writes its `eval` events after `done`, and the stream ends at `done`: read `/trace` until
   * the store holds every expected one, for at most 2 min, or until another run opens.
   */
  private async readEvals(runId: string, store: TraceStore): Promise<void> {
    let detail: RunDetail;
    try {
      detail = await api<RunDetail>('GET', `/api/runs/${encodeURIComponent(runId)}`);
    } catch {
      return;
    }
    if (detail.status === 'cancelled' || !detail.options?.evaluate) return;
    let reads = 0;
    // Keyed on the store, not the run id: reopening the same run makes a new store and ends this chain.
    const read = async (): Promise<void> => {
      if (this.store() !== store) return;
      try {
        const trace = await api<{ events: TraceEvent[] }>(
          'GET',
          `/api/runs/${encodeURIComponent(runId)}/trace`,
        );
        trace.events.forEach((event) => store.add(event));
      } catch {
        // the next read tries again
      }
      reads += 1;
      const evals = store.events().filter((event) => event.kind === 'eval').length;
      if (evals >= store.expectedEvals(detail.final != null) || reads >= EVAL_POLL_READS) return;
      if (this.store() === store) this.evalTimer = setTimeout(() => void read(), EVAL_POLL_MS);
    };
    await read();
  }

  protected short(id: string): string {
    return id.slice(0, 6);
  }

  protected time(iso: string): string {
    return new Date(iso).toLocaleTimeString();
  }

  protected compact(value: unknown): string {
    return JSON.stringify(value) ?? '';
  }

  protected badgeText(badge: TraceEvent): string {
    const value = badge.data?.value;
    return `${badge.data?.metric} ${typeof value === 'number' ? value.toFixed(2) : 'n/a'}`;
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
