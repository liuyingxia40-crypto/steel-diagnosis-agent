import { readFileSync } from "node:fs"
import { lineForEdge } from "./src/graph/hierarchyLine.ts"
import { importedDeviceNames, parseKnowledgeGraph } from "./src/graph/presentation.ts"
import { parseProcessPayload, pipelineLines, shouldRefreshGraph, type PipelineView } from "./src/knowledge/fileDirectory.ts"

const checks: boolean[] = []

function check(name: string, ok: boolean, detail = ""): void {
  checks.push(ok)
  console.log(ok ? "PASS" : "FAIL", name, detail)
}

const root = "D:/工作/steel-diagnosis-agent/frontend/src"
const page = readFileSync(`${root}/pages/KnowledgeGraphPage.tsx`, "utf8")
const directory = readFileSync(`${root}/components/KnowledgeFileDirectory.tsx`, "utf8")
const canvas = readFileSync(`${root}/components/GraphCanvas.tsx`, "utf8")
const scene = readFileSync(`${root}/graph/networkScene.ts`, "utf8")
const app = readFileSync(`${root}/App.tsx`, "utf8")

function fileRecord(status: string, error = "") {
  return {
    file_id: "a".repeat(32),
    original_filename: "护罩记录.txt",
    extension: "txt",
    size_bytes: 40,
    sha256: "b".repeat(64),
    uploaded_at: "2026-10-26T17:49:00+08:00",
    source_type: "user_upload",
    processing_status: status,
    error_message: error,
    cleaned_at: status === "cleaned" ? "2026-10-26T17:49:01+08:00" : null,
    participates_in_qa: false,
    in_graph: false,
  }
}

function pipeline(overrides: Record<string, unknown> = {}): PipelineView {
  const parsed = parseProcessPayload({
    ...fileRecord("cleaned"),
    pipeline: {
      process_id: "c".repeat(32),
      pipeline_status: "completed",
      outcome: "knowledge_added_with_pending_review",
      message: "已写入符合安全规则的包含关系。仍有待审核内容没有入图。",
      knowledge_added: true,
      nothing_added: false,
      pending_review: true,
      counts: {
        entity_count: 14,
        relation_count: 10,
        matched_node_count: 4,
        new_node_count: 2,
        new_relation_count: 2,
        duplicate_relation_count: 1,
        conflict_count: 3,
        review_count: 4,
        blocked_count: 8,
      },
      stages: [
        { stage: "cleaning", status: "completed", reused: false },
        { stage: "extracting", status: "completed" },
        { stage: "previewing", status: "completed" },
        { stage: "importing", status: "completed" },
      ],
      failure_stage: "",
      error_message: "",
      duration_ms: 242,
      status_code: 200,
      import_result: { status: "applied", applied: true, imported_node_count: 2, imported_relation_count: 2 },
      in_graph: false,
      notice: "流程结束只表示各阶段已执行。",
      ...overrides,
    },
  })
  if (!parsed) {
    throw new Error("样本无法解析")
  }
  return parsed.pipeline
}

const added = pipeline()
const addedLines = pipelineLines(added).join("\n")
check(
  "新增2个节点和2条关系时显示真实数量并允许刷新图谱",
  shouldRefreshGraph(added) &&
    addedLines.includes("JSON清洗成功") &&
    addedLines.includes("知识抽取成功") &&
    addedLines.includes("入图预演已完成") &&
    addedLines.includes("新增 2 个节点、2 条关系") &&
    addedLines.includes("重复关系 1") &&
    addedLines.includes("冲突 3") &&
    addedLines.includes("待审核 4") &&
    addedLines.includes("阻止入图 8") &&
    addedLines.includes("242 毫秒") &&
    addedLines.includes("存在待审核知识") &&
    !addedLines.includes("已加入知识图谱"),
  addedLines,
)

