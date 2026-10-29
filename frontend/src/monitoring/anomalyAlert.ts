/** 监测会话内的故障告警调度：仅 fault_alert_required 可自动弹窗。统计偏离不弹故障告警。 */

export const ALERT_STORAGE_PREFIX = "steel-monitor-alert:"
export const ALERT_INSUFFICIENT_TEXT = "当前证据不足，尚未确诊"
export const ALERT_SESSION_NOTE = "统计偏离不等于设备故障确诊。只有后端结构化确认故障后才会故障告警。"
export const FAULT_ALERT_TITLE = "确认故障告警"
export const FAULT_ALERT_NOTE = "该告警来自结构化确认规则，不是统计偏离弹窗，也不是未验证假设。"

export interface DeviceAlertCursor {
  baselined: true
  baselineMaxId: number
  promptedIds: number[]
}

export interface AlertStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

export interface AlertableEvent {
  event_id: number
  device_id: string
  record_id: string
  collected_at: string
  reminder_required?: boolean
  fault_alert_required?: boolean
  diagnosis_phase?: string
  metrics?: readonly { field?: string; current_value?: unknown; median?: unknown; unit?: unknown }[]
}

export interface MonitorAlertSession {
  /** 前端监测会话标识，每次点击「开始监测」新建。 */
  sessionKey: string
  taskId: string
  deviceId: string
  /** 本会话已收集的全部事件 id（防轮询重复计数）。 */
  seenEventIds: number[]
  /** 本会话中已见过的故障告警事件 id。 */
  faultAlertIds: number[]
  /** 兼容字段：统计过程起点（不驱动故障弹窗）。 */
  episodeStartIds: number[]
  /** 是否已自动打开过故障告警弹窗。 */
  autoSummaryShown: boolean
  /** 当前是否展示故障告警弹窗。 */
  summaryOpen: boolean
}

export interface AlertSummary {
  deviceCount: number
  episodeCount: number
  metricCount: number
  latestCollectedAt: string
  eventCount: number
  deviceIds: string[]
  faultAlertCount: number
}

export interface EpisodeGroup {
  id: string
  deviceId: string
  startAt: string
  endAt: string
  status: string
  metricFields: string[]
  recordIds: string[]
  eventIds: number[]
  events: AlertableEvent[]
  leadEventId: number
}

export function alertStorageKey(deviceId: string): string {
  return `${ALERT_STORAGE_PREFIX}${deviceId}`
}

export function createFrontendSessionKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) {
    return `mon-${uuid}`
  }
  return `mon-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`
}

export function createMonitorAlertSession(deviceId: string, taskId: string): MonitorAlertSession {
  return {
    sessionKey: createFrontendSessionKey(),
    taskId,
    deviceId,
    seenEventIds: [],
    faultAlertIds: [],
    episodeStartIds: [],
    autoSummaryShown: false,
    summaryOpen: false,
  }
}

export function readAlertCursor(deviceId: string, storage: AlertStorage): DeviceAlertCursor | null {
  if (!deviceId) {
    return null
  }
  let raw: string | null
  try {
    raw = storage.getItem(alertStorageKey(deviceId))
  } catch {
    return null
  }
  if (!raw) {
    return null
  }
  try {
    const parsed = JSON.parse(raw) as Partial<DeviceAlertCursor>
    if (parsed.baselined !== true || typeof parsed.baselineMaxId !== "number" || !Number.isInteger(parsed.baselineMaxId)) {
      return null
    }
    if (!Array.isArray(parsed.promptedIds) || parsed.promptedIds.some((item) => typeof item !== "number" || !Number.isInteger(item))) {
      return null
    }
    return {
      baselined: true,
      baselineMaxId: parsed.baselineMaxId,
      promptedIds: [...parsed.promptedIds],
    }
  } catch {
    return null
  }
}

export function writeAlertCursor(deviceId: string, cursor: DeviceAlertCursor, storage: AlertStorage): void {
  if (!deviceId) {
    return
  }
  try {
    storage.setItem(alertStorageKey(deviceId), JSON.stringify(cursor))
  } catch {
    return
  }
}

export function establishBaseline(cursor: DeviceAlertCursor | null, eventIds: readonly number[]): DeviceAlertCursor {
  if (cursor?.baselined) {
    return cursor
  }
  const baselineMaxId = eventIds.reduce((max, id) => (Number.isInteger(id) ? Math.max(max, id) : max), 0)
  return { baselined: true, baselineMaxId, promptedIds: [] }
}

/** @deprecated 统计过程提醒不再驱动故障弹窗；请用 needsFaultAlert。 */
export function needsPopupReminder(event: { reminder_required?: boolean }): boolean {
  return event.reminder_required === true
}

/** 仅后端 fault_alert_required=true 时允许故障告警弹窗。 */
export function needsFaultAlert(event: { fault_alert_required?: boolean }): boolean {
  return event.fault_alert_required === true
}

