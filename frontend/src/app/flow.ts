// The flow diagram's data (ADR 0015): the spec's agent loop, one node per tool and the knowledge-base sub-steps.
// Node ids are what `nodeOf` in trace.ts returns. Positions are placed by hand, in SVG units.

export interface FlowNode {
  id: string;
  label: string;
  x: number;
  y: number;
}

export const NODE_W = 116;
export const NODE_H = 34;

export const FLOW: { width: number; height: number; nodes: FlowNode[]; edges: [string, string][] } =
  {
    width: 1116,
    height: 190,
    nodes: [
      { id: 'guard', label: 'guard', x: 10, y: 78 },
      { id: 'agent', label: 'agent', x: 150, y: 78 },
      { id: 'approval', label: 'approval', x: 290, y: 20 },
      { id: 'finalize', label: 'finalize', x: 290, y: 140 },
      { id: 'tools', label: 'tools', x: 430, y: 78 },
      { id: 'search_knowledge_base', label: 'search', x: 570, y: 20 },
      { id: 'get_service_status', label: 'status', x: 570, y: 78 },
      { id: 'create_incident', label: 'incident', x: 570, y: 136 },
      { id: 'kb.embed', label: 'embed', x: 710, y: 0 },
      { id: 'kb.dense', label: 'dense', x: 850, y: 0 },
      { id: 'kb.bm25', label: 'bm25', x: 780, y: 44 },
      { id: 'kb.rrf', label: 'rrf', x: 990, y: 22 },
    ],
    edges: [
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
    ],
  };
