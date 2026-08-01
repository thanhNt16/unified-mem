// CBM-derived graph types (MIT, DeusData). Trimmed to what static demo needs.
export interface GraphNode {
  id: number;
  x: number;
  y: number;
  z: number;
  label: string;
  name: string;
  qualified_name?: string;
  size: number;
  color: string;
  cluster: number;
  subtype?: string;
  deg: number;
}

export interface GraphEdge {
  source: number;
  target: number;
  type: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