export function diagnosisPhaseLabel(phase: string | undefined): string {
  switch (phase) {
    case "confirmed_fault":
      return "已确认故障"
    case "diagnosing":
      return "诊断中"
    case "diagnosis_failed":
      return "分析失败"
    case "insufficient_evidence":
      return "证据不足"
    case "possible_match_unconfirmed":
      return "可能匹配·未确认"
    case "statistical_deviation":
      return "统计偏离"
    default:
      return phase?.trim() ? phase : "统计偏离"
  }
}

/** @deprecated 保留给兼容测试；新调度请使用 ingestSessionEvents。 */
export function freshAlertEvents<T extends { event_id: number; reminder_required?: boolean; fault_alert_required?: boolean }>(
  cursor: DeviceAlertCursor,
  events: readonly T[],
): T[] {
  const prompted = new Set(cursor.promptedIds)
  const seen = new Set<number>()
  return events
    .filter((event) => {
      // 兼容旧测试：若事件带 fault_alert_required 字段，则只认故障告警；否则回退 reminder。
      const eligible = "fault_alert_required" in event ? needsFaultAlert(event) : needsPopupReminder(event)
      if (!eligible) {
        return false
      }
      if (!Number.isInteger(event.event_id) || event.event_id <= cursor.baselineMaxId || prompted.has(event.event_id) || seen.has(event.event_id)) {
        return false
      }
      seen.add(event.event_id)
      return true
    })
    .sort((left, right) => left.event_id - right.event_id)
}

/** @deprecated 兼容旧轮询基线；新逻辑按监测会话调度。 */
export function acceptPolledEvents<T extends { event_id: number; reminder_required?: boolean; fault_alert_required?: boolean }>(
  cursor: DeviceAlertCursor | null,
  events: readonly T[],
): { cursor: DeviceAlertCursor; fresh: T[]; baselinedNow: boolean } {
  if (!cursor?.baselined) {
    return { cursor: establishBaseline(null, events.map((event) => event.event_id)), fresh: [], baselinedNow: true }
  }
  return { cursor, fresh: freshAlertEvents(cursor, events), baselinedNow: false }
}

export function rememberPrompted(cursor: DeviceAlertCursor, eventId: number): DeviceAlertCursor {
  if (!Number.isInteger(eventId) || cursor.promptedIds.includes(eventId)) {
    return cursor
  }
  return { ...cursor, promptedIds: [...cursor.promptedIds, eventId] }
}

export function mergeAlertQueue<T extends { event_id: number }>(queue: readonly T[], fresh: readonly T[]): T[] {
  const seen = new Set(queue.map((event) => event.event_id))
  const additions = fresh.filter((event) => {
    if (seen.has(event.event_id)) {
      return false
    }
    seen.add(event.event_id)
    return true
  })
  if (additions.length === 0) {
    return [...queue]
  }
  return [...queue, ...additions].sort((left, right) => left.event_id - right.event_id)
}

export function visibleAlert<T>(queue: readonly T[]): T | null {
  return queue[0] ?? null
}

export function dedupeEventsById<T extends { event_id: number }>(events: readonly T[]): T[] {
  const seen = new Set<number>()
  const result: T[] = []
  for (const event of events) {
    if (!Number.isInteger(event.event_id) || seen.has(event.event_id)) {
      continue
    }
    seen.add(event.event_id)
    result.push(event)
  }
  return result.sort((left, right) => left.event_id - right.event_id)
}

export function buildAlertSummary(events: readonly AlertableEvent[]): AlertSummary {
  const unique = dedupeEventsById(events)
  const devices = new Set<string>()
  const metrics = new Set<string>()
  let episodeCount = 0
  let latest = ""
  for (const event of unique) {
    if (event.device_id) {
      devices.add(event.device_id)
    }
    if (needsPopupReminder(event)) {
      episodeCount += 1
    }
    for (const metric of event.metrics || []) {
      if (typeof metric.field === "string" && metric.field.trim()) {
        metrics.add(metric.field)
      }
    }
    if (!latest || event.collected_at > latest) {
      latest = event.collected_at
    }
  }
  let faultAlertCount = 0
  for (const event of unique) {
    if (needsFaultAlert(event)) {
      faultAlertCount += 1
    }
  }
  return {
    deviceCount: devices.size,
    episodeCount,
    metricCount: metrics.size,
    latestCollectedAt: latest,
    eventCount: unique.length,
    deviceIds: [...devices].sort(),
    faultAlertCount,
  }
}

/**
 * 摄入本轮轮询事件。
 * - 更新已收集集合（按 event_id 去重）
 * - 仅当出现新的 fault_alert_required 且本会话尚未自动弹过时，打开一次故障告警
 * - 统计偏离 / reminder_required 不自动弹故障窗
 */
