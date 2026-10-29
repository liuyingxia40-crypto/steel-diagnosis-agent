import type {
  AnalysisDeviation,
  AnalysisLevel,
  AnalysisMetric,
  CandidateFault,
  DatasetAnalysis,
  DatasetDevice,
  MonitoringEventList,
  MonitoringProcessResponse,
  StoredMonitorEvent,
  StoredMonitorMetric,
  DatasetDevices,
  DatasetObservation,
  DatasetRecord,
  DatasetRecords,
  DatasetSheet,
  DatasetSummary,
  DiagnosisEvidenceSource,
  DiagnosticClue,
} from "../types/api"

export const ANSWER_LABELS = [
  "设备状态",
  "故障类型",
  "故障严重度",
  "故障位置",
  "诊断置信度",
  "频谱描述",
  "根因分析",
  "建议措施",
  "优先级",
  "影响程度",
] as const

const ANSWER_LABEL_SET = new Set<string>(ANSWER_LABELS)

export const OBSERVATION_FIELDS = [
  "转速(rpm)",
  "转频(Hz)",
  "电机电流(A)",
  "X向振动(mm/s)",
  "Y向振动(mm/s)",
  "最大振动(mm/s)",
  "振动均方根",
  "驱动端轴承温度(°C)",
  "非驱动端轴承温度(°C)",
  "包络谱峰值(g)",
  "BPFO幅值(g)",
  "BPFI幅值(g)",
  "BSF幅值(g)",
  "啮合频率幅值(g)",
  "1X转频幅值(mm/s)",
  "BPFO频率(Hz)",
  "BPFI频率(Hz)",
  "BSF频率(Hz)",
  "啮合频率(Hz)",
] as const

export const PRIMARY_FIELDS = [
  "X向振动(mm/s)",
  "Y向振动(mm/s)",
  "振动均方根",
  "电机电流(A)",
  "驱动端轴承温度(°C)",
  "非驱动端轴承温度(°C)",
  "转速(rpm)",
] as const

export const PLAYBACK_FIELDS = [
  "X向振动(mm/s)",
  "Y向振动(mm/s)",
  "驱动端轴承温度(°C)",
  "非驱动端轴承温度(°C)",
  "电机电流(A)",
  "转速(rpm)",
] as const

export const CHART_MODES = [
  { id: "vibration", label: "X/Y向振动", fields: ["X向振动(mm/s)", "Y向振动(mm/s)"] },
  { id: "temperature", label: "轴承温度", fields: ["驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"] },
  { id: "current", label: "电机电流", fields: ["电机电流(A)"] },
] as const

export type ChartModeId = (typeof CHART_MODES)[number]["id"]

export interface DeviceGroup {
  category: string
  devices: DatasetDevice[]
}

export interface ChartPoint {
  recordId: string
  collectedAt: string
  values: Array<number | null>
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value)
}

function isTextOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string"
}

function isMetricMap(value: unknown): value is Record<string, number | null> {
  if (!isObject(value)) {
    return false
  }
  return Object.values(value).every(
    (item) => item === null || (typeof item === "number" && Number.isFinite(item)),
  )
}

function isSheet(value: unknown): value is DatasetSheet {
  if (!isObject(value)) {
    return false
  }
  return (
    typeof value.sheet_name === "string" &&
    typeof value.equipment_category === "string" &&
    typeof value.sheet_name_matches_category === "boolean" &&
    isInteger(value.device_count) &&
    isInteger(value.record_count) &&
    isTextOrNull(value.time_start) &&
    isTextOrNull(value.time_end) &&
    isInteger(value.duplicate_timestamp_groups)
  )
}

export function isDatasetRecord(value: unknown): value is DatasetRecord {
  if (!isObject(value) || !isMetricMap(value.values)) {
    return false
  }
  return (
    typeof value.record_id === "string" &&
    isInteger(value.excel_row) &&
    typeof value.sheet_name === "string" &&
    typeof value.device_id === "string" &&
    typeof value.equipment_category === "string" &&
    typeof value.collected_at === "string" &&
    typeof value.duplicate_timestamp === "boolean" &&
    isInteger(value.same_timestamp_count)
  )
}

export function isDatasetSummary(value: unknown): value is DatasetSummary {
  if (!isObject(value) || !Array.isArray(value.sheets) || !value.sheets.every(isSheet)) {
    return false
  }
  return (
    typeof value.source_name === "string" &&
    isInteger(value.sheet_count) &&
    isInteger(value.device_count) &&
    isInteger(value.record_count)
  )
}

function isDevice(value: unknown): value is DatasetDevice {
  if (!isObject(value)) {
    return false
  }
  return (
    typeof value.device_id === "string" &&
    typeof value.equipment_category === "string" &&
    typeof value.sheet_name === "string" &&
    isInteger(value.record_count) &&
    isTextOrNull(value.time_start) &&
    isTextOrNull(value.time_end) &&
    isInteger(value.duplicate_timestamp_groups)
  )
}

