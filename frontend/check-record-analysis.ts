import { readFileSync } from "node:fs"

import {
  ANALYSIS_SCOPE_NOTE,
  EMPTY_CANDIDATE_TEXT,
  EXPERIMENTAL_LIMIT_NOTE,
  analysisLevelText,
  formatAnalysisNumber,
  formatMeasurement,
  isDatasetAnalysis,
  metricsForFields,
  shouldApplyAnalysis,
} from "./src/monitoring/view.ts"

const failures: string[] = []

function check(name: string, passed: boolean, detail = "") {
  const line = `${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`
  console.log(line)
  if (!passed) {
    failures.push(line)
  }
}

async function load(recordId: string): Promise<unknown> {
  const response = await fetch(`http://127.0.0.1:8000/api/dataset/analysis/${recordId}`)
  return { status: response.status, body: await response.json() }
}

const motorResponse = await load("REC-MA-20260403-00346")
const motor = motorResponse.body
check("A 接口结构", motorResponse.status === 200 && isDatasetAnalysis(motor))
if (!isDatasetAnalysis(motor)) {
  throw new Error("MM-01 分析结果无法识别")
}
const y = motor.statistical_deviation.analyzed_metrics.find((item) => item.field === "Y向振动(mm/s)")
const xy = motor.diagnostic_clues.find((item) => item.feature_code === "amplitude")
const yText = y ? formatMeasurement(y.current_value, y.unit) : ""
const zText = y ? formatAnalysisNumber(y.robust_z) : ""
check(
  "A MM-01 部分线索",
  motor.observation.device_id === "MM-01" &&
    motor.record_id === "REC-MA-20260403-00346" &&
    motor.statistical_deviation.detection_status === "偏离基线" &&
    yText === "3.017 mm/s" &&
    y?.robust_z === 3.923797970479703 &&
    zText === "3.923797970479703" &&
    motor.diagnosis_level === "diagnostic_clue" &&
    analysisLevelText(motor.diagnosis_level).startsWith("部分诊断线索") &&
    motor.candidate_faults.length === 0 &&
    EMPTY_CANDIDATE_TEXT === "当前观测数据不足以支持具体故障判断。" &&
    xy?.partial_condition_met === true &&
    xy.complete_condition_met === false &&
    xy.line_number === 38 &&
    xy.related_fault_section.includes("转子不平衡") &&
    xy.unverified_reason.includes("不能确认转子不平衡") &&
    motor.data_source === "钢铁设备数据集_开发集.xlsx",
  `${yText} z=${zText} level=${motor.diagnosis_level}`,
)

const gearResponse = await load("REC-GE-20260409-00447")
const gear = gearResponse.body
check("B 接口结构", gearResponse.status === 200 && isDatasetAnalysis(gear))
if (!isDatasetAnalysis(gear)) {
  throw new Error("GB-01 分析结果无法识别")
}
const drive = gear.statistical_deviation.analyzed_metrics.find((item) => item.field === "驱动端轴承温度(°C)")
const other = gear.statistical_deviation.analyzed_metrics.find((item) => item.field === "非驱动端轴承温度(°C)")
const shown = metricsForFields(gear.statistical_deviation, gear.statistical_deviation.anomalous_metrics)
check(
  "B 温度统计偏离不形成润滑故障",
  gear.observation.device_id === "GB-01" &&
    drive?.current_value === 77 &&
    drive.unit === "°C" &&
    drive.metric_status === "偏离基线" &&
    other?.current_value === 73.2 &&
    other.unit === "°C" &&
    other.metric_status === "偏离基线" &&
    gear.statistical_deviation.anomalous_metrics.join() === "驱动端轴承温度(°C),非驱动端轴承温度(°C)" &&
    shown.every((item) => item !== null) &&
    gear.candidate_faults.length === 0 &&
    gear.diagnostic_clues.every((item) => item.feature_code !== "temperature") &&
    gear.diagnosis_level === "diagnostic_clue",
  `drive=${drive?.current_value} other=${other?.current_value}`,
)

check(
  "C 旧请求不能覆盖新记录",
  shouldApplyAnalysis("REC-A", "REC-A", "REC-A") &&
    !shouldApplyAnalysis("REC-A", "REC-B", "REC-A") &&
    !shouldApplyAnalysis("REC-A", "REC-A", "REC-B") &&
    !shouldApplyAnalysis("", "REC-A", "REC-A"),
)

check("D 空值不写成 0", formatAnalysisNumber(null) === "无数据" && formatAnalysisNumber(Number.NaN) === "无数据" && !isDatasetAnalysis({ ok: false }))

const pageSource = readFileSync(new URL("./src/pages/EquipmentMonitoringPage.tsx", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./src/App.tsx", import.meta.url), "utf8")
const labels = ["设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度"]
const viewSource = readFileSync(new URL("./src/monitoring/view.ts", import.meta.url), "utf8")
const analysisUses = pageSource.split("/api/dataset/analysis/").length - 1
check(
  "E/F/G 页面保留原功能且只在点击时分析",
  analysisUses === 1 &&
    pageSource.includes("分析此记录") &&
    pageSource.includes("function analyzeRecord") &&
    pageSource.includes("clearAnalysis()") &&
    pageSource.includes("truncationText") &&
    pageSource.includes("chartPoints") &&
    pageSource.includes("该设备在此时间存在多条不同记录") &&
    pageSource.includes("start_time") &&
    pageSource.includes("EXPERIMENTAL_LIMIT_NOTE") &&
    pageSource.includes("ANALYSIS_SCOPE_NOTE") &&
    pageSource.includes("EMPTY_CANDIDATE_TEXT") &&
    viewSource.includes(EXPERIMENTAL_LIMIT_NOTE) &&
    viewSource.includes(ANALYSIS_SCOPE_NOTE) &&
    viewSource.includes(EMPTY_CANDIDATE_TEXT) &&
    !pageSource.includes("危险状态") &&
    labels.every((label) => !pageSource.includes(label)) &&
    !appSource.includes("智能问答") &&
    appSource.includes("知识图谱") &&
    appSource.includes("<VibrationAnalysisPage />") &&
    appSource.includes("设备运行监测"),
  `analysisUses=${analysisUses}`,
)

console.log(`SUMMARY ${failures.length === 0 ? "ok" : "failed"} ${failures.length}`)
if (failures.length > 0) {
  process.exit(1)
}
