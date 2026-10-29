import type { VibrationAnalysisResponse, VibrationSpectrum } from "../types/api"

export const MAX_CSV_BYTES = 5 * 1024 * 1024

const UNITS = ["mm/s", "m/s²", "未知"] as const

export type VibrationUnit = (typeof UNITS)[number]

export function vibrationInputError(
  file: { size: number } | null,
  samplingRate: string,
  column: string,
): string | null {
  if (!file) {
    return "请选择 CSV 文件。"
  }
  if (file.size > MAX_CSV_BYTES) {
    return "文件超过 5MB，请缩小后再分析。"
  }
  const rateText = samplingRate.trim()
  const rate = Number(rateText)
  if (!rateText || !Number.isFinite(rate) || rate <= 0) {
    return "请输入大于 0 的采样频率，单位是 Hz。"
  }
  if (!column.trim()) {
    return "请填写数据列名。"
  }
  return null
}

export function vibrationSearchParams(samplingRate: string, column: string, unit: string): URLSearchParams {
  const params = new URLSearchParams()
  params.set("sampling_rate", samplingRate.trim())
  params.set("column", column.trim())
  params.set("unit", unit)
  return params
}

export function formatMetric(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return "无法计算"
  }
  return Number(value.toPrecision(10)).toString()
}

export function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function isFiniteOrNull(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value)
}

function isSpectrum(value: unknown): value is VibrationSpectrum {
  if (!isRecord(value)) {
    return false
  }
  return (
    typeof value.window === "string" &&
    typeof value.dc_removed === "boolean" &&
    isFiniteOrNull(value.fft_length) &&
    isFiniteOrNull(value.frequency_resolution_hz) &&
    Array.isArray(value.peaks) &&
    value.peaks.every(
      (peak) => isRecord(peak) && isFiniteNumber(peak.frequency_hz) && isFiniteNumber(peak.amplitude),
    ) &&
    typeof value.amplitude_note === "string"
  )
}

export function isVibrationAnalysisResponse(value: unknown): value is VibrationAnalysisResponse {
  if (!isRecord(value) || value.ok !== true || value.error !== null) {
    return false
  }
  return (
    typeof value.unit === "string" &&
    typeof value.unit_note === "string" &&
    Number.isInteger(value.sample_count) &&
    isFiniteNumber(value.sampling_rate_hz) &&
    isFiniteNumber(value.duration_seconds) &&
    isFiniteNumber(value.mean) &&
    isFiniteNumber(value.rms) &&
    isFiniteNumber(value.peak_absolute) &&
    isFiniteNumber(value.peak_to_peak) &&
    isFiniteOrNull(value.crest_factor) &&
    isFiniteOrNull(value.kurtosis_pearson) &&
    typeof value.kurtosis_type === "string" &&
    typeof value.kurtosis_note === "string" &&
    isSpectrum(value.fft) &&
    Array.isArray(value.notes) &&
    value.notes.every((item) => typeof item === "string") &&
    isRecord(value.conclusions) &&
    Array.isArray(value.conclusions.faults) &&
    Array.isArray(value.conclusions.alarms) &&
    Array.isArray(value.conclusions.probabilities) &&
    typeof value.conclusions.note === "string" &&
    typeof value.data_source === "string" &&
    typeof value.column === "string" &&
    typeof value.analysis_note === "string"
  )
}