export function isDatasetDevices(value: unknown): value is DatasetDevices {
  if (!isObject(value) || !Array.isArray(value.devices) || !value.devices.every(isDevice)) {
    return false
  }
  return isInteger(value.device_count)
}

export function isDatasetRecords(value: unknown): value is DatasetRecords {
  if (!isObject(value) || !Array.isArray(value.records) || !value.records.every(isDatasetRecord)) {
    return false
  }
  return (
    typeof value.found === "boolean" &&
    typeof value.device_id === "string" &&
    (value.equipment_category === null || typeof value.equipment_category === "string") &&
    (value.sheet_name === null || typeof value.sheet_name === "string") &&
    isInteger(value.matched_count) &&
    isInteger(value.returned_count) &&
    isInteger(value.limit) &&
    typeof value.truncated === "boolean" &&
    isInteger(value.duplicate_timestamp_groups)
  )
}

export function isDatasetObservation(value: unknown): value is DatasetObservation {
  if (!isObject(value) || value.found !== true) {
    return false
  }
  return isDatasetRecord(value.record)
}

export function categoryCount(summary: DatasetSummary): number {
  return new Set(summary.sheets.map((sheet) => sheet.equipment_category)).size
}

export function groupDevices(categoryOrder: string[], devices: DatasetDevice[]): DeviceGroup[] {
  const order: string[] = []
  const buckets = new Map<string, DatasetDevice[]>()
  function bucket(category: string): DatasetDevice[] {
    const existing = buckets.get(category)
    if (existing) {
      return existing
    }
    const created: DatasetDevice[] = []
    buckets.set(category, created)
    order.push(category)
    return created
  }
  for (const category of categoryOrder) {
    bucket(category)
  }
  for (const device of devices) {
    bucket(device.equipment_category).push(device)
  }
  return order
    .map((category) => ({ category, devices: buckets.get(category) ?? [] }))
    .filter((group) => group.devices.length > 0)
}

export function parseLimit(value: string): number | null {
  const text = value.trim()
  if (!/^[1-9]\d{0,2}$/.test(text)) {
    return null
  }
  const parsed = Number(text)
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
    return null
  }
  return parsed
}

export function truncationText(returnedCount: number, matchedCount: number, truncated: boolean): string {
  if (truncated) {
    return `本次仅显示前${returnedCount}条记录，共匹配${matchedCount}条。单次最多返回100条，当前结果按接口顺序从较早的采集时间开始。可以缩小时间范围后再查询。`
  }
  if (matchedCount === 0) {
    return "没有匹配的观测记录。"
  }
  return `本次显示全部匹配的${returnedCount}条记录。`
}

export function hasDuplicateTime(record: Pick<DatasetRecord, "duplicate_timestamp" | "same_timestamp_count">): boolean {
  return record.duplicate_timestamp || record.same_timestamp_count > 1
}

export function formatCell(values: Record<string, number | null>, key: string): string {
  if (!Object.prototype.hasOwnProperty.call(values, key)) {
    return "无此字段"
  }
  const value = values[key]
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "无数据"
  }
  return String(value)
}

export function visibleMetrics(values: Record<string, number | null>): Array<[string, number | null]> {
  const known = OBSERVATION_FIELDS.filter((field) => Object.prototype.hasOwnProperty.call(values, field))
  const extras = Object.keys(values).filter((key) => !ANSWER_LABEL_SET.has(key) && !known.includes(key as (typeof OBSERVATION_FIELDS)[number]))
  return [...known, ...extras].map((key) => {
    const value = values[key]
    return [key, typeof value === "number" && Number.isFinite(value) ? value : null]
  })
}

export function chartPoints(records: readonly DatasetRecord[], fields: readonly string[]): ChartPoint[] {
  return records.map((record) => ({
    recordId: record.record_id,
    collectedAt: record.collected_at,
    values: fields.map((field) => {
      const value = record.values[field]
      return typeof value === "number" && Number.isFinite(value) ? value : null
    }),
  }))
}

export const VIBRATION_GROUPS = [
  {
    id: "speed",
    label: "振动速度",
    unit: "mm/s",
    fields: ["X向振动(mm/s)", "Y向振动(mm/s)", "最大振动(mm/s)", "1X转频幅值(mm/s)"],
  },
  {
    id: "amplitude",
    label: "频谱幅值",
    unit: "g",
    fields: ["包络谱峰值(g)", "BPFO幅值(g)", "BPFI幅值(g)", "BSF幅值(g)", "啮合频率幅值(g)"],
  },
  {
    id: "rms",
    label: "振动均方根",
    unit: "",
    fields: ["振动均方根"],
  },
] as const

export const TEMPERATURE_FIELDS = ["驱动端轴承温度(°C)", "非驱动端轴承温度(°C)"] as const
export const SPEED_FIELDS = ["转速(rpm)"] as const

