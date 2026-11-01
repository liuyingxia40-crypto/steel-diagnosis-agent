import type { LineSnapshot, MetricPoint } from "./model"

function series(seed: number, count = 24): number[] {
  return Array.from({ length: count }, (_, index) => {
    const wave = Math.sin(index / 3 + seed) * 4 + Math.cos(index / 5 + seed) * 2
    return Math.round((seed * 8 + 40 + wave) * 10) / 10
  })
}

function metric(id: string, label: string, value: string, unit: string, direction: MetricPoint["direction"], seed: number): MetricPoint {
  const points = series(seed)
  const numeric = Number(value)
  if (Number.isFinite(numeric)) {
    points[points.length - 1] = numeric
  }
  return { id, label, value, unit, direction, series: points }
}

/** 首页设备、参数和报警均为模拟快照，可整体替换为采集接口。 */
export const DEMO_LINE: LineSnapshot = {
  id: "bar-line",
  name: "棒材产线",
  updatedAt: "2026-10-30 18:40:00",
  condition: "稳态轧制 · 模拟工况",
  overviewMetrics: [
    metric("line-speed", "轧机转速", "486", "rpm", "flat", 6),
    metric("line-flow", "冷却水流量", "86", "m³/h", "down", 4),
    metric("line-oil", "供油压力", "0.28", "MPa", "flat", 3),
  ],
  systems: [
    {
      id: "drive",
      name: "棒材主传动链",
      summary: "高压供电进入主电机，经梅花联轴器到主减速机，再由万向轴传到轧辊。",
      metrics: [
        metric("drv-rpm", "电机转速", "486", "rpm", "flat", 6),
        metric("drv-current", "电流", "312", "A", "up", 5),
        metric("drv-torque", "扭矩", "18.4", "kN·m", "flat", 4),
        metric("drv-temp", "轴承温度", "68", "°C", "up", 7),
        metric("drv-rms", "振动 RMS", "1.8", "mm/s", "flat", 2),
      ],
    },
    {
      id: "coolant",
      name: "棒材冷却水回路",
      summary: "净循环水池经吸入管路到循环泵，压力管路进入轧机冷却水管路，在轧辊和轧件处冷却后回水。",
      metrics: [
        metric("cw-pressure", "供水压力", "0.42", "MPa", "flat", 3),
        metric("cw-flow", "流量", "86", "m³/h", "down", 4),
        metric("cw-supply", "供水温度", "27", "°C", "flat", 2),
        metric("cw-return", "回水温度", "36.6", "°C", "up", 5),
        metric("cw-pump", "水泵状态", "运行", "", "flat", 1),
      ],
    },
    {
      id: "lube",
      name: "稀油润滑系统",
      summary: "油箱经吸入管路到油泵，油冷却器与过滤器之间为压力管路，再送到主电机和齿轮箱轴承。",
      metrics: [
        metric("lb-pressure", "供油压力", "0.28", "MPa", "flat", 3),
        metric("lb-temp", "油温", "41", "°C", "flat", 4),
        metric("lb-level", "油箱液位", "72", "%", "down", 6),
        metric("lb-dp", "过滤器压差", "38", "kPa", "up", 3),
        metric("lb-pump", "油泵状态", "运行", "", "flat", 1),
      ],
    },
  ],
  equipment: [
    {
      id: "drv-hv",
      systemId: "drive",
      name: "高压供电",
      code: "HV-01",
      status: "normal",
      metrics: [
        metric("hv-kv", "母线电压", "10", "kV", "flat", 2),
        metric("hv-state", "进线开关", "合闸", "", "flat", 1),
      ],
    },
    {
      id: "drv-motor",
      systemId: "drive",
      name: "主电机",
      code: "M-01",
      status: "normal",
      metrics: [
        metric("m-rpm", "转速", "486", "rpm", "flat", 6),
        metric("m-current", "电流", "312", "A", "up", 5),
        metric("m-temp", "轴承温度", "61", "°C", "flat", 4),
      ],
    },
    {
      id: "drv-coupling",
      systemId: "drive",
      name: "梅花联轴器",
      code: "C-01",
      status: "normal",
      metrics: [
        metric("c-rms", "振动 RMS", "1.2", "mm/s", "flat", 2),
        metric("c-temp", "表面温度", "46", "°C", "flat", 3),
      ],
    },
    {
      id: "drv-gear",
      systemId: "drive",
      name: "主减速机",
      code: "G-01",
      status: "warn",
      metrics: [
        metric("g-temp", "轴承温度", "68", "°C", "up", 7),
        metric("g-rms", "振动 RMS", "2.4", "mm/s", "up", 3),
        metric("g-torque", "输出扭矩", "18.4", "kN·m", "flat", 4),
      ],
    },
    {
      id: "drv-shaft",
      systemId: "drive",
      name: "万向轴",
      code: "S-01",
      status: "normal",
      metrics: [
        metric("s-rpm", "转速", "96", "rpm", "flat", 5),
        metric("s-rms", "振动 RMS", "1.1", "mm/s", "flat", 2),
      ],
    },
    {
      id: "drv-mill",
      systemId: "drive",
      name: "轧辊",
      code: "RM-01",
      status: "normal",
      metrics: [
        metric("rm-rpm", "轧辊转速", "96", "rpm", "flat", 5),
        metric("rm-temp", "辊身温度", "54", "°C", "flat", 4),
        metric("rm-rms", "机架振动", "1.6", "mm/s", "flat", 2),
      ],
    },
    {
      id: "cw-supply",
      systemId: "coolant",
      name: "净循环水池",
      code: "WS-01",
      status: "normal",
      metrics: [
        metric("ws-temp", "供水温度", "27", "°C", "flat", 2),
        metric("ws-level", "水池液位", "81", "%", "flat", 6),
      ],
    },
    {
      id: "cw-pump",
      systemId: "coolant",
      name: "冷却水循环泵",
      code: "P-01",
      status: "normal",
      metrics: [
        metric("wp-state", "运行状态", "运行", "", "flat", 1),
        metric("wp-pressure", "出口压力", "0.42", "MPa", "flat", 3),
        metric("wp-current", "电流", "74", "A", "flat", 4),
      ],
    },
    {
      id: "cw-pipe",
      systemId: "coolant",
      name: "轧机冷却水管路",
      code: "PL-01",
      status: "normal",
      metrics: [
        metric("pl-flow", "流量", "86", "m³/h", "down", 4),
        metric("pl-pressure", "沿线压力", "0.38", "MPa", "flat", 3),
      ],
    },
    {
      id: "cw-spray",
      systemId: "coolant",
      name: "轧辊/轧件冷却",
      code: "SP-01",
      status: "warn",
      metrics: [
        metric("sp-flow", "喷淋流量", "86", "m³/h", "down", 4),
        metric("sp-delta", "供回水温差", "9.6", "°C", "up", 5),
      ],
    },
    {
      id: "cw-return",
      systemId: "coolant",
      name: "回水",
      code: "WR-01",
      status: "normal",
      metrics: [
        metric("wr-temp", "回水温度", "36.6", "°C", "up", 5),
        metric("wr-flow", "回水流量", "84", "m³/h", "flat", 4),
      ],
    },
    {
      id: "lb-tank",
      systemId: "lube",
      name: "油箱",
      code: "TK-01",
      status: "normal",
      metrics: [
        metric("tk-level", "液位", "72", "%", "down", 6),
        metric("tk-temp", "油温", "41", "°C", "flat", 4),
      ],
    },
    {
      id: "lb-pump",
      systemId: "lube",
      name: "油泵",
      code: "P-02",
      status: "normal",
      metrics: [
        metric("op-state", "运行状态", "运行", "", "flat", 1),
        metric("op-pressure", "出口压力", "0.28", "MPa", "flat", 3),
      ],
    },
    {
      id: "lb-cooler",
      systemId: "lube",
      name: "油冷却器",
      code: "CL-01",
      status: "normal",
      metrics: [
        metric("cl-in", "进口油温", "48", "°C", "flat", 4),
        metric("cl-out", "出口油温", "41", "°C", "down", 3),
      ],
    },
    {
      id: "lb-filter",
      systemId: "lube",
      name: "过滤器",
      code: "F-01",
      status: "normal",
      metrics: [metric("ft-dp", "压差", "38", "kPa", "up", 3)],
    },
    {
      id: "lb-point",
      systemId: "lube",
      name: "齿轮箱轴承",
      code: "BR-G",
      status: "normal",
      metrics: [
        metric("br-temp", "轴承温度", "57", "°C", "flat", 4),
        metric("br-flow", "供油流量", "18", "L/min", "flat", 2),
      ],
    },
    {
      id: "lb-motor-bearing",
      systemId: "lube",
      name: "主电机轴承",
      code: "BR-M",
      status: "normal",
      metrics: [metric("mb-temp", "轴承温度", "61", "°C", "flat", 4)],
    },
  ],
  alarms: [
    {
      id: "al-gear",
      equipmentId: "drv-gear",
      level: "warn",
      kind: "sensor",
      title: "主减速机轴承温度高于演示阈值",
      time: "18:36:12",
      note: "来自温度传感器读数。尚未确认故障。",
      metricId: "g-temp",
    },
    {
      id: "al-spray",
      equipmentId: "cw-spray",
      level: "warn",
      kind: "inference",
      title: "轧辊冷却温差偏大，冷却覆盖可能不足",
      time: "18:28:40",
      note: "这是根据温差做出的推断，不是已确认故障。",
      metricId: "sp-delta",
    },
  ],
}

export const TREND_RANGES = [
  { id: "15m", label: "15 分钟", count: 8 },
  { id: "1h", label: "1 小时", count: 16 },
  { id: "8h", label: "8 小时", count: 24 },
] as const
