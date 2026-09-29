import { Component, ElementRef, signal, viewChild } from '@angular/core';
import { EvalTab } from './eval-tab';
import { IncidentsTab } from './incidents-tab';
import { RunsPage } from './runs-page';
import { Tour } from './tour';

export type Tab = 'runs' | 'eval' | 'incidents';

@Component({
  selector: 'app-root',
  imports: [RunsPage, EvalTab, IncidentsTab, Tour],
  template: `
    <header class="app-header">
      <h1>Ops Agent Harness</h1>
      <nav id="tabs" class="tabs" aria-label="Tabs">
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
      <button #help type="button" class="help" aria-haspopup="dialog" (click)="startTour()">
        Help
      </button>
    </header>
    <!-- All panels stay rendered, so the open run's stream and a running evaluation survive a tab switch. -->
    <main>
      <section data-tab="runs" [hidden]="tab() !== 'runs'"><app-runs-page /></section>
      <section data-tab="eval" [hidden]="tab() !== 'eval'"><app-eval-tab /></section>
      <section data-tab="incidents" [hidden]="tab() !== 'incidents'">
        <app-incidents-tab [active]="tab() === 'incidents'" />
      </section>
    </main>
    <app-tour (closed)="helpButton().nativeElement.focus()" />
  `,
})
export class App {
  protected readonly tabs: { id: Tab; label: string }[] = [
    { id: 'runs', label: 'Runs' },
    { id: 'eval', label: 'Evaluation' },
    { id: 'incidents', label: 'Incidents' },
  ];
  readonly tab = signal<Tab>('runs');
  protected readonly helpButton = viewChild.required<ElementRef<HTMLButtonElement>>('help');
  private readonly tour = viewChild.required(Tour);

  /** The tour walks through the Runs tab, so it shows that tab first. */
  startTour(): void {
    this.tab.set('runs');
    this.tour().start();
  }
}