export type VibrationGroupId = (typeof VIBRATION_GROUPS)[number]["id"]
export type AnalyzedWindowMode = "history" | "progress" | "complete" | "waiting" | "outside"

export interface AnalyzedWindow<T> {
  records: T[]
  mode: AnalyzedWindowMode
}

export function cockpitStatusLabel(status: string | null | undefined): string {
  if (status === "运行中" || status === "正在停止") {
    return "正在分析"
  }
  if (status === "已停止") {
    return "已停止"
  }
  if (status === "已结束") {
    return "已完成"
  }
  if (status === "已出错" || status === "已中断") {
    return "运行出错"
  }
  return "尚未开始"
}

export function analyzedRecordWindow<T extends { record_id: string }>(
  records: readonly T[],
  task: { status: string; processed_count: number; current_record_id: string } | null,
  unfilteredPrefix: boolean,
): AnalyzedWindow<T> {
  if (!task) {
    return { records: [...records], mode: "history" }
  }
  if (task.status === "已结束") {
    return { records: [...records], mode: "complete" }
  }
  if (task.processed_count <= 0) {
    return { records: [], mode: "waiting" }
  }
  const index = records.findIndex((item) => item.record_id === task.current_record_id)
  if (index >= 0) {
    return { records: records.slice(0, index + 1), mode: "progress" }
  }
  if (unfilteredPrefix && records.length > 0 && task.processed_count >= records.length) {
    return { records: [...records], mode: "progress" }
  }
  return { records: [], mode: "outside" }
}

export function fieldsWithValues(records: readonly DatasetRecord[], fields: readonly string[]): string[] {
  return fields.filter((field) =>
    records.some((record) => {
      const value = record.values[field]
      return typeof value === "number" && Number.isFinite(value)
    }),
  )
}

export function deviationMarkers(
  records: readonly { record_id: string }[],
  events: readonly Pick<StoredMonitorEvent, "record_id" | "event_id">[],
): Array<{ recordId: string; eventId: number }> {
  const visible = new Set(records.map((item) => item.record_id))
  const seen = new Set<string>()
  const markers: Array<{ recordId: string; eventId: number }> = []
  for (const event of events) {
    if (!visible.has(event.record_id) || seen.has(event.record_id)) {
      continue
    }
    seen.add(event.record_id)
    markers.push({ recordId: event.record_id, eventId: event.event_id })
  }
  return markers
}

export interface DiagnosisStep {
  title: string
  done: boolean
  detail: string
}

export function diagnosisSteps(event: StoredMonitorEvent | null): DiagnosisStep[] {
  if (!event || !diagnosisDetailAvailable(event)) {
    return [
      {
        title: "发现数据变化",
        done: Boolean(event && event.metrics.length > 0),
        detail: event && event.metrics.length > 0 ? "已保存统计偏离指标。" : "还没有可展示的诊断详情。",
      },
      { title: "检索相关诊断知识", done: false, detail: "没有已保存的知识来源。" },
      { title: "核对证据", done: false, detail: "没有已保存的核对结果。" },
      { title: "输出诊断线索", done: false, detail: HISTORICAL_DIAGNOSIS_NOTE },
    ]
  }
  const sources = event.evidence_sources ?? []
  const missing = event.missing_conditions ?? []
  const clueCount = event.diagnostic_clues.length
  return [
    {
      title: "发现数据变化",
      done: event.metrics.length > 0,
      detail: event.metrics.length > 0 ? `${event.metrics.length} 项指标偏离历史水平。` : "事件里没有偏离指标。",
    },
    {
      title: "检索相关诊断知识",
      done: sources.length > 0,
      detail: sources.length > 0 ? sources.map((item) => `${item.document} 第${item.line_number}行`).join("；") : "没有已保存的知识来源。",
    },
    {
      title: "核对证据",
      done: clueCount > 0 || missing.length > 0,
      detail: missing.length > 0 ? `还有 ${missing.length} 项数据没有满足。` : "已保存的核对里没有列出缺失条件。",
    },
    {
      title: "输出诊断线索",
      done: clueCount > 0,
      detail: clueCount > 0 ? event.diagnosis_status || "已保存诊断线索。" : event.diagnosis_status || "没有形成诊断线索。",
    },
  ]
}

export function clueMeasurementLines(clue: DiagnosticClue): string[] {
  const observed = clue.observed_fields
  if (!Array.isArray(observed)) {
    return []
  }
  return observed.flatMap((item) => {
    if (typeof item !== "object" || item === null || typeof item.field !== "string" || item.field.trim() === "") {
      return []
    }
    const value = typeof item.value === "number" && Number.isFinite(item.value) ? String(item.value) : "无数据"
    const unit = typeof item.unit === "string" && item.unit ? item.unit : ""
    return [`${item.field} ${value}${unit ? " " + unit : ""}`]
  })
}

