import { HEALTH_AS_OF } from "./catalog"
import type { EquipmentAsset, MaintenancePlan, MaintenanceRecord, MaintenanceStatus } from "./types"

export function addDays(date: string, days: number): string {
  const [year, month, day] = date.slice(0, 10).split("-").map(Number)
  const next = new Date(Date.UTC(year, month - 1, day))
  next.setUTCDate(next.getUTCDate() + days)
  return next.toISOString().slice(0, 10)
}

export function serviceYears(commissioning: string | undefined, asOf = HEALTH_AS_OF): string {
  if (!commissioning) return "未知"
  const start = Date.parse(`${commissioning.slice(0, 10)}T00:00:00Z`)
  const end = Date.parse(`${asOf.slice(0, 10)}T00:00:00Z`)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "未知"
  const years = (end - start) / (365.25 * 24 * 60 * 60 * 1000)
  return `${years.toFixed(1)} 年`
}

export type PlanSignal = "overdue" | "due" | "ok" | "unknown"

export function planSignal(plan: MaintenancePlan, asset: EquipmentAsset | undefined, asOf = HEALTH_AS_OF, windowDays = 30): PlanSignal {
  if (!plan.enabled) return "ok"
  const signals: PlanSignal[] = []
  if (plan.intervalDays || plan.nextDueAt) {
    if (!plan.nextDueAt) signals.push("unknown")
    else if (plan.nextDueAt < asOf) signals.push("overdue")
    else if (plan.nextDueAt <= addDays(asOf, windowDays)) signals.push("due")
    else signals.push("ok")
  }
  if (plan.intervalOperatingHours || plan.nextDueOperatingHours != null) {
    if (plan.nextDueOperatingHours == null || asset?.operatingHours == null) signals.push("unknown")
    else if (asset.operatingHours >= plan.nextDueOperatingHours) signals.push("overdue")
    else {
      const remaining = plan.nextDueOperatingHours - asset.operatingHours
      const soon = Math.max(200, Math.round((plan.intervalOperatingHours ?? remaining) * 0.1))
      signals.push(remaining <= soon ? "due" : "ok")
    }
  }
  if (signals.length === 0) return "unknown"
  if (signals.includes("overdue")) return "overdue"
  if (signals.includes("due")) return "due"
  if (signals.every((item) => item === "unknown")) return "unknown"
  return "ok"
}

export function equipmentStatus(plans: MaintenancePlan[], asset: EquipmentAsset, asOf = HEALTH_AS_OF, windowDays = 30): MaintenanceStatus {
  const enabled = plans.filter((item) => item.equipmentId === asset.id && item.enabled)
  if (enabled.length === 0) return "unplanned"
  const signals = enabled.map((item) => planSignal(item, asset, asOf, windowDays))
  if (signals.includes("overdue")) return "overdue"
  if (signals.includes("due")) return "due"
  if (signals.every((item) => item === "unknown")) return "unknown"
  return "ok"
}

export function latestRecord(records: MaintenanceRecord[], equipmentId: string): MaintenanceRecord | undefined {
  return records
    .filter((item) => item.equipmentId === equipmentId)
    .sort((left, right) => right.performedAt.localeCompare(left.performedAt))[0]
}

export function latestPreventive(records: MaintenanceRecord[], equipmentId: string): MaintenanceRecord | undefined {
  return records
    .filter((item) => item.equipmentId === equipmentId && item.type === "preventive")
    .sort((left, right) => right.performedAt.localeCompare(left.performedAt))[0]
}

export function nextCareLabel(plans: MaintenancePlan[], asset: EquipmentAsset, asOf = HEALTH_AS_OF, windowDays = 30): string {
  const enabled = plans.filter((item) => item.equipmentId === asset.id && item.enabled)
  if (enabled.length === 0) return "未制定"
  const ranked = [...enabled].sort((left, right) => rankSignal(planSignal(left, asset, asOf, windowDays)) - rankSignal(planSignal(right, asset, asOf, windowDays)))
  const primary = ranked[0]
  const signal = planSignal(primary, asset, asOf, windowDays)
  const hoursTrigger = primary.nextDueOperatingHours != null && asset.operatingHours != null && (signal === "overdue" || signal === "due") && asset.operatingHours + 1 > (primary.nextDueOperatingHours ?? 0) - Math.max(200, Math.round((primary.intervalOperatingHours ?? 0) * 0.1))
  if (hoursTrigger && primary.nextDueOperatingHours != null && (primary.nextDueAt == null || signal === "overdue" && primary.nextDueAt >= asOf)) {
    return `运行满 ${primary.nextDueOperatingHours} h`
  }
  if (primary.nextDueAt) return primary.nextDueAt
  if (primary.nextDueOperatingHours != null) return `运行满 ${primary.nextDueOperatingHours} h`
  return "未排期"
}

function rankSignal(signal: PlanSignal): number {
  if (signal === "overdue") return 0
  if (signal === "due") return 1
  if (signal === "unknown") return 2
  return 3
}

export function sameMonth(date: string, asOf = HEALTH_AS_OF): boolean {
  return date.slice(0, 7) === asOf.slice(0, 7)
}

export const STATUS_RANK: Record<MaintenanceStatus, number> = {
  overdue: 0,
  due: 1,
  unknown: 2,
  ok: 3,
  unplanned: 4,
}
