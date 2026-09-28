import { Component, effect, input, signal } from '@angular/core';
import { Incident, api } from './api';

/** The Incidents tab (spec: UI): the mock incident system, read each time the tab is shown. */
@Component({
  selector: 'app-incidents-tab',
  template: `
    <section class="panel" aria-labelledby="incidents-title">
      <h2 id="incidents-title">Incidents</h2>
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      <table>
        <thead>
          <tr>
            <th scope="col">id</th>
            <th scope="col">run</th>
            <th scope="col">title</th>
            <th scope="col">severity</th>
            <th scope="col">status</th>
            <th scope="col">created</th>
          </tr>
        </thead>
        <tbody>
          @for (incident of incidents(); track incident.id) {
            <tr [title]="incident.description">
              <td class="mono">{{ incident.id }}</td>
              <td class="mono">{{ incident.run_id?.slice(0, 6) ?? '-' }}</td>
              <td>{{ incident.title }}</td>
              <td>{{ incident.severity }}</td>
              <td>{{ incident.status }}</td>
              <td>{{ time(incident.created_at) }}</td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="muted">No incidents yet.</td>
            </tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class IncidentsTab {
  readonly active = input(false);
  readonly incidents = signal<Incident[]>([]);
  readonly error = signal('');

  constructor() {
    effect(() => {
      if (this.active()) void this.load();
    });
  }

  async load(): Promise<void> {
    try {
      this.incidents.set(await api<Incident[]>('GET', '/api/incidents'));
      this.error.set('');
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : String(error));
    }
  }

  protected time(iso: string): string {
    return new Date(iso).toLocaleTimeString();
  }
}