export const ANALYSIS_LEVEL_TEXT: Record<AnalysisLevel, string> = {
  statistical_deviation: "统计偏离。这只说明指标相对历史基线的偏离，不是某种故障已经发生。",
  diagnostic_clue: "部分诊断线索。下面只展示实际返回的线索和未满足的条件，不能据此认定具体故障。",
  possible_match: "可能故障匹配。这不是故障确诊。",
  insufficient_evidence: "证据不足，不能形成具体故障判断。",
  unable_to_evaluate: "无法评估。现有数据或工况不足以判断，不能写成设备正常。",
}

export const EXPERIMENTAL_LIMIT_NOTE = "当前采用实验性统计偏离判据，不是官方设备报警限值。"
export const ANALYSIS_SCOPE_NOTE = "官方开发集历史记录辅助分析，不代表真实传感器实时监测，也不代表故障确诊。"
export const EMPTY_CANDIDATE_TEXT = "当前观测数据不足以支持具体故障判断。"
export const HISTORICAL_DIAGNOSIS_NOTE = "该历史事件暂无诊断详情"
export const NOT_CONFIRMED_TEXT = "尚未确诊"
export const EMPTY_EVENT_FAULT_TEXT = "没有候选故障，页面不会生成故障名称。"
export const MISSING_MEASUREMENT_TEXT = "数据不足，现有记录没有完整频谱和测量条件。"

export function eventDiagnosisLabel(level: AnalysisLevel): string {
  if (level === "diagnostic_clue") {
    return "诊断线索"
  }
  return ANALYSIS_LEVEL_TEXT[level]
}

export function diagnosisDetailAvailable(event: StoredMonitorEvent): boolean {
  if (event.diagnosis_available === false) {
    return false
  }
  return (
    typeof event.diagnosis_status === "string" &&
    event.diagnosis_status.trim() !== "" &&
    Array.isArray(event.evidence_sources) &&
    Array.isArray(event.missing_conditions) &&
    event.confirmed === false
  )
}

const ANALYSIS_LEVEL_SET = new Set<string>(Object.keys(ANALYSIS_LEVEL_TEXT))

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value))
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
}

function isAnalysisMetric(value: unknown): value is AnalysisMetric {
  if (!isObject(value)) {
    return false
  }
  return (
    typeof value.field === "string" &&
    (value.unit === null || typeof value.unit === "string") &&
    isNullableNumber(value.current_value) &&
    isNullableNumber(value.median) &&
    isNullableNumber(value.mad) &&
    isNullableNumber(value.robust_z) &&
    typeof value.metric_status === "string" &&
    typeof value.reason === "string"
  )
}

function isDiagnosticClue(value: unknown): value is DiagnosticClue {
  if (!isObject(value)) {
    return false
  }
  return (
    typeof value.feature_code === "string" &&
    typeof value.related_fault_section === "string" &&
    typeof value.document === "string" &&
    isInteger(value.line_number) &&
    typeof value.partial_condition_met === "boolean" &&
    typeof value.complete_condition_met === "boolean" &&
    typeof value.unverified_reason === "string"
  )
}

function isEvidenceSource(value: unknown): value is DiagnosisEvidenceSource {
  if (!isObject(value)) {
    return false
  }
  return (
    typeof value.document === "string" &&
    value.document.trim() !== "" &&
    isInteger(value.line_number) &&
    typeof value.feature_code === "string" &&
    typeof value.related_fault_section === "string"
  )
}

function storedDiagnosisFieldsOk(value: Record<string, unknown>): boolean {
  if ("diagnosis_available" in value && typeof value.diagnosis_available !== "boolean") {
    return false
  }
  if ("diagnosis_status" in value && typeof value.diagnosis_status !== "string") {
    return false
  }
  if ("confirmed" in value && value.confirmed !== false) {
    return false
  }
  if ("evidence_sources" in value && (!Array.isArray(value.evidence_sources) || !value.evidence_sources.every(isEvidenceSource))) {
    return false
  }
  if ("missing_conditions" in value && !isStringList(value.missing_conditions)) {
    return false
  }
  if (value.diagnosis_available === true) {
    return (
      typeof value.diagnosis_status === "string" &&
      value.diagnosis_status.trim() !== "" &&
      Array.isArray(value.evidence_sources) &&
      Array.isArray(value.missing_conditions) &&
      value.confirmed === false
    )
  }
  return true
}

function isCandidateFault(value: unknown): value is CandidateFault {
  if (!isObject(value)) {
    return false
  }
  return typeof value.fault === "string" && value.fault.trim() !== "" && value.confirmed === false && typeof value.note === "string"
}

