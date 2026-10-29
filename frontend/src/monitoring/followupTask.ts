export interface FollowupTask {
  task_id: string
  record_id: string
  device_id: string
  item_id: string
  title: string
  measurement_item: string
  purpose: string
  missing_evidence: string
  knowledge_ref: string
  data_type: string
  verifiable_condition: string
  priority: string
  priority_basis: string
  status: string
  gap_class: string
  upload_supported: boolean
  upload_mode?: string
  measurement_ids: string[]
  condition_text: string
  confirmed?: boolean
  supports_fault?: boolean
}

export const UNSUPPORTED_UPLOAD = "暂不支持直接上传此类测量"

export function isFollowupTask(value: unknown): value is FollowupTask {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.task_id === "string" && typeof row.title === "string" && typeof row.status === "string" && Array.isArray(row.measurement_ids)
}

export function followupStatusLabel(status: string): string {
  if (status === "可计算") return "已获得数值证据"
  if (status === "已获得可用证据") return "已获得数值证据"
  if (status === "已完成条款校验") return "条款已经得到校验"
  return status
}

export function followupSummary(task: FollowupTask): {
  item: string
  priority: string
  status: string
  missing: string
} {
  return {
    item: task.measurement_item || task.title,
    priority: task.priority,
    status: followupStatusLabel(task.status),
    missing: task.missing_evidence || task.condition_text || "—",
  }
}

export function followupLines(task: FollowupTask): string[] {
  const lines = [
    `测量项目：${task.measurement_item}`,
    `补测目的：${task.purpose}`,
    `当前缺失：${task.missing_evidence}`,
    `关联位置：${task.knowledge_ref}`,
    `建议优先级：${task.priority}。${task.priority_basis}`,
    `当前状态：${followupStatusLabel(task.status)}`,
    `可使用的数据类型：${task.data_type}`,
    `补测后可能验证：${task.verifiable_condition}`,
    task.measurement_ids.length ? `已关联补充测量：${task.measurement_ids.join("、")}` : "已关联补充测量：无",
    task.condition_text,
  ]
  if (!task.upload_supported) {
    lines.push(UNSUPPORTED_UPLOAD)
  } else if (task.upload_mode === "synchronous") {
    lines.push("可上传同步多通道 CSV。通道间相位差不能代替键相绝对相位。")
  } else if (task.upload_mode === "structured") {
    lines.push("可登记结构化轴向或其他测量证据。口头结论不会被接受。")
  }
  return lines
}
