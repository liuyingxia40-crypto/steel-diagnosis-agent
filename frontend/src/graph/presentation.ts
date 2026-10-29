import type { GraphEdge, GraphNode, KnowledgeGraph, SourceRef } from "../types/graph"

export const TYPE_LABEL: Record<string, string> = {
  device: "设备",
  component: "部件",
  fault: "故障",
  feature: "特征",
  cause: "原因",
  action: "处置",
  case: "案例",
  source: "来源",
  parameter: "参数",
  "ui-note": "记载摘要",
}

export const TYPE_COLOR: Record<string, string> = {
  device: "#3d9e8f",
  component: "#5b8def",
  fault: "#e06c75",
  feature: "#e0b15a",
  cause: "#c084fc",
  action: "#e39b5a",
  case: "#7d8cff",
  source: "#8b9bab",
  parameter: "#3db8c9",
}

export const RELATION_LABEL: Record<string, string> = {
  HAS_COMPONENT: "包含部件",
  HAS_FAULT: "存在故障",
  HAS_FEATURE: "具有特征",
  HAS_CAUSE: "原因",
  RECOMMENDS_ACTION: "建议处置",
  CONNECTS_TO: "连接",
  OCCURS_ON: "发生于",
  SUPPORTED_BY: "来源支撑",
  HAS_PARAMETER: "具有参数",
  HAS_ACTION: "记载处置",
}

const TYPE_ORDER = [
  "device",
  "component",
  "fault",
  "feature",
  "cause",
  "action",
  "case",
  "parameter",
  "source",
]

const FOCUS_ORDER = ["device", "fault", "component", "case", "feature", "action", "cause"]

export type ViewMode = "local" | "full"

export interface VisibleGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focusId: string | null
  layout: "radial" | "concentric"
}

export function typeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type
}

export function relationLabel(relation: string): string {
  return RELATION_LABEL[relation] ?? relation
}

export function typeColor(type: string): string {
  return TYPE_COLOR[type] ?? "#8b9bab"
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function readOptionalText(record: Record<string, unknown>): {
  value?: string
  unit?: string
  kind?: string
  applicability?: string
  device_label?: string
} {
  const extra: {
    value?: string
    unit?: string
    kind?: string
    applicability?: string
    device_label?: string
  } = {}
  for (const key of ["value", "unit", "kind", "applicability", "device_label"] as const) {
    const text = record[key]
    if (typeof text === "string" && text.trim()) {
      extra[key] = text
    }
  }
  return extra
}

function readSourceRefs(value: unknown): SourceRef[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    if (!isRecord(item)) {
      return []
    }
    const document = typeof item.document === "string" ? item.document : ""
    const section = typeof item.section === "string" ? item.section : ""
    const locator = typeof item.locator === "string" ? item.locator : ""
    if (!document && !section && !locator) {
      return []
    }
    return [{ document, section, locator }]
  })
}

export function parseKnowledgeGraph(value: unknown): KnowledgeGraph {
  if (!isRecord(value) || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error("图谱数据缺少 nodes 或 edges")
  }

  const nodes: GraphNode[] = []
  for (const item of value.nodes) {
    if (!isRecord(item) || typeof item.id !== "string" || !isRecord(item.data)) {
      continue
    }
    const label = typeof item.data.label === "string" ? item.data.label : item.id
    const type = typeof item.data.type === "string" ? item.data.type : "unknown"
    const extra = readOptionalText(item.data)
    const category = typeof item.data.category === "string" && item.data.category.trim() ? item.data.category : undefined
    const parentId = typeof item.parentId === "string" && item.parentId.trim() && item.parentId !== item.id ? item.parentId : undefined
    nodes.push({
      id: item.id,
      ...(parentId ? { parentId } : {}),
      data: {
        label,
        type,
        source_refs: readSourceRefs(item.data.source_refs),
        ...(category ? { category } : {}),
        ...extra,
      },
    })
  }

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges: GraphEdge[] = []
  for (const item of value.edges) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.source !== "string" ||
      typeof item.target !== "string" ||
      !nodeIds.has(item.source) ||
      !nodeIds.has(item.target)
    ) {
      continue
    }
    const relation =
      isRecord(item.data) && typeof item.data.relation === "string"
        ? item.data.relation
        : ""
    edges.push({
      id: item.id,
      source: item.source,
      target: item.target,
      data: {
        relation,
        source_refs: isRecord(item.data) ? readSourceRefs(item.data.source_refs) : [],
      },
    })
  }

  return { nodes, edges }
}