export function isDatasetAnalysis(value: unknown): value is DatasetAnalysis {
  if (!isObject(value) || value.ok !== true || value.confirmed !== false || !isDatasetRecord(value.observation)) {
    return false
  }
  const deviation = value.statistical_deviation
  if (
    !isObject(deviation) ||
    typeof deviation.detection_status !== "string" ||
    deviation.detection_status.trim() === "" ||
    !isInteger(deviation.history_sample_count) ||
    !isStringList(deviation.anomalous_metrics) ||
    !Array.isArray(deviation.analyzed_metrics) ||
    !deviation.analyzed_metrics.every(isAnalysisMetric) ||
    !isObject(deviation.experimental_threshold) ||
    typeof deviation.experimental_threshold.note !== "string" ||
    !isObject(deviation.operating_condition) ||
    typeof deviation.operating_condition.comparable !== "boolean"
  ) {
    return false
  }
  return (
    typeof value.record_id === "string" &&
    value.observation.record_id === value.record_id &&
    typeof value.diagnosis_level === "string" &&
    ANALYSIS_LEVEL_SET.has(value.diagnosis_level) &&
    typeof value.data_source === "string" &&
    typeof value.analysis_note === "string" &&
    Array.isArray(value.diagnostic_clues) &&
    value.diagnostic_clues.every(isDiagnosticClue) &&
    Array.isArray(value.candidate_faults) &&
    value.candidate_faults.every(isCandidateFault) &&
    isStringList(value.missing_data) &&
    isStringList(value.insufficient_reasons) &&
    isStringList(value.warnings)
  )
}

export function formatAnalysisNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "无数据"
  }
  if (Number.isInteger(value)) {
    return String(value)
  }
  const abs = Math.abs(value)
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-4)) {
    return value.toExponential(3)
  }
  const text = value.toFixed(4).replace(/\.?0+$/, "")
  return text === "-0" ? "0" : text
}

export function formatMeasurement(value: unknown, unit: unknown): string {
  const number = formatAnalysisNumber(value)
  if (number === "无数据" || typeof unit !== "string" || unit.trim() === "") {
    return number
  }
  return `${number} ${unit}`
}

export function shouldApplyAnalysis(requestRecordId: string, selectedRecordId: string, responseRecordId: string): boolean {
  return requestRecordId !== "" && requestRecordId === selectedRecordId && requestRecordId === responseRecordId
}

export function analysisLevelText(level: AnalysisLevel): string {
  return ANALYSIS_LEVEL_TEXT[level]
}

export function metricsForFields(deviation: AnalysisDeviation, fields: readonly string[]): Array<AnalysisMetric | null> {
  return fields.map((field) => deviation.analyzed_metrics.find((item) => item.field === field) ?? null)
}

export function unresolvedMetrics(deviation: AnalysisDeviation): AnalysisMetric[] {
  return deviation.analyzed_metrics.filter((item) => item.metric_status === "基线不足" || item.metric_status === "无法判定")
}

export type PlaybackSpeed = 1 | 2
export type PlaybackStatus = "idle" | "playing" | "paused" | "finished"

export interface PlaybackState {
  status: PlaybackStatus
  index: number
  speed: PlaybackSpeed
}

export const PLAYBACK_SCOPE_NOTE = "官方开发集历史数据回放，不代表真实设备传感器实时采集。"

export function initialPlayback(): PlaybackState {
  return { status: "idle", index: 0, speed: 1 }
}

export function playbackIntervalMs(speed: PlaybackSpeed): number {
  return 1000 / speed
}

export function startPlayback(state: PlaybackState, length: number): PlaybackState {
  if (length <= 0) {
    return { ...state, status: "idle", index: 0 }
  }
  const restart = state.status === "idle" || state.status === "finished" || state.index <= 0 || state.index >= length
  const index = restart ? 1 : state.index
  if (index >= length) {
    return { ...state, status: "finished", index: length }
  }
  return { ...state, status: "playing", index }
}

export function pausePlayback(state: PlaybackState): PlaybackState {
  if (state.status !== "playing") {
    return state
  }
  return { ...state, status: "paused" }
}

export function resumePlayback(state: PlaybackState, length: number): PlaybackState {
  if (state.status !== "paused") {
    return state
  }
  if (state.index <= 0 || state.index >= length) {
    return { ...state, status: "finished", index: Math.max(0, Math.min(state.index, length)) }
  }
  return { ...state, status: "playing" }
}

export function resetPlayback(state: PlaybackState): PlaybackState {
  return { status: "idle", index: 0, speed: state.speed }
}

export function tickPlayback(state: PlaybackState, length: number): PlaybackState {
  if (state.status !== "playing" || length <= 0 || state.index >= length) {
    return state.status === "playing" ? { ...state, status: "finished", index: Math.min(state.index, length) } : state
  }
  const index = state.index + 1
  return { ...state, index, status: index >= length ? "finished" : "playing" }
}

export function changePlaybackSpeed(state: PlaybackState, speed: PlaybackSpeed): PlaybackState {
  return { ...state, speed }
}

