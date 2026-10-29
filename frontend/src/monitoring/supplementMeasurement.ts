/** 把补充测量接口结果拆成可展示的官方记录与 CSV 证据。不把两者合并成一次观测。 */

export const VIBRATION_UNITS = ["mm/s", "m/s", "g", "m/s^2", "m/s²", "mm/s^2", "mm/s²", "um", "μm", "mil"] as const

export const VIBRATION_UNIT_OPTIONS = [
  { value: "mm/s", label: "mm/s" },
  { value: "m/s", label: "m/s" },
  { value: "g", label: "g" },
  { value: "m/s^2", label: "m/s²" },
  { value: "m/s²", label: "m/s²" },
  { value: "um", label: "μm" },
  { value: "μm", label: "μm" },
  { value: "mil", label: "mil" },
] as const

export const STATUS_LABEL: Record<string, string> = {
  consistent: "数值一致",
  contradicted: "数值矛盾",
  insufficient_data: "数据不足",
  not_applicable: "不适用",
}

export const ORIGIN_LABEL: Record<string, string> = {
  synthetic_test_signal: "模拟测试数据",
  uploaded_csv: "用户补充数据",
  uploaded_measurement: "用户补充数据",
  official_dataset: "官方历史记录",
  official_excel: "官方历史记录",
}

const PAIRS: { official: string; supplement: string; label: string }[] = [
  { official: "waveform_statistics", "supplement": "supplement_waveform_statistics", label: "峭度与峰值因子" },
  { official: "dominant_1x", "supplement": "supplement_dominant_1x", label: "1X主导峰" },
  { official: "second_harmonic_ratio", "supplement": "supplement_second_harmonic_ratio", label: "2X与1X" },
  { official: "phase_stability", "supplement": "supplement_phase", label: "相位" },
  { official: "bpfo_harmonics", "supplement": "supplement_bearing_harmonics", label: "轴承谐波" },
]

export interface SupplementMeasurement {
  measurement_id: string
  record_id: string
  device_id: string
  measured_at: string
  sensor_position: string
  sampling_rate_hz: number
  unit: string
  file_name: string
  content_sha256: string
  data_origin: string
  origin_note: string
}

export interface SyncChannel {
  column: string
  name: string
  direction: string
  sensor_position: string
  sample_count?: number
  metrics?: {
    peaks?: { frequency_hz?: number; amplitude?: number }[]
    crest_factor?: number
    kurtosis_pearson?: number
  }
}

export interface SyncMeasurement {
  measurement_id: string
  record_id: string
  device_id: string
  measured_at: string
  sampling_rate_hz: number
  unit: string
  file_name: string
  content_sha256: string
  data_origin: string
  origin_note: string
  sync_method: string
  sync_method_note?: string
  time_base: string
  time_base_note?: string
  channel_count: number
  channels: SyncChannel[]
  phase?: {
    phase_reliable?: boolean
    interchannel_phase_computed?: boolean
    phase_difference_deg?: number | null
    absolute_phase_deg?: number | null
    keyphasor_present?: boolean
    reason?: string
    quality?: string
    channels?: { name?: string; frequency_1x_hz?: number; amplitude_1x?: number; reliable_1x?: boolean }[]
  }
  channel_mode?: string
}

export interface PhysicalCheck {
  check_id: string
  title: string
  status: string
  limit: string
  condition_effect?: string
  supports_fault?: boolean
  evidence_source?: {
    kind?: string
    measurement_ids?: string[]
  }
  computed?: {
    values_merged?: boolean
    measurements?: Record<string, unknown>[]
  }
}

export interface MetricLine {
  measurementId: string
  sha256: string
  sensorPosition: string
  measuredAt: string
  unit: string
  label: string
  value: string
}

export function defaultMeasuredAt(collectedAt: string): string {
  return collectedAt.trim()
}

export function fileBaseName(name: string): string {
  const parts = name.split(/[/\\]/)
  const base = parts[parts.length - 1]?.trim() ?? ""
  return base || "vibration.csv"
}

export function apiErrorMessage(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) {
    return fallback
  }
  const record = body as Record<string, unknown>
  if (typeof record.message === "string" && record.message.trim()) {
    return record.message
  }
  if (typeof record.detail === "string" && record.detail.trim()) {
    return record.detail
  }
  return fallback
}

export function isSupplementCheck(check: PhysicalCheck): boolean {
  return check.evidence_source?.kind === "supplementary_measurement" || check.check_id.startsWith("supplement_")
}

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status
}

