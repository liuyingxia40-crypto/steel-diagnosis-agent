import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { NO_HYPOTHESIS, UNCHANGED_VERSION, evidenceText, timelineText, type DiagnosisSummary } from "../frontend/src/monitoring/diagnosisTimeline.ts"
import { evidenceLines, type DiagnosticEvidence } from "../frontend/src/monitoring/diagnosticEvidence.ts"
import { UNSUPPORTED_UPLOAD, followupLines, type FollowupTask } from "../frontend/src/monitoring/followupTask.ts"
import { syncPhaseLines, type SyncMeasurement } from "../frontend/src/monitoring/supplementMeasurement.ts"
import {
  apiErrorMessage,
  conditionChanges,
  defaultMeasuredAt,
  describeSecondHarmonic,
  fileBaseName,
  groupByMeasurement,
  metricLines,
  originLabel,
  splitChecks,
  type PhysicalCheck,
  type SupplementMeasurement,
} from "../frontend/src/monitoring/supplementMeasurement.ts"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const page = readFileSync(resolve(root, "frontend/src/pages/EquipmentMonitoringPage.tsx"), "utf8")
const panel = readFileSync(resolve(root, "frontend/src/monitoring/SupplementaryMeasurementPanel.tsx"), "utf8")
const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

const officialRatio: PhysicalCheck = {
  check_id: "second_harmonic_ratio",
  title: "2X与1X比值",
  status: "insufficient_data",
  limit: "没有 2X 幅值，不能计算 2X/1X。",
}
const officialWave: PhysicalCheck = {
  check_id: "waveform_statistics",
  title: "峭度与峰值因子",
  status: "insufficient_data",
  limit: "没有原始波形，不能生成峭度或峰值因子。",
}
const officialPhase: PhysicalCheck = {
  check_id: "phase_stability",
  title: "相位稳定",
  status: "insufficient_data",
  limit: "没有相位，不能证明相位稳定。",
}
const supplementRatio: PhysicalCheck = {
  check_id: "supplement_second_harmonic_ratio",
  title: "补充信号的2X与1X比值",
  status: "consistent",
  limit: "比值没有故障阈值。",
  supports_fault: false,
  evidence_source: { kind: "supplementary_measurement", measurement_ids: ["m-1"] },
  computed: {
    values_merged: false,
    measurements: [
      {
        measurement_id: "m-1",
        content_sha256: "abc",
        unit: "mm/s",
        frequency_1x_hz: 25,
        amplitude_1x: 1,
        frequency_2x_hz: 50,
        amplitude_2x: 0.3,
        ratio_2x_to_1x: 0.3,
      },
    ],
  },
}
const supplementWave: PhysicalCheck = {
  check_id: "supplement_waveform_statistics",
  title: "补充波形的峭度与峰值因子",
  status: "consistent",
  limit: "没有故障阈值。",
  supports_fault: false,
  evidence_source: { kind: "supplementary_measurement", measurement_ids: ["m-1"] },
  computed: { values_merged: false, measurements: [{ measurement_id: "m-1", content_sha256: "abc", kurtosis_pearson: 1.5, crest_factor: 1.414 }] },
}
const supplementPhase: PhysicalCheck = {
  check_id: "supplement_phase",
  title: "补充信号的相位",
  status: "insufficient_data",
  limit: "只有一个通道，不能计算相位差。",
  evidence_source: { kind: "supplementary_measurement", measurement_ids: ["m-1"] },
}
const measurements: SupplementMeasurement[] = [
  {
    measurement_id: "m-1",
    record_id: "REC-MA-20260403-00346",
    device_id: "MM-01",
    measured_at: "2026-04-03 11:05:00",
    sensor_position: "驱动端水平",
    sampling_rate_hz: 256,
    unit: "mm/s",
    file_name: "synthetic.csv",
    content_sha256: "abc",
    data_origin: "synthetic_test_signal",
    origin_note: "合成测试信号，只用于验证程序，不是工厂测量。",
  },
]
const checks = [officialRatio, officialWave, officialPhase, supplementRatio, supplementWave, supplementPhase]
const split = splitChecks(checks)
const changes = conditionChanges(checks)
const harmonic = describeSecondHarmonic(checks, measurements)
const lines = metricLines(checks, measurements)
const groups = groupByMeasurement(lines)