export function visiblePlaybackRecords<T>(records: readonly T[], state: PlaybackState): readonly T[] {
  if (state.status === "idle") {
    return records
  }
  return records.slice(0, Math.max(0, Math.min(state.index, records.length)))
}

export function playbackScopeText(returnedCount: number, matchedCount: number, truncated: boolean): string {
  if (truncated || matchedCount > returnedCount) {
    return `历史回放只覆盖当前已加载的 ${returnedCount} 条演示窗口；该设备筛选条件下共匹配 ${matchedCount} 条。超过已加载窗口的记录请用上方历史查询分页打开，不要把回放当成全部历史。`
  }
  return `历史回放覆盖当前已加载的 ${returnedCount} 条记录；这仍是演示窗口，不等于跨设备的全部官方历史。`
}

export function advancePlayback(state: PlaybackState, length: number, elapsedMs: number): PlaybackState {
  if (state.status !== "playing" || elapsedMs <= 0) {
    return state
  }
  const steps = Math.floor(elapsedMs / playbackIntervalMs(state.speed))
  let next = state
  for (let step = 0; step < steps; step += 1) {
    next = tickPlayback(next, length)
  }
  return next
}

export const MONITOR_EVENT_NOTE = "统计偏离事件采用实验性判据，不等于官方设备报警，也不代表故障确诊。"

export type MonitorPhase = "idle" | "analyzing" | "waiting" | "paused" | "finished"

export function monitorPhaseText(phase: MonitorPhase): string {
  if (phase === "analyzing") {
    return "正在分析"
  }
  if (phase === "waiting") {
    return "正在按目标速度等待下一条"
  }
  if (phase === "paused") {
    return "已暂停"
  }
  if (phase === "finished") {
    return "回放检测结束"
  }
  return "未开始检测"
}

export function playbackLevelLabel(level: AnalysisLevel): string {
  if (level === "diagnostic_clue") {
    return "部分诊断线索"
  }
  return analysisLevelText(level)
}

export interface MonitorEventMetric {
  field: string
  metricStatus: string
  currentText: string
  medianText: string
  robustZText: string
}

export interface MonitorEvent {
  id: string
  recordId: string
  deviceId: string
  collectedAt: string
  category: "统计偏离" | "分析失败"
  metrics: MonitorEventMetric[]
  diagnosisLevel: AnalysisLevel | null
  levelLabel: string
  clues: DiagnosticClue[]
  candidateFaults: CandidateFault[]
  source: string
  message: string
  analysis: DatasetAnalysis | null
}

export type PlaybackAnalysisAction =
  | { action: "analyze"; recordId: string }
  | { action: "wait"; recordId: string; waitMs: number }
  | { action: "hold" }
  | { action: "done" }

export function playbackAnalysisAction(
  state: PlaybackState,
  records: readonly { record_id: string }[],
  processedIds: readonly string[],
  inFlightRecordId: string | null,
): PlaybackAnalysisAction {
  if (state.status === "idle" || state.index <= 0) {
    return { action: "done" }
  }
  const current = records[state.index - 1]
  if (!current) {
    return { action: "done" }
  }
  if (state.status === "paused" || inFlightRecordId !== null) {
    return { action: "hold" }
  }
  if (!processedIds.includes(current.record_id)) {
    return { action: "analyze", recordId: current.record_id }
  }
  if (state.status !== "playing") {
    return { action: "done" }
  }
  return { action: "wait", recordId: current.record_id, waitMs: playbackIntervalMs(state.speed) }
}

export function deviationMetrics(analysis: DatasetAnalysis): AnalysisMetric[] {
  return analysis.statistical_deviation.analyzed_metrics.filter((item) => item.metric_status === "偏离基线")
}

export function playbackCheckStatus(analysis: DatasetAnalysis, createdDeviation: boolean): string {
  const detection = analysis.statistical_deviation.detection_status
  if (createdDeviation) {
    return `已记录统计偏离。统计检测状态：${detection}。`
  }
  if (detection === "基线不足" || detection === "无法判定") {
    return `统计检测状态：${detection}。这不是设备正常，也没有生成统计偏离事件。`
  }
  return `统计检测状态：${detection}。没有指标返回偏离基线，不能写成设备绝对健康。`
}

export interface PlaybackCheckInput {
  requestSessionId: number
  currentSessionId: number
  record: Pick<DatasetRecord, "record_id" | "device_id" | "collected_at">
  processedIds: readonly string[]
  events: readonly MonitorEvent[]
}

export interface PlaybackCheckResult {
  accepted: boolean
  processedIds: string[]
  events: MonitorEvent[]
  statusText: string
}

function unchangedCheck(input: PlaybackCheckInput, statusText: string): PlaybackCheckResult {
  return {
    accepted: false,
    processedIds: [...input.processedIds],
    events: [...input.events],
    statusText,
  }
}

