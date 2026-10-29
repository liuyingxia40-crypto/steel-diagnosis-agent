import config from "./graph_view_config.json"
import {
  placeLocal,
  placeOverview,
  type PlacementNode,
  type Point,
  type RegionSpec,
} from "./groupLayout"
import type { KnowledgeView, ViewNode } from "./knowledgeView"
import type { GraphEdge, GraphNode, KnowledgeGraph, SourceRef } from "../types/graph"

export const VIEW_CONFIG = config

export interface SceneNode {
  id: string
  data: GraphNode["data"] & { shortLabel: string }
  presetX?: number
  presetY?: number
  categoryColor: string
  visualSize: number
  headline: boolean
  hub: boolean
  uiGroup: boolean
  region: string
  role: "hub" | "core" | "member" | "minor"
  order: number
  hiddenCount: number
  hiddenLabels: string[]
  expandLevel: number
  summary: string
  parentId: string
}

export interface SceneModel {
  nodes: SceneNode[]
  edges: GraphEdge[]
  focusId: string | null
  title: string
}

const SPECS: Array<RegionSpec & { module: string }> = config.categories
const DISPLAY_NAMES = config.displayNames as Record<string, string>
const SIZES = config.sizes

function byId(graph: KnowledgeGraph): Map<string, GraphNode> {
  return new Map(graph.nodes.map((node) => [node.id, node]))
}

function childrenOf(view: KnowledgeView): Map<string, ViewNode[]> {
  const children = new Map<string, ViewNode[]>()
  const known = new Set(view.nodes.map((node) => node.id))
  for (const node of view.nodes) {
    if (!node.parent_id || !known.has(node.parent_id)) {
      continue
    }
    const list = children.get(node.parent_id) ?? []
    list.push(node)
    children.set(node.parent_id, list)
  }
  return children
}

