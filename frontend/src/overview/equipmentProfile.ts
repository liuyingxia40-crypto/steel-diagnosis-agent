export type EquipmentProfile = {
  name: string
  type: string
  models: string
  power: string
  speed: string
}

const PROFILES: Record<string, EquipmentProfile> = {
  "drv-motor": {
    name: "棒线主轧机电机",
    type: "三相异步电动机",
    models: "YKK 800-4 5500kW，YKK 1000-4 7200kW",
    power: "5500、7200、9000 kW",
    speed: "1500 rpm",
  },
  "drv-gear": {
    name: "棒线主减速机/齿轮箱",
    type: "三级减速硬齿面齿轮箱",
    models: "M3RHF 12.5 紧凑型，DCY 800-12.5",
    power: "5500 kW",
    speed: "1500 rpm",
  },
  "cw-pump": {
    name: "棒线冷却水循环泵",
    type: "单级离心泵",
    models: "KQDP200-50，ISG200-250",
    power: "200 kW",
    speed: "2950 rpm",
  },
}

export function equipmentProfile(id: string): EquipmentProfile | undefined {
  return PROFILES[id]
}
