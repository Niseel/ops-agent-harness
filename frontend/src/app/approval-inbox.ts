import { JsonPipe } from '@angular/common';
import { Component, DestroyRef, inject, input, output, signal } from '@angular/core';
import { ApiError, Approval, api } from './api';

/**
 * Pending approvals of all runs (spec: UI): a countdown to `expires_at`, then approve, edit or reject with a
 * reason. The approver token lives in a signal only: never stored, never logged, asked again after a reload.
 */
@Component({
  selector: 'app-approval-inbox',
  imports: [JsonPipe],
  template: `
    <label class="token">
      Approver token
      <input
        type="password"
        autocomplete="off"
        placeholder="only if the API sets one"
        (input)="token.set(value($event))"
      />
    </label>
    @if (error()) {
      <p class="error" role="alert">{{ error() }}</p>
    }
    <ul class="inbox">
      @for (approval of approvals(); track approval.id) {
        <li class="approval">
          <div class="row">
            <strong>{{ approval.tool }}</strong>
            <button type="button" class="mono" (click)="openRun.emit(approval.run_id)">
              run {{ approval.run_id.slice(0, 6) }}
            </button>
            <span class="mono countdown" title="Time left to decide">{{
              countdown(approval)
            }}</span>
          </div>
          <pre>{{ approval.args | json }}</pre>
          @if (form()?.id === approval.id) {
            <form class="decide" (submit)="submit($event, approval)">
              @if (form()?.kind === 'edit') {
                <label>
                  Args (JSON object)
                  <textarea
                    rows="6"
                    [value]="draft()"
                    (input)="draft.set(value($event))"
                  ></textarea>
                </label>
              } @else {
                <label>
                  Reason
                  <input maxlength="500" [value]="draft()" (input)="draft.set(value($event))" />
                </label>
              }
              <div class="row">
                <button type="submit" [disabled]="busy() || !draft().trim()">
                  {{ form()?.kind === 'edit' ? 'Send edit' : 'Send reject' }}
                </button>
                <button type="button" [disabled]="busy()" (click)="form.set(null)">Cancel</button>
              </div>
            </form>
          } @else {
            <div class="row">
              <button
                type="button"
                [disabled]="busy()"
                (click)="decide(approval, { decision: 'approve' })"
              >
                Approve
              </button>
              <button type="button" [disabled]="busy()" (click)="openForm(approval, 'edit')">
                Edit
              </button>
              <button type="button" [disabled]="busy()" (click)="openForm(approval, 'reject')">
                Reject
              </button>
            </div>
          }
        </li>
      } @empty {
        <li class="muted">No pending approvals.</li>
      }
    </ul>
  `,
})
export class ApprovalInbox {
  readonly approvals = input<Approval[]>([]);
  readonly decided = output<void>();
  readonly openRun = output<string>();
  readonly token = signal('');
  readonly now = signal(Date.now());
  readonly form = signal<{ id: string; kind: 'edit' | 'reject' } | null>(null); // one form open at a time
  readonly draft = signal(''); // the reason, or the edited args as JSON text
  readonly busy = signal(false);
  readonly error = signal('');

  constructor() {
    const timer = setInterval(() => this.now.set(Date.now()), 1000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  /** `mm:ss` to `expires_at` on the browser clock. At zero the buttons stay: the server decides until its sweep. */
  countdown(approval: Approval): string {
    const seconds = Math.ceil((Date.parse(approval.expires_at) - this.now()) / 1000);
    if (seconds <= 0) return 'expired';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`;
  }

  openForm(approval: Approval, kind: 'edit' | 'reject'): void {
    this.form.set({ id: approval.id, kind });
    this.draft.set(kind === 'edit' ? JSON.stringify(approval.args, null, 2) : '');
    this.error.set('');
  }

  submit(event: Event, approval: Approval): void {
    event.preventDefault();
    if (this.form()?.kind === 'reject') {
      void this.decide(approval, { decision: 'reject', reason: this.draft() });
      return;
    }
    let args: unknown;
    try {
      args = JSON.parse(this.draft());
    } catch {
      args = null;
    }
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
      this.error.set('args must be a JSON object');
      return;
    }
    void this.decide(approval, { decision: 'edit', args });
  }

  /** 200: done. 404 or 409: decided elsewhere or expired, so the list reloads. 401 or 422: the item stays. */
  async decide(approval: Approval, body: Record<string, unknown>): Promise<void> {
    if (/[^\x00-\xff]/.test(this.token())) {
      // `fetch` throws on such a header value, which `api` would report as a network error
      this.error.set('the approver token has a character an HTTP header cannot carry');
      return;
    }
    const headers: Record<string, string> = this.token()
      ? { 'X-Approver-Token': this.token() }
      : {};
    const path = `/api/runs/${encodeURIComponent(approval.run_id)}/approvals/${encodeURIComponent(approval.id)}`;
    this.busy.set(true);
    this.error.set('');
    try {
      await api('POST', path, body, headers);
      this.closeForm(approval);
      this.decided.emit();
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : String(error));
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
        this.closeForm(approval);
        this.decided.emit();
      }
    } finally {
      this.busy.set(false);
    }
  }

  /** Only this item's form: approving another item keeps an open draft. */
  private closeForm(approval: Approval): void {
    if (this.form()?.id === approval.id) this.form.set(null);
  }

  protected value(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  }
}
