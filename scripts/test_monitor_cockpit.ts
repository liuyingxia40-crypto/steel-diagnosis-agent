import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  SPEED_FIELDS,
  TEMPERATURE_FIELDS,
  VIBRATION_GROUPS,
  analyzedRecordWindow,
  cockpitStatusLabel,
  deviationMarkers,
  diagnosisSteps,
  fieldsWithValues,
} from "../frontend/src/monitoring/view.ts"
import type { DatasetRecord, StoredMonitorEvent } from "../frontend/src/types/api.ts"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

function record(id: string, values: Record<string, number | null>): DatasetRecord {
  return {
    record_id: id,
    device_id: "CC-01",
    equipment_category: "冷床",
    collected_at: `2026-03-14 10:0${id.slice(-1)}:00`,
    sheet_name: "冷床",
    excel_row: 1,
    duplicate_timestamp: false,
    same_timestamp_count: 1,
    values,
  }
}

const rows = [
  record("REC-1", { "X向振动(mm/s)": 1.2, "Y向振动(mm/s)": 1.1, "转速(rpm)": 900, "驱动端轴承温度(°C)": 40 }),
  record("REC-2", { "X向振动(mm/s)": 1.4, "Y向振动(mm/s)": 1.3, "转速(rpm)": 910, "驱动端轴承温度(°C)": 41 }),
  record("REC-3", { "X向振动(mm/s)": 3.2, "Y向振动(mm/s)": 1.2, "转速(rpm)": 905 }),
]

check("1 状态名称使用普通中文", cockpitStatusLabel(null) === "尚未开始" && cockpitStatusLabel("运行中") === "正在分析" && cockpitStatusLabel("正在停止") === "正在分析" && cockpitStatusLabel("已停止") === "已停止" && cockpitStatusLabel("已结束") === "已完成" && cockpitStatusLabel("已出错") === "运行出错")
const progress = analyzedRecordWindow(rows, { status: "运行中", processed_count: 2, current_record_id: "REC-2" }, true)
check("2 图表不提前画出未处理记录", progress.mode === "progress" && progress.records.map((item) => item.record_id).join(",") === "REC-1,REC-2")
const outside = analyzedRecordWindow(rows, { status: "运行中", processed_count: 1, current_record_id: "REC-9" }, false)
check("3 当前记录不在查询窗口时不把窗口当成已分析", outside.mode === "outside" && outside.records.length === 0)
check("4 振动、温度、转速只保留实际有数值的指标", fieldsWithValues(rows, VIBRATION_GROUPS[0].fields).join(",") === "X向振动(mm/s),Y向振动(mm/s)" && fieldsWithValues(rows, TEMPERATURE_FIELDS).join(",") === "驱动端轴承温度(°C)" && fieldsWithValues(rows, SPEED_FIELDS).join(",") === "转速(rpm)" && fieldsWithValues(rows, ["包络谱峰值(g)"]).length === 0)
const markers = deviationMarkers(progress.records, [
  { record_id: "REC-2", event_id: 8 },
  { record_id: "REC-3", event_id: 9 },
])
check("5 异常标记只对应已经进入图表的记录", markers.length === 1 && markers[0].recordId === "REC-2" && markers[0].eventId === 8)