check("默认测量时间使用记录采集时间", defaultMeasuredAt("2026-04-03 11:05:00") === "2026-04-03 11:05:00")
check("文件名不保留服务器路径", fileBaseName("D:\\hidden\\wave.csv") === "wave.csv")
check("接口错误原文可以展示", apiErrorMessage({ success: false, message: "设备编号与这条记录不一致，不能合并观测。" }, "失败") === "设备编号与这条记录不一致，不能合并观测。")
check("官方检查和补充检查分开", split.official.every((item) => !item.check_id.startsWith("supplement_")) && split.supplement.length === 3)
check(
  "官方没有2X时仍保留官方缺失，并单独显示CSV算出的2X",
  harmonic.officialLine.includes("官方记录") &&
    harmonic.officialLine.includes("数据不足") &&
    harmonic.supplementLines[0].includes("2X/1X=0.3") &&
    harmonic.supplementLines[0].includes("m-1") &&
    harmonic.supplementLines[0].includes("SHA-256 abc") &&
    harmonic.supplementLines[0].includes("不是官方记录") &&
    !harmonic.supplementLines[0].startsWith("没有 2X 数据"),
)
check("原来不能验证的峭度现在可以计算，相位仍然不能验证", changes.gained.includes("峭度与峰值因子") && changes.still.includes("相位") && !changes.gained.includes("相位"))
check(
  "计算结果按测量编号分开并只保留成功指标",
  groups.length === 1 &&
    groups[0].sensorPosition === "驱动端水平" &&
    groups[0].measuredAt === "2026-04-03 11:05:00" &&
    lines.some((item) => item.label === "2X/1X" && item.value === "0.3") &&
    lines.some((item) => item.label === "Pearson 峭度") &&
    lines.every((item) => item.measurementId === "m-1"),
)
check(
  "监测页的异常详情、历史详情和记录详情都有补充测量入口",
  (page.match(/<SupplementaryMeasurementPanel /g) || []).length === 3 &&
    panel.includes("补充测量") &&
    panel.includes("重新执行物理校验") &&
    panel.includes("重新评估故障") &&
    panel.includes("历史数据回放不等于实时监测") &&
    panel.includes("诊断历史") &&
    panel.includes("查看证据") &&
    panel.includes("建议补测") &&
    panel.includes("去上传振动 CSV") &&
    panel.includes("同步多通道") &&
    panel.includes("其他测量证据") &&
    panel.includes("登记结构化证据") &&
    panel.includes("新增测量") &&
    panel.includes("测量数据") &&
    panel.includes("物理校验") &&
    panel.includes("UNSUPPORTED_UPLOAD"),
)