export function ingestSessionEvents<T extends AlertableEvent>(
  session: MonitorAlertSession,
  events: readonly T[],
): { session: MonitorAlertSession; autoOpened: boolean; newEventCount: number; newEpisodeCount: number; newFaultAlertCount: number } {
  const seen = new Set(session.seenEventIds)
  const episodeStarts = new Set(session.episodeStartIds)
  const faultAlerts = new Set(session.faultAlertIds || [])
  let newEventCount = 0
  let newEpisodeCount = 0
  let newFaultAlertCount = 0
  for (const event of dedupeEventsById(events)) {
    if (event.device_id !== session.deviceId) {
      continue
    }
    const alreadySeen = seen.has(event.event_id)
    if (!alreadySeen) {
      seen.add(event.event_id)
      newEventCount += 1
      if (needsPopupReminder(event) && !episodeStarts.has(event.event_id)) {
        episodeStarts.add(event.event_id)
        newEpisodeCount += 1
      }
    }
    // 诊断后写回的故障告警：即使 event_id 已见过，fault_alert_required 从 false→true 也要计入。
    if (needsFaultAlert(event) && !faultAlerts.has(event.event_id)) {
      faultAlerts.add(event.event_id)
      newFaultAlertCount += 1
    }
  }
  const shouldAutoOpen = !session.autoSummaryShown && newFaultAlertCount > 0
  const next: MonitorAlertSession = {
    ...session,
    seenEventIds: [...seen].sort((a, b) => a - b),
    episodeStartIds: [...episodeStarts].sort((a, b) => a - b),
    faultAlertIds: [...faultAlerts].sort((a, b) => a - b),
    autoSummaryShown: session.autoSummaryShown || shouldAutoOpen,
    summaryOpen: shouldAutoOpen ? true : session.summaryOpen,
  }
  return { session: next, autoOpened: shouldAutoOpen, newEventCount, newEpisodeCount, newFaultAlertCount }
}

export function dismissAlertSummary(session: MonitorAlertSession): MonitorAlertSession {
  return { ...session, summaryOpen: false, autoSummaryShown: true }
}

export function openAlertSummary(session: MonitorAlertSession): MonitorAlertSession {
  return { ...session, summaryOpen: true, autoSummaryShown: true }
}

/** 将同一设备上连续的过程起点 + 持续记录归并为异常过程，供异常中心展示。 */
export function groupEventsIntoEpisodes(events: readonly AlertableEvent[]): EpisodeGroup[] {
  const sorted = dedupeEventsById(events)
  const groups: EpisodeGroup[] = []
  let current: EpisodeGroup | null = null

  function flush() {
    if (current) {
      groups.push(current)
      current = null
    }
  }

  function startGroup(event: AlertableEvent, status: EpisodeGroup["status"]): EpisodeGroup {
    const fields = metricFieldsOf(event)
    return {
      id: `ep-${event.event_id}`,
      deviceId: event.device_id,
      startAt: event.collected_at,
      endAt: event.collected_at,
      status,
      metricFields: fields,
      recordIds: [event.record_id],
      eventIds: [event.event_id],
      events: [event],
      leadEventId: event.event_id,
    }
  }

  for (const event of sorted) {
    if (!current || current.deviceId !== event.device_id || needsPopupReminder(event)) {
      flush()
      const phase = diagnosisPhaseLabel(event.diagnosis_phase)
      const base = needsFaultAlert(event)
        ? `故障告警·${phase}`
        : needsPopupReminder(event)
          ? `待核查·过程起点·${phase}`
          : `待核查·持续异常·${phase}`
      current = startGroup(event, base)
      continue
    }
    current.endAt = event.collected_at
    current.events.push(event)
    current.eventIds.push(event.event_id)
    if (!current.recordIds.includes(event.record_id)) {
      current.recordIds.push(event.record_id)
    }
    for (const field of metricFieldsOf(event)) {
      if (!current.metricFields.includes(field)) {
        current.metricFields.push(field)
      }
    }
  }
  flush()
  return groups
}

function metricFieldsOf(event: AlertableEvent): string[] {
  const fields: string[] = []
  for (const metric of event.metrics || []) {
    if (typeof metric.field === "string" && metric.field.trim() && !fields.includes(metric.field)) {
      fields.push(metric.field)
    }
  }
  return fields
}

export function filterEpisodeGroups(
  groups: readonly EpisodeGroup[],
  filters: { deviceId?: string; metric?: string; startAt?: string; endAt?: string },
): EpisodeGroup[] {
  return groups.filter((group) => {
    if (filters.deviceId && group.deviceId !== filters.deviceId) {
      return false
    }
    if (filters.metric && !group.metricFields.includes(filters.metric)) {
      return false
    }
    if (filters.startAt && group.endAt < filters.startAt) {
      return false
    }
    if (filters.endAt && group.startAt > filters.endAt) {
      return false
    }
    return true
  })
}