export function unitDisplay(unit: string): string {
  if (unit === "um" || unit === "μm") return "μm"
  if (unit === "m/s^2") return "m/s²"
  if (unit === "mm/s^2") return "mm/s²"
  return unit
}

export function originLabel(dataOrigin: string, originNote = "", fileName = ""): string {
  if (ORIGIN_LABEL[dataOrigin]) {
    return ORIGIN_LABEL[dataOrigin]
  }
  const haystack = `${dataOrigin} ${originNote} ${fileName}`.toLowerCase()
  if (haystack.includes("synthetic") || haystack.includes("integration_synthetic") || originNote.includes("合成") || originNote.includes("模拟")) {
    return "模拟测试数据"
  }
  if (haystack.includes("official") || originNote.includes("官方")) {
    return "官方历史记录"
  }
  if (dataOrigin || originNote || fileName) {
    return "用户补充数据"
  }
  return "来源未标注"
}

export function canVerifyLabel(status: string): string {
  if (status === "consistent" || status === "contradicted") return "可以验证"
  if (status === "insufficient_data") return "仍然证据不足"
  if (status === "not_applicable") return "不适用"
  return statusLabel(status)
}

export function sourceKindLabel(check: PhysicalCheck): string {
  if (isSupplementCheck(check)) return "用户补充数据"
  return "官方历史记录"
}

export function splitChecks(checks: readonly PhysicalCheck[]): { official: PhysicalCheck[]; supplement: PhysicalCheck[] } {
  const official: PhysicalCheck[] = []
  const supplement: PhysicalCheck[] = []
  for (const check of checks) {
    if (isSupplementCheck(check)) {
      supplement.push(check)
    } else {
      official.push(check)
    }
  }
  return { official, supplement }
}

export function conditionChanges(checks: readonly PhysicalCheck[]): { gained: string[]; still: string[] } {
  const byId = new Map(checks.map((check) => [check.check_id, check]))
  const gained: string[] = []
  const still: string[] = []
  for (const pair of PAIRS) {
    const official = byId.get(pair.official)
    const supplement = byId.get(pair.supplement)
    if (supplement?.status === "consistent" && official?.status === "insufficient_data") {
      gained.push(pair.label)
    } else if (!supplement || supplement.status === "insufficient_data") {
      still.push(pair.label)
    }
  }
  return { gained, still }
}

export function describeSecondHarmonic(checks: readonly PhysicalCheck[], measurements: readonly SupplementMeasurement[]): { officialLine: string; supplementLines: string[] } {
  const official = checks.find((check) => check.check_id === "second_harmonic_ratio")
  const supplement = checks.find((check) => check.check_id === "supplement_second_harmonic_ratio")
  const officialLine = official
    ? `官方记录的 2X 检查仍是${statusLabel(official.status)}。${official.limit || "官方记录没有单独的 2X 幅值。"}`
    : "官方记录没有返回 2X 检查。"
  const rows = supplement?.computed?.measurements ?? []
  const supplementLines = rows.length
    ? rows.map((row) => formatRatioLine(row, measurements))
    : ["补充 CSV 没有算出 2X。官方记录的缺失状态保持不变。"]
  return { officialLine, supplementLines }
}

export function metricLines(checks: readonly PhysicalCheck[], measurements: readonly SupplementMeasurement[]): MetricLine[] {
  const lines: MetricLine[] = []
  for (const check of checks) {
    if (!isSupplementCheck(check)) {
      continue
    }
    for (const row of check.computed?.measurements ?? []) {
      const measurementId = text(row.measurement_id)
      const known = measurements.find((item) => item.measurement_id === measurementId)
      const base = {
        measurementId,
        sha256: text(row.content_sha256) || known?.content_sha256 || "",
        sensorPosition: known?.sensor_position || "",
        measuredAt: known?.measured_at || "",
        unit: text(row.unit) || known?.unit || "",
      }
      appendNumber(lines, base, "Pearson 峭度", row.kurtosis_pearson)
      appendNumber(lines, base, "峰值因子", row.crest_factor)
      appendNumber(lines, base, "1X 频率(Hz)", row.frequency_1x_hz)
      appendNumber(lines, base, "1X 幅值", row.amplitude_1x)
      appendNumber(lines, base, "2X 频率(Hz)", row.frequency_2x_hz)
      appendNumber(lines, base, "2X 幅值", row.amplitude_2x)
      appendNumber(lines, base, "2X/1X", row.ratio_2x_to_1x)
      appendNumber(lines, base, "主导频率(Hz)", row.dominant_frequency_hz)
      const peaks = Array.isArray(row.peaks) ? row.peaks : []
      peaks.forEach((peak, index) => {
        if (typeof peak !== "object" || peak === null) {
          return
        }
        const item = peak as Record<string, unknown>
        const frequency = item.frequency_hz
        const amplitude = item.amplitude
        if (typeof frequency !== "number" || typeof amplitude !== "number") {
          return
        }
        lines.push({ ...base, unit: text(item.unit) || base.unit, label: `频率峰 ${index + 1}`, value: `${frequency} Hz，幅值 ${amplitude}` })
      })
    }
  }
  return lines
}