const baseHistory: DiagnosisSummary = {
  assessment_id: "a1",
  assessment_no: 2,
  record_id: "REC-TEMP-1",
  device_id: "MM-01",
  created_at: "2026-04-03T11:06:00+08:00",
  trace_id: "",
  diagnosis_level: "diagnostic_clue",
  stop_reason: "连续两轮没有新增有效证据",
  model_called: false,
  confirmed: false,
  no_hypothesis: false,
  change_lines: ["轴承磨损：未验证 V1 → 被削弱 V2"],
  hypotheses: [{ fault: "轴承磨损", version: 2, verification_status: "被削弱" }],
  measurements: [{ measurement_id: "m-1", content_sha256: "abc", measured_at: "2026-04-03 11:05:00" }],
  missing_measurements: ["相位"],
  l2_status: "numeric_contradiction",
}
const changedLines = timelineText(baseHistory)
const unchangedLines = timelineText({ ...baseHistory, assessment_no: 3, change_lines: [UNCHANGED_VERSION], no_hypothesis: false })
const emptyLines = timelineText({
  ...baseHistory,
  assessment_no: 1,
  diagnosis_level: "diagnostic_clue",
  no_hypothesis: true,
  change_lines: [NO_HYPOTHESIS],
  hypotheses: [],
  l2_status: "no_testable_hypothesis",
  missing_measurements: ["相位"],
})
check(
  "时间线显示版本变化、保持不变和没有假设",
  changedLines.includes("轴承磨损：未验证 V1 → 被削弱 V2") &&
    changedLines.some((line) => line.includes("补充测量 2026-04-03 11:05:00 m-1")) &&
    changedLines.includes("重新校验结果：数值矛盾") &&
    changedLines.includes("停止原因：连续两轮没有新增有效证据") &&
    unchangedLines.includes(UNCHANGED_VERSION) &&
    emptyLines.includes("初次分析结果：诊断线索") &&
    emptyLines.includes(NO_HYPOTHESIS) &&
    emptyLines.some((line) => line.includes("缺失测量：相位")),
)
check("证据文本保留来源", evidenceText({ evidence_id: "L2-speed", source: "validate_research_physics", detail: "转速矛盾" }).includes("validate_research_physics"))

