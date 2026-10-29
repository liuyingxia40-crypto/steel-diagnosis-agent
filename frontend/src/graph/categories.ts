import type { ExploreState } from "./explore"
import type { KnowledgeView, ViewNode } from "./knowledgeView"
import type { GraphNode, KnowledgeGraph } from "../types/graph"

export interface CategoryInfo {
  id: string
  label: string
  hint: string
  color: string
}

export const CATEGORIES: CategoryInfo[] = [
  { id: "equipment", label: "设备知识", hint: "设备、部件和常见故障", color: "#5b95ff" },
  { id: "fault", label: "故障模式", hint: "定义、频谱特征和判据", color: "#f0a85a" },
  { id: "procedure", label: "操作规程", hint: "参数、限值、报警和危险", color: "#4dcc9a" },
  { id: "case", label: "专家案例", hint: "诊断思路、证据和经验", color: "#c49bff" },
  { id: "relation", label: "设备关联", hint: "上下游、连接和耦合", color: "#4ed4e4" },
]

const CATEGORY_BY_ID = new Map(CATEGORIES.map((item) => [item.id, item]))
const MODULE_CATEGORY: Record<string, string> = {
  "KB-01": "equipment",
  "KB-02": "fault",
  "KB-03": "procedure",
  "KB-04": "case",
  "KB-05": "relation",
  "KB-06": "case",
}
const CATEGORY_MODULE: Record<string, string> = {
  equipment: "KB-01",
  fault: "KB-02",
  procedure: "KB-03",
  case: "KB-04",
  relation: "KB-05",
}

const CORE_DEVICES = ["棒线主轧机电机", "棒线主减速机/齿轮箱", "棒线冷却水循环泵"]
const FAULT_MODES = ["轴承磨损", "转子不平衡", "齿轮异常", "润滑故障"]
const GROUP_ANCHOR: Record<string, { x: number; y: number }> = {
  equipment: { x: -230, y: -120 },
  fault: { x: 230, y: -130 },
  procedure: { x: -240, y: 145 },
  case: { x: 0, y: 165 },
  relation: { x: 240, y: 140 },
}

export interface NodeMeta {
  categoryIds: string[]
  color: string
  important: boolean
  headline: boolean
  uiGroup: boolean
  hub: boolean
  region: string
}

export interface KnowledgeBrief {
  text: string
  parameters: Array<{ name: string; value: string }>
  points: Array<{ label: string; text: string }>
  note: string
}

export interface GroupInfo {
  categoryId: string
  hubId: string
  memberIds: string[]
}

export interface DisplayModel {
  graph: KnowledgeGraph
  meta: Map<string, NodeMeta>
  overview: ExploreState
  groups: GroupInfo[]
  positions: Record<string, { x: number; y: number }>
  briefs: Map<string, KnowledgeBrief>
  overviewStats: { realNodes: number; realEdges: number }
}

function categoryOf(id: string): CategoryInfo {
  return CATEGORY_BY_ID.get(id) ?? CATEGORIES[0]
}

function addCategory(target: Map<string, Set<string>>, id: string, category: string) {
  const current = target.get(id) ?? new Set<string>()
  current.add(category)
  target.set(id, current)
}

function childrenOf(view: KnowledgeView): Map<string, ViewNode[]> {
  const children = new Map<string, ViewNode[]>()
  const byId = new Map(view.nodes.map((node) => [node.id, node]))
  for (const node of view.nodes) {
    if (!node.parent_id || !byId.has(node.parent_id)) {
      continue
    }
    const list = children.get(node.parent_id) ?? []
    list.push(node)
    children.set(node.parent_id, list)
  }
  return children
}

function descendants(children: Map<string, ViewNode[]>, rootId: string): ViewNode[] {
  const found: ViewNode[] = []
  const queue = [...(children.get(rootId) ?? [])]
  const seen = new Set<string>()
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current || seen.has(current.id)) {
      continue
    }
    seen.add(current.id)
    found.push(current)
    queue.push(...(children.get(current.id) ?? []))
  }
  return found
}

function clip(text: string, max: number): string {
  const value = text.replace(/\s+/g, " ").trim()
  return value.length > max ? `${value.slice(0, max)}…` : value
}

function primaryCategory(node: GraphNode, categories: Set<string>): string {
  const type = node.data.type
  if (type === "parameter") {
    return "procedure"
  }
  if (type === "case" || type === "action") {
    return "case"
  }
  if (type === "feature" || type === "fault") {
    return categories.has("fault") ? "fault" : "equipment"
  }
  if (CORE_DEVICES.includes(node.data.label)) {
    return "equipment"
  }
  if (type === "device" && categories.has("relation") && !categories.has("equipment")) {
    return "relation"
  }
  if (type === "device" || type === "component" || categories.has("equipment")) {
    return "equipment"
  }
  if (categories.has("relation")) {
    return "relation"
  }
  if (categories.has("case")) {
    return "case"
  }
  if (categories.has("fault")) {
    return "fault"
  }
  return "equipment"
}

