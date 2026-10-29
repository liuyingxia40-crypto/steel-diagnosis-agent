import { chooseFocus } from "./presentation"
import type { GraphEdge, GraphNode, KnowledgeGraph } from "../types/graph"

export interface ExploreState {
  roots: string[]
  expandedIds: string[]
}

interface Link {
  otherId: string
  relation: string
}

function allowed(node: GraphNode, selectedTypes: ReadonlySet<string>, revealSources: boolean): boolean {
  if (selectedTypes.has(node.data.type)) {
    return true
  }
  return node.data.type === "source" && revealSources
}

function linksFrom(graph: KnowledgeGraph, revealSources: boolean): Map<string, Link[]> {
  const links = new Map<string, Link[]>()
  const add = (from: string, otherId: string, relation: string) => {
    const list = links.get(from) ?? []
    const existing = list.find((item) => item.otherId === otherId)
    if (!existing) {
      list.push({ otherId, relation })
    } else if (existing.relation === "UI_GROUP" && relation !== "UI_GROUP") {
      existing.relation = relation
    }
    links.set(from, list)
  }
  for (const edge of graph.edges) {
    if (!revealSources && edge.data.relation === "SUPPORTED_BY") {
      continue
    }
    add(edge.source, edge.target, edge.data.relation)
    add(edge.target, edge.source, edge.data.relation)
  }
  return links
}

function navigationIds(graph: KnowledgeGraph): Set<string> {
  return new Set(graph.nodes.filter((node) => node.data.type === "ui-group").map((node) => node.id))
}

/** 虚线分类只从分类入口向外走，避免把导航线当成设备的工业关系继续展开。 */
function canFollow(fromId: string, link: Link, navigation: ReadonlySet<string>): boolean {
  return link.relation !== "UI_GROUP" || navigation.has(fromId)
}

function allowedIds(
  graph: KnowledgeGraph,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): Set<string> {
  return new Set(
    graph.nodes.filter((node) => allowed(node, selectedTypes, revealSources)).map((node) => node.id),
  )
}

function reachedExpanded(
  roots: readonly string[],
  expanded: ReadonlySet<string>,
  links: Map<string, Link[]>,
  allowedSet: ReadonlySet<string>,
  navigation: ReadonlySet<string>,
): Set<string> {
  const reached = new Set<string>()
  const queue: string[] = []
  for (const root of roots) {
    if (expanded.has(root) && allowedSet.has(root) && !reached.has(root)) {
      reached.add(root)
      queue.push(root)
    }
  }
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) {
      break
    }
    for (const link of links.get(current) ?? []) {
      if (!canFollow(current, link, navigation)) {
        continue
      }
      if (!allowedSet.has(link.otherId) || !expanded.has(link.otherId) || reached.has(link.otherId)) {
        continue
      }
      reached.add(link.otherId)
      queue.push(link.otherId)
    }
  }
  return reached
}

export function createExplore(rootId: string): ExploreState {
  return { roots: [rootId], expandedIds: [rootId] }
}

export function defaultDeviceId(
  graph: KnowledgeGraph,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): string | null {
  const pool = graph.nodes.filter(
    (node) => allowed(node, selectedTypes, revealSources) && node.data.type === "device",
  )
  return (
    chooseFocus(pool, graph.edges) ??
    chooseFocus(
      graph.nodes.filter((node) => allowed(node, selectedTypes, revealSources)),
      graph.edges,
    )
  )
}

export function searchExplore(
  graph: KnowledgeGraph,
  query: string,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): ExploreState | null {
  const text = query.trim().toLowerCase()
  if (!text) {
    return null
  }
  const matches = graph.nodes.filter(
    (node) =>
      allowed(node, selectedTypes, revealSources) && node.data.label.toLowerCase().includes(text),
  )
  if (matches.length === 0) {
    return null
  }
  const ids = matches.map((node) => node.id)
  return { roots: ids, expandedIds: ids }
}

export interface ExploreView {
  nodes: GraphNode[]
  edges: GraphEdge[]
  visibleIds: Set<string>
  expandedIds: Set<string>
}

export function exploreView(
  graph: KnowledgeGraph,
  state: ExploreState,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): ExploreView {
  const allowedSet = allowedIds(graph, selectedTypes, revealSources)
  const links = linksFrom(graph, revealSources)
  const navigation = navigationIds(graph)
  const expanded = reachedExpanded(state.roots, new Set(state.expandedIds), links, allowedSet, navigation)
  const visible = new Set<string>()
  for (const root of state.roots) {
    if (allowedSet.has(root)) {
      visible.add(root)
    }
  }
  for (const id of expanded) {
    visible.add(id)
    for (const link of links.get(id) ?? []) {
      if (!canFollow(id, link, navigation) || !allowedSet.has(link.otherId)) {
        continue
      }
      visible.add(link.otherId)
    }
  }
  const nodes = graph.nodes.filter((node) => visible.has(node.id))
  const edges = graph.edges.filter(
    (edge) =>
      visible.has(edge.source) &&
      visible.has(edge.target) &&
      (revealSources || edge.data.relation !== "SUPPORTED_BY"),
  )
  return { nodes, edges, visibleIds: visible, expandedIds: expanded }
}

