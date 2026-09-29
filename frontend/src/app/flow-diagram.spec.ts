import { TestBed } from '@angular/core/testing';
import { TraceEvent } from './api';
import { FLOW, NODE_H, NODE_W } from './flow';
import { FlowDiagram } from './flow-diagram';

const event = (fields: Partial<TraceEvent>): TraceEvent => ({
  seq: 1,
  run_id: 'r1',
  t_ms: 1,
  kind: 'tool',
  node: 'tools',
  tool: 'get_service_status',
  status: 'timeout',
  attention: null,
  msg: null,
  data: null,
  created_at: '',
  ...fields,
});

describe('FLOW', () => {
  it("has exactly the plan's 12 nodes and 16 edges", () => {
    expect(new Set(FLOW.nodes.map((n) => n.id))).toEqual(
      new Set([
        'guard',
        'agent',
        'approval',
        'tools',
        'search_knowledge_base',
        'kb.embed',
        'kb.dense',
        'kb.bm25',
        'kb.rrf',
        'get_service_status',
        'create_incident',
        'finalize',
      ]),
    );
    expect(FLOW.nodes.length).toBe(12);
    expect(FLOW.edges).toEqual(
      expect.arrayContaining([
        ['guard', 'agent'],
        ['guard', 'finalize'],
        ['agent', 'finalize'],
        ['agent', 'guard'],
        ['agent', 'approval'],
        ['agent', 'tools'],
        ['approval', 'tools'],
        ['tools', 'guard'],
        ['tools', 'search_knowledge_base'],
        ['tools', 'get_service_status'],
        ['tools', 'create_incident'],
        ['search_knowledge_base', 'kb.embed'],
        ['kb.embed', 'kb.dense'],
        ['kb.dense', 'kb.rrf'],
        ['search_knowledge_base', 'kb.bm25'],
        ['kb.bm25', 'kb.rrf'],
      ]),
    );
    expect(FLOW.edges.length).toBe(16);
  });

  it('fits a viewBox of at most 900×170, with every node inside it', () => {
    expect(FLOW.width).toBeLessThanOrEqual(900);
    expect(FLOW.height).toBeLessThanOrEqual(170);
    const outside = FLOW.nodes.filter(
      (n) => n.x < 0 || n.y < 0 || n.x + NODE_W > FLOW.width || n.y + NODE_H > FLOW.height,
    );
    expect(outside).toEqual([]);
  });

  it('places no node over another', () => {
    const overlaps = FLOW.nodes.flatMap((a, i) =>
      FLOW.nodes
        .slice(i + 1)
        .filter((b) => Math.abs(a.x - b.x) < NODE_W && Math.abs(a.y - b.y) < NODE_H)
        .map((b) => `${a.id}/${b.id}`),
    );
    expect(overlaps).toEqual([]);
  });
});

describe('FlowDiagram', () => {
  async function render(nodes: Record<string, TraceEvent>, active: string | null) {
    const fixture = TestBed.createComponent(FlowDiagram);
    fixture.componentRef.setInput('nodes', nodes);
    fixture.componentRef.setInput('active', active);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('draws every node of the flow data and outlines the active one', async () => {
    const root = await render({}, 'get_service_status');
    expect(root.querySelectorAll('g.node').length).toBe(FLOW.nodes.length);
    expect(root.querySelectorAll('line.edge').length).toBe(FLOW.edges.length);
    const active = [...root.querySelectorAll('g.node.active')].map((g) =>
      g.getAttribute('data-node'),
    );
    expect(active).toEqual(['get_service_status']);
  });

  it("gives each node its role's class and names the roles in a legend", async () => {
    const root = await render({}, 'agent');
    const role = (id: string) =>
      root
        .querySelector(`g[data-node="${id}"]`)
        ?.getAttribute('class')
        ?.match(/role-(\w+)/)?.[1];
    expect(FLOW.nodes.map((n) => [n.id, role(n.id)])).toEqual(
      FLOW.nodes.map((n) => [n.id, n.role]),
    );
    expect(['agent', 'guard', 'approval', 'kb.rrf'].map(role)).toEqual([
      'llm',
      'harness',
      'person',
      'tool',
    ]);
    expect(root.querySelector('g[data-node="agent"]')?.classList.contains('active')).toBe(true);
    const legend = [...root.querySelectorAll('.legend span')].map((s) => [
      s.textContent?.trim(),
      s.className,
    ]);
    expect(legend).toEqual([
      ['LLM', 'role-llm'],
      ['Harness', 'role-harness'],
      ['Tool', 'role-tool'],
      ['Person', 'role-person'],
    ]);
  });

  it('shows the icon, status and colour of a node with attention', async () => {
    const failed = event({ status: 'timeout', attention: 'error' });
    const root = await render(
      { get_service_status: failed, guard: event({ kind: 'stage', status: null }) },
      null,
    );
    const state = root.querySelector('g[data-node="get_service_status"] text.state')!;
    expect(state.textContent?.trim()).toBe('✖ timeout');
    expect(state.getAttribute('class')).toContain('red');
    expect(root.querySelector('g[data-node="guard"] text.state')?.textContent?.trim()).toBe(
      'stage',
    );
    expect(root.querySelector('g.node.active')).toBeNull();
  });
});