const phaseTask: FollowupTask = {
  task_id: "phase",
  record_id: "REC-TEMP-1",
  device_id: "MM-01",
  item_id: "phase_stability",
  title: "相位",
  measurement_item: "带键相参考的 1X 相位稳定性，以及负荷变化下的幅值",
  purpose: "核对相位是否稳定",
  missing_evidence: "没有相位数据",
  knowledge_ref: "KB-02_故障模式诊断知识库.md 第36行：1X相位稳定，幅值随负荷变化",
  data_type: "同步多通道振动 CSV；键相通道目前仍缺失",
  verifiable_condition: "1X 相位是否稳定",
  priority: "同一条款未核对条件",
  priority_basis: "这是规则上的建议顺序，不是工业风险等级，也没有估计采集成本。",
  status: "已校验但条件不足",
  gap_class: "reference_mismatch",
  upload_supported: true,
  upload_mode: "synchronous",
  measurement_ids: ["sync-1"],
  condition_text: "缺少键相参考，不能生成绝对相位。",
}
const axialTask: FollowupTask = {
  ...phaseTask,
  task_id: "axial",
  item_id: "axial_eccentricity",
  title: "轴向偏心测量",
  upload_supported: false,
  upload_mode: "unsupported",
  measurement_ids: [],
  status: "待补测",
  condition_text: "X/Y 振动不能代替轴向偏心。",
}
const csvTask: FollowupTask = {
  ...phaseTask,
  task_id: "dominant",
  item_id: "dominant_1x",
  title: "1X是否为频谱主导峰",
  upload_supported: true,
  status: "已获得可用证据",
  measurement_ids: ["m-1"],
  condition_text: "补充前，官方检查 dominant_1x 是数据不足。补充后，supplement_dominant_1x 为 consistent。",
}
const phaseLines = followupLines(phaseTask)
const axialLines = followupLines(axialTask)
const csvLines = followupLines(csvTask)
check(
  "补测任务展示状态、来源和不支持的上传",
  !phaseLines.includes(UNSUPPORTED_UPLOAD) &&
    phaseLines.some((line) => line.includes("同步多通道 CSV")) &&
    phaseLines.some((line) => line.includes("KB-02_故障模式诊断知识库.md 第36行")) &&
    phaseLines.some((line) => line.includes("当前状态：已校验但条件不足")) &&
    axialLines.includes(UNSUPPORTED_UPLOAD) &&
    csvLines.some((line) => line.includes("已关联补充测量：m-1")) &&
    csvLines.some((line) => line.includes("已获得数值证据")) &&
    !csvLines.includes(UNSUPPORTED_UPLOAD),
)
const syncItem: SyncMeasurement = {
  measurement_id: "sync-1",
  record_id: "REC-TEMP-1",
  device_id: "MM-01",
  measured_at: "2026-04-03 11:05:00",
  sampling_rate_hz: 256,
  unit: "mm/s",
  file_name: "sync.csv",
  content_sha256: "abc",
  data_origin: "synthetic_test_signal",
  origin_note: "合成测试信号，只用于验证程序，不是工厂测量。",
  sync_method: "shared_time_column",
  sync_method_note: "同一 CSV 的共享时间列。不同文件没有被当成同步测量。",
  time_base: "relative_seconds",
  channel_count: 2,
  channels: [
    { column: "x", name: "X", direction: "X", sensor_position: "驱动端水平" },
    { column: "y", name: "Y", direction: "Y", sensor_position: "驱动端垂直" },
  ],
  phase: {
    phase_reliable: true,
    interchannel_phase_computed: true,
    phase_difference_deg: 90,
    absolute_phase_deg: null,
    keyphasor_present: false,
    reason: "缺少键相参考。通道间 1X 相位差不是相对于键相的绝对相位，也不能把 KB-02 相位条款标成已满足。",
    quality: "两路 1X 都落在转频对应的频率仓内",
    channels: [
      { name: "X", frequency_1x_hz: 25, amplitude_1x: 1, reliable_1x: true },
      { name: "Y", frequency_1x_hz: 25, amplitude_1x: 1, reliable_1x: true },
    ],
  },
}
const syncLines = syncPhaseLines(syncItem)
check(
  "同步测量展示相位差、质量和缺少键相",
  syncLines.some((line) => line.includes("通道间 1X 相位差：90°")) &&
    syncLines.some((line) => line.includes("缺少键相参考")) &&
    syncLines.some((line) => line.includes("绝对相位：无")) &&
    syncLines.some((line) => line.includes("测量编号 sync-1")),
)
const evidenceItem: DiagnosticEvidence = {
  evidence_id: "e1",
  evidence_kind: "axial_eccentricity",
  quantity_name: "axial_eccentricity",
  unit: "um",
  sensor_position: "轴向探头",
  method: "runout_measurement",
  reference_basis: "shaft_axis",
  measured_at: "2026-04-03 11:05:00",
  content_sha256: "abc",
  registration_status: "已登记",
  validation_status: "可计算",
  validation_note: "已登记轴向偏心。不能确诊。",
  origin_note: "合成测试数据，只用于验证程序，不是工厂测量。",
  values: { value: 15 },
  confirmed: false,
}
const evidenceTextLines = evidenceLines(evidenceItem)
check(
  "其他证据展示数值、状态和来源",
  evidenceTextLines.some((line) => line.includes("轴向偏心或轴向跳动")) &&
    evidenceTextLines.some((line) => line.includes("数值：15 μm")) &&
    evidenceTextLines.some((line) => line.includes("已获得数值证据")) &&
    evidenceTextLines.some((line) => line.includes("轴向跳动测量")) &&
    evidenceTextLines.some((line) => line.includes("轴线参考基准")) &&
    evidenceTextLines.some((line) => line.includes("故障尚未确诊")),
)

check(
  "面板使用四标签页并收起复杂表单",
  panel.includes('role="tablist"') &&
    panel.includes("建议补测") &&
    panel.includes("测量数据") &&
    panel.includes("物理校验") &&
    panel.includes("诊断历史") &&
    panel.includes("新增测量") &&
    panel.includes("模拟测试数据") &&
    panel.includes("用户补充数据") &&
    panel.includes("故障尚未确诊"),
)

check(
  "合成测试文件显示为模拟测试数据",
  originLabel("synthetic_test_signal", "合成测试信号", "integration_synthetic_1ch.csv") === "模拟测试数据" &&
    originLabel("uploaded_csv", "登记的补充测量", "wave.csv") === "用户补充数据",
)

console.log(`RESULT passed=${results.filter(Boolean).length} failed=${results.length - results.filter(Boolean).length}`)
if (results.some((item) => !item)) {
  process.exit(1)
}
