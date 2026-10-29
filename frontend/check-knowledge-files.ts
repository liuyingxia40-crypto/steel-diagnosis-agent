import { readFileSync } from "node:fs"
import { formatBytes, parseKnowledgeFileList, statusLabel } from "./src/knowledge/fileDirectory.ts"

const checks: boolean[] = []

function check(name: string, ok: boolean, detail = ""): void {
  checks.push(ok)
  console.log(ok ? "PASS" : "FAIL", name, detail)
}

const root = "D:/工作/steel-diagnosis-agent/frontend/src"
const page = readFileSync(`${root}/pages/KnowledgeGraphPage.tsx`, "utf8")
const canvas = readFileSync(`${root}/components/GraphCanvas.tsx`, "utf8")
const directory = readFileSync(`${root}/components/KnowledgeFileDirectory.tsx`, "utf8")
const sample = parseKnowledgeFileList({
  files: [
    {
      file_id: "kb-" + "a".repeat(13),
      original_filename: "KB-01_设备知识图谱.docx",
      extension: "docx",
      size_bytes: 38486,
      sha256: "a".repeat(64),
      uploaded_at: null,
      source_type: "existing_knowledge",
      processing_status: "official_catalog",
      participates_in_qa: false,
      in_graph: false,
    },
    {
      file_id: "b".repeat(32),
      original_filename: "轴承磨损记录.txt",
      extension: "txt",
      size_bytes: 12,
      sha256: "b".repeat(64),
      uploaded_at: "2026-10-26T16:21:00+08:00",
      source_type: "user_upload",
      processing_status: "pending_cleaning",
      participates_in_qa: false,
      in_graph: false,
    },
  ],
})

check("目录解析保留中文文件名", sample[1]?.original_filename === "轴承磨损记录.txt")
check("新增资料状态显示待清洗", statusLabel(sample[1]?.processing_status ?? "pending_cleaning") === "待清洗" && formatBytes(12) === "12 B")
let rejected = false
try {
  parseKnowledgeFileList({
    files: [
      {
        ...sample[1],
        processing_status: "imported",
        status_text: "已加入图谱",
      },
    ],
  })
} catch {
  rejected = true
}
check("已入图状态不会被目录解析接受", rejected)
check("页面保留图谱交互和三栏", page.includes("KnowledgeFileDirectory") && page.includes("返回全局") && page.includes("适应画布") && page.includes("onDeeper"))
check("页面保留实线虚线点线图例", page.includes("实线：一级、二级层级连接") && page.includes("虚线：三级及以下层级连接") && page.includes("点线：非层级关联"))
check(
  "目录包含原有知识库、新增资料、添加文件和 JSON 预览",
  directory.includes("原有知识库") &&
    directory.includes("新增资料") &&
    directory.includes("添加文件") &&
    directory.includes("查看JSON") &&
    directory.includes("文件清洗成功不代表已经加入知识图谱。"),
)
check("图谱仍使用圆形节点、双击、加号和层级线型", canvas.includes('type: "circle"') && canvas.includes("NodeEvent.DBLCLICK") && canvas.includes('text: "+"') && canvas.includes("lineDashOf"))

console.log(`${checks.filter(Boolean).length}/${checks.length} passed`)
if (checks.some((item) => !item)) {
  process.exit(1)
}