function seedCloud(center: { x: number; y: number }, ids: string[]): Record<string, { x: number; y: number }> {
  const placed: Record<string, { x: number; y: number }> = {}
  ids.forEach((id, index) => {
    const angle = index * 2.399963
    const radius = 38 + 13 * Math.sqrt(index)
    placed[id] = {
      x: Math.round(center.x + Math.cos(angle) * radius),
      y: Math.round(center.y + Math.sin(angle) * radius),
    }
  })
  return placed
}

export function clusterPositions(hubId: string, memberIds: string[], radius = 210): Record<string, { x: number; y: number }> {
  const placed: Record<string, { x: number; y: number }> = { [hubId]: { x: 0, y: 0 } }
  memberIds.forEach((id, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(memberIds.length, 1)
    placed[id] = {
      x: Math.round(Math.cos(angle) * radius),
      y: Math.round(Math.sin(angle) * radius),
    }
  })
  return placed
}

export function categoryLabel(id: string): string {
  return categoryOf(id).label
}

export function localExplore(graph: KnowledgeGraph, nodeId: string): ExploreState {
  const node = graph.nodes.find((item) => item.id === nodeId)
  const expanded = [nodeId]
  if (node?.data.type === "device") {
    const byId = new Map(graph.nodes.map((item) => [item.id, item]))
    for (const edge of graph.edges) {
      if (edge.data.relation !== "HAS_COMPONENT") {
        continue
      }
      const otherId = edge.source === nodeId ? edge.target : edge.target === nodeId ? edge.source : ""
      if (otherId && byId.get(otherId)?.data.type === "component") {
        expanded.push(otherId)
      }
    }
  }
  return { roots: [nodeId], expandedIds: [...new Set(expanded)] }
}

function briefFrom(node: ViewNode, children: ViewNode[], note: string): KnowledgeBrief {
  const points = children
    .filter((item) => item.kind === "knowledge" && (item.label || item.text))
    .slice(0, 8)
    .map((item) => ({ label: item.label, text: clip(item.text, 160) }))
  const parameters = [...node.parameters]
  for (const child of children) {
    for (const parameter of child.parameters) {
      if (parameters.length >= 8) {
        break
      }
      parameters.push(parameter)
    }
  }
  return { text: clip(node.text, 400), parameters, points, note }
}

