export type SystemId = "drive" | "coolant" | "lube"

export type ViewSelection = { type: "overview" } | { type: "system"; id: SystemId } | { type: "equipment"; id: string }

export type EquipStatus = "normal" | "warn"

export type AlarmKind = "sensor" | "inference"

export interface MetricPoint {
  id: string
  label: string
  value: string
  unit: string
  direction: "up" | "down" | "flat"
  series: number[]
}

export interface EquipmentNode {
  id: string
  systemId: SystemId
  name: string
  code: string
  status: EquipStatus
  metrics: MetricPoint[]
}

export interface AlarmEvent {
  id: string
  equipmentId: string
  level: EquipStatus
  kind: AlarmKind
  title: string
  time: string
  note: string
  metricId?: string
}

export interface ProcessSystem {
  id: SystemId
  name: string
  summary: string
  metrics: MetricPoint[]
}

export interface LineSnapshot {
  id: string
  name: string
  updatedAt: string
  condition: string
  systems: ProcessSystem[]
  equipment: EquipmentNode[]
  alarms: AlarmEvent[]
  overviewMetrics: MetricPoint[]
}

export const SYSTEM_ORDER: SystemId[] = ["drive", "coolant", "lube"]

export function systemOf(line: LineSnapshot, id: SystemId): ProcessSystem {
  const found = line.systems.find((item) => item.id === id)
  if (!found) {
    throw new Error(`缺少系统 ${id}`)
  }
  return found
}

export function equipmentOf(line: LineSnapshot, id: string): EquipmentNode | undefined {
  return line.equipment.find((item) => item.id === id)
}

export function selectionSystem(line: LineSnapshot, selection: ViewSelection): SystemId | null {
  if (selection.type === "system") {
    return selection.id
  }
  if (selection.type === "equipment") {
    return equipmentOf(line, selection.id)?.systemId ?? null
  }
  return null
}

export function activeMetrics(line: LineSnapshot, selection: ViewSelection): MetricPoint[] {
  if (selection.type === "equipment") {
    return equipmentOf(line, selection.id)?.metrics ?? line.overviewMetrics
  }
  if (selection.type === "system") {
    return systemOf(line, selection.id).metrics
  }
  return line.overviewMetrics
}

export function activeAlarms(line: LineSnapshot, selection: ViewSelection): AlarmEvent[] {
  if (selection.type === "equipment") {
    return line.alarms.filter((item) => item.equipmentId === selection.id)
  }
  if (selection.type === "system") {
    const ids = new Set(line.equipment.filter((item) => item.systemId === selection.id).map((item) => item.id))
    return line.alarms.filter((item) => ids.has(item.equipmentId))
  }
  return line.alarms
}

export function headline(line: LineSnapshot, selection: ViewSelection): { title: string; code: string; status: EquipStatus | "mixed" } {
  if (selection.type === "equipment") {
    const equipment = equipmentOf(line, selection.id)
    if (equipment) {
      return { title: equipment.name, code: equipment.code, status: equipment.status }
    }
  }
  if (selection.type === "system") {
    const system = systemOf(line, selection.id)
    const warned = line.equipment.some((item) => item.systemId === system.id && item.status === "warn")
    return { title: system.name, code: "系统", status: warned ? "warn" : "normal" }
  }
  const warned = line.alarms.length > 0
  return { title: line.name, code: "产线", status: warned ? "mixed" : "normal" }
}
