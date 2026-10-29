export interface SourceRef {
  document: string
  section: string
  locator: string
}

export interface GraphNode {
  id: string
  parentId?: string
  data: {
    label: string
    type: string
    source_refs: SourceRef[]
    category?: string
    value?: string
    unit?: string
    kind?: string
    applicability?: string
    device_label?: string
  }
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  data: {
    relation: string
    source_refs: SourceRef[]
  }
}

export interface KnowledgeGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}
