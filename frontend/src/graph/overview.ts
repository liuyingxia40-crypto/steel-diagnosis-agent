import { chooseFocus, relationLabel, typeLabel } from "./presentation"
import type { GraphEdge, GraphNode, KnowledgeGraph } from "../types/graph"

/** 界面聚合节点的前缀。这些 id 只存在于画布，不能写入正式图谱。 */
export const UI_GROUP_PREFIX = "ui-group:"

export interface OverviewAggregate {
  count: number
  representativeId: string
  representativeLabel: string
}

export interface OverviewNode extends GraphNode {
  aggregate: OverviewAggregate
}

export interface OverviewEdge extends GraphEdge {
  caption: string
}

export interface OverviewModel {
  nodes: OverviewNode[]
  edges: OverviewEdge[]
}

function allowed(node: GraphNode, selectedTypes: ReadonlySet<string>, revealSources: boolean): boolean {
  if (selectedTypes.has(node.data.type)) {
    return true
  }
  return node.data.type === "source" && revealSources
}

export function isUiGroupId(id: string): boolean {
  return id.startsWith(UI_GROUP_PREFIX)
}

/**
 * 按节点类型汇总真实图谱，并只用真实跨类型关系连接这些汇总点。
 * 返回值仅供画布使用。
 */
export function buildOverview(
  graph: KnowledgeGraph,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): OverviewModel {
  const members = new Map<string, GraphNode[]>()
  for (const node of graph.nodes) {
    if (!allowed(node, selectedTypes, revealSources)) {
      continue
    }
    const list = members.get(node.data.type) ?? []
    list.push(node)
    members.set(node.data.type, list)
  }

  const nodes: OverviewNode[] = []
  for (const [type, group] of members) {
    const representativeId = chooseFocus(group, graph.edges)
    const representative = group.find((node) => node.id === representativeId) ?? group[0]
    if (!representative) {
      continue
    }
    nodes.push({
      id: `${UI_GROUP_PREFIX}${type}`,
      data: {
        label: `${typeLabel(type)} · ${group.length}\n${representative.data.label}`,
        type,
        source_refs: [],
      },
      aggregate: {
        count: group.length,
        representativeId: representative.id,
        representativeLabel: representative.data.label,
      },
    })
  }

  const relationCounts = new Map<string, Map<string, number>>()
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  for (const edge of graph.edges) {
    if (!revealSources && edge.data.relation === "SUPPORTED_BY") {
      continue
    }
    const source = byId.get(edge.source)
    const target = byId.get(edge.target)
    if (!source || !target) {
      continue
    }
    if (!allowed(source, selectedTypes, revealSources) || !allowed(target, selectedTypes, revealSources)) {
      continue
    }
    if (source.data.type === target.data.type) {
      continue
    }
    const pair = [source.data.type, target.data.type].sort().join("|")
    const counts = relationCounts.get(pair) ?? new Map<string, number>()
    counts.set(edge.data.relation, (counts.get(edge.data.relation) ?? 0) + 1)
    relationCounts.set(pair, counts)
  }

  const edges: OverviewEdge[] = []
  for (const [pair, counts] of relationCounts) {
    const [left, right] = pair.split("|")
    if (!left || !right) {
      continue
    }
    const parts = [...counts.entries()].sort((a, b) => b[1] - a[1])
    const caption = parts.map(([relation, count]) => `${relationLabel(relation)} ${count}`).join("，")
    edges.push({
      id: `${UI_GROUP_PREFIX}link:${pair}`,
      source: `${UI_GROUP_PREFIX}${left}`,
      target: `${UI_GROUP_PREFIX}${right}`,
      caption,
      data: {
        relation: parts[0]?.[0] ?? "",
        source_refs: [],
      },
    })
  }

  return { nodes, edges }
}