const repeated = pipeline({
  outcome: "pending_review",
  message: "没有重复创建节点或关系。仍有待审核内容没有入图。",
  knowledge_added: false,
  nothing_added: true,
  counts: {
    entity_count: 14,
    relation_count: 10,
    matched_node_count: 4,
    new_node_count: 0,
    new_relation_count: 0,
    duplicate_relation_count: 1,
    conflict_count: 3,
    review_count: 4,
    blocked_count: 8,
  },
  stages: [
    { stage: "cleaning", status: "completed", reused: true },
    { stage: "extracting", status: "completed" },
    { stage: "previewing", status: "completed" },
    { stage: "importing", status: "completed" },
  ],
})
check(
  "重复处理后新增为0时不刷新图谱",
  !shouldRefreshGraph(repeated) && pipelineLines(repeated).join("\n").includes("没有可安全入图的知识") && pipelineLines(repeated).join("\n").includes("没有新增知识"),
)

const reviewOnly = pipeline({
  outcome: "pending_review",
  message: "没有符合安全规则的新知识，没有入图。仍有待审核内容。",
  knowledge_added: false,
  nothing_added: true,
  pending_review: true,
  counts: {
    entity_count: 4,
    relation_count: 2,
    matched_node_count: 0,
    new_node_count: 0,
    new_relation_count: 0,
    duplicate_relation_count: 0,
    conflict_count: 0,
    review_count: 2,
    blocked_count: 2,
  },
})
const reviewText = pipelineLines(reviewOnly).join("\n")
check("全部待审核时显示待审核而不是已入图", reviewText.includes("存在待审核知识") && reviewText.includes("没有可安全入图的知识") && !reviewText.includes("已加入知识图谱") && !shouldRefreshGraph(reviewOnly))

const failed = parseProcessPayload({
  ...fileRecord("cleaning_failed", "暂未完成该格式清洗"),
  pipeline: {
    process_id: "d".repeat(32),
    pipeline_status: "failed",
    outcome: "failed",
    message: "暂未完成该格式清洗",
    knowledge_added: false,
    nothing_added: false,
    pending_review: false,
    counts: {
      entity_count: 0,
      relation_count: 0,
      matched_node_count: 0,
      new_node_count: 0,
      new_relation_count: 0,
      duplicate_relation_count: 0,
      conflict_count: 0,
      review_count: 0,
      blocked_count: 0,
    },
    stages: [{ stage: "cleaning", status: "failed" }],
    failure_stage: "cleaning",
    error_message: "暂未完成该格式清洗",
    duration_ms: 30,
    in_graph: false,
    notice: "",
  },
})
const failedText = failed ? pipelineLines(failed.pipeline).join("\n") : ""
check(
  "清洗失败保留文件并显示真实原因",
  failed?.file.processing_status === "cleaning_failed" &&
    failedText.includes("文件已保存") &&
    failedText.includes("JSON清洗：失败") &&
    failedText.includes("知识抽取：未执行") &&
    failedText.includes("失败阶段：JSON清洗") &&
    failedText.includes("暂未完成该格式清洗") &&
    !failedText.includes("安全入图已完成") &&
    !shouldRefreshGraph(failed!.pipeline),
  failedText,
)

const stored = parseProcessPayload({
  ...fileRecord("cleaned"),
  pipeline: added,
})
check("重新读取的处理结果仍能解析", stored?.pipeline.process_id === "c".repeat(32) && stored.pipeline.counts.new_node_count === 2)
check("409响应不会被当成处理成功", parseProcessPayload({ success: false, message: "这个文件正在处理，不能同时再跑一遍。" }) === null)

