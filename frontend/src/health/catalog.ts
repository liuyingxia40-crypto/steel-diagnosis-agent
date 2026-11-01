import { DEMO_LINE } from "../overview/demoData"
import { equipmentProfile } from "../overview/equipmentProfile"
import { systemOf } from "../overview/model"
import type { EquipmentAsset, MaintenancePlan, MaintenanceRecord } from "./types"

/** 维护状态以这份模拟快照为“当前时刻”，与首页同一时间基准。 */
export const HEALTH_AS_OF = DEMO_LINE.updatedAt.slice(0, 10)

const EXTRA: Record<string, { commissioningDate?: string; operatingHours?: number; equipmentType?: string }> = {
  "drv-gear": { equipmentType: "减速机", commissioningDate: "2022-03-18", operatingHours: 18640 },
  "drv-motor": { equipmentType: "电机", commissioningDate: "2023-09-01", operatingHours: 12480 },
  "cw-pump": { equipmentType: "泵", commissioningDate: "2021-02-11", operatingHours: 22150 },
  "lb-tank": { equipmentType: "油箱", commissioningDate: "2022-11-06", operatingHours: 7900 },
  "drv-mill": { equipmentType: "轧机", commissioningDate: "2021-07-19", operatingHours: 7100 },
}

function equipmentType(id: string, name: string): string {
  const named = EXTRA[id]?.equipmentType
  if (named) return named
  if (name.includes("电机")) return "电机"
  if (name.includes("减速")) return "减速机"
  if (name.includes("泵")) return "泵"
  if (name.includes("油箱")) return "油箱"
  if (name.includes("轧辊") && !name.includes("冷却")) return "轧机"
  if (name.includes("过滤")) return "过滤器"
  if (name.includes("冷却器")) return "冷却器"
  if (name.includes("联轴")) return "联轴器"
  return name
}

export function equipmentAssets(): EquipmentAsset[] {
  return DEMO_LINE.equipment.map((item) => {
    const profile = equipmentProfile(item.id)
    const extra = EXTRA[item.id]
    return {
      id: item.id,
      name: item.name,
      code: item.code,
      plateName: profile?.name,
      equipmentType: equipmentType(item.id, item.name),
      systemId: item.systemId,
      systemName: systemOf(DEMO_LINE, item.systemId).name,
      commissioningDate: extra?.commissioningDate,
      operatingHours: extra?.operatingHours,
      model: profile?.models,
      dataMode: "simulated",
    }
  })
}

export function assetById(id: string): EquipmentAsset | undefined {
  return equipmentAssets().find((item) => item.id === id)
}

const simulated = { dataMode: "simulated" as const, persisted: false as const, revisions: [] }

export const SEED_PLANS: MaintenancePlan[] = [
  {
    id: "plan-gear-lube",
    equipmentId: "drv-gear",
    title: "减速机润滑与密封检查",
    type: "preventive",
    intervalDays: 90,
    lastCompletedAt: "2026-08-05",
    nextDueAt: "2026-11-03",
    assignedTo: "演示维护班",
    enabled: true,
    ...simulated,
  },
  {
    id: "plan-pump-bearing",
    equipmentId: "cw-pump",
    title: "循环泵轴承与密封检查",
    type: "inspection",
    intervalDays: 120,
    lastCompletedAt: "2026-05-20",
    nextDueAt: "2026-09-17",
    assignedTo: "演示维护班",
    enabled: true,
    ...simulated,
  },
  {
    id: "plan-motor-service",
    equipmentId: "drv-motor",
    title: "主电机定期保养",
    type: "preventive",
    intervalDays: 180,
    lastCompletedAt: "2026-09-15",
    nextDueAt: "2027-03-14",
    assignedTo: "演示维护班",
    enabled: true,
    ...simulated,
  },
  {
    id: "plan-tank-clean",
    equipmentId: "lb-tank",
    title: "油箱清污",
    type: "preventive",
    intervalOperatingHours: 4000,
    lastCompletedOperatingHours: 4000,
    nextDueOperatingHours: 8000,
    assignedTo: "演示维护班",
    enabled: true,
    ...simulated,
  },
  {
    id: "plan-mill-either",
    equipmentId: "drv-mill",
    title: "轧辊轴承检查",
    type: "preventive",
    intervalDays: 365,
    intervalOperatingHours: 6000,
    lastCompletedAt: "2026-01-10",
    lastCompletedOperatingHours: 1000,
    nextDueAt: "2027-01-10",
    nextDueOperatingHours: 7000,
    assignedTo: "演示维护班",
    enabled: true,
    ...simulated,
  },
]

export const SEED_RECORDS: MaintenanceRecord[] = [
  {
    id: "rec-gear-20260805",
    equipmentId: "drv-gear",
    maintenancePlanId: "plan-gear-lube",
    type: "preventive",
    performedAt: "2026-08-05",
    description: "更换减速机润滑油，检查轴承和密封。演示档案，不是现场工单。",
    operator: "演示维护班",
    result: "已完成",
    replacedComponents: ["润滑油"],
    createdAt: "2026-08-05T09:00:00",
    ...simulated,
  },
  {
    id: "rec-gear-20260611",
    equipmentId: "drv-gear",
    type: "corrective",
    performedAt: "2026-06-11",
    description: "处理一次密封渗油。这次故障维修没有关闭定期润滑计划。",
    operator: "演示维护班",
    result: "已完成",
    replacedComponents: ["轴封"],
    createdAt: "2026-06-11T14:20:00",
    ...simulated,
  },
  {
    id: "rec-pump-20260520",
    equipmentId: "cw-pump",
    maintenancePlanId: "plan-pump-bearing",
    type: "inspection",
    performedAt: "2026-05-20",
    description: "检查循环泵轴承温度和机械密封。演示档案。",
    operator: "演示维护班",
    result: "已完成",
    createdAt: "2026-05-20T10:00:00",
    ...simulated,
  },
  {
    id: "rec-motor-20260915",
    equipmentId: "drv-motor",
    maintenancePlanId: "plan-motor-service",
    type: "preventive",
    performedAt: "2026-09-15",
    description: "完成主电机定期保养，清洁风路并紧固接线。演示档案。",
    operator: "演示维护班",
    result: "已完成",
    createdAt: "2026-09-15T08:30:00",
    ...simulated,
  },
  {
    id: "rec-motor-20261006",
    equipmentId: "drv-motor",
    type: "inspection",
    performedAt: "2026-10-06",
    description: "本月巡检，记录轴承温度。这次巡检不代替定期保养。",
    operator: "演示维护班",
    result: "已完成",
    createdAt: "2026-10-06T11:00:00",
    ...simulated,
  },
  {
    id: "rec-mill-20260110",
    equipmentId: "drv-mill",
    maintenancePlanId: "plan-mill-either",
    type: "preventive",
    performedAt: "2026-01-10",
    description: "检查轧辊轴承。演示档案。",
    operator: "演示维护班",
    result: "已完成",
    createdAt: "2026-01-10T09:40:00",
    ...simulated,
  },
  {
    id: "rec-tank-hours",
    equipmentId: "lb-tank",
    maintenancePlanId: "plan-tank-clean",
    type: "preventive",
    performedAt: "2025-12-02",
    description: "按运行小时完成一次油箱清污。演示档案没有现场化验单。",
    operator: "演示维护班",
    result: "已完成",
    createdAt: "2025-12-02T15:00:00",
    ...simulated,
  },
]
