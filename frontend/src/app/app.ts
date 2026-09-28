import { Component, signal } from '@angular/core';
import { RunsPage } from './runs-page';

export type Tab = 'runs' | 'eval' | 'incidents';

@Component({
  selector: 'app-root',
  imports: [RunsPage],
  template: `
    <header class="app-header">
      <h1>Ops Agent Harness</h1>
      <nav class="tabs" aria-label="Tabs">
        @for (t of tabs; track t.id) {
          <button
            type="button"
            [attr.aria-current]="tab() === t.id ? 'page' : null"
            (click)="tab.set(t.id)"
          >
            {{ t.label }}
          </button>
        }
      </nav>
    </header>
    <!-- All panels stay rendered, so the open run's stream and a running evaluation survive a tab switch. -->
    <main>
      <section data-tab="runs" [hidden]="tab() !== 'runs'"><app-runs-page /></section>
      <section data-tab="eval" [hidden]="tab() !== 'eval'"><p class="muted">Evaluation</p></section>
      <section data-tab="incidents" [hidden]="tab() !== 'incidents'">
        <p class="muted">Incidents</p>
      </section>
    </main>
  `,
})
export class App {
  protected readonly tabs: { id: Tab; label: string }[] = [
    { id: 'runs', label: 'Runs' },
    { id: 'eval', label: 'Evaluation' },
    { id: 'incidents', label: 'Incidents' },
  ];
  readonly tab = signal<Tab>('runs');
}
