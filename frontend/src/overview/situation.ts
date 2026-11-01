import { equipmentOf, type AlarmEvent, type LineSnapshot, type SystemId } from "./model"

export type HealthStatus = "healthy" | "attention" | "warning" | "critical" | "unknown"

export type VisualizationMode = "operation" | "health" | "replay"

export interface EquipmentHealth {
  equipmentId: string
  status: HealthStatus
  evaluatedAt?: string
  score?: number
  indicators: {
    name: string
    value: number
    unit: string
    status: HealthStatus
  }[]
  dataSource: "live" | "simulated"
}

export interface ProductionEvent {
  id: string
  timestamp: string
  equipmentId: string
  systemId: SystemId
  type: "operation" | "alarm" | "diagnosis" | "maintenance"
  severity: "info" | "attention" | "warning" | "critical"
  title: string
  description?: string
  source: "live" | "simulated"
  snapshotId?: string
}

/** 已评估状态的严重程度。unknown 不参与比较。 */
const KNOWN_RANK: Record<Exclude<HealthStatus, "unknown">, number> = {
  healthy: 0,
  attention: 1,
  warning: 2,
  critical: 3,
}

/**
 * 系统态势取已评估设备中的最严重状态。
 * 全部没有有效评估时返回 unknown，不把缺数据当成正常。
 */
export function aggregateHealth(statuses: HealthStatus[]): HealthStatus {
  const known = statuses.filter((item): item is Exclude<HealthStatus, "unknown"> => item !== "unknown")
  if (known.length === 0) {
    return "unknown"
  }
  return known.reduce((worst, item) => (KNOWN_RANK[item] > KNOWN_RANK[worst] ? item : worst))
}

export const HEALTH_LABEL: Record<HealthStatus, string> = {
  healthy: "正常",
  attention: "关注",
  warning: "异常",
  critical: "异常",
  unknown: "未评估",
}

function indicatorStatus(alarm: AlarmEvent | undefined): HealthStatus {
  if (!alarm) {
    return "unknown"
  }
  if (alarm.kind === "inference") {
    return "attention"
  }
  return alarm.level === "warn" ? "warning" : "unknown"
}

/** 健康结论只来自已有报警及其对应指标，不生成健康分。 */
export function equipmentHealth(line: LineSnapshot): EquipmentHealth[] {
  return line.equipment.map((item) => {
    const alarms = line.alarms.filter((alarm) => alarm.equipmentId === item.id)
    const indicators = item.metrics.flatMap((metric) => {
      const value = Number(metric.value)
      if (!Number.isFinite(value)) {
        return []
      }
      const alarm = alarms.find((entry) => entry.metricId === metric.id)
      return [{ name: metric.label, value, unit: metric.unit, status: indicatorStatus(alarm) }]
    })
    const status = aggregateHealth(indicators.map((indicator) => indicator.status))
    return {
      equipmentId: item.id,
      status,
      evaluatedAt: status === "unknown" ? undefined : line.updatedAt,
      indicators,
      dataSource: "simulated",
    }
  })
}

export function abnormalCount(health: EquipmentHealth[]): number {
  return health.filter((item) => item.status === "warning" || item.status === "critical").length
}

/** 物理连接。诊断推断不写在这里。 */
const PHYSICAL_LINKS: [string, string][] = [
  ["drv-hv", "drv-motor"],
  ["drv-motor", "drv-coupling"],
  ["drv-coupling", "drv-gear"],
  ["drv-gear", "drv-shaft"],
  ["drv-shaft", "drv-mill"],
  ["cw-supply", "cw-pump"],
  ["cw-pump", "cw-pipe"],
  ["cw-pipe", "cw-spray"],
  ["cw-spray", "cw-return"],
  ["lb-tank", "lb-pump"],
  ["lb-pump", "lb-cooler"],
  ["lb-cooler", "lb-filter"],
  ["lb-filter", "lb-point"],
  ["lb-filter", "lb-motor-bearing"],
  ["lb-motor-bearing", "drv-motor"],
  ["lb-point", "drv-gear"],
]

/** 现有推断报警对应的可能关联，不是已确认因果。 */
const DIAGNOSTIC_LINKS: { from: string; to: string; note: string }[] = [
  { from: "cw-spray", to: "drv-mill", note: "冷却覆盖可能不足，尚未确认因果" },
]

export function relatedEquipment(equipmentId: string): { physical: string[]; diagnostic: { id: string; note: string }[] } {
  const diagnostic = DIAGNOSTIC_LINKS.flatMap((link) => {
    if (link.from === equipmentId) {
      return [{ id: link.to, note: link.note }]
    }
    if (link.to === equipmentId) {
      return [{ id: link.from, note: link.note }]
    }
    return []
  })
  const diagnosticIds = new Set(diagnostic.map((item) => item.id))
  const physical = PHYSICAL_LINKS.flatMap(([left, right]) => {
    if (left === equipmentId) {
      return [right]
    }
    if (right === equipmentId) {
      return [left]
    }
    return []
  }).filter((id) => !diagnosticIds.has(id))
  return { physical, diagnostic }
}

export function productionEvents(line: LineSnapshot): ProductionEvent[] {
  const day = line.updatedAt.slice(0, 10)
  return line.alarms
    .map((alarm) => {
      const equipment = equipmentOf(line, alarm.equipmentId)
      const inference = alarm.kind === "inference"
      return {
        id: alarm.id,
        timestamp: `${day} ${alarm.time}`,
        equipmentId: alarm.equipmentId,
        systemId: equipment?.systemId ?? "drive",
        type: inference ? "diagnosis" : "alarm",
        severity: inference ? "attention" : "warning",
        title: alarm.title,
        description: alarm.note,
        source: "simulated" as const,
        snapshotId: line.id,
      } satisfies ProductionEvent
    })
    .sort((left, right) => left.timestamp.localeCompare(right.timestamp))
}

export function eventClock(timestamp: string): string {
  const clock = timestamp.slice(11, 16)
  return clock || timestamp.slice(0, 5)
}

export const EVENT_TYPE_LABEL: Record<ProductionEvent["type"], string> = {
  operation: "工况",
  alarm: "告警",
  diagnosis: "推断",
  maintenance: "维护",
}
