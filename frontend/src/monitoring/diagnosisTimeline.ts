export const UNCHANGED_VERSION = "本次评估未发现足以改变假设的新增证据，版本保持不变"
export const NO_HYPOTHESIS = "尚无可验证的故障假设"

export interface DiagnosisHypothesisSummary {
  fault: string
  version: number
  verification_status: string
}

export interface DiagnosisMeasurementRef {
  measurement_id: string
  content_sha256: string
  measured_at: string
  sensor_position?: string
  data_origin?: string
  origin_note?: string
}

export interface DiagnosisEvidence {
  evidence_id?: string
  source?: string
  locator?: string
  detail?: string
  fault?: string
}

export interface DiagnosisSummary {
  assessment_id: string
  assessment_no: number
  record_id: string
  device_id: string
  created_at: string
  trace_id: string
  diagnosis_level: string
  stop_reason: string
  model_called: boolean
  confirmed: boolean
  no_hypothesis: boolean
  change_lines: string[]
  hypotheses: DiagnosisHypothesisSummary[]
  measurements: DiagnosisMeasurementRef[]
  missing_measurements: string[]
  l2_status: string
}

export interface DiagnosisDetail extends DiagnosisSummary {
  hypotheses_detail?: {
    faults?: string[]
    version?: number
    verification_status?: string
    revision_reason?: string
    version_history?: { version?: number; verification_status?: string }[]
    supporting_evidence?: DiagnosisEvidence[]
    opposing_evidence?: DiagnosisEvidence[]
    missing_measurements?: string[]
  }[]
  added_support?: DiagnosisEvidence[]
  added_oppose?: DiagnosisEvidence[]
  l2?: {
    called?: boolean
    overall_status?: string
    no_fault_hypothesis?: boolean
    results?: {
      level?: string
      overall_status?: string
      consistent_checks?: string[]
      contradicted_checks?: string[]
      insufficient_checks?: string[]
      not_applicable_checks?: string[]
      supplementary_measurements?: DiagnosisMeasurementRef[]
    }[]
  }
  public_answer?: string
}

export function isDiagnosisSummary(value: unknown): value is DiagnosisSummary {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.assessment_id === "string" && typeof row.assessment_no === "number" && Array.isArray(row.change_lines)
}

const LEVEL_LABEL: Record<string, string> = {
  statistical_deviation: "统计偏离",
  diagnostic_clue: "诊断线索",
  possible_match: "可能匹配",
  insufficient_evidence: "证据不足",
  unable_to_evaluate: "无法评估",
}

const L2_LABEL: Record<string, string> = {
  no_testable_hypothesis: "无可验证假设",
  numeric_contradiction: "数值矛盾",
  consistent: "数值一致",
  insufficient_data: "数据不足",
  contradicted: "数值矛盾",
}

export function diagnosisLevelLabel(level: string): string {
  return LEVEL_LABEL[level] || level
}

export function l2StatusLabel(status: string): string {
  return L2_LABEL[status] || status
}

export function timelineSummary(item: DiagnosisSummary): {
  time: string
  change: string
  version: string
  stopReason: string
  confirmed: boolean
} {
  const hypo = item.hypotheses[0]
  const version = hypo
    ? `${hypo.fault} ${hypo.verification_status} V${hypo.version}`
    : item.no_hypothesis
      ? "尚无故障假设"
      : "—"
  return {
    time: item.created_at,
    change: item.change_lines[0] || "—",
    version,
    stopReason: item.stop_reason || "未返回",
    confirmed: item.confirmed,
  }
}

export function timelineText(item: DiagnosisSummary): string[] {
  const lines: string[] = []
  if (item.assessment_no === 1) {
    lines.push(`初次分析结果：${diagnosisLevelLabel(item.diagnosis_level) || "工具没有返回诊断等级"}`)
  } else {
    lines.push("重新评估结果")
  }
  if (item.measurements.length === 0) {
    lines.push("这次评估没有关联补充测量。")
  } else {
    for (const measurement of item.measurements) {
      lines.push(`补充测量 ${measurement.measured_at || "时间未记录"} ${measurement.measurement_id} SHA-256 ${measurement.content_sha256}`)
    }
  }
  lines.push(`重新校验结果：${l2StatusLabel(item.l2_status) || "没有 L2 结果"}`)
  lines.push(...item.change_lines)
  if (item.no_hypothesis) {
    const missing = item.missing_measurements.length ? item.missing_measurements.join("、") : "无"
    lines.push(`已有物理校验：${l2StatusLabel(item.l2_status) || "没有 L2 结果"}。缺失测量：${missing}。`)
  }
  lines.push(`停止原因：${item.stop_reason || "未返回"}`)
  if (item.trace_id) {
    lines.push(`关联轨迹：${item.trace_id}`)
  }
  if (item.model_called) {
    lines.push("这次评估调用了模型。")
  }
  if (!item.confirmed) {
    lines.push("故障尚未确诊。")
  }
  return lines
}

export function evidenceText(item: DiagnosisEvidence): string {
  const source = item.source || "来源未记录"
  const detail = item.detail || ""
  const locator = item.locator || ""
  return [item.evidence_id, source, locator, detail].filter(Boolean).join(" ")
}