function analysisMatchesRecord(record: PlaybackCheckInput["record"], analysis: DatasetAnalysis): boolean {
  return (
    analysis.record_id === record.record_id &&
    analysis.observation.record_id === record.record_id &&
    analysis.observation.device_id === record.device_id &&
    analysis.observation.collected_at === record.collected_at
  )
}

function nextEventId(sessionId: number, events: readonly MonitorEvent[]): string {
  return `M${sessionId}-${events.length + 1}`
}

export function applyPlaybackSuccess(input: PlaybackCheckInput, analysis: DatasetAnalysis): PlaybackCheckResult {
  if (input.requestSessionId !== input.currentSessionId || input.processedIds.includes(input.record.record_id)) {
    return unchangedCheck(input, "")
  }
  if (!analysisMatchesRecord(input.record, analysis)) {
    return unchangedCheck(input, "")
  }
  const metrics = deviationMetrics(analysis).map((item) => ({
    field: item.field,
    metricStatus: item.metric_status,
    currentText: formatMeasurement(item.current_value, item.unit),
    medianText: formatMeasurement(item.median, item.unit),
    robustZText: formatAnalysisNumber(item.robust_z),
  }))
  const events = [...input.events]
  if (metrics.length > 0) {
    events.push({
      id: nextEventId(input.requestSessionId, input.events),
      recordId: input.record.record_id,
      deviceId: input.record.device_id,
      collectedAt: input.record.collected_at,
      category: "统计偏离",
      metrics,
      diagnosisLevel: analysis.diagnosis_level,
      levelLabel: playbackLevelLabel(analysis.diagnosis_level),
      clues: analysis.diagnostic_clues,
      candidateFaults: analysis.candidate_faults,
      source: analysis.data_source,
      message: analysis.analysis_note,
      analysis,
    })
  }
  return {
    accepted: true,
    processedIds: [...input.processedIds, input.record.record_id],
    events,
    statusText: playbackCheckStatus(analysis, metrics.length > 0),
  }
}

const PLAYBACK_SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/

export function createPlaybackSessionId(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  const id = uuid ? `pb-${uuid}` : `pb-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e12).toString(36)}`
  if (!PLAYBACK_SESSION_PATTERN.test(id)) {
    throw new Error("回放会话标识无法生成。")
  }
  return id
}

function isStoredMetric(value: unknown): value is StoredMonitorMetric {
  if (!isObject(value) || typeof value.field !== "string" || value.field.trim() === "") {
    return false
  }
  return (
    (value.unit === null || typeof value.unit === "string") &&
    isNullableNumber(value.current_value) &&
    isNullableNumber(value.median) &&
    isNullableNumber(value.mad) &&
    isNullableNumber(value.robust_z) &&
    typeof value.metric_status === "string"
  )
}

export function isStoredMonitorEvent(value: unknown): value is StoredMonitorEvent {
  if (!isObject(value) || !isInteger(value.event_id) || value.event_id <= 0) {
    return false
  }
  // 旧后端可能省略该字段；兼容 null，避免整页事件列表校验失败。
  if (value.reminder_required != null && typeof value.reminder_required !== "boolean") {
    return false
  }
  if (value.fault_alert_required != null && typeof value.fault_alert_required !== "boolean") {
    return false
  }
  if (value.diagnosis_phase != null && typeof value.diagnosis_phase !== "string") {
    return false
  }
  return (
    typeof value.session_id === "string" &&
    PLAYBACK_SESSION_PATTERN.test(value.session_id) &&
    typeof value.record_id === "string" &&
    value.record_id.trim() !== "" &&
    typeof value.device_id === "string" &&
    value.device_id.trim() !== "" &&
    typeof value.collected_at === "string" &&
    value.collected_at.trim() !== "" &&
    typeof value.processed_at === "string" &&
    value.processed_at.trim() !== "" &&
    typeof value.event_type === "string" &&
    value.event_type.trim() !== "" &&
    typeof value.diagnosis_level === "string" &&
    ANALYSIS_LEVEL_SET.has(value.diagnosis_level) &&
    typeof value.detection_status === "string" &&
    value.detection_status.trim() !== "" &&
    typeof value.data_source === "string" &&
    typeof value.event_scope === "string" &&
    typeof value.event_note === "string" &&
    Array.isArray(value.metrics) &&
    value.metrics.every(isStoredMetric) &&
    Array.isArray(value.diagnostic_clues) &&
    value.diagnostic_clues.every(isDiagnosticClue) &&
    Array.isArray(value.candidate_faults) &&
    value.candidate_faults.every(isCandidateFault) &&
    storedDiagnosisFieldsOk(value)
  )
}

