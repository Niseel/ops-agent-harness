import { Component, computed, signal } from '@angular/core';
import { ApiError, EvalReport, EvalSummary, api, detailText, readSse } from './api';

const METRICS: (keyof EvalSummary)[] = [
  'hit@3',
  'mrr@10',
  'recall@3',
  'context_precision',
  'context_recall',
];

/** The Evaluation tab (spec: UI): the latest golden-set report, and a new run over SSE with its progress. */
@Component({
  selector: 'app-eval-tab',
  template: `
    <section class="panel" aria-labelledby="eval-title">
      <h2 id="eval-title">Knowledge-base evaluation</h2>
      <div class="row">
        <button type="button" [disabled]="running()" (click)="run()">Run evaluation</button>
        @if (progress(); as p) {
          <label class="row">
            <progress [max]="p.total" [value]="p.done"></progress>
            {{ p.done }}/{{ p.total }} questions
          </label>
        } @else if (running()) {
          <label class="row">
            <progress></progress>
            waiting for the first question (a local judge can take a minute)
          </label>
        }
      </div>
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      @if (report(); as report) {
        <p class="muted">
          Report of {{ time(report.created_at) }} · embed {{ report.models.embed_model }} · judge
          {{ report.models.judge_model }}
        </p>
        <table>
          <thead>
            <tr>
              <th scope="col">mode</th>
              <th scope="col">questions</th>
              <th scope="col">errors</th>
              @for (metric of metrics; track metric) {
                <th scope="col">{{ metric }}</th>
              }
            </tr>
          </thead>
          <tbody>
            @for (row of rows(); track row.mode) {
              <tr>
                <th scope="row">{{ row.mode }}</th>
                <td>{{ row.summary.questions }}</td>
                <td>{{ row.summary.errors }}</td>
                @for (metric of metrics; track metric) {
                  <td>{{ number(row.summary[metric]) }}</td>
                }
              </tr>
            }
          </tbody>
        </table>
        @for (row of rows(); track row.mode) {
          @if (row.summary.judge_error) {
            <p class="muted">{{ row.mode }}: judge error: {{ row.summary.judge_error }}</p>
          }
        }
      }
    </section>
  `,
})
export class EvalTab {
  protected readonly metrics = METRICS;
  readonly report = signal<EvalReport | null>(null);
  readonly progress = signal<{ done: number; total: number } | null>(null);
  readonly running = signal(false);
  readonly error = signal('');
  readonly rows = computed(() =>
    Object.entries(this.report()?.summary ?? {}).map(([mode, summary]) => ({ mode, summary })),
  );

  constructor() {
    void this.load();
  }

  /** The latest report; a 404 shows its detail (`no evaluation report yet`). The panel keeps its last data. */
  async load(): Promise<void> {
    try {
      this.report.set(await api<EvalReport>('GET', '/api/eval/kb/latest'));
    } catch (error) {
      this.error.set(message(error));
    }
  }

  /** No body: every mode (`{}` would be a 422). `progress`, then `report` or `error`. */
  async run(): Promise<void> {
    this.running.set(true);
    this.error.set('');
    this.progress.set(null);
    try {
      const response = await fetch('/api/eval/kb', { method: 'POST' }).catch(() => null);
      if (!response) throw new ApiError(0, 'network error: the API cannot be reached');
      if (!response.ok || !response.body) {
        const answer = await response.json().catch(() => null);
        throw new ApiError(
          response.status,
          answer?.detail ?? (response.statusText || `HTTP ${response.status}`),
        );
      }
      let ended = false;
      await readSse(response.body, (name, data) => {
        if (name === 'progress') this.progress.set(data);
        else if (name === 'report') this.report.set(data);
        else if (name === 'error') this.error.set(detailText(data?.detail));
        ended ||= name === 'report' || name === 'error';
      });
      if (!ended) this.error.set('the evaluation stream ended early'); // a dropped connection or proxy
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.running.set(false);
    }
    if (!this.error()) await this.load(); // the stored report
  }

  protected number(value: unknown): string {
    return typeof value === 'number' ? value.toFixed(2) : 'n/a';
  }

  protected time(iso: string): string {
    return new Date(iso).toLocaleString();
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
