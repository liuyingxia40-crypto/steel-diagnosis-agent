import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  ANALYSIS_STATUS_OPTIONS,
  analysisStatusDisplay,
  buildSearchParams,
  deviceDisplayName,
  formatDisplayNumber,
  isUnanalyzedStatus,
  primaryMetricText,
} from "../frontend/src/monitoring/historySearch.ts"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const page = readFileSync(resolve(root, "frontend/src/pages/EquipmentMonitoringPage.tsx"), "utf8")
const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

check(
  "检索参数包含分页与状态",
  buildSearchParams({
    q: "主轧机电机",
    deviceId: "MM-01",
    startTime: "2026-04-03 11:00:00",
    endTime: "2026-04-03 12:00:00",
    page: 2,
    analysisStatus: "all",
  }).toString().includes("page=2") &&
    buildSearchParams({
      q: "",
      deviceId: "",
      startTime: "",
      endTime: "",
      page: 1,
      analysisStatus: "deviation",
    }).get("analysis_status") === "deviation",
)
check("尚未分析状态文案正确", analysisStatusDisplay("尚未分析") === "尚未分析" && isUnanalyzedStatus("尚未分析") && !isUnanalyzedStatus("偏离基线"))
check("设备中文名优先", deviceDisplayName({ device_id: "MM-01", device_name: "主轧机电机", equipment_category: "主轧机电机" }) === "主轧机电机")
check("展示数字最多四位小数", formatDisplayNumber(1.23456789) === "1.2346" && formatDisplayNumber(25) === "25")
check("主要指标摘要可读", primaryMetricText({ "X向振动(mm/s)": 1.25, "Y向振动(mm/s)": 0.8 }, ["X向振动(mm/s)", "Y向振动(mm/s)"]).includes("1.25"))
check("状态筛选项完整", ANALYSIS_STATUS_OPTIONS.length === 3)
check(
  "监测页有检索入口与异常直达",
  page.includes("/api/dataset/records/search") &&
    page.includes("查看记录详情") &&
    page.includes("返回监测界面") &&
    page.includes("返回记录列表") &&
    page.includes("记录编号") &&
    page.includes("原始表格行号") &&
    page.includes("展开完整统计信息") &&
    page.includes("尚未分析，不等于正常") &&
    page.includes("设备中文名称、设备编号或记录编号"),
)

console.log(`RESULT passed=${results.filter(Boolean).length} failed=${results.length - results.filter(Boolean).length}`)
if (results.some((item) => !item)) {
  process.exit(1)
}
