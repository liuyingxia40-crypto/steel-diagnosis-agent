export interface ViewSource {
  document: string
  section: string
  locator: string
}

export interface GraphRef {
  id: string
  type: string
  label: string
}

export interface ViewParameter {
  name: string
  value: string
}

export type ViewKind = "nav" | "knowledge" | "reference"

export interface ViewNode {
  id: string
  parent_id: string | null
  kind: ViewKind
  module: string
  label: string
  depth: number
  knowledge_type: string
  text: string
  parameters: ViewParameter[]
  source: ViewSource | null
  graph_refs: GraphRef[]
  alias_graph_refs: GraphRef[]
  canonical_id: string | null
  note: string
}

export interface ViewGap {
  module: string
  item: string
  reason: string
}

export interface KnowledgeView {
  root_id: string
  nodes: ViewNode[]
  gaps: ViewGap[]
}

export const MODULE_TITLE: Record<string, string> = {
  "": "知识库总入口",
  "KB-01": "设备知识 KB-01",
  "KB-02": "故障模式 KB-02",
  "KB-03": "操作规程 KB-03",
  "KB-04": "专家经验与典型案例 KB-04",
  "KB-05": "设备关联关系 KB-05",
  "KB-06": "RAG检索知识 KB-06",
}

export const KIND_LABEL: Record<ViewKind, string> = {
  nav: "目录导航",
  knowledge: "正式知识",
  reference: "检索引用",
}

const GRAPH_REF_ORDER = ["device", "fault", "case", "component", "action", "feature", "parameter"]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function readText(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function readSource(value: unknown): ViewSource | null {
  if (!isRecord(value)) {
    return null
  }
  const document = readText(value.document)
  const section = readText(value.section)
  const locator = readText(value.locator)
  if (!document && !section && !locator) {
    return null
  }
  return { document, section, locator }
}

function readRefs(value: unknown): GraphRef[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    if (!isRecord(item) || typeof item.id !== "string" || !item.id) {
      return []
    }
    return [
      {
        id: item.id,
        type: readText(item.type),
        label: readText(item.label) || item.id,
      },
    ]
  })
}

function readParameters(value: unknown): ViewParameter[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    if (!isRecord(item)) {
      return []
    }
    const name = readText(item.name)
    const parameterValue = readText(item.value)
    if (!name && !parameterValue) {
      return []
    }
    return [{ name, value: parameterValue }]
  })
}

function readNode(value: unknown): ViewNode | null {
  if (!isRecord(value) || typeof value.id !== "string" || !value.id) {
    return null
  }
  const kind = value.kind
  if (kind !== "nav" && kind !== "knowledge" && kind !== "reference") {
    return null
  }
  const parent = value.parent_id
  return {
    id: value.id,
    parent_id: typeof parent === "string" ? parent : null,
    kind,
    module: readText(value.module),
    label: readText(value.label) || value.id,
    depth: typeof value.depth === "number" ? value.depth : 0,
    knowledge_type: readText(value.knowledge_type) || "知识",
    text: readText(value.text),
    parameters: readParameters(value.parameters),
    source: readSource(value.source),
    graph_refs: readRefs(value.graph_refs),
    alias_graph_refs: readRefs(value.alias_graph_refs),
    canonical_id: typeof value.canonical_id === "string" ? value.canonical_id : null,
    note: readText(value.note),
  }
}

export function parseKnowledgeView(value: unknown): KnowledgeView {
  if (!isRecord(value) || typeof value.root_id !== "string" || !Array.isArray(value.nodes)) {
    throw new Error("知识展示数据缺少 root_id 或 nodes")
  }
  const nodes = value.nodes.flatMap((item) => {
    const node = readNode(item)
    return node ? [node] : []
  })
  if (!nodes.some((node) => node.id === value.root_id)) {
    throw new Error("知识展示数据缺少总入口")
  }
  const gaps = Array.isArray(value.gaps)
    ? value.gaps.flatMap((item) => {
        if (!isRecord(item)) {
          return []
        }
        return [
          {
            module: readText(item.module),
            item: readText(item.item),
            reason: readText(item.reason),
          },
        ]
      })
    : []
  return { root_id: value.root_id, nodes, gaps }
}

export function preferredGraphRef(node: ViewNode): GraphRef | null {
  for (const type of GRAPH_REF_ORDER) {
    const found = node.graph_refs.find((item) => item.type === type)
    if (found) {
      return found
    }
  }
  return node.graph_refs[0] ?? null
}
