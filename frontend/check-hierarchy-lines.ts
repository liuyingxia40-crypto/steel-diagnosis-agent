import { readFileSync } from "node:fs"
import { hierarchyLevel, lineDashOf, lineForEdge } from "./src/graph/hierarchyLine.ts"
import { parseKnowledgeView } from "./src/graph/knowledgeView.ts"
import { buildOverview, growScene, openFurther } from "./src/graph/networkScene.ts"
import { parseKnowledgeGraph } from "./src/graph/presentation.ts"

const checks: boolean[] = []

function check(name: string, ok: boolean, detail = ""): void {
  checks.push(ok)
  console.log(ok ? "PASS" : "FAIL", name, detail)
}

const chain = [
  { id: "root", parentId: "" },
  { id: "l1", parentId: "root" },
  { id: "l2", parentId: "l1" },
  { id: "l3", parentId: "l2" },
  { id: "l4", parentId: "l3" },
  { id: "l5", parentId: "l4" },
  { id: "side", parentId: "root" },
]

check("根节点层级是 0", hierarchyLevel("root", chain) === 0)
check("一级到四级层级正确", [1, 2, 3, 4].every((level, index) => hierarchyLevel(`l${level}`, chain) === index + 1))
check("根到一级是实线", lineForEdge("root", "l1", chain).kind === "solid" && lineDashOf("solid").length === 0)
check("一级到二级是实线", lineForEdge("l1", "l2", chain).kind === "solid")
check("二级到三级是虚线", lineForEdge("l2", "l3", chain).kind === "dashed" && lineDashOf("dashed").join() === "5,4")
check("三级到四级是虚线", lineForEdge("l3", "l4", chain).kind === "dashed")
check("四级以下是虚线", lineForEdge("l4", "l5", chain).kind === "dashed" && lineForEdge("l4", "l5", chain).childLevel === 5)
check("同级关联不是父子线", lineForEdge("l1", "side", chain).kind === "association")
check("反向边仍按父子判断", lineForEdge("l3", "l2", chain).kind === "dashed")

const root = "D:/工作/steel-diagnosis-agent"
const graphText = readFileSync(`${root}/data/processed/graph/graph.json`, "utf8")
const viewText = readFileSync(`${root}/data/processed/graph/knowledge_view.json`, "utf8")
const graph = parseKnowledgeGraph(JSON.parse(graphText))
const view = parseKnowledgeView(JSON.parse(viewText))
check("图谱仍是 252 个节点和 464 条关系", graph.nodes.length === 252 && graph.edges.length === 464, `${graph.nodes.length}/${graph.edges.length}`)

const overview = buildOverview(graph, view)
const parentEdges = overview.edges.filter((edge) => lineForEdge(edge.source, edge.target, overview.nodes).kind !== "association")
const level1 = parentEdges.filter((edge) => lineForEdge(edge.source, edge.target, overview.nodes).childLevel === 1)
const level2 = parentEdges.filter((edge) => lineForEdge(edge.source, edge.target, overview.nodes).childLevel === 2)
check("默认画面有根到一级的实线", level1.length > 0 && level1.every((edge) => lineForEdge(edge.source, edge.target, overview.nodes).kind === "solid"), String(level1.length))
check("默认画面有一级到二级的实线", level2.length > 0 && level2.every((edge) => lineForEdge(edge.source, edge.target, overview.nodes).kind === "solid"), String(level2.length))

const associations = overview.edges.filter((edge) => lineForEdge(edge.source, edge.target, overview.nodes).kind === "association")
check(
  "非父子边保留原关系且不按层级改判",
  associations.length > 0 && associations.every((edge) => edge.data.relation !== "" && lineForEdge(edge.source, edge.target, overview.nodes).childLevel === null),
  String(associations.length),
)

const level2Nodes = overview.nodes.filter((node) => hierarchyLevel(node.id, overview.nodes) === 2 && !node.id.startsWith("ui-"))
let expanded = overview
let expandedName = ""
for (const node of level2Nodes) {
  const next = openFurther(graph, view, {}, node.id)
  if (!next) {
    continue
  }
  const scene = growScene(graph, view, next)
  const children = scene.edges.filter((edge) => lineForEdge(edge.source, edge.target, scene.nodes).childLevel === 3)
  if (children.length > 0) {
    expanded = scene
    expandedName = node.data.label
    break
  }
}
const freshParent = expanded.edges.filter((edge) => lineForEdge(edge.source, edge.target, expanded.nodes).childLevel === 3)
check(
  "展开二级节点后，新的三级连接是虚线",
  freshParent.length > 0 && freshParent.every((edge) => lineForEdge(edge.source, edge.target, expanded.nodes).kind === "dashed"),
  `${expandedName || "无"} ${freshParent.length}`,
)

let openedAll: Record<string, number> = {}
let deepest = expanded
const seenOpens = new Set<string>()
for (let round = 0; round < 400; round += 1) {
  const scene = growScene(graph, view, openedAll)
  deepest = scene
  let grew = false
  for (const node of scene.nodes) {
    const key = `${node.id}:${openedAll[node.id] ?? 0}`
    if (seenOpens.has(key)) {
      continue
    }
    seenOpens.add(key)
    const next = openFurther(graph, view, openedAll, node.id)
    if (!next) {
      continue
    }
    openedAll = next
    grew = true
    break
  }
  if (!grew) {
    break
  }
}
const fourthEdges = deepest.edges.filter((edge) => lineForEdge(edge.source, edge.target, deepest.nodes).childLevel === 4)
const deepestLevel = deepest.nodes.reduce((max, node) => Math.max(max, hierarchyLevel(node.id, deepest.nodes)), 0)
const deepEdges = deepest.edges.filter((edge) => {
  const level = lineForEdge(edge.source, edge.target, deepest.nodes).childLevel
  return level !== null && level >= 3
})
check(
  "继续展开后，三级及以下父子连线保持虚线",
  deepEdges.length > 0 && deepEdges.every((edge) => lineForEdge(edge.source, edge.target, deepest.nodes).kind === "dashed"),
  `最深层 ${deepestLevel}，三级及以下 ${deepEdges.length}，四级 ${fourthEdges.length}`,
)

const page = readFileSync(`${root}/frontend/src/pages/KnowledgeGraphPage.tsx`, "utf8")
check(
  "图例包含分类颜色、实线和虚线",
  page.includes("VIEW_CONFIG.categories.map") &&
    page.includes("实线：一级、二级层级连接") &&
    page.includes("虚线：三级及以下层级连接") &&
    page.includes("strokeDasharray=\"5 3\""),
)
check("图例单独说明非层级关联", page.includes("点线：非层级关联") && page.includes("strokeDasharray=\"2 2\""))

console.log(`${checks.filter(Boolean).length}/${checks.length} passed`)
if (checks.some((item) => !item)) {
  process.exit(1)
}