export function isMonitoringProcessResponse(value: unknown): value is MonitoringProcessResponse {
  if (!isObject(value) || !isDatasetAnalysis(value.analysis)) {
    return false
  }
  if (typeof value.session_id !== "string" || !PLAYBACK_SESSION_PATTERN.test(value.session_id)) {
    return false
  }
  if (typeof value.record_id !== "string" || value.record_id !== value.analysis.record_id) {
    return false
  }
  if (typeof value.saved !== "boolean" || typeof value.duplicate !== "boolean") {
    return false
  }
  if (value.reminder_required != null && typeof value.reminder_required !== "boolean") {
    return false
  }
  if (value.event === null) {
    return value.saved === false && value.duplicate === false
  }
  return (
    isStoredMonitorEvent(value.event) &&
    value.event.record_id === value.record_id &&
    value.event.session_id === value.session_id &&
    ((value.saved && !value.duplicate) || (!value.saved && value.duplicate))
  )
}

export function isMonitoringEventList(value: unknown): value is MonitoringEventList {
  if (!isObject(value) || !isInteger(value.count) || !isInteger(value.limit) || value.limit < 1 || value.limit > 100) {
    return false
  }
  if (!Array.isArray(value.events) || value.events.length !== value.count || value.count > value.limit) {
    return false
  }
  const sessionOk = value.session_id === null || (typeof value.session_id === "string" && PLAYBACK_SESSION_PATTERN.test(value.session_id))
  const deviceOk = value.device_id === null || (typeof value.device_id === "string" && value.device_id.trim() !== "")
  return sessionOk && deviceOk && value.events.every(isStoredMonitorEvent)
}

export interface StoredPlaybackInput {
  requestSessionId: number
  currentSessionId: number
  playbackSessionId: string
  record: Pick<DatasetRecord, "record_id" | "device_id" | "collected_at">
  processedIds: readonly string[]
  events: readonly StoredMonitorEvent[]
}

export interface StoredPlaybackResult {
  accepted: boolean
  processedIds: string[]
  events: StoredMonitorEvent[]
  statusText: string
}

function unchangedStored(input: StoredPlaybackInput, statusText: string): StoredPlaybackResult {
  return {
    accepted: false,
    processedIds: [...input.processedIds],
    events: [...input.events],
    statusText,
  }
}

export function applyPlaybackProcess(input: StoredPlaybackInput, response: MonitoringProcessResponse): StoredPlaybackResult {
  if (input.requestSessionId !== input.currentSessionId || input.playbackSessionId === "" || response.session_id !== input.playbackSessionId) {
    return unchangedStored(input, "")
  }
  if (input.processedIds.includes(input.record.record_id)) {
    return unchangedStored(input, "")
  }
  if (!analysisMatchesRecord(input.record, response.analysis) || response.record_id !== input.record.record_id) {
    return unchangedStored(input, "")
  }
  if (response.event === null) {
    return {
      accepted: true,
      processedIds: [...input.processedIds, input.record.record_id],
      events: [...input.events],
      statusText: playbackCheckStatus(response.analysis, false),
    }
  }
  if (
    response.event.device_id !== input.record.device_id ||
    response.event.collected_at !== input.record.collected_at ||
    response.event.record_id !== input.record.record_id
  ) {
    return unchangedStored(input, "")
  }
  const stored = response.event
  const alreadyShown = input.events.some((item) => item.event_id === stored.event_id)
  const events = alreadyShown ? [...input.events] : [...input.events, stored]
  const statusText = alreadyShown || stored && response.duplicate
    ? `返回已有事件 ${stored.event_id}，不重复显示。统计检测状态：${stored.detection_status}。`
    : `已保存统计偏离事件 ${stored.event_id}。统计检测状态：${stored.detection_status}。`
  return {
    accepted: true,
    processedIds: [...input.processedIds, input.record.record_id],
    events,
    statusText,
  }
}

export function applyPlaybackError(input: StoredPlaybackInput, message: string): StoredPlaybackResult {
  if (input.requestSessionId !== input.currentSessionId || input.processedIds.includes(input.record.record_id)) {
    return unchangedStored(input, "")
  }
  const detail = message.trim() || "记录分析失败"
  return {
    accepted: true,
    processedIds: [...input.processedIds, input.record.record_id],
    events: [...input.events],
    statusText: `分析失败：${detail}`,
  }
}

export function applyPlaybackFailure(input: PlaybackCheckInput, message: string): PlaybackCheckResult {
  if (input.requestSessionId !== input.currentSessionId || input.processedIds.includes(input.record.record_id)) {
    return unchangedCheck(input, "")
  }
  const detail = message.trim() || "记录分析失败"
  const events = [
    ...input.events,
    {
      id: nextEventId(input.requestSessionId, input.events),
      recordId: input.record.record_id,
      deviceId: input.record.device_id,
      collectedAt: input.record.collected_at,
      category: "分析失败" as const,
      metrics: [],
      diagnosisLevel: null,
      levelLabel: "分析失败",
      clues: [],
      candidateFaults: [],
      source: "",
      message: detail,
      analysis: null,
    },
  ]
  return {
    accepted: true,
    processedIds: [...input.processedIds, input.record.record_id],
    events,
    statusText: `分析失败：${detail}`,
  }
}