export function groupByMeasurement(lines: readonly MetricLine[]): { measurementId: string; sha256: string; sensorPosition: string; measuredAt: string; unit: string; lines: MetricLine[] }[] {
  const groups: { measurementId: string; sha256: string; sensorPosition: string; measuredAt: string; unit: string; lines: MetricLine[] }[] = []
  for (const line of lines) {
    let group = groups.find((item) => item.measurementId === line.measurementId)
    if (!group) {
      group = {
        measurementId: line.measurementId,
        sha256: line.sha256,
        sensorPosition: line.sensorPosition,
        measuredAt: line.measuredAt,
        unit: line.unit,
        lines: [],
      }
      groups.push(group)
    }
    group.lines.push(line)
  }
  return groups
}

function formatRatioLine(row: Record<string, unknown>, measurements: readonly SupplementMeasurement[]): string {
  const measurementId = text(row.measurement_id)
  const known = measurements.find((item) => item.measurement_id === measurementId)
  const ratio = typeof row.ratio_2x_to_1x === "number" ? String(row.ratio_2x_to_1x) : ""
  if (!ratio) {
    return `补充测量 ${measurementId || "未编号"} 没有算出 2X。官方记录的缺失状态保持不变。`
  }
  const where = known ? `，时间 ${known.measured_at}，位置 ${known.sensor_position}` : ""
  return `补充测量 ${measurementId} 算出 2X/1X=${ratio}，单位 ${text(row.unit) || known?.unit || "未知"}，SHA-256 ${text(row.content_sha256) || known?.content_sha256 || ""}${where}。这不是官方记录里的 2X 字段。`
}

function appendNumber(lines: MetricLine[], base: Omit<MetricLine, "label" | "value">, label: string, value: unknown): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return
  }
  lines.push({ ...base, label, value: String(value) })
}

export function syncPhaseLines(item: SyncMeasurement): string[] {
  const phase = item.phase || {}
  const lines = [
    `同步方式：${item.sync_method_note || item.sync_method}`,
    `时间基准：${item.time_base_note || item.time_base}`,
    `通道数：${item.channel_count}`,
  ]
  for (const channel of item.channels || []) {
    lines.push(`通道 ${channel.name}（${channel.direction}，${channel.sensor_position}）`)
  }
  for (const channel of phase.channels || []) {
    if (channel.reliable_1x) {
      lines.push(`1X ${channel.name}：${channel.frequency_1x_hz} Hz，幅值 ${channel.amplitude_1x}`)
    } else {
      lines.push(`1X ${channel.name || "通道"}：没有有效 1X 峰`)
    }
  }
  if (phase.interchannel_phase_computed && typeof phase.phase_difference_deg === "number") {
    lines.push(`通道间 1X 相位差：${phase.phase_difference_deg}°`)
  } else {
    lines.push(`通道间相位：未可靠计算。${phase.reason || "缺少同步信息或没有有效 1X 峰"}`)
  }
  if (!phase.keyphasor_present) {
    lines.push("缺少键相参考，不能生成绝对相位。")
  }
  if (phase.absolute_phase_deg == null) {
    lines.push("绝对相位：无")
  }
  if (phase.quality) {
    lines.push(`计算质量：${phase.quality}`)
  }
  if (phase.reason) {
    lines.push(`说明：${phase.reason}`)
  }
  lines.push(`来源标识：${originLabel(item.data_origin, item.origin_note, item.file_name)}`)
  lines.push(`来源说明：${item.origin_note}`)
  lines.push(`测量编号 ${item.measurement_id}，SHA-256 ${item.content_sha256}`)
  return lines
}

export function isSyncMeasurement(value: unknown): value is SyncMeasurement {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.measurement_id === "string" && typeof row.content_sha256 === "string" && Array.isArray(row.channels)
}

function text(value: unknown): string {
  return typeof value === "string" ? value : ""
}
