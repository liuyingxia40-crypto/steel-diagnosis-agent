import { readFileSync } from "node:fs"

import {
  ANSWER_LABELS,
  OBSERVATION_FIELDS,
  PRIMARY_FIELDS,
  chartPoints,
  formatCell,
  groupDevices,
  isDatasetDevices,
  isDatasetObservation,
  isDatasetRecords,
  isDatasetSummary,
  truncationText,
  visibleMetrics,
} from "./src/monitoring/view.ts"

const BASE = "http://127.0.0.1:8000"
const failures: string[] = []

function check(name: string, passed: boolean, detail = "") {
  const line = `${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`
  console.log(line)
  if (!passed) {
    failures.push(line)
  }
}

async function getJson(path: string): Promise<unknown> {
  const response = await fetch(BASE + path)
  const body: unknown = await response.json()
  return { status: response.status, body }
}

const summaryResponse = await getJson("/api/dataset/summary")
const devicesResponse = await getJson("/api/dataset/devices")
const summary = summaryResponse.body
const devices = devicesResponse.body
check(
  "A 概况",
  summaryResponse.status === 200 &&
    isDatasetSummary(summary) &&
    summary.sheet_count === 6 &&
    new Set(summary.sheets.map((sheet) => sheet.equipment_category)).size === 6 &&
    summary.device_count === 46 &&
    summary.record_count === 24000 &&
    summary.source_name === "钢铁设备数据集_开发集.xlsx",
  isDatasetSummary(summary) ? `${summary.device_count}/${summary.record_count}` : "结构不符",
)

check("B 设备清单结构", devicesResponse.status === 200 && isDatasetDevices(devices))
if (!isDatasetSummary(summary) || !isDatasetDevices(devices)) {
  throw new Error("接口结构无法继续核对")
}
const groups = groupDevices(
  summary.sheets.map((sheet) => sheet.equipment_category),
  devices.devices,
)
const groupedCount = groups.reduce((total, group) => total + group.devices.length, 0)
const motor = groups.find((group) => group.category === "主轧机电机")
const mm01 = motor?.devices.find((device) => device.device_id === "MM-01")
check(
  "B 46台按类别分组且包含MM-01",
  devices.device_count === 46 &&
    devices.devices.length === 46 &&
    groups.length === 6 &&
    groupedCount === 46 &&
    mm01?.equipment_category === "主轧机电机" &&
    mm01.record_count === 1030 &&
    mm01.time_start === "2026-03-14 00:05:00",
  `groups=${groups.length} devices=${groupedCount} mm=${mm01?.record_count ?? "missing"}`,
)

const pageResponse = await getJson("/api/dataset/records?device_id=MM-01&limit=100")
const page = pageResponse.body
check("C 记录结构", pageResponse.status === 200 && isDatasetRecords(page))
if (!isDatasetRecords(page)) {
  throw new Error("记录结构无法继续核对")
}
const ordered = page.records.every((record, index) => {
  if (index === 0) {
    return true
  }
  const previous = page.records[index - 1]
  if (!previous) {
    return false
  }
  if (previous.collected_at !== record.collected_at) {
    return previous.collected_at < record.collected_at
  }
  if (previous.excel_row !== record.excel_row) {
    return previous.excel_row < record.excel_row
  }
  return previous.record_id <= record.record_id
})
const first = page.records[0]
const observationResponse = await getJson(`/api/dataset/observations/${first.record_id}`)
const observation = observationResponse.body
const sameValues =
  isDatasetObservation(observation) &&
  PRIMARY_FIELDS.every((field) => formatCell(observation.record.values, field) === formatCell(first.values, field))
check(
  "C 时间顺序和观测值一致",
  page.found === true &&
    page.device_id === "MM-01" &&
    ordered &&
    first.collected_at === "2026-03-14 00:05:00" &&
    formatCell(first.values, "Y向振动(mm/s)") === "1.205" &&
    formatCell(first.values, "振动均方根") === "1.41" &&
    formatCell(first.values, "驱动端轴承温度(°C)") === "62.4" &&
    formatCell(first.values, "电机电流(A)") === "889.9" &&
    sameValues,
  `${first.record_id} ${first.collected_at} y=${formatCell(first.values, "Y向振动(mm/s)")}`,
)