export function hiddenNeighborCount(
  graph: KnowledgeGraph,
  visibleIds: ReadonlySet<string>,
  nodeId: string,
  revealSources: boolean,
  selectedTypes: ReadonlySet<string>,
): number {
  const allowedSet = allowedIds(graph, selectedTypes, revealSources)
  const links = linksFrom(graph, revealSources)
  const navigation = navigationIds(graph)
  let count = 0
  for (const link of links.get(nodeId) ?? []) {
    if (!canFollow(nodeId, link, navigation)) {
      continue
    }
    if (allowedSet.has(link.otherId) && !visibleIds.has(link.otherId)) {
      count += 1
    }
  }
  return count
}

export function expandNode(state: ExploreState, nodeId: string): ExploreState {
  if (state.expandedIds.includes(nodeId)) {
    return state
  }
  return { ...state, expandedIds: [...state.expandedIds, nodeId] }
}

export function collapseNode(
  graph: KnowledgeGraph,
  state: ExploreState,
  nodeId: string,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): ExploreState {
  const allowedSet = allowedIds(graph, selectedTypes, revealSources)
  const links = linksFrom(graph, revealSources)
  const next = new Set(state.expandedIds)
  next.delete(nodeId)
  const kept = reachedExpanded(state.roots, next, links, allowedSet, navigationIds(graph))
  return { ...state, expandedIds: [...kept] }
}

/** 全局主干：全部设备，以及没有连到任何设备的故障。这些故障仍是正式节点。 */
export function globalRoots(graph: KnowledgeGraph): string[] {
  const links = linksFrom(graph, false)
  const devices = graph.nodes.filter((node) => node.data.type === "device").map((node) => node.id)
  const seen = new Set<string>()
  const queue = [...devices]
  for (const id of devices) {
    seen.add(id)
  }
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) {
      break
    }
    for (const link of links.get(current) ?? []) {
      if (seen.has(link.otherId)) {
        continue
      }
      seen.add(link.otherId)
      queue.push(link.otherId)
    }
  }
  const detachedFaults = graph.nodes
    .filter((node) => node.data.type === "fault" && !seen.has(node.id))
    .map((node) => node.id)
  return [...devices, ...detachedFaults]
}

export function createGlobalExplore(graph: KnowledgeGraph): ExploreState {
  const roots = globalRoots(graph)
  return { roots, expandedIds: [...roots] }
}

/** 从当前探索根出发，只沿已经显示的节点计算展示深度。根是第 1 级。 */
export function nodeDepths(
  graph: KnowledgeGraph,
  state: ExploreState,
  visibleIds: ReadonlySet<string>,
): Map<string, number> {
  const links = linksFrom(graph, false)
  const depth = new Map<string, number>()
  const queue: string[] = []
  for (const root of state.roots) {
    if (!visibleIds.has(root) || depth.has(root)) {
      continue
    }
    depth.set(root, 1)
    queue.push(root)
  }
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) {
      break
    }
    const nextDepth = (depth.get(current) ?? 1) + 1
    for (const link of links.get(current) ?? []) {
      if (!visibleIds.has(link.otherId) || depth.has(link.otherId)) {
        continue
      }
      depth.set(link.otherId, nextDepth)
      queue.push(link.otherId)
    }
  }
  return depth
}

export const DEPTH_LEGEND = [
  { level: "第1级", name: "蓝色", hint: "核心入口，主要设备或知识主题", color: "#4c84f5" },
  { level: "第2级", name: "绿色", hint: "直接关联，入口直接连接的知识", color: "#3cba8b" },
  { level: "第3级", name: "橙色", hint: "深入关联，沿真实关系继续展开", color: "#e39a45" },
  { level: "第4级及以上", name: "紫色", hint: "深层关联，进一步探索到的知识", color: "#b07cff" },
] as const

export function depthColor(depth: number): string {
  if (depth <= 1) {
    return DEPTH_LEGEND[0].color
  }
  if (depth === 2) {
    return DEPTH_LEGEND[1].color
  }
  if (depth === 3) {
    return DEPTH_LEGEND[2].color
  }
  return DEPTH_LEGEND[3].color
}

export function depthLabel(depth: number): string {
  if (depth <= 1) {
    return "第1级"
  }
  if (depth === 2) {
    return "第2级"
  }
  if (depth === 3) {
    return "第3级"
  }
  return "第4级及以上"
}

export function canCollapse(
  graph: KnowledgeGraph,
  state: ExploreState,
  nodeId: string,
  selectedTypes: ReadonlySet<string>,
  revealSources: boolean,
): boolean {
  if (!state.expandedIds.includes(nodeId)) {
    return false
  }
  const before = exploreView(graph, state, selectedTypes, revealSources).nodes.length
  const after = exploreView(
    graph,
    collapseNode(graph, state, nodeId, selectedTypes, revealSources),
    selectedTypes,
    revealSources,
  ).nodes.length
  return after < before
}