const motor = "device-motor"
const pump = "device-pump"
const before = parseKnowledgeGraph({
  nodes: [
    { id: motor, data: { label: "棒线主轧机电机", type: "device", source_refs: [] } },
    { id: pump, data: { label: "棒线冷却水循环泵", type: "device", source_refs: [] } },
  ],
  edges: [],
})
const after = parseKnowledgeGraph({
  nodes: [
    ...before.nodes,
    {
      id: "import-component-a",
      parentId: motor,
      data: {
        label: "车间新增护罩",
        type: "component",
        category: "equipment",
        device_label: "棒线主轧机电机",
        source_refs: [{ document: "护罩记录.txt", section: "section-001", locator: "行 1" }],
      },
    },
    {
      id: "import-component-b",
      parentId: pump,
      data: {
        label: "车间新增护罩",
        type: "component",
        category: "equipment",
        device_label: "棒线冷却水循环泵",
        source_refs: [{ document: "护罩记录.txt", section: "section-002", locator: "行 2" }],
      },
    },
  ],
  edges: [
    { id: "import-edge-a", source: motor, target: "import-component-a", data: { relation: "HAS_COMPONENT", source_refs: [] } },
    { id: "import-edge-b", source: pump, target: "import-component-b", data: { relation: "HAS_COMPONENT", source_refs: [] } },
  ],
})
const addedNodes = after.nodes.filter((node) => node.id.startsWith("import-component-"))
const line = lineForEdge(motor, "import-component-a", [
  { id: "ui-group:equipment", parentId: "" },
  { id: motor, parentId: "ui-group:equipment" },
  { id: "import-component-a", parentId: motor },
])
check(
  "新增节点保留后端编号、父节点、分类和来源，并按二级实线归属设备",
  addedNodes.length === 2 &&
    addedNodes.every((node) => node.data.category === "equipment" && node.data.source_refs[0]?.document === "护罩记录.txt") &&
    new Set(addedNodes.map((node) => node.parentId)).size === 2 &&
    importedDeviceNames(before, after).join("、") === "棒线主轧机电机、棒线冷却水循环泵" &&
    before.nodes.every((node) => after.nodes.some((item) => item.id === node.id && item.data.label === node.data.label)) &&
    line.kind === "solid" &&
    line.childLevel === 2,
  JSON.stringify({ parents: addedNodes.map((node) => node.parentId), line }),
)

const reload = page.slice(page.indexOf("async function reloadMergedGraph"), page.indexOf("function resetOverview"))
check(
  "新增知识才重新读取图谱，且不重置画布",
  reload.includes('fetch("/api/knowledge/graph")') &&
    reload.includes("setRefreshError") &&
    !reload.includes("setLayoutEpoch") &&
    !reload.includes("setOpened({})") &&
    page.includes("onKnowledgeAdded={reloadMergedGraph}") &&
    directory.includes("shouldRefreshGraph"),
)
check(
  "目录提供处理结果、重新处理和原有 JSON 预览",
  directory.includes("查看处理结果") &&
    directory.includes("重新处理") &&
    directory.includes("正在处理") &&
    directory.includes("查看JSON") &&
    directory.includes("文件清洗成功不代表已经加入知识图谱。") &&
    directory.includes("process-result") &&
    directory.includes("/process"),
)
check("原有六份资料不显示重新处理", !directory.slice(directory.indexOf("function FileList"), directory.indexOf("function CleanedView")).includes("重新处理"))
check("图谱仍保留圆形节点、双击、加号和层级线", canvas.includes('type: "circle"') && canvas.includes("NodeEvent.DBLCLICK") && canvas.includes('text: "+"') && canvas.includes("lineDashOf"))
check("展开时使用后端父节点", scene.includes("created.parentId = real.parentId || id"))
check(
  "其他页面入口没有被改掉",
  !app.includes("智能问答") &&
    !app.includes("<HomePage />") &&
    app.includes("知识图谱") &&
    app.includes("设备运行监测") &&
    app.includes("<VibrationAnalysisPage />") &&
    app.includes("<EquipmentMonitoringPage />"),
)

console.log(`${checks.filter(Boolean).length}/${checks.length} passed`)
if (checks.some((item) => !item)) {
  process.exit(1)
}
