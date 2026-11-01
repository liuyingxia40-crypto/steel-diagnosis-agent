import { useSyncExternalStore } from "react"
import { assetById, SEED_PLANS, SEED_RECORDS } from "./catalog"
import { addDays } from "./status"
import type { HealthAudit, MaintenanceKind, MaintenancePlan, MaintenanceRecord } from "./types"

const STORAGE_KEY = "steel-health-demo"

interface Overlay {
  records: MaintenanceRecord[]
  plans: MaintenancePlan[]
  deletedRecordIds: string[]
  deletedPlanIds: string[]
  audits: HealthAudit[]
}

export interface HealthState {
  records: MaintenanceRecord[]
  plans: MaintenancePlan[]
  audits: HealthAudit[]
  persisted: false
}

export interface RecordDraft {
  equipmentId: string
  maintenancePlanId?: string
  type: MaintenanceKind
  performedAt: string
  description: string
  operator: string
  result: string
  replacedComponents: string[]
  relatedEventIds: string[]
  diagnosisId?: string
  attachmentNames: string[]
}

export interface PlanDraft {
  equipmentId: string
  title: string
  type: MaintenanceKind
  intervalDays?: number
  intervalOperatingHours?: number
  nextDueAt?: string
  nextDueOperatingHours?: number
  assignedTo: string
  enabled: boolean
}

const EMPTY: Overlay = { records: [], plans: [], deletedRecordIds: [], deletedPlanIds: [], audits: [] }

let overlay = readOverlay()
let snapshot = materialize(overlay)
const listeners = new Set<() => void>()

function readOverlay(): Overlay {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object") return EMPTY
    const value = parsed as Partial<Overlay>
    return {
      records: Array.isArray(value.records) ? value.records : [],
      plans: Array.isArray(value.plans) ? value.plans : [],
      deletedRecordIds: Array.isArray(value.deletedRecordIds) ? value.deletedRecordIds : [],
      deletedPlanIds: Array.isArray(value.deletedPlanIds) ? value.deletedPlanIds : [],
      audits: Array.isArray(value.audits) ? value.audits : [],
    }
  } catch {
    return EMPTY
  }
}

function materialize(source: Overlay): HealthState {
  const deletedRecords = new Set(source.deletedRecordIds)
  const deletedPlans = new Set(source.deletedPlanIds)
  const records = new Map<string, MaintenanceRecord>()
  const plans = new Map<string, MaintenancePlan>()
  for (const item of SEED_RECORDS) {
    if (!deletedRecords.has(item.id)) records.set(item.id, item)
  }
  for (const item of source.records) {
    if (!deletedRecords.has(item.id)) records.set(item.id, item)
  }
  for (const item of SEED_PLANS) {
    if (!deletedPlans.has(item.id)) plans.set(item.id, item)
  }
  for (const item of source.plans) {
    if (!deletedPlans.has(item.id)) plans.set(item.id, item)
  }
  return {
    records: [...records.values()].sort((left, right) => right.performedAt.localeCompare(left.performedAt)),
    plans: [...plans.values()],
    audits: source.audits,
    persisted: false,
  }
}

function commit(next: Overlay) {
  overlay = next
  snapshot = materialize(next)
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  listeners.forEach((listener) => listener())
}

function audit(action: HealthAudit["action"], targetId: string, operator: string, note: string): HealthAudit {
  return { id: `health-audit-${Date.now()}`, at: new Date().toISOString(), action, targetId, operator, note, persisted: false }
}

function upsert<T extends { id: string }>(items: T[], item: T): T[] {
  const index = items.findIndex((entry) => entry.id === item.id)
  if (index < 0) return [item, ...items]
  const next = [...items]
  next[index] = item
  return next
}

export function getHealthState(): HealthState {
  return snapshot
}