function subtree(children: Map<string, ViewNode[]>, rootId: string): ViewNode[] {
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

export function shortLabelFor(label: string, type: string): string {
  const named = DISPLAY_NAMES[label]
  if (named) {
    return named
  }
  if (type === "case") {
    const matched = label.match(/^(EXP-\d{4}-\d+)/)
    if (matched?.[1]) {
      return matched[1]
    }
  }
  if (type === "feature" && label.includes("：")) {
    const tail = label.split("：").slice(1).join("：")
    const head = tail.split(/[（(，,。；;]/)[0]?.trim() ?? ""
    if (head) {
      return head.length <= 8 ? head : `${head.slice(0, 8)}…`
    }
  }
  return label.length <= 12 ? label : `${label.slice(0, 12)}…`
}

function isHiddenCase(node: GraphNode): boolean {
  return node.data.type === "case" && !node.data.label.startsWith("EXP-2024-")
}

function clip(text: string): string {
  const value = text.replace(/\s+/g, " ").trim()
  return value.length > 160 ? `${value.slice(0, 160)}…` : value
}

function summaryFor(view: KnowledgeView, graphId: string): string {
  const matches = view.nodes.filter(
    (node) => node.module !== "KB-06" && node.kind !== "reference" && node.graph_refs.some((ref) => ref.id === graphId),
  )
  const chosen = matches.find((node) => node.text.trim()) ?? matches[0]
  return chosen ? clip(chosen.text) : ""
}

function viewSource(node: ViewNode | undefined): SourceRef[] {
  if (!node?.source) {
    return []
  }
  const { document, section, locator } = node.source
  if (!document && !section && !locator) {
    return []
  }
  return [{ document, section, locator }]
}

function categoryOfNode(node: GraphNode, membership: ReadonlyMap<string, string>): string {
  if (node.data.category && SPECS.some((item) => item.id === node.data.category)) {
    return node.data.category
  }
  return membership.get(node.id) ?? fallbackCategory(node.data.type)
}

function fallbackCategory(type: string): string {
  if (type === "fault" || type === "feature") {
    return "fault"
  }
  if (type === "parameter") {
    return "procedure"
  }
  if (type === "case" || type === "action") {
    return "case"
  }
  if (type === "device" || type === "component") {
    return "equipment"
  }
  return "relation"
}

function colorOf(categoryId: string): string {
  return SPECS.find((item) => item.id === categoryId)?.color ?? "#9aa8b8"
}

function sizeOf(role: SceneNode["role"]): number {
  if (role === "hub") {
    return SIZES.hub
  }
  if (role === "core") {
    return SIZES.core
  }
  if (role === "member") {
    return SIZES.member
  }
  return SIZES.minor
}

interface Link {
  otherId: string
  relation: string
  edge: GraphEdge
}

function realEdges(graph: KnowledgeGraph): GraphEdge[] {
  return graph.edges.filter((edge) => edge.data.relation !== "SUPPORTED_BY" && edge.data.relation !== "UI_GROUP")
}

function primaryRelation(type: string, relation: string): boolean {
  if (type === "device") {
    return relation === "HAS_COMPONENT"
  }
  if (type === "fault") {
    return relation === "HAS_FEATURE" || relation === "HAS_FAULT"
  }
  if (type === "component") {
    return relation === "HAS_COMPONENT" || relation === "HAS_FAULT"
  }
  if (type === "case") {
    return relation === "OCCURS_ON" || relation === "HAS_ACTION"
  }
  if (type === "feature") {
    return relation === "HAS_FEATURE"
  }
  if (type === "parameter") {
    return relation === "HAS_PARAMETER"
  }
  if (type === "action") {
    return relation === "HAS_ACTION"
  }
  return relation !== "SUPPORTED_BY"
}

function tierOf(type: string, relation: string): number {
  if (primaryRelation(type, relation)) {
    return 1
  }
  if (relation === "HAS_PARAMETER") {
    return 3
  }
  return 2
}

function linksFrom(graph: KnowledgeGraph, id: string, level: number): Link[] {
  const node = byId(graph).get(id)
  if (!node || level < 1) {
    return []
  }
  const links: Link[] = []
  for (const edge of realEdges(graph)) {
    const otherId = edge.source === id ? edge.target : edge.target === id ? edge.source : ""
    if (!otherId) {
      continue
    }
    const other = byId(graph).get(otherId)
    if (!other || other.data.type === "source" || isHiddenCase(other)) {
      continue
    }
    if (tierOf(node.data.type, edge.data.relation) > level) {
      continue
    }
    links.push({ otherId, relation: edge.data.relation, edge })
  }
  return links
}

function findView(view: KnowledgeView, id: string): ViewNode | undefined {
  return view.nodes.find((node) => node.id === id)
}

function organizedTargets(graph: KnowledgeGraph, view: KnowledgeView, uiId: string): string[] {
  const nodes = byId(graph)
  const children = childrenOf(view)
  if (uiId.startsWith("ui-group:")) {
    const category = uiId.slice("ui-group:".length)
    return buildOverview(graph, view)
      .nodes.filter((node) => node.region === category && node.id !== uiId)
      .map((node) => node.id)
  }
  if (uiId.startsWith("ui-procedure:")) {
    const viewNode = findView(view, uiId.slice("ui-procedure:".length))
    if (!viewNode) {
      return []
    }
    const refs = [viewNode, ...subtree(children, viewNode.id)].flatMap((item) => item.graph_refs)
    const parameters = refs.map((ref) => ref.id).filter((id) => nodes.get(id)?.data.type === "parameter")
    const devices = new Set<string>()
    for (const edge of realEdges(graph)) {
      if (edge.data.relation !== "HAS_PARAMETER") {
        continue
      }
      const parameterId = parameters.find((id) => id === edge.source || id === edge.target)
      if (!parameterId) {
        continue
      }
      const otherId = edge.source === parameterId ? edge.target : edge.source
      if (nodes.get(otherId)?.data.type === "device") {
        devices.add(otherId)
      }
    }
    return [...new Set([...parameters, ...devices])]
  }
  if (uiId.startsWith("ui-chain:")) {
    const viewNode = findView(view, uiId.slice("ui-chain:".length))
    if (!viewNode) {
      return []
    }
    const refs = [viewNode, ...subtree(children, viewNode.id)].flatMap((item) => item.graph_refs)
    return [...new Set(refs.map((ref) => ref.id).filter((id) => nodes.get(id)?.data.type === "device"))]
  }
  return []
}

function pickFault(graph: KnowledgeGraph, view: KnowledgeView, label: string): GraphNode | undefined {
  const nodes = byId(graph)
  const referenced = view.nodes
    .filter((node) => node.module === "KB-02" && node.label === label)
    .flatMap((node) => node.graph_refs)
    .map((ref) => nodes.get(ref.id))
    .filter((node): node is GraphNode => node?.data.type === "fault")
  const named = graph.nodes.filter((node) => node.data.type === "fault" && node.data.label === label)
  const pool = referenced.length > 0 ? referenced : named
  const featured = pool.filter((node) =>
    graph.edges.some(
      (edge) =>
        edge.data.relation === "HAS_FEATURE" &&
        (edge.source === node.id || edge.target === node.id),
    ),
  )
  return featured[0] ?? pool[0]
}

const DEVICE_LEAVES: Record<string, readonly string[]> = {
  棒线主轧机电机: ["定子", "驱动端轴承"],
  "棒线主减速机/齿轮箱": ["高速级齿轮副", "润滑系统"],
  棒线冷却水循环泵: ["叶轮", "机封"],
}

const FAULT_LEAVES: Record<string, readonly string[]> = {
  轴承磨损: ["envelope_spectrum", "kurtosis"],
  转子不平衡: ["dominant_frequency", "amplitude"],
  齿轮异常: ["gear_mesh_freq", "sidebands"],
  润滑故障: ["noise", "broadband"],
}

const FEATURE_SHORT: Record<string, string> = {
  envelope_spectrum: "包络谱",
  kurtosis: "峭度",
  dominant_frequency: "1X转频",
  amplitude: "径向振动",
  gear_mesh_freq: "啮合频率",
  sidebands: "边带",
  noise: "摩擦异音",
  broadband: "宽频噪声",
}

const PROCEDURE_PARAMETERS: Record<string, readonly string[]> = {
  棒线主轧机电机操作规程: ["额定电压", "额定电流"],
  棒线冷却水循环泵操作规程: ["额定流量", "额定转速"],
}

const PROCEDURE_NOTES: Record<string, readonly string[]> = {
  棒线齿轮箱操作规程: ["油温", "油压"],
}

const CHAIN_DEVICES: Record<string, readonly string[]> = {
  棒材主传动链: ["主电机", "主减速机"],
  棒材冷却水回路: ["冷却水循环泵", "净循环水池"],
  棒材稀油润滑系统: ["油箱", "油泵"],
}

function neighborByLabel(graph: KnowledgeGraph, id: string, relation: string, label: string, coded = false): GraphNode | undefined {
  const nodes = byId(graph)
  for (const edge of realEdges(graph)) {
    if (edge.data.relation !== relation) {
      continue
    }
    const otherId = edge.source === id ? edge.target : edge.target === id ? edge.source : ""
    const other = otherId ? nodes.get(otherId) : undefined
    if (!other) {
      continue
    }
    const name = other.data.label
    if (coded ? name.startsWith(`${label}：`) || name.startsWith(`${label}:`) : name === label) {
      return other
    }
  }
  return undefined
}

function attachLeaf(nodes: SceneNode[], edges: GraphEdge[], shown: Set<string>, parentId: string, child: GraphNode, view: KnowledgeView, membership: Map<string, string>, region: string, order: number, shortName: string, grouped: boolean) {
  if (shown.has(child.id)) {
    return
  }
  shown.add(child.id)
  const created = makeRealNode(child, view, membership, "minor", region, order, 0)
  created.parentId = parentId
  created.headline = true
  created.data = { ...created.data, shortLabel: shortName }
  nodes.push(created)
  if (grouped) {
    edges.push(uiEdge(parentId, child.id))
  }
}

function addSelectedLeaves(graph: KnowledgeGraph, view: KnowledgeView, nodes: SceneNode[], edges: GraphEdge[], shown: Set<string>) {
  const membership = membershipOf(view)
  const children = childrenOf(view)
  let order = 300
  for (const [deviceLabel, names] of Object.entries(DEVICE_LEAVES)) {
    const device = nodes.find((node) => node.data.label === deviceLabel)
    const source = graph.nodes.find((node) => node.data.type === "device" && node.data.label === deviceLabel)
    if (!device || !source) {
      continue
    }
    for (const name of names) {
      const child = neighborByLabel(graph, source.id, "HAS_COMPONENT", name)
      if (child) {
        attachLeaf(nodes, edges, shown, device.id, child, view, membership, device.region, order, name, false)
        order += 1
      }
    }
  }
  for (const [faultLabel, codes] of Object.entries(FAULT_LEAVES)) {
    const fault = nodes.find((node) => node.data.type === "fault" && node.data.label === faultLabel)
    if (!fault) {
      continue
    }
    for (const code of codes) {
      const child = neighborByLabel(graph, fault.id, "HAS_FEATURE", code, true)
      const shortName = FEATURE_SHORT[code] ?? code
      if (child) {
        attachLeaf(nodes, edges, shown, fault.id, child, view, membership, fault.region, order, shortName, false)
        order += 1
      }
    }
  }
  for (const procedure of nodes.filter((node) => node.id.startsWith("ui-procedure:"))) {
    const viewId = procedure.id.slice("ui-procedure:".length)
    const viewNode = findView(view, viewId)
    if (!viewNode) {
      continue
    }
    const deviceRef = [viewNode, ...subtree(children, viewId)].flatMap((item) => item.graph_refs).map((ref) => byId(graph).get(ref.id)).find((node) => node?.data.type === "device")
    for (const name of PROCEDURE_PARAMETERS[viewNode.label] ?? []) {
      const parameter = deviceRef ? neighborByLabel(graph, deviceRef.id, "HAS_PARAMETER", name) : undefined
      if (parameter) {
        attachLeaf(nodes, edges, shown, procedure.id, parameter, view, membership, procedure.region, order, name, true)
        order += 1
      }
    }
    for (const name of PROCEDURE_NOTES[viewNode.label] ?? []) {
      const note = subtree(children, viewId).find((item) => item.label === name)
      if (!note) {
        continue
      }
      const id = `ui-note:${note.id}`
      const created = makeUiNode(id, note.label, "procedure", "minor", order, viewSource(note), clip(note.text))
      created.data = { ...created.data, type: "ui-note", shortLabel: note.label }
      created.uiGroup = false
      created.headline = true
      created.parentId = procedure.id
      nodes.push(created)
      edges.push(uiEdge(procedure.id, id))
      order += 1
    }
  }
  for (const chain of nodes.filter((node) => node.id.startsWith("ui-chain:"))) {
    const viewId = chain.id.slice("ui-chain:".length)
    const viewNode = findView(view, viewId)
    if (!viewNode) {
      continue
    }
    const names = CHAIN_DEVICES[viewNode.label] ?? []
    const devices = subtree(children, viewId)
      .flatMap((item) => item.graph_refs)
      .map((ref) => byId(graph).get(ref.id))
      .filter((node): node is GraphNode => node?.data.type === "device")
    for (const name of names) {
      const device = devices.find((node) => node.data.label === name)
      if (device) {
        attachLeaf(nodes, edges, shown, chain.id, device, view, membership, chain.region, order, shortLabelFor(device.data.label, device.data.type), true)
        order += 1
      }
    }
  }
}

function membershipOf(view: KnowledgeView): Map<string, string> {
  const membership = new Map<string, string>()
  for (const spec of SPECS) {
    for (const node of view.nodes) {
      if (node.module !== spec.module || node.module === "KB-06") {
        continue
      }
      for (const ref of node.graph_refs) {
        if (!membership.has(ref.id)) {
          membership.set(ref.id, spec.id)
        }
      }
    }
  }
  return membership
}

function makeRealNode(
  node: GraphNode,
  view: KnowledgeView,
  membership: ReadonlyMap<string, string>,
  role: SceneNode["role"],
  region: string,
  order: number,
  hiddenCount: number,
): SceneNode {
  return {
    id: node.id,
    data: {
      ...node.data,
      source_refs: node.data.source_refs.length > 0 ? node.data.source_refs : [],
      shortLabel: shortLabelFor(node.data.label, node.data.type),
    },
    categoryColor: colorOf(categoryOfNode(node, membership)),
    visualSize: sizeOf(role),
    headline: role !== "minor",
    hub: false,
    uiGroup: false,
    region,
    role,
    order,
    hiddenCount,
    hiddenLabels: [],
    expandLevel: 0,
    summary: summaryFor(view, node.id),
    parentId: "",
  }
}

function makeUiNode(
  id: string,
  label: string,
  categoryId: string,
  role: SceneNode["role"],
  order: number,
  source: SourceRef[],
  summary: string,
): SceneNode {
  return {
    id,
    data: { label, type: "ui-group", source_refs: source, shortLabel: shortLabelFor(label, "ui-group") },
    categoryColor: colorOf(categoryId),
    visualSize: sizeOf(role),
    headline: true,
    hub: role === "hub",
    uiGroup: true,
    region: categoryId,
    role,
    order,
    hiddenCount: 0,
    hiddenLabels: [],
    expandLevel: 0,
    summary,
    parentId: "",
  }
}

function uiEdge(source: string, target: string): GraphEdge {
  return { id: `ui:${source}:${target}`, source, target, data: { relation: "UI_GROUP", source_refs: [] } }
}

function hiddenCaseIds(graph: KnowledgeGraph): Set<string> {
  return new Set(graph.nodes.filter((node) => isHiddenCase(node)).map((node) => node.id))
}

export function buildOverview(graph: KnowledgeGraph, view: KnowledgeView): SceneModel {
  const membership = membershipOf(view)
  const hidden = hiddenCaseIds(graph)
  const sceneNodes: SceneNode[] = []
  const sceneEdges: GraphEdge[] = []
  const shown = new Set<string>()

  const addReal = (node: GraphNode, role: SceneNode["role"], region: string, order: number, parentId = ""): SceneNode | undefined => {
    if (hidden.has(node.id)) {
      return undefined
    }
    const existing = sceneNodes.find((item) => item.id === node.id)
    if (existing) {
      return existing
    }
    shown.add(node.id)
    const created = makeRealNode(node, view, membership, role, region, order, 0)
    created.parentId = parentId
    sceneNodes.push(created)
    return created
  }

  SPECS.forEach((spec, index) => {
    sceneNodes.push(makeUiNode(`ui-group:${spec.id}`, spec.label, spec.id, "hub", index * 100, [], spec.label))
  })

  config.coreDevices.forEach((label, index) => {
    const node = graph.nodes.find((item) => item.data.type === "device" && item.data.label === label)
    if (!node) {
      return
    }
    addReal(node, "core", "equipment", index + 1, "ui-group:equipment")
    sceneEdges.push(uiEdge("ui-group:equipment", node.id))
  })
  config.faultModes.forEach((label, index) => {
    const node = pickFault(graph, view, label)
    if (!node) {
      return
    }
    addReal(node, "core", "fault", index + 1, "ui-group:fault")
    sceneEdges.push(uiEdge("ui-group:fault", node.id))
  })
  graph.nodes
    .filter((node) => node.data.type === "case" && /^EXP-2024-00[1-7]/.test(node.data.label))
    .sort((a, b) => a.data.label.localeCompare(b.data.label))
    .forEach((node, index) => {
      addReal(node, "member", "case", index + 1, "ui-group:case")
      sceneEdges.push(uiEdge("ui-group:case", node.id))
    })

  view.nodes
    .filter((node) => node.module === "KB-03" && node.knowledge_type === "操作规程")
    .forEach((node, index) => {
      const id = `ui-procedure:${node.id}`
      const procedure = makeUiNode(id, node.label, "procedure", "member", index + 1, viewSource(node), clip(node.text))
      procedure.parentId = "ui-group:procedure"
      sceneNodes.push(procedure)
      sceneEdges.push(uiEdge("ui-group:procedure", id))
    })
  view.nodes
    .filter((node) => node.module === "KB-05" && node.knowledge_type === "系统链路")
    .forEach((node, index) => {
      const id = `ui-chain:${node.id}`
      const chain = makeUiNode(id, node.label, "relation", "member", index + 1, viewSource(node), clip(node.text))
      chain.parentId = "ui-group:relation"
      sceneNodes.push(chain)
      sceneEdges.push(uiEdge("ui-group:relation", id))
    })

  addSelectedLeaves(graph, view, sceneNodes, sceneEdges, shown)

  for (const edge of realEdges(graph)) {
    if (shown.has(edge.source) && shown.has(edge.target)) {
      sceneEdges.push(edge)
    }
  }

  return { nodes: applyExpandHints(graph, view, sceneNodes, {}), edges: sceneEdges, focusId: null, title: "五大知识网络" }
}

function visualDepth(node: SceneNode, index: ReadonlyMap<string, SceneNode>): number {
  let depth = 1
  let cursor = node
  const seen = new Set<string>()
  while (cursor.parentId && !seen.has(cursor.id)) {
    seen.add(cursor.id)
    const parent = index.get(cursor.parentId)
    if (!parent) {
      break
    }
    depth += 1
    cursor = parent
  }
  return depth
}

/** 下一次双击真正会新出现的节点。已经在画面上的关系不算。 */
function pendingLabels(graph: KnowledgeGraph, view: KnowledgeView, id: string, level: number, visible: ReadonlySet<string>): string[] {
  if (level >= 3 || id.startsWith("ui-group:")) {
    return []
  }
  const nodes = byId(graph)
  if (id.startsWith("ui-")) {
    if (level >= 1) {
      return []
    }
    const labels: string[] = []
    const seen = new Set<string>()
    for (const target of organizedTargets(graph, view, id)) {
      if (visible.has(target) || seen.has(target)) {
        continue
      }
      const real = nodes.get(target)
      if (!real || real.data.type === "source" || isHiddenCase(real)) {
        continue
      }
      seen.add(target)
      labels.push(real.data.label)
    }
    return labels
  }
  for (let next = level + 1; next <= 3; next += 1) {
    const labels: string[] = []
    const seen = new Set<string>()
    for (const link of linksFrom(graph, id, next)) {
      if (visible.has(link.otherId) || seen.has(link.otherId)) {
        continue
      }
      const real = nodes.get(link.otherId)
      if (!real || isHiddenCase(real)) {
        continue
      }
      seen.add(link.otherId)
      labels.push(real.data.label)
    }
    if (labels.length > 0) {
      return labels
    }
  }
  return []
}

function applyExpandHints(
  graph: KnowledgeGraph,
  view: KnowledgeView,
  nodes: readonly SceneNode[],
  levels: Readonly<Record<string, number>>,
): SceneNode[] {
  const visible = new Set(nodes.map((node) => node.id))
  const index = new Map(nodes.map((node) => [node.id, node]))
  return nodes.map((node) => {
    const labels = pendingLabels(graph, view, node.id, levels[node.id] ?? 0, visible)
    return {
      ...node,
      hiddenCount: labels.length,
      hiddenLabels: labels,
      expandLevel: labels.length > 0 ? visualDepth(node, index) + 1 : 0,
    }
  })
}

export function growScene(
  graph: KnowledgeGraph,
  view: KnowledgeView,
  levels: Readonly<Record<string, number>>,
): SceneModel {
  const base = buildOverview(graph, view)
  if (Object.keys(levels).length === 0) {
    return base
  }
  const membership = membershipOf(view)
  const nodes = byId(graph)
  const sceneNodes = base.nodes.map((node) => ({ ...node }))
  const sceneEdges = [...base.edges]
  const visible = new Set(sceneNodes.map((node) => node.id))
  const regionOf = new Map(sceneNodes.map((node) => [node.id, node.region]))
  const edgeIds = new Set(sceneEdges.map((edge) => edge.id))
  const addEdge = (edge: GraphEdge) => {
    if (edgeIds.has(edge.id)) {
      return
    }
    edgeIds.add(edge.id)
    sceneEdges.push(edge)
  }
  for (const [id, level] of Object.entries(levels)) {
    if (level < 1 || !visible.has(id)) {
      continue
    }
    const parentRegion = regionOf.get(id) ?? "equipment"
    if (id.startsWith("ui-")) {
      for (const target of organizedTargets(graph, view, id)) {
        const real = nodes.get(target)
        if (!real || real.data.type === "source" || isHiddenCase(real) || visible.has(target)) {
          continue
        }
        visible.add(target)
        regionOf.set(target, parentRegion)
        const created = makeRealNode(real, view, membership, "minor", parentRegion, sceneNodes.length + 1, 0)
        created.parentId = id
        created.headline = created.data.shortLabel.length <= 10
        sceneNodes.push(created)
        addEdge(uiEdge(id, target))
      }
      continue
    }
    for (const link of linksFrom(graph, id, level)) {
      const real = nodes.get(link.otherId)
      if (!real || isHiddenCase(real)) {
        continue
      }
      if (!visible.has(link.otherId)) {
        visible.add(link.otherId)
        regionOf.set(link.otherId, parentRegion)
        const created = makeRealNode(real, view, membership, "minor", parentRegion, sceneNodes.length + 1, 0)
        created.parentId = real.parentId || id
        created.headline = created.data.shortLabel.length <= 10
        sceneNodes.push(created)
      }
      addEdge(link.edge)
    }
  }
  for (const edge of realEdges(graph)) {
    if (visible.has(edge.source) && visible.has(edge.target)) {
      const source = nodes.get(edge.source)
      const target = nodes.get(edge.target)
      if ((source && isHiddenCase(source)) || (target && isHiddenCase(target))) {
        continue
      }
      addEdge(edge)
    }
  }
  return { nodes: applyExpandHints(graph, view, sceneNodes, levels), edges: sceneEdges, focusId: null, title: "五大知识网络" }
}

export function openFurther(
  graph: KnowledgeGraph,
  view: KnowledgeView,
  levels: Readonly<Record<string, number>>,
  id: string,
): Record<string, number> | null {
  const current = levels[id] ?? 0
  if (current >= 3) {
    return null
  }
  const before = growScene(graph, view, levels).nodes.length
  for (let next = current + 1; next <= 3; next += 1) {
    const candidate = { ...levels, [id]: next }
    if (growScene(graph, view, candidate).nodes.length > before) {
      return candidate
    }
  }
  return null
}

export function buildLocal(
  graph: KnowledgeGraph,
  view: KnowledgeView,
  rootId: string,
  levels: Readonly<Record<string, number>>,
  width: number,
  height: number,
  kept: ReadonlyMap<string, Point>,
): SceneModel {
  const nodes = byId(graph)
  const membership = membershipOf(view)
  const visible = new Set<string>([rootId])
  const edges: GraphEdge[] = []
  const edgeIds = new Set<string>()
  const addEdge = (edge: GraphEdge) => {
    if (edgeIds.has(edge.id)) {
      return
    }
    edgeIds.add(edge.id)
    edges.push(edge)
  }
  for (const [id, level] of Object.entries(levels)) {
    if (level < 1) {
      continue
    }
    if (id.startsWith("ui-")) {
      for (const target of organizedTargets(graph, view, id)) {
        visible.add(target)
        addEdge(uiEdge(id, target))
      }
      continue
    }
    for (const link of linksFrom(graph, id, level)) {
      visible.add(link.otherId)
      addEdge(link.edge)
    }
  }
  for (const edge of realEdges(graph)) {
    if (visible.has(edge.source) && visible.has(edge.target) && !isHiddenCase(nodes.get(edge.source) ?? edgeAsNode(edge.source)) && !isHiddenCase(nodes.get(edge.target) ?? edgeAsNode(edge.target))) {
      addEdge(edge)
    }
  }
  const root = nodes.get(rootId)
  const rootLabel = root?.data.label ?? labelOfUi(view, rootId)
  const sceneNodes: SceneNode[] = []
  let order = 0
  for (const id of visible) {
    const real = nodes.get(id)
    if (real) {
      const role = id === rootId ? "core" : real.data.type === "device" || real.data.type === "fault" ? "core" : "minor"
      sceneNodes.push(makeRealNode(real, view, membership, role, "local", order, 0))
    } else if (id.startsWith("ui-")) {
      const label = labelOfUi(view, id)
      const category = id.includes("procedure") ? "procedure" : id.includes("chain") || id.includes("relation") ? "relation" : "equipment"
      const viewNode = findView(view, id.split(":").slice(1).join(":"))
      sceneNodes.push(makeUiNode(id, label, category, id === rootId ? "hub" : "member", order, viewSource(viewNode), viewNode ? clip(viewNode.text) : ""))
      const created = sceneNodes[sceneNodes.length - 1]
      if (created) {
        created.region = "local"
      }
    }
    order += 1
  }
  const focus = sceneNodes.find((node) => node.id === rootId) ?? sceneNodes[0]
  const neighbors = sceneNodes.filter((node) => node.id !== focus?.id)
  const placed = focus
    ? placeLocal(
        width,
        height,
        toPlacement(focus),
        neighbors.map((node) => toPlacement(node)),
      )
    : new Map<string, Point>()
  const showNames = sceneNodes.length <= 18
  const withPlace = sceneNodes.map((node) => {
    const keptPoint = kept.get(node.id)
    const fresh = placed.get(node.id)
    const point = keptPoint ?? fresh
    return {
      ...node,
      region: "local",
      headline: showNames || node.id === rootId,
      presetX: point?.x,
      presetY: point?.y,
    }
  })
  return {
    nodes: applyExpandHints(graph, view, withPlace, levels),
    edges,
    focusId: rootId,
    title: `局部关系：${rootLabel}`,
  }
}

function edgeAsNode(id: string): GraphNode {
  return { id, data: { label: id, type: "unknown", source_refs: [] } }
}

function labelOfUi(view: KnowledgeView, id: string): string {
  if (id.startsWith("ui-group:")) {
    const category = id.slice("ui-group:".length)
    return SPECS.find((item) => item.id === category)?.label ?? category
  }
  const viewId = id.split(":").slice(1).join(":")
  return findView(view, viewId)?.label ?? id
}

function toPlacement(node: SceneNode): PlacementNode {
  return {
    id: node.id,
    region: node.region,
    hub: node.hub,
    label: node.data.shortLabel,
    size: node.visualSize,
    order: node.order,
    parentId: node.parentId,
  }
}

export function overviewPlacement(width: number, height: number, nodes: readonly SceneNode[]): Map<string, Point> {
  return placeOverview(
    width,
    height,
    SPECS,
    nodes.map((node) => toPlacement(node)),
  )
}

export function deepen(graph: KnowledgeGraph, view: KnowledgeView, levels: Readonly<Record<string, number>>, id: string): Record<string, number> | null {
  const current = levels[id] ?? 0
  if (current >= 3) {
    return null
  }
  const rootId = Object.keys(levels)[0] ?? id
  const before = buildLocal(graph, view, rootId, levels, 800, 600, new Map()).nodes.length
  for (let next = current + 1; next <= 3; next += 1) {
    const candidate = { ...levels, [id]: next }
    const after = buildLocal(graph, view, rootId, candidate, 800, 600, new Map()).nodes.length
    if (after > before) {
      return candidate
    }
  }
  return null
}

export function findGraphNode(graph: KnowledgeGraph, query: string): GraphNode | undefined {
  const text = query.trim().toLowerCase()
  if (!text) {
    return undefined
  }
  const matches = graph.nodes.filter((node) => node.data.type !== "source" && !isHiddenCase(node) && node.data.label.toLowerCase().includes(text))
  return matches[0]
}
