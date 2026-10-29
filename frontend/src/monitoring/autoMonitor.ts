/** 设备运行监测页使用的自动监测状态。不在这里判断异常。 */

export const AUTO_POLL_MS = 2000
export const AUTO_MODE_LABEL = "官方历史数据模拟监测"
export const MISSING_AUTO_TASK_MESSAGE = "没有找到自动监测任务。"
export const DUPLICATE_AUTO_TASK_MESSAGE = "这台设备已有自动监测任务正在运行。"
export const DEMO_PLAYBACK_NOTE = "这是页面演示回放，只移动当前查看位置，不会分析记录，也不会写入监测事件。"

const ACTIVE_STATUSES = new Set(["运行中", "正在停止"])

export interface AutoMonitorTask {
  task_id: string
  device_id: string
  status: string
  current_record_id: string
  processed_count: number
  deviation_count: number
  total_count: number
  last_processed_at: string
  error_message: string
  monitoring_note: string
}

export function startRequestBody(deviceId: string): { device_id: string; interval_seconds: 1 } {
  return { device_id: deviceId, interval_seconds: 1 }
}

export function shouldPollAuto(status: string): boolean {
  return ACTIVE_STATUSES.has(status)
}

export function autoHeadline(status: string, errorMessage: string): string {
  if (status === "运行中" || status === "正在停止") {
    return "历史数据模拟监测中"
  }
  if (status === "已停止") {
    return "已停止"
  }
  if (status === "已结束") {
    return "已完成"
  }
  const error = errorMessage.trim()
  if (error) {
    return error
  }
  if (status === "已出错" || status === "已中断") {
    return "监测没有完成。"
  }
  return "尚未开始自动监测"
}

export function apiMessage(body: unknown): string {
  if (typeof body !== "object" || body === null || !("message" in body)) {
    return ""
  }
  const message = body.message
  return typeof message === "string" ? message : ""
}

export function isMissingAutoTask(status: number, body: unknown): boolean {
  return status === 404 && apiMessage(body) === MISSING_AUTO_TASK_MESSAGE
}

export function duplicateStartText(status: number, body: unknown): string {
  if (status !== 409) {
    return ""
  }
  return apiMessage(body) || DUPLICATE_AUTO_TASK_MESSAGE
}

export function parseAutoTask(value: unknown): AutoMonitorTask | null {
  if (typeof value !== "object" || value === null) {
    return null
  }
  const record = value as Record<string, unknown>
  if (record.success === false) {
    return null
  }
  if (typeof record.task_id !== "string" || !record.task_id || typeof record.device_id !== "string" || !record.device_id) {
    return null
  }
  if (typeof record.status !== "string" || !record.status) {
    return null
  }
  if (!isCount(record.processed_count) || !isCount(record.deviation_count) || !isCount(record.total_count)) {
    return null
  }
  return {
    task_id: record.task_id,
    device_id: record.device_id,
    status: record.status,
    current_record_id: typeof record.current_record_id === "string" ? record.current_record_id : "",
    processed_count: record.processed_count,
    deviation_count: record.deviation_count,
    total_count: record.total_count,
    last_processed_at: typeof record.last_processed_at === "string" ? record.last_processed_at : "",
    error_message: typeof record.error_message === "string" ? record.error_message : "",
    monitoring_note: typeof record.monitoring_note === "string" ? record.monitoring_note : "",
  }
}

export function parseAutoTaskList(value: unknown): AutoMonitorTask[] | null {
  if (typeof value !== "object" || value === null || !("tasks" in value)) {
    return null
  }
  const tasks = value.tasks
  if (!Array.isArray(tasks)) {
    return null
  }
  const parsed = tasks.map((item) => parseAutoTask(item))
  if (parsed.some((item) => item === null)) {
    return null
  }
  return parsed as AutoMonitorTask[]
}

export function pickRestoredTask(tasks: readonly AutoMonitorTask[]): AutoMonitorTask | null {
  return tasks.find((item) => shouldPollAuto(item.status)) ?? tasks[0] ?? null
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
}
