import type { SystemId } from "../overview/model"

export type WorkEventType = "alarm" | "diagnosis" | "operation" | "system"

export type WorkSeverity = "info" | "attention" | "warning" | "critical"

export type WorkStatus = "pending" | "acknowledged" | "investigating" | "resolved" | "closed" | "review" | "reviewed" | "recorded"

export type AuditAction = "acknowledge" | "investigate" | "resolve" | "close" | "reopen" | "review"

export interface AuditEntry {
  id: string
  eventId: string
  action: AuditAction
  at: string
  operator: string
  note: string
  statusAfter: WorkStatus
  persisted: false
}

export interface WorkEvent {
  id: string
  type: WorkEventType
  severity: WorkSeverity
  title: string
  description: string
  equipmentId?: string
  equipmentName?: string
  equipmentCode?: string
  systemId?: SystemId
  occurredAt: string
  receivedAt: string
  source: "sensor" | "inference" | "operator" | "system"
  status: WorkStatus
  relatedEventIds: string[]
  diagnosisId?: string
  snapshotId: string
  dataMode: "simulated"
  metricId?: string
  metricLabel?: string
  actualValue?: string
  threshold?: string
  series?: number[]
  log?: {
    level: string
    module: string
    message: string
    errorCode?: string
    taskId?: string
    detail?: string
  }
  diagnosis?: {
    taskId: string
    candidates: string[]
    evidence: string[]
    modelNote: string
  }
}

export const TYPE_LABEL: Record<WorkEventType, string> = {
  alarm: "报警",
  diagnosis: "AI 诊断",
  operation: "运行事件",
  system: "系统日志",
}

export const SEVERITY_LABEL: Record<WorkSeverity, string> = {
  info: "信息",
  attention: "关注",
  warning: "警告",
  critical: "严重",
}

export const STATUS_LABEL: Record<WorkStatus, string> = {
  pending: "待确认",
  acknowledged: "已确认 / 待调查",
  investigating: "处理中",
  resolved: "已解决",
  closed: "已关闭",
  review: "待复核",
  reviewed: "已复核",
  recorded: "已记录",
}

export const SEVERITY_RANK: Record<WorkSeverity, number> = {
  info: 0,
  attention: 1,
  warning: 2,
  critical: 3,
}
