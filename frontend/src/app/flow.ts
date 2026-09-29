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
    width: 884, // fits the Runs tab's center column at 1280×720 with labels of about 10 px
    height: 162,
    nodes: [
      { id: 'guard', label: 'guard', x: 0, y: 64 },
      { id: 'agent', label: 'agent', x: 128, y: 64 },
      { id: 'approval', label: 'approval', x: 256, y: 4 },
      { id: 'finalize', label: 'finalize', x: 256, y: 124 },
      { id: 'tools', label: 'tools', x: 384, y: 64 },
      { id: 'search_knowledge_base', label: 'search', x: 512, y: 4 },
      { id: 'get_service_status', label: 'status', x: 512, y: 64 },
      { id: 'create_incident', label: 'incident', x: 512, y: 124 },
      { id: 'kb.embed', label: 'embed', x: 640, y: 4 },
      { id: 'kb.dense', label: 'dense', x: 768, y: 4 },
      { id: 'kb.bm25', label: 'bm25', x: 640, y: 64 },
      { id: 'kb.rrf', label: 'rrf', x: 768, y: 64 },
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
