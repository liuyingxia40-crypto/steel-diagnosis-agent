export const EVIDENCE_KINDS = [
  { value: "axial_displacement", label: "轴向位移" },
  { value: "axial_vibration", label: "轴向振动" },
  { value: "axial_eccentricity", label: "轴向偏心或轴向跳动" },
  { value: "load_1x_series", label: "负荷变化下的 1X 观测" },
  { value: "envelope_bearing_harmonics", label: "包络谱轴承谐波" },
  { value: "gear_mesh_sidebands", label: "啮合频率及边带" },
] as const

export const METHOD_OPTIONS: Record<string, { value: string; label: string }[]> = {
  axial_displacement: [
    { value: "eddy_current_probe", label: "电涡流位移探头" },
    { value: "laser_displacement", label: "激光位移测量" },
    { value: "dial_indicator", label: "百分表测量" },
  ],
  axial_vibration: [
    { value: "accelerometer", label: "加速度传感器" },
    { value: "velocity_probe", label: "速度探头" },
  ],
  axial_eccentricity: [
    { value: "runout_measurement", label: "轴向跳动测量" },
    { value: "eddy_current_runout", label: "电涡流跳动测量" },
    { value: "laser_runout", label: "激光跳动测量" },
  ],
  load_1x_series: [
    { value: "load_sweep", label: "负荷扫描观测" },
  ],
  envelope_bearing_harmonics: [
    { value: "envelope_spectrum", label: "包络谱分析" },
  ],
  gear_mesh_sidebands: [
    { value: "sideband_spectrum", label: "边带频谱分析" },
  ],
}

export const REFERENCE_OPTIONS: Record<string, { value: string; label: string }[]> = {
  axial_displacement: [
    { value: "housing_relative", label: "机壳相对基准" },
    { value: "probe_tip_zero", label: "探头零点基准" },
    { value: "shaft_shoulder", label: "轴肩基准" },
  ],
  axial_vibration: [
    { value: "bearing_housing_axial", label: "轴承座轴向基准" },
    { value: "machine_frame_axial", label: "机架轴向基准" },
  ],
  axial_eccentricity: [
    { value: "shaft_axis", label: "轴线参考基准" },
    { value: "coupling_face", label: "联轴器端面基准" },
    { value: "probe_circle", label: "探头圆周基准" },
  ],
  load_1x_series: [
    { value: "process_load", label: "工艺负荷基准" },
  ],
  envelope_bearing_harmonics: [
    { value: "bpfo_catalog", label: "轴承特征频率目录" },
  ],
  gear_mesh_sidebands: [
    { value: "gear_mesh_catalog", label: "齿轮啮合频率目录" },
  ],
}

export const UNIT_OPTIONS = [
  { value: "um", label: "μm" },
  { value: "μm", label: "μm" },
  { value: "mm", label: "mm" },
  { value: "mil", label: "mil" },
  { value: "mm/s", label: "mm/s" },
  { value: "m/s", label: "m/s" },
  { value: "g", label: "g" },
  { value: "m/s^2", label: "m/s²" },
  { value: "m/s²", label: "m/s²" },
] as const

const METHOD_LABELS: Record<string, string> = Object.fromEntries(
  Object.values(METHOD_OPTIONS).flat().map((item) => [item.value, item.label]),
)
const REFERENCE_LABELS: Record<string, string> = Object.fromEntries(
  Object.values(REFERENCE_OPTIONS).flat().map((item) => [item.value, item.label]),
)

export interface DiagnosticEvidence {
  evidence_id: string
  evidence_kind: string
  quantity_name: string
  unit: string
  sensor_position: string
  method: string
  reference_basis: string
  measured_at: string
  content_sha256: string
  registration_status: string
  validation_status: string
  validation_note: string
  origin_note: string
  values: Record<string, unknown>
  confirmed?: boolean
  supports_fault?: boolean
  data_origin?: string
}

export function isDiagnosticEvidence(value: unknown): value is DiagnosticEvidence {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.evidence_id === "string" && typeof row.evidence_kind === "string" && typeof row.validation_status === "string"
}

export function evidenceStatusLabel(status: string): string {
  if (status === "已登记") return "已登记"
  if (status === "可计算") return "已获得数值证据"
  if (status === "已完成条款校验") return "条款已经得到校验"
  if (status === "已登记、待验证") return "已登记、待验证"
  if (status === "仍然证据不足") return "仍然证据不足"
  if (status === "已获得可用证据") return "已获得数值证据"
  return status
}

export function kindLabel(kind: string): string {
  return EVIDENCE_KINDS.find((item) => item.value === kind)?.label || kind
}

export function methodLabel(method: string): string {
  return METHOD_LABELS[method] || method
}

export function referenceLabel(reference: string): string {
  return REFERENCE_LABELS[reference] || reference
}

export function unitLabel(unit: string): string {
  if (unit === "um" || unit === "μm") return "μm"
  if (unit === "m/s^2") return "m/s²"
  if (unit === "mm/s^2") return "mm/s²"
  return unit
}

export function applyEvidenceKindDefaults(kind: string): {
  method: string
  reference: string
  unit: string
} {
  if (kind === "axial_displacement") {
    return { method: "eddy_current_probe", reference: "housing_relative", unit: "um" }
  }
  if (kind === "axial_vibration") {
    return { method: "accelerometer", reference: "bearing_housing_axial", unit: "mm/s" }
  }
  if (kind === "axial_eccentricity") {
    return { method: "runout_measurement", reference: "shaft_axis", unit: "um" }
  }
  if (kind === "load_1x_series") {
    return { method: "load_sweep", reference: "process_load", unit: "mm/s" }
  }
  if (kind === "envelope_bearing_harmonics") {
    return { method: "envelope_spectrum", reference: "bpfo_catalog", unit: "g" }
  }
  return { method: "sideband_spectrum", reference: "gear_mesh_catalog", unit: "g" }
}

export function evidenceValueText(item: DiagnosticEvidence): string {
  if (typeof item.values.value === "number") {
    return `${item.values.value} ${unitLabel(item.unit)}`
  }
  if (Array.isArray(item.values.observations)) {
    return `${item.values.observations.length} 个负荷观测点`
  }
  if (Array.isArray(item.values.peaks)) {
    return `${item.values.peaks.length} 个频率峰`
  }
  return "—"
}

export function evidenceLines(item: DiagnosticEvidence): string[] {
  const lines = [
    `测量项目：${kindLabel(item.evidence_kind)}`,
    `验证状态：${evidenceStatusLabel(item.registration_status)} / ${evidenceStatusLabel(item.validation_status)}`,
    `单位：${unitLabel(item.unit)}`,
    `传感器位置：${item.sensor_position}`,
    `测量方法：${methodLabel(item.method)}`,
    `参考基准：${referenceLabel(item.reference_basis)}`,
    `来源：${item.origin_note}`,
    `内部编号：${item.evidence_id}`,
    `SHA-256：${item.content_sha256}`,
    item.validation_note,
  ]
  if (typeof item.values.value === "number") {
    lines.splice(1, 0, `数值：${item.values.value} ${unitLabel(item.unit)}`)
  }
  if (Array.isArray(item.values.observations)) {
    lines.splice(1, 0, `负荷观测点数：${item.values.observations.length}`)
  }
  if (Array.isArray(item.values.peaks)) {
    lines.splice(1, 0, `频率峰数量：${item.values.peaks.length}`)
  }
  if (!item.confirmed) {
    lines.push("故障尚未确诊，也没有写入知识图谱。")
  }
  return lines
}