const limitTenResponse = await getJson("/api/dataset/records?device_id=MM-01&limit=10")
const limitTen = limitTenResponse.body
const text = isDatasetRecords(limitTen) ? truncationText(limitTen.returned_count, limitTen.matched_count, limitTen.truncated) : ""
check(
  "D 截断提示",
  isDatasetRecords(limitTen) &&
    limitTen.truncated === true &&
    limitTen.returned_count === 10 &&
    limitTen.matched_count === 1030 &&
    text.includes("本次仅显示前10条记录，共匹配1030条") &&
    !text.includes("最新"),
  text,
)
const fullText = truncationText(page.returned_count, page.matched_count, page.truncated)
check(
  "D 默认100条也标明截断",
  page.truncated === true && page.returned_count === 100 && page.matched_count === 1030 && fullText.includes("本次仅显示前100条记录，共匹配1030条"),
  fullText,
)

const fanResponse = await getJson(
  "/api/dataset/records?device_id=FF-01&start_time=2026-03-14%2000:15:00&end_time=2026-03-14%2000:15:00&limit=100",
)
const fan = fanResponse.body
if (!isDatasetRecords(fan)) {
  check("E 重复时间", false, "结构不符")
} else {
  const points = chartPoints(fan.records, ["X向振动(mm/s)", "Y向振动(mm/s)"])
  const reversed = chartPoints([...fan.records].reverse(), ["X向振动(mm/s)"])
  const average = fan.records.reduce((total, record) => total + Number(record.values["X向振动(mm/s)"]), 0) / fan.records.length
  const keptSeparate =
    fan.records.length === 2 &&
    points.length === 2 &&
    new Set(fan.records.map((record) => record.record_id)).size === 2 &&
    fan.records.every((record) => record.collected_at === "2026-03-14 00:15:00" && record.duplicate_timestamp === true && record.same_timestamp_count === 2) &&
    points.every((point) => point.values[0] !== average) &&
    reversed.map((point) => point.recordId).join() === [...fan.records].reverse().map((record) => record.record_id).join()
  const firstObservation = await getJson(`/api/dataset/observations/${fan.records[0].record_id}`)
  const secondObservation = await getJson(`/api/dataset/observations/${fan.records[1].record_id}`)
  check(
    "E 重复时间分别保留",
    keptSeparate &&
      isDatasetObservation(firstObservation.body) &&
      isDatasetObservation(secondObservation.body) &&
      firstObservation.body.record.record_id !== secondObservation.body.record.record_id &&
      formatCell(firstObservation.body.record.values, "X向振动(mm/s)") !== formatCell(secondObservation.body.record.values, "X向振动(mm/s)"),
    fan.records.map((record) => `${record.record_id}:${formatCell(record.values, "X向振动(mm/s)")}`).join(" "),
  )
}

const poisoned = visibleMetrics({ "振动均方根": 1.41, 故障类型: 1, 设备状态: null })
check(
  "F 指标过滤答案标签",
  poisoned.length === 1 &&
    poisoned[0][0] === "振动均方根" &&
    !OBSERVATION_FIELDS.includes("振动均方根(mm/s)" as (typeof OBSERVATION_FIELDS)[number]) &&
    ANSWER_LABELS.length === 10 &&
    PRIMARY_FIELDS.includes("振动均方根"),
)

const pageSource = readFileSync(new URL("./src/pages/EquipmentMonitoringPage.tsx", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./src/App.tsx", import.meta.url), "utf8")
const forbidden = ["故障类型", "设备状态", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度", "最新100", "analyze_equipment_record", "detect_anomaly", "/api/ai/chat", "振动均方根(mm/s)"]
check(
  "F 页面源码不展示答案标签",
  forbidden.every((word) => !pageSource.includes(word)) &&
    pageSource.includes("官方开发集 · 历史数据查询") &&
    pageSource.includes("/api/dataset/summary") &&
    pageSource.includes("/api/dataset/devices") &&
    pageSource.includes("/api/dataset/records") &&
    pageSource.includes("/api/dataset/observations/") &&
    !appSource.includes("智能问答") &&
    appSource.includes("知识图谱") &&
    appSource.includes("<VibrationAnalysisPage />") &&
    appSource.includes("设备运行监测") &&
    !pageSource.includes("new Date") &&
    !pageSource.includes("toLocaleString") &&
    !pageSource.includes("toISOString"),
  forbidden.filter((word) => pageSource.includes(word)).join(","),
)

console.log(`SUMMARY ${failures.length === 0 ? "ok" : "failed"} ${failures.length}`)
if (failures.length > 0) {
  process.exit(1)
}
