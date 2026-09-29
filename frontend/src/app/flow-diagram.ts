import { Component, computed, input } from '@angular/core';
import { TraceEvent } from './api';
import { FLOW, NODE_H, NODE_W } from './flow';
import { attentionStyle } from './trace';

/**
 * The flow diagram, drawn only from `FLOW`: each node in its role's colour, the active node outlined and tinted,
 * and a node with an event shows its status. The legend names the roles in words.
 */
@Component({
  selector: 'app-flow-diagram',
  template: `
    <svg
      class="flow"
      [attr.viewBox]="'0 0 ' + flow.width + ' ' + flow.height"
      role="img"
      aria-label="Agent flow"
    >
      @for (edge of edges(); track $index) {
        <line
          class="edge"
          [attr.x1]="edge.x1"
          [attr.y1]="edge.y1"
          [attr.x2]="edge.x2"
          [attr.y2]="edge.y2"
        />
      }
      @for (node of flow.nodes; track node.id) {
        <g
          [attr.class]="'node role-' + node.role + (node.id === active() ? ' active' : '')"
          [attr.data-node]="node.id"
        >
          <title>{{ node.id }}</title>
          <rect [attr.x]="node.x" [attr.y]="node.y" [attr.width]="w" [attr.height]="h" rx="6" />
          <text [attr.x]="node.x + 8" [attr.y]="node.y + 14">{{ node.label }}</text>
          @if (nodes()[node.id]; as event) {
            <text
              [attr.class]="'state ' + (style(event)?.colour ?? '')"
              [attr.x]="node.x + 8"
              [attr.y]="node.y + 28"
            >
              {{ style(event)?.icon ?? '' }} {{ event.status ?? event.kind }}
            </text>
          }
        </g>
      }
    </svg>
    <p class="legend">
      <span class="role-llm">LLM</span> · <span class="role-harness">Harness</span> ·
      <span class="role-tool">Tool</span> · <span class="role-person">Person</span>
    </p>
  `,
})
export class FlowDiagram {
  readonly nodes = input<Record<string, TraceEvent>>({});
  readonly active = input<string | null>(null);
  protected readonly flow = FLOW;
  protected readonly w = NODE_W;
  protected readonly h = NODE_H;
  protected readonly style = attentionStyle;

  /** Lines between node centres, under the nodes. */
  protected readonly edges = computed(() => {
    const at = new Map(FLOW.nodes.map((n) => [n.id, { x: n.x + NODE_W / 2, y: n.y + NODE_H / 2 }]));
    return FLOW.edges.map(([from, to]) => ({
      x1: at.get(from)!.x,
      y1: at.get(from)!.y,
      x2: at.get(to)!.x,
      y2: at.get(to)!.y,
    }));
  });
}
