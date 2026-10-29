export interface HealthResponse {
  status: string
  message: string
}

export interface ApiErrorResponse {
  success: false
  message: string
}

export interface ChatRequest {
  question: string
}

export interface KnowledgeSource {
  original_document: string
  document: string
  section: string
  locator: string
}

export interface ChatResponse {
  answer: string
  sources: KnowledgeSource[]
}

export interface VibrationPeak {
  frequency_hz: number
  amplitude: number
}

export interface VibrationSpectrum {
  window: string
  dc_removed: boolean
  fft_length: number | null
  frequency_resolution_hz: number | null
  peaks: VibrationPeak[]
  amplitude_note: string
}

export interface VibrationConclusions {
  faults: unknown[]
  alarms: unknown[]
  probabilities: unknown[]
  note: string
}

export interface DatasetSheet {
  sheet_name: string
  equipment_category: string
  sheet_name_matches_category: boolean
  device_count: number
  record_count: number
  time_start: string | null
  time_end: string | null
  duplicate_timestamp_groups: number
}

export interface DatasetSummary {
  source_name: string
  sheet_count: number
  device_count: number
  record_count: number
  sheets: DatasetSheet[]
}

export interface DatasetDevice {
  device_id: string
  device_name?: string
  equipment_category: string
  sheet_name: string
  record_count: number
  time_start: string | null
  time_end: string | null
  duplicate_timestamp_groups: number
}

export interface DatasetDevices {
  device_count: number
  devices: DatasetDevice[]
}

export interface DatasetRecord {
  record_id: string
  excel_row: number
  sheet_name: string
  device_id: string
  device_name?: string
  equipment_category: string
  collected_at: string
  duplicate_timestamp: boolean
  same_timestamp_count: number
  values: Record<string, number | null>
  analysis_status?: string
  analysis_status_note?: string
}

export interface DatasetSearchResult {
  found: boolean
  query: string
  device_id: string | null
  matched_count: number
  returned_count: number
  page: number
  page_size: number
  total_pages: number
  analysis_status: string
  status_filter_note: string
  records: DatasetRecord[]
}

export interface DatasetRecords {
  found: boolean
  device_id: string
  equipment_category: string | null
  sheet_name: string | null
  matched_count: number
  returned_count: number
  limit: number
  truncated: boolean
  duplicate_timestamp_groups: number
  records: DatasetRecord[]
}

export interface DatasetObservation {
  found: true
  record: DatasetRecord
}

export type AnalysisLevel =
  | "statistical_deviation"
  | "diagnostic_clue"
  | "possible_match"
  | "insufficient_evidence"
  | "unable_to_evaluate"

export interface AnalysisMetric {
  field: string
  unit: string | null
  current_value: number | null
  median: number | null
  mad: number | null
  robust_z: number | null
  metric_status: string
  reason: string
}

export interface AnalysisDeviation {
  detection_status: string
  history_sample_count: number
  anomalous_metrics: string[]
  analyzed_metrics: AnalysisMetric[]
  experimental_threshold: {
    note: string
  }
  operating_condition: {
    comparable: boolean
  }
}

export interface DiagnosticClue {
  feature_code: string
  related_fault_section: string
  document: string
  line_number: number
  partial_condition_met: boolean
  complete_condition_met: boolean
  unverified_reason: string
  official_condition?: string
  method?: string
  observed_fields?: Array<{ field?: string; value?: number | null; unit?: string | null }>
}

export interface CandidateFault {
  fault: string
  confirmed: false
  note: string
}

export interface DatasetAnalysis {
  ok: true
  record_id: string
  diagnosis_level: AnalysisLevel
  confirmed: false
  data_source: string
  analysis_note: string
  observation: DatasetRecord
  statistical_deviation: AnalysisDeviation
  diagnostic_clues: DiagnosticClue[]
  candidate_faults: CandidateFault[]
  missing_data: string[]
  insufficient_reasons: string[]
  warnings: string[]
}

export interface StoredMonitorMetric {
  field: string
  unit: string | null
  current_value: number | null
  median: number | null
  mad: number | null
  robust_z: number | null
  metric_status: string
}

export interface DiagnosisEvidenceSource {
  document: string
  line_number: number
  feature_code: string
  related_fault_section: string
}

export interface StoredMonitorEvent {
  event_id: number
  session_id: string
  record_id: string
  device_id: string
  collected_at: string
  processed_at: string
  event_type: string
  diagnosis_level: AnalysisLevel
  diagnosis_available?: boolean
  diagnosis_status?: string
  detection_status: string
  data_source: string
  event_scope: string
  event_note: string
  metrics: StoredMonitorMetric[]
  diagnostic_clues: DiagnosticClue[]
  evidence_sources?: DiagnosisEvidenceSource[]
  missing_conditions?: string[]
  candidate_faults: CandidateFault[]
  confirmed?: false
  /** 后端异常过程生命周期：仅首次进入异常时为 true，持续异常为 false。不触发故障弹窗。 */
  reminder_required?: boolean
  /** 仅结构化确认故障后为 true；前端只据此自动弹故障告警。 */
  fault_alert_required?: boolean
  diagnosis_phase?: string
  assessment_id?: string
  episode_key?: string
}

export interface MonitoringProcessResponse {
  session_id: string
  record_id: string
  analysis: DatasetAnalysis
  event: StoredMonitorEvent | null
  saved: boolean
  duplicate: boolean
  reminder_required?: boolean
}

export interface MonitoringEventList {
  session_id: string | null
  device_id: string | null
  count: number
  limit: number
  events: StoredMonitorEvent[]
}

export interface VibrationAnalysisResponse {
  ok: true
  error: null
  unit: string
  unit_note: string
  sample_count: number
  sampling_rate_hz: number
  duration_seconds: number
  mean: number
  rms: number
  peak_absolute: number
  peak_to_peak: number
  crest_factor: number | null
  kurtosis_pearson: number | null
  kurtosis_type: string
  kurtosis_note: string
  fft: VibrationSpectrum
  notes: string[]
  conclusions: VibrationConclusions
  data_source: string
  column: string
  analysis_note: string
}
