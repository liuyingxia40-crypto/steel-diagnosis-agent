import type { SystemId } from "../overview/model"

export type MaintenanceKind = "preventive" | "corrective" | "inspection" | "replacement" | "other"

export type MaintenanceStatus = "overdue" | "due" | "ok" | "unplanned" | "unknown"

export type HealthDataMode = "simulated" | "demo-local"

export interface EquipmentAsset {
  id: string
  name: string
  code: string
  plateName?: string
  equipmentType: string
  systemId: SystemId
  systemName: string
  commissioningDate?: string
  operatingHours?: number
  model?: string
  dataMode: "simulated"
}

export interface RecordRevision {
  at: string
  operator: string
  summary: string
}

export interface MaintenanceRecord {
  id: string
  equipmentId: string
  maintenancePlanId?: string
  type: MaintenanceKind
  performedAt: string
  description: string
  operator?: string
  result: string
  replacedComponents?: string[]
  relatedEventIds?: string[]
  diagnosisId?: string
  attachmentNames?: string[]
  createdAt: string
  revisions: RecordRevision[]
  dataMode: HealthDataMode
  persisted: false
}

export interface MaintenancePlan {
  id: string
  equipmentId: string
  title: string
  type: MaintenanceKind
  intervalDays?: number
  intervalOperatingHours?: number
  lastCompletedAt?: string
  lastCompletedOperatingHours?: number
  nextDueAt?: string
  nextDueOperatingHours?: number
  assignedTo?: string
  enabled: boolean
  dataMode: HealthDataMode
  persisted: false
}

export interface HealthAudit {
  id: string
  at: string
  action: "create-record" | "update-record" | "delete-record" | "create-plan" | "update-plan" | "complete-plan"
  targetId: string
  operator: string
  note: string
  persisted: false
}

export const KIND_LABEL: Record<MaintenanceKind, string> = {
  preventive: "定期保养",
  corrective: "故障维修",
  inspection: "日常巡检",
  replacement: "部件更换",
  other: "其他维护",
}

export const STATUS_LABEL: Record<MaintenanceStatus, string> = {
  overdue: "已逾期",
  due: "即将保养",
  ok: "正常",
  unplanned: "未制定计划",
  unknown: "无法判断",
}