const event = {
  event_id: 8,
  session_id: "auto-view",
  record_id: "REC-2",
  device_id: "CC-01",
  collected_at: "2026-03-14 10:02:00",
  processed_at: "2026-10-27T16:00:00+08:00",
  event_type: "统计偏离",
  diagnosis_level: "diagnostic_clue",
  diagnosis_available: true,
  diagnosis_status: "已关联部分诊断线索。证据不足，无法确定具体故障原因。",
  detection_status: "偏离基线",
  data_source: "钢铁设备数据集_开发集.xlsx",
  event_scope: "official_development_set_history_replay",
  event_note: "实验性统计偏离",
  metrics: [{ field: "Y向振动(mm/s)", unit: "mm/s", current_value: 3, median: 1, mad: 0.2, robust_z: 4, metric_status: "偏离基线" }],
  diagnostic_clues: [
    {
      feature_code: "amplitude",
      related_fault_section: "转子不平衡 / 频谱特征",
      document: "KB-02_故障模式诊断知识库.md",
      line_number: 38,
      partial_condition_met: true,
      complete_condition_met: false,
      unverified_reason: "只比较到 X 向大于 Y 向。",
      observed_fields: [{ field: "X向振动(mm/s)", value: 1.4, unit: "mm/s" }],
    },
  ],
  evidence_sources: [{ document: "KB-02_故障模式诊断知识库.md", line_number: 38, feature_code: "amplitude", related_fault_section: "转子不平衡 / 频谱特征" }],
  missing_conditions: ["2X幅值", "相位"],
  candidate_faults: [],
  confirmed: false,
} satisfies StoredMonitorEvent
const steps = diagnosisSteps(event)
check("6 只有已保存内容对应的步骤记为完成", steps.filter((item) => item.done).map((item) => item.title).join(",") === "发现数据变化,检索相关诊断知识,核对证据,输出诊断线索" && steps[1].detail.includes("KB-02_故障模式诊断知识库.md"))
check("7 没有诊断痕迹时不把后续步骤记成完成", diagnosisSteps({ ...event, diagnosis_available: false, diagnosis_status: "", evidence_sources: [], missing_conditions: [] }).slice(1).every((item) => !item.done))

const page =
  readFileSync(resolve(root, "frontend/src/pages/EquipmentMonitoringPage.tsx"), "utf8") +
  "\n" +
  readFileSync(resolve(root, "frontend/src/monitoring/CockpitCharts.tsx"), "utf8")
const app = readFileSync(resolve(root, "frontend/src/App.tsx"), "utf8")
const graph = readFileSync(resolve(root, "frontend/src/pages/KnowledgeGraphPage.tsx"), "utf8")
const nav = app.slice(app.indexOf("<nav"), app.indexOf("</nav>"))
check(
  "8 页面包含监测主区域与真实历史查询入口",
  page.includes("钢铁设备智能监测中心") &&
    page.includes("持续分析设备数据，自动发现异常变化，辅助判断可能原因。") &&
    page.includes("官方数据集 · 历史数据模拟监测") &&
    page.includes("开始监测") &&
    page.includes("停止监测") &&
    page.includes("运行数据") &&
    page.includes("发现的数据异常变化") &&
    page.includes("智能诊断分析") &&
    page.includes("设备信息和历史数据") &&
    page.includes("历史查询") &&
    page.includes("开始采集时间") &&
    page.includes("结束采集时间") &&
    page.includes("检索记录") &&
    page.includes("每页 {searchResult.page_size} 条") &&
    page.includes("/api/dataset/records/search") &&
    page.includes("record-table") &&
    page.includes("分析此记录"),
)
check(
  "9 前端轮询不逐条触发处理或模型",
  page.includes("/api/monitoring/auto/start") &&
    page.includes("/api/monitoring/auto/stop") &&
    page.includes("/api/monitoring/auto/status") &&
    page.includes("/api/monitoring/events?") &&
    page.includes("/api/dataset/records?") &&
    !page.includes("process-record") &&
    !page.includes("DeepSeek") &&
    !page.includes("detect_anomaly"),
)
check(
  "10 知识图谱与监测两个主导航仍在且无智能问答入口",
  nav.split('className="nav-button"').length - 1 === 2 &&
    nav.includes("知识图谱") &&
    nav.includes("设备运行监测") &&
    !nav.includes("智能问答") &&
    graph.includes("function KnowledgeGraphPage") &&
    app.includes("<EquipmentMonitoringPage />"),
)
check("11 诊断面板保留依据、缺失条件和尚未确诊", page.includes("判断依据") && page.includes("还需补充的数据") && page.includes("相关诊断线索") && page.includes("查看技术依据") && page.includes("确诊情况：{NOT_CONFIRMED_TEXT}") && page.includes("知识来源") && page.includes("缺失条件"))

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)
