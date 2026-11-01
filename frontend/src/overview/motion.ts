import type { LineSnapshot, SystemId } from "./model"

/** 归一化运动只驱动动画。0.2 约等于演示数据里传动轴 96 rpm 与电机 486 rpm 的比例，不是标定转速。 */
export const DRIVE_RATIO = {
  motor: 1,
  coupling: 1,
  gearIn: 1,
  gearOut: 0.55,
  shaft: 0.2,
  roll: 0.2,
} as const

export const FLOW_RATE = {
  coolant: 0.82,
  lube: 0.58,
} as const

export type EquipmentMotionState = {
  equipmentId: string
  running: boolean
  speedNormalized?: number
  flowNormalized?: number
  direction?: 1 | -1
  alarmLevel?: "normal" | "warning" | "critical"
  dataMode: "live" | "simulation"
}

const SPEED: Record<string, number> = {
  "drv-motor": DRIVE_RATIO.motor,
  "drv-coupling": DRIVE_RATIO.coupling,
  "drv-gear": DRIVE_RATIO.shaft,
  "drv-shaft": DRIVE_RATIO.shaft,
  "drv-mill": DRIVE_RATIO.roll,
  "cw-pump": FLOW_RATE.coolant,
  "lb-pump": FLOW_RATE.lube,
}

const FLOW: Partial<Record<SystemId, number>> = {
  coolant: FLOW_RATE.coolant,
  lube: FLOW_RATE.lube,
}

export function motionStates(line: LineSnapshot): EquipmentMotionState[] {
  return line.equipment.map((item) => ({
    equipmentId: item.id,
    running: true,
    speedNormalized: SPEED[item.id],
    flowNormalized: FLOW[item.systemId],
    direction: 1,
    alarmLevel: item.status === "warn" ? "warning" : "normal",
    dataMode: "simulation",
  }))
}
