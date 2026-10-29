import type { GraphEdge, GraphNode, KnowledgeGraph } from "../types/graph"
import { typeLabel } from "../graph/presentation"

export interface NameMatch {
  id: string
  label: string
  type: string
  typeText: string
  scope: string
  affiliation: string
}

export function nameMatches(graph: KnowledgeGraph, label: string): NameMatch[] {
  const text = label.trim()
  if (!text) {
    return []
  }
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  return graph.nodes
    .filter((node) => node.data.label === text)
    .map((node) => {
      const parent = parentNode(node, byId, graph.edges)
      return {
        id: node.id,
        label: node.data.label,
        type: node.data.type,
        typeText: typeLabel(node.data.type),
        scope: scopeOf(node, byId, graph.edges),
        affiliation: parent?.data.label || "",
      }
    })
}

export function researchIdentity(matches: NameMatch[], choiceId: string): { id: string; scope: string } | null {
  if (matches.length === 0) {
    return null
  }
  const chosen = matches.length === 1 ? matches[0] : matches.find((item) => item.id === choiceId)
  if (!chosen) {
    return null
  }
  return { id: chosen.id, scope: chosen.affiliation }
}

export function choiceReady(matches: NameMatch[], choiceId: string): boolean {
  if (matches.length <= 1) {
    return true
  }
  return matches.some((item) => item.id === choiceId)
}

export function modelStateText(called: boolean, callCount: number, reason: string): string {
  if (!called) {
    return `DeepSeek未执行。${reason || "没有服务器外发授权。"}`
  }
  return `DeepSeek已调用 ${callCount} 次。模型返回的内容仍是未验证假设，不是故障确诊。`
}

export function checkStatusText(status: string): string {
  if (status === "consistent") {
    return "L2数值一致"
  }
  if (status === "contradicted") {
    return "L2数值矛盾"
  }
  if (status === "insufficient_data") {
    return "数据不足"
  }
  if (status === "not_applicable") {
    return "不适用"
  }
  return status || "未返回状态"
}

export function overallStatusText(status: string): string {
  if (status === "record_not_found") {
    return "没有找到这条观测记录"
  }
  if (status === "numeric_contradiction") {
    return "存在明确的数值矛盾"
  }
  if (status === "no_testable_hypothesis") {
    return "没有可检验的因果假设"
  }
  if (status === "partial_numeric_support") {
    return "仅部分数值条件得到支持"
  }
  if (status === "insufficient_data") {
    return "存在可关注的条件，但数据不足"
  }
  return status || "未返回校验状态"
}

function parentNode(node: GraphNode, byId: Map<string, GraphNode>, edges: GraphEdge[]): GraphNode | undefined {
  if (node.parentId && byId.has(node.parentId)) {
    return byId.get(node.parentId)
  }
  const parents = edges.filter((edge) => edge.target === node.id && edge.data.relation === "HAS_COMPONENT" && byId.has(edge.source))
  if (parents.length === 1) {
    return byId.get(parents[0].source)
  }
  return undefined
}

function scopeOf(node: GraphNode, byId: Map<string, GraphNode>, edges: GraphEdge[]): string {
  const names: string[] = []
  if (node.data.device_label) {
    names.push(node.data.device_label)
  }
  let current = parentNode(node, byId, edges)
  while (current && names.length < 3) {
    if (current.data.label && !names.includes(current.data.label)) {
      names.push(current.data.label)
    }
    current = parentNode(current, byId, edges)
  }
  return names.join(" / ") || "归属未标明"
}
