import { DEMO_LINE } from "../overview/demoData"
import { equipmentProfile } from "../overview/equipmentProfile"
import { equipmentOf } from "../overview/model"
import type { WorkEvent } from "./types"

const SNAPSHOT_DAY = DEMO_LINE.updatedAt.slice(0, 10)

function at(clock: string): string {
  return `${SNAPSHOT_DAY}T${clock}`
}

function fromAlarms(): WorkEvent[] {
  return DEMO_LINE.alarms.map((alarm) => {
    const equipment = equipmentOf(DEMO_LINE, alarm.equipmentId)
    const metric = equipment?.metrics.find((item) => item.id === alarm.metricId)
    const occurredAt = at(alarm.time)
    if (alarm.kind === "inference") {
      return {
        id: alarm.id,
        type: "diagnosis",
        severity: "attention",
        title: alarm.title,
        description: alarm.note,
        equipmentId: alarm.equipmentId,
        equipmentName: equipmentProfile(alarm.equipmentId)?.name ?? equipment?.name,
        equipmentCode: equipment?.code,
        systemId: equipment?.systemId,
        occurredAt,
        receivedAt: occurredAt,
        source: "inference",
        status: "review",
        relatedEventIds: [],
        diagnosisId: `dx-${alarm.id}`,
        snapshotId: DEMO_LINE.id,
        dataMode: "simulated",
        metricId: alarm.metricId,
        metricLabel: metric?.label,
        actualValue: metric ? `${metric.value} ${metric.unit}`.trim() : undefined,
        threshold: "源记录只写了演示阈值，没有给出数值",
        series: metric?.series,
        diagnosis: {
          taskId: `dx-${alarm.id}`,
          candidates: [alarm.title],
          evidence: [alarm.note, metric ? `${metric.label} 当前值 ${metric.value} ${metric.unit}`.trim() : "没有对应测点读数"].filter(Boolean),
          modelNote: "未接入诊断模型，没有算法名称或置信度输出。下列内容只复述已有推断，不是已确认故障。",
        },
      }
    }
    return {
      id: alarm.id,
      type: "alarm",
      severity: "warning",
      title: alarm.title,
      description: alarm.note,
      equipmentId: alarm.equipmentId,
      equipmentName: equipmentProfile(alarm.equipmentId)?.name ?? equipment?.name,
      equipmentCode: equipment?.code,
      systemId: equipment?.systemId,
      occurredAt,
      receivedAt: occurredAt,
      source: "sensor",
      status: "pending",
      relatedEventIds: [],
      snapshotId: DEMO_LINE.id,
      dataMode: "simulated",
      metricId: alarm.metricId,
      metricLabel: metric?.label,
      actualValue: metric ? `${metric.value} ${metric.unit}`.trim() : undefined,
      threshold: "源记录只写了演示阈值，没有给出数值",
      series: metric?.series,
    }
  })
}

const EXTRA: WorkEvent[] = [
  {
    id: "op-condition",
    type: "operation",
    severity: "info",
    title: "演示工况保持稳态轧制",
    description: DEMO_LINE.condition,
    occurredAt: at("18:40:00"),
    receivedAt: at("18:40:00"),
    source: "operator",
    status: "recorded",
    relatedEventIds: [],
    snapshotId: DEMO_LINE.id,
    dataMode: "simulated",
  },
  {
    id: "sys-snapshot",
    type: "system",
    severity: "info",
    title: "模拟快照已装载",
    description: "首页与事件页使用同一份模拟快照，未连接 PLC。",
    occurredAt: at("18:40:00"),
    receivedAt: at("18:40:00"),
    source: "system",
    status: "recorded",
    relatedEventIds: [],
    snapshotId: DEMO_LINE.id,
    dataMode: "simulated",
    log: {
      level: "info",
      module: "overview.snapshot",
      message: `已装载快照 ${DEMO_LINE.id}，更新时间 ${DEMO_LINE.updatedAt}`,
      detail: JSON.stringify({ snapshotId: DEMO_LINE.id, updatedAt: DEMO_LINE.updatedAt, dataMode: "simulated" }, null, 2),
    },
  },
]

export function workEvents(): WorkEvent[] {
  return [...fromAlarms(), ...EXTRA].sort((left, right) => right.occurredAt.localeCompare(left.occurredAt))
}

export function eventById(id: string): WorkEvent | undefined {
  return workEvents().find((item) => item.id === id)
}

export function latestInstant(events: WorkEvent[]): number {
  return events.reduce((max, item) => Math.max(max, Date.parse(item.occurredAt)), 0)
}