export function buildDisplayModel(graph: KnowledgeGraph, view: KnowledgeView): DisplayModel {
  const membership = new Map<string, Set<string>>()
  const aliasIds = new Set<string>()
  for (const node of view.nodes) {
    const category = MODULE_CATEGORY[node.module]
    if (!category) {
      continue
    }
    for (const ref of node.alias_graph_refs) {
      aliasIds.add(ref.id)
    }
    if (node.module === "KB-06") {
      continue
    }
    for (const ref of node.graph_refs) {
      addCategory(membership, ref.id, category)
    }
  }

  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  const hiddenCases = new Set(
    graph.nodes
      .filter((node) => node.data.type === "case" && !node.data.label.startsWith("EXP-2024-"))
      .map((node) => node.id),
  )
  for (const id of aliasIds) {
    hiddenCases.add(id)
  }

  const visibleReal = graph.nodes.filter((node) => node.data.type !== "source" && !hiddenCases.has(node.id))
  const visibleIds = new Set(visibleReal.map((node) => node.id))
  const realEdges = graph.edges.filter(
    (edge) =>
      edge.data.relation !== "SUPPORTED_BY" &&
      edge.data.relation !== "UI_GROUP" &&
      visibleIds.has(edge.source) &&
      visibleIds.has(edge.target),
  )
  const meta = new Map<string, NodeMeta>()
  for (const node of visibleReal) {
    const categories = membership.get(node.id) ?? new Set<string>()
    meta.set(node.id, {
      categoryIds: [...categories].sort(),
      color: categoryOf(primaryCategory(node, categories)).color,
      important: false,
      headline: false,
      uiGroup: false,
      hub: false,
      region: "",
    })
  }

  const nodes: GraphNode[] = [...visibleReal]
  const edges = [...realEdges]
  const groups: GroupInfo[] = []
  const positions: Record<string, { x: number; y: number }> = {}
  const childMap = childrenOf(view)

  const addUi = (id: string, label: string, categoryId: string, hub: boolean) => {
    nodes.push({ id, data: { label, type: "ui-group", source_refs: [] } })
    meta.set(id, {
      categoryIds: [categoryId],
      color: categoryOf(categoryId).color,
      important: true,
      headline: true,
      uiGroup: true,
      hub,
      region: categoryId,
    })
  }
  const linkGroup = (source: string, target: string) => {
    edges.push({
      id: `ui-link:${source}:${target}`,
      source,
      target,
      data: { relation: "UI_GROUP", source_refs: [] },
    })
  }
  const markMember = (id: string, categoryId: string) => {
    const current = meta.get(id)
    if (!current) {
      return
    }
    current.important = true
    if (categoryId === "fault" || categoryId === "case" || categoryId === "relation" || categoryId === "procedure") {
      current.color = categoryOf(categoryId).color
    }
  }

  for (const category of CATEGORIES) {
    const hubId = `ui-group:${category.id}`
    addUi(hubId, category.label, category.id, true)
    const anchor = GROUP_ANCHOR[category.id] ?? { x: 0, y: 0 }
    positions[hubId] = anchor
    groups.push({ categoryId: category.id, hubId, memberIds: [] })
  }
  const groupOf = (categoryId: string) => groups.find((item) => item.categoryId === categoryId)

  const coreIds = CORE_DEVICES.flatMap((label) => {
    const found = visibleReal.find((node) => node.data.type === "device" && node.data.label === label)
    return found ? [found.id] : []
  })
  const faultIds = FAULT_MODES.flatMap((label) => {
    const named = visibleReal.filter(
      (node) => node.data.type === "fault" && node.data.label === label && membership.get(node.id)?.has("fault"),
    )
    const featured = named.filter((node) =>
      realEdges.some((edge) => edge.source === node.id && edge.data.relation === "HAS_FEATURE"),
    )
    const chosen = featured[0] ?? named[0]
    return chosen ? [chosen.id] : []
  })
  const caseIds = visibleReal
    .filter((node) => node.data.type === "case" && /^EXP-2024-00[1-7]/.test(node.data.label))
    .sort((a, b) => a.data.label.localeCompare(b.data.label))
    .map((node) => node.id)

  const equipment = groupOf("equipment")
  const fault = groupOf("fault")
  const cases = groupOf("case")
  if (equipment) {
    equipment.memberIds = coreIds
  }
  if (fault) {
    fault.memberIds = faultIds
  }
  if (cases) {
    cases.memberIds = caseIds
  }
  for (const id of coreIds) {
    linkGroup("ui-group:equipment", id)
    markMember(id, "equipment")
  }
  for (const id of faultIds) {
    linkGroup("ui-group:fault", id)
    markMember(id, "fault")
  }
  for (const id of caseIds) {
    linkGroup("ui-group:case", id)
    markMember(id, "case")
  }

  const procedures = view.nodes.filter((node) => node.module === "KB-03" && node.knowledge_type === "操作规程")
  const procedureGroup = groupOf("procedure")
  for (const item of procedures) {
    const id = `ui-procedure:${item.id}`
    addUi(id, item.label, "procedure", false)
    linkGroup("ui-group:procedure", id)
    procedureGroup?.memberIds.push(id)
    const linked = new Set<string>()
    for (const ref of item.graph_refs) {
      if (visibleIds.has(ref.id)) {
        linked.add(ref.id)
      }
    }
    for (const child of descendants(childMap, item.id)) {
      for (const ref of child.graph_refs) {
        if (ref.type === "parameter" && visibleIds.has(ref.id)) {
          linked.add(ref.id)
        }
      }
    }
    for (const target of linked) {
      linkGroup(id, target)
      const current = meta.get(target)
      if (current?.categoryIds.includes("procedure") || byId.get(target)?.data.type === "parameter") {
        current && (current.color = categoryOf("procedure").color)
      }
    }
  }

  const chains = view.nodes.filter((node) => node.module === "KB-05" && node.knowledge_type === "系统链路")
  const relationGroup = groupOf("relation")
  for (const chain of chains) {
    const id = `ui-chain:${chain.id}`
    addUi(id, chain.label, "relation", false)
    linkGroup("ui-group:relation", id)
    relationGroup?.memberIds.push(id)
    const linked = new Set<string>()
    for (const child of descendants(childMap, chain.id)) {
      for (const ref of child.graph_refs) {
        if (ref.type === "device" && visibleIds.has(ref.id) && byId.get(ref.id)?.data.type === "device") {
          linked.add(ref.id)
        }
      }
    }
    for (const target of linked) {
      linkGroup(id, target)
    }
  }

  const linked = (id: string, relation: string, type: string) => {
    const found: string[] = []
    for (const edge of realEdges) {
      if (edge.data.relation !== relation) {
        continue
      }
      const otherId = edge.source === id ? edge.target : edge.target === id ? edge.source : ""
      if (otherId && byId.get(otherId)?.data.type === type) {
        found.push(otherId)
      }
    }
    return found
  }
  const componentIds = [...new Set(coreIds.flatMap((id) => linked(id, "HAS_COMPONENT", "component")))]
  const featureIds = [...new Set(faultIds.flatMap((id) => linked(id, "HAS_FEATURE", "feature")))]
  const actionIds = [...new Set(caseIds.flatMap((id) => linked(id, "HAS_ACTION", "action")))]
  const limitIds = [...new Set(coreIds.flatMap((id) => linked(id, "HAS_PARAMETER", "parameter")))].filter(
    (id) => byId.get(id)?.data.kind === "operating_limit",
  )
  const relatedDeviceIds = [...new Set([...coreIds, ...caseIds].flatMap((id) => linked(id, "CONNECTS_TO", "device").concat(linked(id, "OCCURS_ON", "device"))))].filter(
    (id) => !coreIds.includes(id),
  )
  const shown = new Set<string>([...coreIds, ...faultIds, ...caseIds, ...componentIds, ...featureIds, ...actionIds, ...limitIds, ...relatedDeviceIds])
  const headlineIds = new Set<string>([...coreIds, ...faultIds, ...caseIds])
  for (const id of shown) {
    const current = meta.get(id)
    const node = byId.get(id)
    if (!current || !node) {
      continue
    }
    if (headlineIds.has(id)) {
      current.important = true
      current.headline = true
    }
    if (node.data.type === "feature" || faultIds.includes(id)) {
      current.color = categoryOf("fault").color
    } else if (node.data.type === "parameter" || node.data.type === "action" || caseIds.includes(id)) {
      current.color = node.data.type === "parameter" ? categoryOf("procedure").color : categoryOf("case").color
    } else if (relatedDeviceIds.includes(id)) {
      current.color = categoryOf("relation").color
    }
  }

  const buckets: Record<string, string[]> = {
    equipment: [...coreIds, ...componentIds],
    fault: [...faultIds, ...featureIds],
    procedure: [...(procedureGroup?.memberIds ?? []), ...limitIds],
    case: [...caseIds, ...actionIds],
    relation: [...(relationGroup?.memberIds ?? []), ...relatedDeviceIds],
  }
  for (const group of groups) {
    const anchor = positions[group.hubId] ?? { x: 0, y: 0 }
    positions[group.hubId] = anchor
    Object.assign(positions, seedCloud(anchor, buckets[group.categoryId] ?? []))
    group.memberIds = buckets[group.categoryId] ?? []
    for (const id of group.memberIds) {
      const current = meta.get(id)
      if (current && !current.region) {
        current.region = group.categoryId
      }
    }
  }

  const briefs = new Map<string, KnowledgeBrief>()
  for (const node of visibleReal) {
    const categories = meta.get(node.id)?.categoryIds ?? []
    const preferred = categories.map((id) => CATEGORY_MODULE[id]).filter((id): id is string => Boolean(id))
    const matches = view.nodes.filter(
      (item) =>
        item.module !== "KB-06" &&
        item.kind !== "reference" &&
        item.graph_refs.some((ref) => ref.id === node.id),
    )
    const chosen = matches.find((item) => preferred.includes(item.module)) ?? matches[0]
    if (!chosen) {
      continue
    }
    const note =
      node.data.type === "case" && node.data.label.startsWith("EXP-2024-")
        ? "检索库中的重复记载只用来对照来源，这里显示的是编号案例。"
        : ""
    briefs.set(node.id, briefFrom(chosen, childMap.get(chosen.id) ?? [], note))
  }
  for (const item of [...procedures, ...chains]) {
    const prefix = item.knowledge_type === "操作规程" ? "ui-procedure" : "ui-chain"
    briefs.set(`${prefix}:${item.id}`, briefFrom(item, childMap.get(item.id) ?? [], ""))
  }

  const roots = [...groups.map((group) => group.hubId), ...shown, ...(procedureGroup?.memberIds ?? []), ...(relationGroup?.memberIds ?? [])]
  const shownEdges = realEdges.filter((edge) => shown.has(edge.source) && shown.has(edge.target))
  return {
    graph: { nodes, edges },
    meta,
    overview: { roots: [...new Set(roots)], expandedIds: [] },
    groups,
    positions,
    briefs,
    overviewStats: { realNodes: shown.size, realEdges: shownEdges.length },
  }
}