export function importedDeviceNames(previous: KnowledgeGraph, next: KnowledgeGraph): string[] {
  const previousIds = new Set(previous.nodes.map((node) => node.id))
  const byId = new Map(next.nodes.map((node) => [node.id, node]))
  const names: string[] = []
  for (const node of next.nodes) {
    if (previousIds.has(node.id)) {
      continue
    }
    const named = node.data.device_label || (node.parentId ? byId.get(node.parentId)?.data.label : "")
    if (named && !names.includes(named)) {
      names.push(named)
    }
  }
  return names
}

export function presentTypes(nodes: GraphNode[]): string[] {
  const found = new Set(nodes.map((node) => node.data.type))
  const ordered = TYPE_ORDER.filter((type) => found.has(type))
  for (const type of found) {
    if (!ordered.includes(type)) {
      ordered.push(type)
    }
  }
  return ordered
}

function businessDegree(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const degree = new Map(nodes.map((node) => [node.id, 0]))
  for (const edge of edges) {
    if (edge.data.relation === "SUPPORTED_BY") {
      continue
    }
    if (degree.has(edge.source)) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    }
    if (degree.has(edge.target)) {
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
    }
  }
  return degree
}

export function chooseFocus(nodes: GraphNode[], edges: GraphEdge[]): string | null {
  if (nodes.length === 0) {
    return null
  }
  const degree = businessDegree(nodes, edges)
  const ranked = (group: GraphNode[]) =>
    [...group].sort((left, right) => {
      const delta = (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0)
      if (delta !== 0) {
        return delta
      }
      return left.data.label.localeCompare(right.data.label, "zh-CN")
    })

  for (const type of FOCUS_ORDER) {
    const group = nodes.filter((node) => node.data.type === type)
    if (group.length > 0) {
      return ranked(group)[0].id
    }
  }
  return ranked(nodes)[0].id
}

function includeNode(node: GraphNode, selectedTypes: ReadonlySet<string>, revealSources: boolean): boolean {
  if (selectedTypes.has(node.data.type)) {
    return true
  }
  return node.data.type === "source" && revealSources
}

export function buildVisibleGraph(input: {
  graph: KnowledgeGraph
  selectedTypes: ReadonlySet<string>
  query: string
  mode: ViewMode
  focusId: string | null
  showSources: boolean
}): VisibleGraph {
  const { graph, selectedTypes, mode, focusId } = input
  const revealSources = input.showSources || selectedTypes.has("source")
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  const neighbors = new Map<string, Set<string>>()
  for (const edge of graph.edges) {
    if (!revealSources && edge.data.relation === "SUPPORTED_BY") {
      continue
    }
    const forward = neighbors.get(edge.source) ?? new Set<string>()
    forward.add(edge.target)
    neighbors.set(edge.source, forward)
    const backward = neighbors.get(edge.target) ?? new Set<string>()
    backward.add(edge.source)
    neighbors.set(edge.target, backward)
  }

  const query = input.query.trim().toLowerCase()
  let seedIds: string[] = []
  if (query) {
    seedIds = graph.nodes
      .filter(
        (node) =>
          includeNode(node, selectedTypes, revealSources) &&
          node.data.label.toLowerCase().includes(query),
      )
      .map((node) => node.id)
  } else if (mode === "full") {
    seedIds = graph.nodes
      .filter((node) => includeNode(node, selectedTypes, revealSources))
      .map((node) => node.id)
  } else {
    const current = focusId ? byId.get(focusId) : undefined
    let center =
      current && includeNode(current, selectedTypes, revealSources) ? current.id : null
    if (!center) {
      const pool = graph.nodes.filter(
        (node) => includeNode(node, selectedTypes, revealSources) && node.data.type !== "source",
      )
      center =
        chooseFocus(pool, graph.edges) ??
        chooseFocus(
          graph.nodes.filter((node) => includeNode(node, selectedTypes, revealSources)),
          graph.edges,
        )
    }
    seedIds = center ? [center] : []
  }

  const visible = new Set(seedIds)
  if (query || mode === "local") {
    for (const id of seedIds) {
      for (const next of neighbors.get(id) ?? []) {
        const node = byId.get(next)
        if (node && includeNode(node, selectedTypes, revealSources)) {
          visible.add(next)
        }
      }
    }
  }

  const nodes = graph.nodes.filter((node) => visible.has(node.id))
  const edges = graph.edges.filter(
    (edge) =>
      visible.has(edge.source) &&
      visible.has(edge.target) &&
      (revealSources || edge.data.relation !== "SUPPORTED_BY"),
  )
  const center = seedIds.find((id) => visible.has(id)) ?? nodes[0]?.id ?? null
  return {
    nodes,
    edges,
    focusId: center,
    layout: nodes.length > 70 ? "concentric" : "radial",
  }
}