export function subscribeHealth(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useHealthState(): HealthState {
  return useSyncExternalStore(subscribeHealth, getHealthState, getHealthState)
}

export function createRecord(draft: RecordDraft): MaintenanceRecord {
  const record: MaintenanceRecord = {
    ...draftFrom(draft),
    id: `rec-local-${Date.now()}`,
    createdAt: new Date().toISOString(),
    revisions: [],
    dataMode: "demo-local",
    persisted: false,
  }
  commit({
    ...overlay,
    records: [record, ...overlay.records],
    audits: [audit("create-record", record.id, draft.operator, "新增维护记录"), ...overlay.audits],
  })
  return record
}

export function updateRecord(id: string, draft: RecordDraft, summary: string): void {
  const current = snapshot.records.find((item) => item.id === id)
  if (!current) return
  const next: MaintenanceRecord = {
    ...current,
    ...draftFrom(draft),
    id: current.id,
    createdAt: current.createdAt,
    revisions: [{ at: new Date().toISOString(), operator: draft.operator, summary }, ...current.revisions],
    dataMode: "demo-local",
    persisted: false,
  }
  commit({
    ...overlay,
    records: upsert(overlay.records, next),
    audits: [audit("update-record", id, draft.operator, summary), ...overlay.audits],
  })
}

export function deleteRecord(id: string, operator: string): void {
  commit({
    ...overlay,
    records: overlay.records.filter((item) => item.id !== id),
    deletedRecordIds: [...overlay.deletedRecordIds, id],
    audits: [audit("delete-record", id, operator, "删除维护记录"), ...overlay.audits],
  })
}

export function createPlan(draft: PlanDraft): MaintenancePlan {
  const plan: MaintenancePlan = {
    ...draft,
    id: `plan-local-${Date.now()}`,
    dataMode: "demo-local",
    persisted: false,
  }
  commit({
    ...overlay,
    plans: [plan, ...overlay.plans],
    audits: [audit("create-plan", plan.id, draft.assignedTo || "演示用户", "新增维护计划"), ...overlay.audits],
  })
  return plan
}

export function updatePlan(id: string, draft: PlanDraft): void {
  const current = snapshot.plans.find((item) => item.id === id)
  if (!current) return
  const next: MaintenancePlan = { ...current, ...draft, id: current.id, dataMode: "demo-local", persisted: false }
  commit({
    ...overlay,
    plans: upsert(overlay.plans, next),
    audits: [audit("update-plan", id, draft.assignedTo || "演示用户", "编辑维护计划"), ...overlay.audits],
  })
}

export function completePlan(planId: string, draft: RecordDraft): void {
  const plan = snapshot.plans.find((item) => item.id === planId)
  const asset = assetById(plan?.equipmentId ?? "")
  if (!plan) return
  const record: MaintenanceRecord = {
    ...draftFrom({ ...draft, equipmentId: plan.equipmentId, maintenancePlanId: plan.id }),
    id: `rec-local-${Date.now()}`,
    createdAt: new Date().toISOString(),
    revisions: [],
    dataMode: "demo-local",
    persisted: false,
  }
  const nextPlan: MaintenancePlan = {
    ...plan,
    lastCompletedAt: draft.performedAt.slice(0, 10),
    dataMode: "demo-local",
    persisted: false,
  }
  if (plan.intervalDays) nextPlan.nextDueAt = addDays(draft.performedAt, plan.intervalDays)
  if (plan.intervalOperatingHours) {
    if (asset?.operatingHours == null) {
      nextPlan.lastCompletedOperatingHours = undefined
      nextPlan.nextDueOperatingHours = undefined
    } else {
      nextPlan.lastCompletedOperatingHours = asset.operatingHours
      nextPlan.nextDueOperatingHours = asset.operatingHours + plan.intervalOperatingHours
    }
  }
  commit({
    ...overlay,
    records: [record, ...overlay.records],
    plans: upsert(overlay.plans, nextPlan),
    audits: [audit("complete-plan", plan.id, draft.operator, `完成计划并生成记录 ${record.id}`), ...overlay.audits],
  })
}

function draftFrom(draft: RecordDraft): Omit<MaintenanceRecord, "id" | "createdAt" | "revisions" | "dataMode" | "persisted"> {
  return {
    equipmentId: draft.equipmentId,
    maintenancePlanId: draft.maintenancePlanId || undefined,
    type: draft.type,
    performedAt: draft.performedAt.slice(0, 10),
    description: draft.description.trim(),
    operator: draft.operator.trim(),
    result: draft.result.trim(),
    replacedComponents: draft.replacedComponents.filter(Boolean),
    relatedEventIds: draft.relatedEventIds.filter(Boolean),
    diagnosisId: draft.diagnosisId || undefined,
    attachmentNames: draft.attachmentNames.filter(Boolean),
  }
}
