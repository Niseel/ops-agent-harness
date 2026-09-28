import { Component, output, signal } from '@angular/core';
import { api } from './api';

/** Fault targets and their modes (backend/app/tools/faults.py). Latency keeps the server's default `ms`. */
export const FAULT_TARGETS: { target: string; modes: string[] }[] = [
  { target: 'search_knowledge_base', modes: ['timeout', 'error', 'bad_output', 'latency'] },
  { target: 'get_service_status', modes: ['timeout', 'error', 'bad_output', 'latency'] },
  {
    target: 'create_incident',
    modes: ['timeout', 'error', 'bad_output', 'latency', 'timeout_after_commit'],
  },
  { target: 'llm', modes: ['malformed', 'timeout'] },
  { target: 'embeddings', modes: ['error'] },
];

/** The keys of `config.yaml > limits`. A blank field keeps the config value. */
export const LIMIT_KEYS = [
  'max_steps',
  'max_tool_calls',
  'max_run_seconds',
  'max_repeat_calls',
  'max_repairs',
  'max_calls_per_reply',
  'max_incidents_per_run',
];

/**
 * The `POST /api/runs` body from the form's fields: only what was set, numbers as numbers (the API is strict),
 * and no `options` key when nothing was set.
 */
export function runBody(values: Record<string, string>): Record<string, unknown> {
  const body: Record<string, unknown> = { objective: values['objective'] ?? '' };
  if (values['llm'] && values['llm'] !== 'default') body['llm'] = values['llm'];
  const options: Record<string, unknown> = {};
  if (values['evaluate'] === 'on' || values['evaluate'] === 'off') {
    options['evaluate'] = values['evaluate'] === 'on';
  }
  const faults: Record<string, unknown> = {};
  for (const { target } of FAULT_TARGETS) {
    const mode = values[`fault.${target}`];
    if (!mode || mode === 'off') continue;
    const times = values[`times.${target}`];
    faults[target] = times ? { mode, times: Number(times) } : { mode };
  }
  if (Object.keys(faults).length) options['faults'] = faults;
  const limits: Record<string, number> = {};
  for (const key of LIMIT_KEYS) {
    if (values[`limit.${key}`]) limits[key] = Number(values[`limit.${key}`]);
  }
  if (Object.keys(limits).length) options['limits'] = limits;
  if (Object.keys(options).length) body['options'] = options;
  return body;
}

@Component({
  selector: 'app-run-form',
  template: `
    <form class="run-form" (submit)="submit($event)">
      <label>
        Objective
        <textarea name="objective" rows="3" required maxlength="2000"></textarea>
      </label>
      <div class="row">
        <label>
          LLM
          <select name="llm">
            <option value="default">default</option>
            <option value="fake">fake</option>
            <option value="openai">openai</option>
          </select>
        </label>
        <label>
          Evaluate
          <select name="evaluate">
            <option value="default">default</option>
            <option value="on">on</option>
            <option value="off">off</option>
          </select>
        </label>
      </div>
      <details>
        <summary>Fault switches</summary>
        @for (fault of faultTargets; track fault.target) {
          <div class="row">
            <label>
              {{ fault.target }}
              <select [name]="'fault.' + fault.target">
                <option value="off">off</option>
                @for (mode of fault.modes; track mode) {
                  <option [value]="mode">{{ mode }}</option>
                }
              </select>
            </label>
            <label>
              times
              <input
                type="number"
                min="1"
                step="1"
                placeholder="1"
                [name]="'times.' + fault.target"
              />
            </label>
          </div>
        }
      </details>
      <details>
        <summary>Limits</summary>
        @for (key of limitKeys; track key) {
          <label class="row">
            {{ key }}
            <input type="number" min="1" step="1" placeholder="config" [name]="'limit.' + key" />
          </label>
        }
      </details>
      <button type="submit" [disabled]="busy()">Run</button>
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
    </form>
  `,
})
export class RunForm {
  readonly created = output<string>();
  protected readonly faultTargets = FAULT_TARGETS;
  protected readonly limitKeys = LIMIT_KEYS;
  readonly busy = signal(false);
  readonly error = signal('');

  async submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const form = event.target as HTMLFormElement;
    const values = Object.fromEntries(
      [...new FormData(form)].map(([key, value]) => [key, String(value)]),
    );
    this.busy.set(true);
    this.error.set('');
    try {
      const { run_id } = await api<{ run_id: string }>('POST', '/api/runs', runBody(values));
      this.created.emit(run_id);
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : String(error));
    } finally {
      this.busy.set(false);
    }
  }
}
