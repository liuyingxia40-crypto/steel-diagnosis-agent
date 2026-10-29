import type { DatasetRecord, DatasetSearchResult } from "../types/api"

export type AnalysisStatusFilter = "all" | "deviation" | "no_significant_deviation"

export const PAGE_SIZE = 20

export const ANALYSIS_STATUS_OPTIONS: { value: AnalysisStatusFilter; label: string }[] = [
  { value: "all", label: "全部记录" },
  { value: "deviation", label: "已分析且偏离基线" },
  { value: "no_significant_deviation", label: "已分析且未发现显著偏离" },
]

export function analysisStatusDisplay(status: string | undefined): string {
  if (status === "偏离基线") return "已分析且偏离基线"
  if (status === "未发现显著偏离") return "已分析且未发现显著偏离"
  if (status === "尚未分析" || status === "未分析" || !status) return "尚未分析"
  return status
}

export function isUnanalyzedStatus(status: string | undefined): boolean {
  return !status || status === "尚未分析" || status === "未分析"
}

export function deviceDisplayName(record: Pick<DatasetRecord, "device_id" | "device_name" | "equipment_category">): string {
  return record.device_name || record.equipment_category || record.device_id
}

export function buildSearchParams(input: {
  q: string
  deviceId: string
  startTime: string
  endTime: string
  page: number
  pageSize?: number
  analysisStatus: AnalysisStatusFilter
}): URLSearchParams {
  const params = new URLSearchParams()
  if (input.q.trim()) params.set("q", input.q.trim())
  if (input.deviceId.trim()) params.set("device_id", input.deviceId.trim())
  if (input.startTime.trim()) params.set("start_time", input.startTime.trim())
  if (input.endTime.trim()) params.set("end_time", input.endTime.trim())
  params.set("page", String(Math.max(1, input.page)))
  params.set("page_size", String(input.pageSize ?? PAGE_SIZE))
  params.set("analysis_status", input.analysisStatus)
  return params
}

export function isDatasetSearchResult(value: unknown): value is DatasetSearchResult {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return (
    typeof row.found === "boolean" &&
    typeof row.matched_count === "number" &&
    typeof row.returned_count === "number" &&
    typeof row.page === "number" &&
    typeof row.page_size === "number" &&
    typeof row.total_pages === "number" &&
    Array.isArray(row.records)
  )
}

export function primaryMetricText(values: Record<string, number | null>, fields: readonly string[]): string {
  const parts: string[] = []
  for (const field of fields.slice(0, 3)) {
    const value = values[field]
    if (typeof value === "number" && Number.isFinite(value)) {
      parts.push(`${field} ${formatDisplayNumber(value)}`)
    }
  }
  return parts.length ? parts.join("；") : "主要指标暂无数值"
}

/** 仅用于界面展示，不改变后台计算精度。 */
export function formatDisplayNumber(value: number): string {
  if (!Number.isFinite(value)) return "无数据"
  if (Number.isInteger(value)) return String(value)
  const abs = Math.abs(value)
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-4)) {
    return value.toExponential(3)
  }
  const text = value.toFixed(4).replace(/\.?0+$/, "")
  return text === "-0" ? "0" : text
}

export function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  return Promise.reject(new Error("当前环境不支持复制"))
}
