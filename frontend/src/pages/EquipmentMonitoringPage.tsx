import { useEffect, useMemo, useRef, useState } from "react"
import type { ApiErrorResponse, DatasetAnalysis, DatasetDevice, DatasetRecord, DatasetRecords, DatasetSearchResult, DatasetSummary, StoredMonitorEvent } from "../types/api"
import { CockpitCharts } from "../monitoring/CockpitCharts"
import {
  ANALYSIS_STATUS_OPTIONS,
  PAGE_SIZE,
  analysisStatusDisplay,
  buildSearchParams,
  copyText,
  deviceDisplayName,
  formatDisplayNumber,
  isDatasetSearchResult,
  isUnanalyzedStatus,
  primaryMetricText,
  type AnalysisStatusFilter,
} from "../monitoring/historySearch"
import { SupplementaryMeasurementPanel } from "../monitoring/SupplementaryMeasurementPanel"
import {
  ALERT_INSUFFICIENT_TEXT,
  ALERT_SESSION_NOTE,
  FAULT_ALERT_NOTE,
  FAULT_ALERT_TITLE,
  buildAlertSummary,
  createMonitorAlertSession,
  diagnosisPhaseLabel,
  dismissAlertSummary,
  filterEpisodeGroups,
  groupEventsIntoEpisodes,
  ingestSessionEvents,
  openAlertSummary,
  writeAlertCursor,
  type EpisodeGroup,
  type MonitorAlertSession,
} from "../monitoring/anomalyAlert"
import {
  AUTO_POLL_MS,
  DEMO_PLAYBACK_NOTE,
  duplicateStartText,
  isMissingAutoTask,
  parseAutoTask,
  parseAutoTaskList,
  pickRestoredTask,
  shouldPollAuto,
  startRequestBody,
  apiMessage,
  type AutoMonitorTask,
} from "../monitoring/autoMonitor"
import {
  ANALYSIS_SCOPE_NOTE,
  EMPTY_CANDIDATE_TEXT,
  EMPTY_EVENT_FAULT_TEXT,
  EXPERIMENTAL_LIMIT_NOTE,
  HISTORICAL_DIAGNOSIS_NOTE,
  MISSING_MEASUREMENT_TEXT,
  NOT_CONFIRMED_TEXT,
  MONITOR_EVENT_NOTE,
  PLAYBACK_FIELDS,
  PLAYBACK_SCOPE_NOTE,
  PRIMARY_FIELDS,
  TEMPERATURE_FIELDS,
  SPEED_FIELDS,
  analysisLevelText,
  analyzedRecordWindow,
  categoryCount,
  clueMeasurementLines,
  cockpitStatusLabel,
  diagnosisDetailAvailable,
  diagnosisSteps,
  eventDiagnosisLabel,
  formatAnalysisNumber,
  formatCell,
  formatMeasurement,
  groupDevices,
  hasDuplicateTime,
  isDatasetAnalysis,
  isDatasetDevices,
  isMonitoringEventList,
  isDatasetObservation,
  isDatasetRecords,
  isDatasetSummary,
  initialPlayback,
  metricsForFields,
  monitorPhaseText,
  pausePlayback,
  playbackAnalysisAction,
  playbackIntervalMs,
  playbackScopeText,
  resetPlayback,
  resumePlayback,
  shouldApplyAnalysis,
  startPlayback,
  tickPlayback,
  unresolvedMetrics,
  visibleMetrics,
  changePlaybackSpeed,
  type MonitorPhase,
  type PlaybackState,
  type VibrationGroupId,
} from "../monitoring/view"

function datasetAnalysisPath(recordId: string): string {
  return `/api/dataset/analysis/${encodeURIComponent(recordId)}`
}

function isApiError(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return record.success === false && typeof record.message === "string"
}

function readableError(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "请求已取消。"
  }
  if (error instanceof Error) {
    return error.message === "Failed to fetch" ? "无法连接后端。请确认本机后端已启动。" : error.message
  }
  return fallback
}

async function readAuto(url: string, signal: AbortSignal, init?: RequestInit): Promise<{ status: number; body: unknown }> {
  const response = await fetch(url, { ...init, signal })
  const body: unknown = await response.json().catch(() => null)
  return { status: response.status, body }
}

async function readJson(url: string, signal: AbortSignal, init?: RequestInit): Promise<unknown> {
  const response = await fetch(url, { ...init, signal })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(isApiError(body) ? body.message : `请求失败，HTTP ${response.status}`)
  }
  return body
}

export function EquipmentMonitoringPage() {
  const [summary, setSummary] = useState<DatasetSummary | null>(null)
  const [devices, setDevices] = useState<DatasetDevice[]>([])
  const [deviceCount, setDeviceCount] = useState<number | null>(null)
  const [loadError, setLoadError] = useState("")
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [category, setCategory] = useState("")
  const [deviceId, setDeviceId] = useState("")
  const [startTime, setStartTime] = useState("")
  const [endTime, setEndTime] = useState("")
  const [searchText, setSearchText] = useState("")
  const [analysisStatusFilter, setAnalysisStatusFilter] = useState<AnalysisStatusFilter>("all")
  const [searchPage, setSearchPage] = useState(1)
  const [searchResult, setSearchResult] = useState<DatasetSearchResult | null>(null)
  const [detailSource, setDetailSource] = useState<"list" | "anomaly" | null>(null)
  const [techFactsOpen, setTechFactsOpen] = useState(false)
  const [statsExpanded, setStatsExpanded] = useState(false)
  const [copyNotice, setCopyNotice] = useState("")
  const [queryError, setQueryError] = useState("")
  const [querying, setQuerying] = useState(false)
  const [records, setRecords] = useState<DatasetRecords | null>(null)
  const searchRequest = useRef<AbortController | null>(null)
  const [vibrationGroup, setVibrationGroup] = useState<VibrationGroupId>("speed")
  const [focusEventId, setFocusEventId] = useState<number | null>(null)
  const [techOpen, setTechOpen] = useState(false)
  const [archiveTab, setArchiveTab] = useState<"device" | "query" | "events" | "stats">("query")
  const [selectedId, setSelectedId] = useState("")
  const [detail, setDetail] = useState<DatasetRecord | null>(null)
  const [detailError, setDetailError] = useState("")
  const [detailLoading, setDetailLoading] = useState(false)
  const [analysis, setAnalysis] = useState<DatasetAnalysis | null>(null)
  const [analysisError, setAnalysisError] = useState("")
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const queryRequest = useRef<AbortController | null>(null)
  const detailRequest = useRef<AbortController | null>(null)
  const analysisRequest = useRef<AbortController | null>(null)
  const selectedIdRef = useRef("")
  const [playback, setPlayback] = useState<PlaybackState>(initialPlayback())
  const playbackRef = useRef<PlaybackState>(initialPlayback())
  const playbackTimer = useRef<number | null>(null)
  const playbackRecordsRef = useRef<DatasetRecord[]>([])
  const monitorSessionRef = useRef(0)
  const processedIdsRef = useRef<string[]>([])
  const monitorEventsRef = useRef<StoredMonitorEvent[]>([])
  const analyzingRecordRef = useRef<string | null>(null)
  const [monitorPhase, setMonitorPhase] = useState<MonitorPhase>("idle")
  const [monitorDetail, setMonitorDetail] = useState("")
  const [historyEvents, setHistoryEvents] = useState<StoredMonitorEvent[]>([])
  const [historyError, setHistoryError] = useState("")
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyNote, setHistoryNote] = useState("")
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [expandedHistoryId, setExpandedHistoryId] = useState("")
  const historyRequest = useRef<AbortController | null>(null)
  const historyScopeRef = useRef<"device" | "session" | "">("")
  const [autoTask, setAutoTask] = useState<AutoMonitorTask | null>(null)
  const [autoEvents, setAutoEvents] = useState<StoredMonitorEvent[]>([])
  const [alertSession, setAlertSession] = useState<MonitorAlertSession | null>(null)
  const [anomalyCenterOpen, setAnomalyCenterOpen] = useState(false)
  const [anomalyMetricFilter, setAnomalyMetricFilter] = useState("")
  const [anomalyStartFilter, setAnomalyStartFilter] = useState("")
  const [anomalyEndFilter, setAnomalyEndFilter] = useState("")
  const [expandedEpisodeId, setExpandedEpisodeId] = useState("")
  const [autoError, setAutoError] = useState("")
  const [autoBusy, setAutoBusy] = useState<"start" | "stop" | "">("")
  const [autoNonce, setAutoNonce] = useState(0)
  const autoTaskRef = useRef<AutoMonitorTask | null>(null)
  const alertSessionRef = useRef<MonitorAlertSession | null>(null)
  const restoredRef = useRef(false)
  const deviceIdRef = useRef("")

  function clearPlaybackTimer() {
    if (playbackTimer.current !== null) {
      window.clearTimeout(playbackTimer.current)
      playbackTimer.current = null
    }
  }

  function commitPlayback(next: PlaybackState) {
    playbackRef.current = next
    setPlayback(next)
  }

  function stopMonitorSession() {
    clearPlaybackTimer()
    analyzingRecordRef.current = null
    monitorSessionRef.current += 1
    processedIdsRef.current = []
    monitorEventsRef.current = []
    setMonitorPhase("idle")
    setMonitorDetail("")
  }

  function commitMonitor(processed: string[], events: StoredMonitorEvent[], detail: string) {
    processedIdsRef.current = processed
    monitorEventsRef.current = events
    setMonitorDetail(detail)
  }

  function scheduleAdvance(sessionId: number) {
    clearPlaybackTimer()
    if (monitorSessionRef.current !== sessionId || playbackRef.current.status !== "playing") {
      return
    }
    setMonitorPhase("waiting")
    playbackTimer.current = window.setTimeout(() => {
      playbackTimer.current = null
      if (monitorSessionRef.current !== sessionId || playbackRef.current.status !== "playing") {
        return
      }
      const next = tickPlayback(playbackRef.current, playbackRecordsRef.current.length)
      commitPlayback(next)
      void inspectCurrent(sessionId)
    }, playbackIntervalMs(playbackRef.current.speed))
  }

  async function inspectCurrent(sessionId: number) {
    if (monitorSessionRef.current !== sessionId) {
      return
    }
    const action = playbackAnalysisAction(
      playbackRef.current,
      playbackRecordsRef.current,
      processedIdsRef.current,
      analyzingRecordRef.current,
    )
    if (action.action === "wait") {
      scheduleAdvance(sessionId)
      return
    }
    if (action.action !== "analyze") {
      if (playbackRef.current.status === "finished") {
        setMonitorPhase("finished")
      }
      return
    }
    const record = playbackRecordsRef.current[playbackRef.current.index - 1]
    if (!record || record.record_id !== action.recordId) {
      return
    }
    const processed = processedIdsRef.current.includes(record.record_id)
      ? processedIdsRef.current
      : [...processedIdsRef.current, record.record_id]
    commitMonitor(processed, monitorEventsRef.current, `演示查看 ${record.record_id}，没有写入监测事件。`)
    setMonitorPhase(playbackRef.current.status === "playing" ? "waiting" : playbackRef.current.status === "paused" ? "paused" : "finished")
    if (playbackRef.current.status === "playing") {
      scheduleAdvance(sessionId)
    }
  }

  function haltPlayback() {
    stopMonitorSession()
    if (playbackRef.current.status === "idle" && playbackRef.current.index === 0) {
      return
    }
    commitPlayback(resetPlayback(playbackRef.current))
  }

  function beginPlayback() {
    stopMonitorSession()
    playbackRecordsRef.current = records?.records ?? []
    const next = startPlayback(playbackRef.current, playbackRecordsRef.current.length)
    commitPlayback(next)
    if (next.status === "idle") {
      return
    }
    void inspectCurrent(monitorSessionRef.current)
  }

  function pauseCurrentPlayback() {
    clearPlaybackTimer()
    commitPlayback(pausePlayback(playbackRef.current))
    setMonitorPhase("paused")
  }

  function resumeCurrentPlayback() {
    const next = resumePlayback(playbackRef.current, playbackRecordsRef.current.length)
    commitPlayback(next)
    if (next.status !== "playing") {
      if (next.status === "finished") {
        setMonitorPhase("finished")
      }
      return
    }
    void inspectCurrent(monitorSessionRef.current)
  }

  function choosePlaybackSpeed(speed: 1 | 2) {
    const next = changePlaybackSpeed(playbackRef.current, speed)
    commitPlayback(next)
    if (next.status === "playing" && playbackTimer.current !== null) {
      scheduleAdvance(monitorSessionRef.current)
    }
  }

  useEffect(() => {
    playbackRecordsRef.current = records?.records ?? []
  }, [records])

  useEffect(() => {
    autoTaskRef.current = autoTask
    deviceIdRef.current = deviceId
  }, [autoTask, deviceId])

  useEffect(() => {
    if (!deviceId || category) {
      return
    }
    const found = devices.find((item) => item.device_id === deviceId)
    if (found) {
      setCategory(found.equipment_category)
    }
  }, [deviceId, devices, category])

  useEffect(() => {
    const controller = new AbortController()
    let timer: number | null = null
    let cancelled = false

    async function pullEvents(task: AutoMonitorTask) {
      const params = new URLSearchParams({
        session_id: task.task_id,
        device_id: task.device_id,
        limit: "100",
      })
      const response = await readAuto(`/api/monitoring/events?${params.toString()}`, controller.signal)
      if (cancelled || response.status !== 200 || !isMonitoringEventList(response.body)) {
        return
      }
      if (response.body.session_id !== task.task_id || response.body.device_id !== task.device_id) {
        return
      }
      setAutoEvents(response.body.events)
      const currentSession =
        alertSessionRef.current &&
        alertSessionRef.current.taskId === task.task_id &&
        alertSessionRef.current.deviceId === task.device_id
          ? alertSessionRef.current
          : createMonitorAlertSession(task.device_id, task.task_id)
      const ingested = ingestSessionEvents(currentSession, response.body.events)
      alertSessionRef.current = ingested.session
      setAlertSession(ingested.session)
    }

    async function pull(): Promise<AutoMonitorTask | null> {
      const selected = deviceIdRef.current
      const url = selected
        ? `/api/monitoring/auto/status?device_id=${encodeURIComponent(selected)}`
        : "/api/monitoring/auto/status"
      const response = await readAuto(url, controller.signal)
      if (cancelled || controller.signal.aborted) {
        return autoTaskRef.current
      }
      if (!selected && restoredRef.current) {
        return null
      }
      if (!selected && !restoredRef.current) {
        restoredRef.current = true
        const tasks = parseAutoTaskList(response.body)
        if (response.status !== 200 || !tasks) {
          if (response.status !== 200) {
            setAutoError(apiMessage(response.body) || `监测状态请求失败，HTTP ${response.status}`)
          }
          return null
        }
        const picked = pickRestoredTask(tasks)
        setAutoError("")
        setAutoTask(picked)
        if (picked && !deviceIdRef.current) {
          setDeviceId(picked.device_id)
        }
        if (picked) {
          await pullEvents(picked)
        }
        return picked
      }
      if (selected && isMissingAutoTask(response.status, response.body)) {
        setAutoTask(null)
        setAutoEvents([])
        setAutoError("")
        return null
      }
      if (response.status !== 200) {
        setAutoError(apiMessage(response.body) || `监测状态请求失败，HTTP ${response.status}`)
        return autoTaskRef.current
      }
      const task = parseAutoTask(response.body)
      if (!task || task.device_id !== selected) {
        setAutoError("监测状态返回了无法识别的结构。")
        return null
      }
      setAutoError("")
      setAutoTask(task)
      await pullEvents(task)
      return task
    }

    async function loop() {
      const task = await pull().catch((error: unknown) => {
        if (!cancelled && !controller.signal.aborted) {
          setAutoError(readableError(error, "读取自动监测状态失败"))
        }
        return null
      })
      if (cancelled || !task || !shouldPollAuto(task.status)) {
        return
      }
      timer = window.setTimeout(() => {
        void loop()
      }, AUTO_POLL_MS)
    }

    void loop()
    return () => {
      cancelled = true
      if (timer !== null) {
        window.clearTimeout(timer)
      }
      controller.abort()
    }
  }, [deviceId, autoNonce])

  useEffect(() => {
    alertSessionRef.current = alertSession
  }, [alertSession])

  async function startAutomatic() {
    if (!deviceId || autoBusy) {
      return
    }
    const controller = new AbortController()
    setAutoBusy("start")
    setAutoError("")
    try {
      const response = await readAuto("/api/monitoring/auto/start", controller.signal, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(startRequestBody(deviceId)),
      })
      const duplicate = duplicateStartText(response.status, response.body)
      if (duplicate) {
        setAutoError(duplicate)
        setAutoNonce((value) => value + 1)
        return
      }
      if (response.status !== 200) {
        throw new Error(apiMessage(response.body) || `无法启动自动监测，HTTP ${response.status}`)
      }
      const task = parseAutoTask(response.body)
      if (!task || task.device_id !== deviceId) {
        throw new Error("自动监测启动返回了无法识别的结构。")
      }
      // 新监测会话：旧历史不作为本轮新告警；每会话最多自动汇总弹窗一次。
      writeAlertCursor(deviceId, { baselined: true, baselineMaxId: 0, promptedIds: [] }, window.localStorage)
      const session = createMonitorAlertSession(deviceId, task.task_id)
      alertSessionRef.current = session
      setAlertSession(session)
      setAutoEvents([])
      setAnomalyCenterOpen(false)
      setExpandedEpisodeId("")
      setAutoTask(task)
      setAutoNonce((value) => value + 1)
    } catch (error) {
      setAutoError(readableError(error, "无法启动自动监测"))
    } finally {
      setAutoBusy("")
    }
  }

  async function stopAutomatic() {
    const task = autoTaskRef.current
    if (!task || autoBusy) {
      return
    }
    const controller = new AbortController()
    setAutoBusy("stop")
    setAutoError("")
    try {
      const response = await readAuto("/api/monitoring/auto/stop", controller.signal, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: task.device_id, task_id: task.task_id }),
      })
      if (response.status !== 200) {
        throw new Error(apiMessage(response.body) || `无法停止自动监测，HTTP ${response.status}`)
      }
      const next = parseAutoTask(response.body)
      if (!next) {
        throw new Error("自动监测停止返回了无法识别的结构。")
      }
      setAutoTask(next)
      setAutoNonce((value) => value + 1)
    } catch (error) {
      setAutoError(readableError(error, "无法停止自动监测"))
    } finally {
      setAutoBusy("")
    }
  }

  useEffect(() => {
    return () => {
      queryRequest.current?.abort()
      detailRequest.current?.abort()
      analysisRequest.current?.abort()
      historyRequest.current?.abort()
      monitorSessionRef.current += 1
      clearPlaybackTimer()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    async function loadCatalog() {
      try {
        const [summaryBody, deviceBody] = await Promise.all([
          readJson("/api/dataset/summary", controller.signal),
          readJson("/api/dataset/devices", controller.signal),
        ])
        if (!active) {
          return
        }
        if (!isDatasetSummary(summaryBody) || !isDatasetDevices(deviceBody)) {
          throw new Error("官方数据接口返回了无法识别的结构。")
        }
        setSummary(summaryBody)
        setDevices(deviceBody.devices)
        setDeviceCount(deviceBody.device_count)
        setLoadError("")
      } catch (error) {
        if (!active || controller.signal.aborted) {
          return
        }
        setLoadError(readableError(error, "读取官方数据失败"))
      } finally {
        if (active) {
          setLoadingCatalog(false)
        }
      }
    }
    void loadCatalog()
    return () => {
      active = false
      controller.abort()
    }
  }, [])

  useEffect(() => {
    if (loadingCatalog || devices.length === 0 || searchResult) {
      return
    }
    const preferred = devices.find((item) => item.device_id === "MM-01") || devices[0]
    if (!deviceId && preferred) {
      setCategory(preferred.equipment_category)
      setDeviceId(preferred.device_id)
    }
    void runHistorySearch(1, { deviceId: preferred?.device_id || deviceId, q: "" })
    // 仅在目录首次加载后给历史查询一个默认结果，避免空白列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingCatalog, devices.length])

  useEffect(() => {
    if (!deviceId) {
      return
    }
    const controller = new AbortController()
    queryRequest.current?.abort()
    queryRequest.current = controller
    const requestedDevice = deviceId
    setQuerying(true)
    setQueryError("")
    const params = new URLSearchParams({ device_id: requestedDevice, limit: "100" })
    void readJson(`/api/dataset/records?${params.toString()}`, controller.signal)
      .then((body) => {
        if (queryRequest.current !== controller) {
          return
        }
        if (!isDatasetRecords(body) || body.returned_count !== body.records.length || (body.found && body.device_id !== requestedDevice)) {
          throw new Error("记录接口返回了无法识别的结构。")
        }
        setRecords(body)
      })
      .catch((error: unknown) => {
        if (queryRequest.current !== controller || controller.signal.aborted) {
          return
        }
        setRecords(null)
        setQueryError(readableError(error, "查询记录失败"))
      })
      .finally(() => {
        if (queryRequest.current === controller) {
          setQuerying(false)
        }
      })
    return () => {
      controller.abort()
    }
  }, [deviceId])

  const groups = useMemo(
    () => groupDevices(summary?.sheets.map((sheet) => sheet.equipment_category) ?? [], devices),
    [summary, devices],
  )
  const selectedGroup = groups.find((group) => group.category === category)
  const selectedDevice = devices.find((device) => device.device_id === deviceId) ?? null
  const selectedRow =
    (detail && detail.record_id === selectedId ? detail : null) ||
    searchResult?.records.find((record) => record.record_id === selectedId) ||
    records?.records.find((record) => record.record_id === selectedId) ||
    null
  const playbackRecord = playback.status !== "idle" && playback.index > 0 ? records?.records[playback.index - 1] : undefined
  const taskForDevice = autoTask && autoTask.device_id === deviceId ? autoTask : null
  const analyzed = analyzedRecordWindow(records?.records ?? [], taskForDevice, startTime.trim() === "" && endTime.trim() === "")
  const currentObserved = taskForDevice ? records?.records.find((record) => record.record_id === taskForDevice.current_record_id) : undefined
  const focusEvent =
    [...autoEvents, ...historyEvents].find((item) => item.event_id === focusEventId) ??
    (taskForDevice ? autoEvents[0] : undefined) ??
    null
  const sessionEvents = useMemo(
    () => autoEvents.filter((item) => item.device_id === deviceId),
    [autoEvents, deviceId],
  )
  const episodeGroups = useMemo(() => {
    const groups = groupEventsIntoEpisodes(sessionEvents)
    return filterEpisodeGroups(groups, {
      deviceId: deviceId || undefined,
      metric: anomalyMetricFilter || undefined,
      startAt: anomalyStartFilter.trim() || undefined,
      endAt: anomalyEndFilter.trim() || undefined,
    })
  }, [sessionEvents, deviceId, anomalyMetricFilter, anomalyStartFilter, anomalyEndFilter])
  const alertSummary = useMemo(() => buildAlertSummary(sessionEvents), [sessionEvents])
  const metricFilterOptions = useMemo(() => {
    const fields = new Set<string>()
    for (const event of sessionEvents) {
      for (const metric of event.metrics) {
        if (metric.field) {
          fields.add(metric.field)
        }
      }
    }
    return [...fields].sort()
  }, [sessionEvents])

  function clearAnalysis() {
    analysisRequest.current?.abort()
    analysisRequest.current = null
    setAnalysis(null)
    setAnalysisError("")
    setAnalysisLoading(false)
  }

  function clearHistory() {
    historyRequest.current?.abort()
    historyRequest.current = null
    historyScopeRef.current = ""
    setHistoryEvents([])
    setHistoryError("")
    setHistoryLoading(false)
    setHistoryNote("")
    setHistoryLoaded(false)
    setExpandedHistoryId("")
  }

  function clearResult() {
    queryRequest.current?.abort()
    detailRequest.current?.abort()
    clearAnalysis()
    clearHistory()
    haltPlayback()
    selectedIdRef.current = ""
    setRecords(null)
    setSelectedId("")
    setDetail(null)
    setDetailError("")
    setQueryError("")
  }

  function chooseCategory(nextCategory: string) {
    setCategory(nextCategory)
    setDeviceId("")
    clearResult()
  }

  function chooseDevice(nextDeviceId: string) {
    setDeviceId(nextDeviceId)
    clearResult()
  }

  async function openRecord(recordId: string) {
    detailRequest.current?.abort()
    clearAnalysis()
    selectedIdRef.current = recordId
    const controller = new AbortController()
    detailRequest.current = controller
    setSelectedId(recordId)
    setDetail(null)
    setDetailError("")
    setDetailLoading(true)
    try {
      const body = await readJson(`/api/dataset/observations/${encodeURIComponent(recordId)}`, controller.signal)
      if (detailRequest.current !== controller) {
        return
      }
      if (!isDatasetObservation(body) || body.record.record_id !== recordId) {
        throw new Error("按记录编号核对时没有得到同一条观测。")
      }
      setDetail(body.record)
    } catch (error) {
      if (detailRequest.current !== controller || controller.signal.aborted) {
        return
      }
      setDetailError(readableError(error, "读取记录详情失败"))
    } finally {
      if (detailRequest.current === controller) {
        setDetailLoading(false)
      }
    }
  }

  async function runHistorySearch(page = searchPage, overrides?: Partial<{ q: string; deviceId: string; start: string; end: string; status: AnalysisStatusFilter }>) {
    searchRequest.current?.abort()
    const controller = new AbortController()
    searchRequest.current = controller
    setQuerying(true)
    setQueryError("")
    const params = buildSearchParams({
      q: overrides?.q ?? searchText,
      deviceId: overrides?.deviceId ?? deviceId,
      startTime: overrides?.start ?? startTime,
      endTime: overrides?.end ?? endTime,
      page,
      pageSize: PAGE_SIZE,
      analysisStatus: overrides?.status ?? analysisStatusFilter,
    })
    try {
      const body = await readJson(`/api/dataset/records/search?${params.toString()}`, controller.signal)
      if (searchRequest.current !== controller) {
        return
      }
      if (!isDatasetSearchResult(body)) {
        throw new Error("历史检索返回了无法识别的结构。")
      }
      setSearchPage(body.page)
      setSearchResult(body)
    } catch (error) {
      if (searchRequest.current !== controller || controller.signal.aborted) {
        return
      }
      setQueryError(readableError(error, "历史检索失败"))
    } finally {
      if (searchRequest.current === controller) {
        setQuerying(false)
      }
    }
  }

  async function openRecordFromList(recordId: string, source: "list" | "anomaly" = "list", analyze = false) {
    const row = searchResult?.records.find((item) => item.record_id === recordId)
      || records?.records.find((item) => item.record_id === recordId)
    if (row?.device_id && row.device_id !== deviceId) {
      const device = devices.find((item) => item.device_id === row.device_id)
      if (device) {
        setCategory(device.equipment_category)
      }
      setDeviceId(row.device_id)
    }
    setDetailSource(source)
    setArchiveTab("query")
    setTechFactsOpen(false)
    setStatsExpanded(false)
    await openRecord(recordId)
    if (analyze) {
      await analyzeRecord(recordId)
    }
  }

  async function openRecordFromAnomaly(event: StoredMonitorEvent) {
    const device = devices.find((item) => item.device_id === event.device_id)
    if (device) {
      setCategory(device.equipment_category)
    }
    setDeviceId(event.device_id)
    setFocusEventId(event.event_id)
    setAnomalyCenterOpen(true)
    await openRecordFromList(event.record_id, "anomaly", true)
  }

  function closeRecordDetail() {
    clearAnalysis()
    setSelectedId("")
    selectedIdRef.current = ""
    setDetail(null)
    setDetailError("")
    setDetailSource(null)
    setTechFactsOpen(false)
    setStatsExpanded(false)
    setCopyNotice("")
  }

  async function analyzeRecord(recordId: string) {
    if (analysisLoading || recordId !== selectedIdRef.current) {
      return
    }
    analysisRequest.current?.abort()
    const controller = new AbortController()
    analysisRequest.current = controller
    setAnalysis(null)
    setAnalysisError("")
    setAnalysisLoading(true)
    try {
      const body = await readJson(datasetAnalysisPath(recordId), controller.signal)
      if (analysisRequest.current !== controller || !isDatasetAnalysis(body)) {
        if (analysisRequest.current !== controller) {
          return
        }
        throw new Error("记录分析返回了无法识别的结构。")
      }
      if (!shouldApplyAnalysis(recordId, selectedIdRef.current, body.record_id) || body.observation.record_id !== recordId) {
        return
      }
      setAnalysis(body)
    } catch (error) {
      if (analysisRequest.current !== controller || controller.signal.aborted) {
        return
      }
      setAnalysis(null)
      setAnalysisError(readableError(error, "记录分析失败"))
    } finally {
      if (analysisRequest.current === controller) {
        setAnalysisLoading(false)
      }
    }
  }

  async function loadHistory(scope: "device" | "session") {
    const targetDevice = deviceId
    const targetSession = autoTaskRef.current?.task_id ?? ""
    if (scope === "device" && targetDevice === "") {
      setHistoryError("请先选择设备。")
      return
    }
    if (scope === "session" && targetSession === "") {
      setHistoryError("当前没有自动监测任务。")
      return
    }
    historyRequest.current?.abort()
    const controller = new AbortController()
    historyRequest.current = controller
    historyScopeRef.current = scope
    setHistoryLoading(true)
    setHistoryError("")
    const params = new URLSearchParams({ limit: "100" })
    if (scope === "device") {
      params.set("device_id", targetDevice)
    } else {
      params.set("session_id", targetSession)
    }
    try {
      const body = await readJson(`/api/monitoring/events?${params.toString()}`, controller.signal)
      if (historyRequest.current !== controller || controller.signal.aborted) {
        return
      }
      if (scope === "session" && autoTaskRef.current?.task_id !== targetSession) {
        return
      }
      if (!isMonitoringEventList(body)) {
        throw new Error("历史事件返回了无法识别的结构。")
      }
      if (scope === "device" && body.device_id !== targetDevice) {
        throw new Error("历史事件返回的设备与当前设备不一致。")
      }
      if (scope === "session" && body.session_id !== targetSession) {
        throw new Error("历史事件返回的任务与当前自动监测任务不一致。")
      }
      setHistoryEvents(body.events)
      setHistoryLoaded(true)
      setHistoryNote(`本次返回 ${body.count} 条，接口 limit 为 ${body.limit}，最多 100 条，不代表数据库中的全部事件。`)
    } catch (error) {
      if (historyRequest.current !== controller || controller.signal.aborted) {
        return
      }
      setHistoryEvents([])
      setHistoryNote("")
      setHistoryLoaded(false)
      setHistoryError(readableError(error, "读取历史监测事件失败"))
    } finally {
      if (historyRequest.current === controller) {
        setHistoryLoading(false)
      }
    }
  }

  const categories = summary ? categoryCount(summary) : 0
  const shownDeviceCount = summary?.device_count
  const catalogMismatch = summary !== null && deviceCount !== null && (summary.device_count !== deviceCount || devices.length !== deviceCount)

  return (
    <main className="home monitor-page">
      <section className="panel monitor-panel">
        <header className="cockpit-hero">
          <div>
            <p className="cockpit-badge">官方数据集 · 历史数据模拟监测</p>
            <h1>钢铁设备智能监测中心</h1>
            <p className="cockpit-lead">持续分析设备数据，自动发现异常变化，辅助判断可能原因。</p>
          </div>
          <p className="cockpit-source">当前没有连接实时传感器。离开本页不会停止已经启动的后端任务。</p>
        </header>

        {loadingCatalog && <p className="status">正在读取官方数据集。</p>}
        {loadError && (
          <p className="status error" role="alert">
            {loadError}
          </p>
        )}
        {summary && (
          <>
            <div className="cockpit-stats">
              <Metric label="设备类别" value={String(categories)} />
              <Metric label="设备数量" value={String(shownDeviceCount)} />
              <Metric label="数据记录" value={String(summary.record_count)} />
              <Metric label="当前设备" value={deviceId || "未选择"} />
              <Metric label="监测模式" value="历史数据模拟" />
            </div>
            {catalogMismatch && (
              <p className="status error" role="alert">
                概况中的设备数量与设备清单不一致。
              </p>
            )}
            <div className="cockpit-categories" role="list">
              {summary.sheets.map((sheet) => (
                <button
                  key={sheet.sheet_name}
                  type="button"
                  className="cockpit-category"
                  role="listitem"
                  aria-pressed={category === sheet.equipment_category}
                  onClick={() => chooseCategory(sheet.equipment_category)}
                >
                  <strong>{sheet.equipment_category}</strong>
                  <span>{sheet.device_count} 台</span>
                  <span>{sheet.record_count} 条</span>
                </button>
              ))}
            </div>
          </>
        )}

        <div className="monitor-pick">
          <label className="field-label" htmlFor="equipment-category">
            设备类别
          </label>
          <label className="field-label" htmlFor="equipment-id">
            设备编号
          </label>
          <select
            id="equipment-category"
            className="text-input"
            value={category}
            onChange={(event) => chooseCategory(event.target.value)}
          >
            <option value="">请选择设备类别</option>
            {groups.map((group) => (
              <option key={group.category} value={group.category}>
                {group.category}（{group.devices.length} 台）
              </option>
            ))}
          </select>
          <select
            id="equipment-id"
            className="text-input"
            value={deviceId}
            disabled={!selectedGroup}
            onChange={(event) => chooseDevice(event.target.value)}
          >
            <option value="">请选择设备编号</option>
            {selectedGroup?.devices.map((device) => (
              <option key={device.device_id} value={device.device_id}>
                {device.device_id}（{device.record_count} 条）
              </option>
            ))}
          </select>
        </div>
        <section className="cockpit-console" aria-label="自动监测控制台">
          <div className="cockpit-console-actions">
            <button type="button" className="test-button" disabled={!deviceId || autoBusy !== "" || (taskForDevice !== null && shouldPollAuto(taskForDevice.status))} onClick={() => void startAutomatic()}>
              {autoBusy === "start" ? "正在启动" : "开始监测"}
            </button>
            <button type="button" className="ghost-button" disabled={!taskForDevice || !shouldPollAuto(taskForDevice.status) || autoBusy !== ""} onClick={() => void stopAutomatic()}>
              {autoBusy === "stop" ? "正在停止" : "停止监测"}
            </button>
          </div>
          <div className="cockpit-console-status">
            <p role="status">
              当前运行状态：<strong>{cockpitStatusLabel(taskForDevice?.status)}</strong>
            </p>
            <p>
              已处理记录数 / 总记录数：{taskForDevice ? `${taskForDevice.processed_count} / ${taskForDevice.total_count}` : "0 / 0"}
            </p>
            <div
              className="cockpit-progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={taskForDevice?.total_count ?? 0}
              aria-valuenow={taskForDevice?.processed_count ?? 0}
              aria-label="监测进度"
            >
              <span style={{ width: `${taskForDevice && taskForDevice.total_count > 0 ? Math.min(100, (taskForDevice.processed_count / taskForDevice.total_count) * 100) : 0}%` }} />
            </div>
            <p>已发现的数据偏离记录数：{taskForDevice ? taskForDevice.deviation_count : 0}</p>
            <p>
              当前处理的记录时间：
              {currentObserved ? currentObserved.collected_at : taskForDevice?.current_record_id ? `记录 ${taskForDevice.current_record_id} 不在本次查询结果中` : "尚无"}
            </p>
            {taskForDevice?.last_processed_at && <p className="file-meta">服务器处理时间：{taskForDevice.last_processed_at}</p>}
            <div className="monitor-toggles">
              <button
                type="button"
                className="ghost-button"
                disabled={sessionEvents.length === 0}
                onClick={() => {
                  setAnomalyCenterOpen(true)
                  window.requestAnimationFrame(() => {
                    document.getElementById("anomaly-center")?.scrollIntoView({ block: "start" })
                  })
                }}
              >
                查看异常列表
              </button>
              {alertSession && (alertSession.faultAlertIds?.length ?? 0) > 0 && (
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => setAlertSession((current) => (current ? openAlertSummary(current) : current))}
                >
                  查看故障告警
                </button>
              )}
            </div>
            <p className="monitor-note">{ALERT_SESSION_NOTE}</p>
          </div>
          {autoError && (
            <p className="status error" role="alert">
              {autoError}
            </p>
          )}
          {taskForDevice?.error_message && (
            <p className="status error" role="alert">
              {taskForDevice.error_message}
            </p>
          )}
        </section>

        <CockpitCharts
          records={analyzed.records}
          mode={analyzed.mode}
          events={[...autoEvents, ...historyEvents].filter((item) => item.device_id === deviceId)}
          group={vibrationGroup}
          onGroup={setVibrationGroup}
          onMarker={(eventId) => {
            setFocusEventId(eventId)
            setAnomalyCenterOpen(true)
            setTechOpen(false)
          }}
        />

        <div className="cockpit-insight">
          <AnomalyCenter
            deviceId={deviceId}
            open={anomalyCenterOpen || sessionEvents.length > 0}
            count={taskForDevice?.deviation_count ?? sessionEvents.length}
            groups={episodeGroups}
            metricOptions={metricFilterOptions}
            metricFilter={anomalyMetricFilter}
            startFilter={anomalyStartFilter}
            endFilter={anomalyEndFilter}
            expandedEpisodeId={expandedEpisodeId}
            activeId={focusEvent?.event_id ?? null}
            onMetricFilter={setAnomalyMetricFilter}
            onStartFilter={setAnomalyStartFilter}
            onEndFilter={setAnomalyEndFilter}
            onToggleEpisode={(id) => setExpandedEpisodeId((current) => (current === id ? "" : id))}
            onOpen={(eventId) => {
              setFocusEventId(eventId)
              setTechOpen(false)
              setAnomalyCenterOpen(true)
            }}
            onOpenRecord={(event) => void openRecordFromAnomaly(event)}
          />
          <DiagnosisBoard
            event={focusEvent}
            techOpen={techOpen}
            onToggleTech={() => setTechOpen((current) => !current)}
            onOpenRecord={(event) => void openRecordFromAnomaly(event)}
          />
        </div>

        <section className="cockpit-archive" aria-label="设备信息和历史数据">
          <h2>设备信息和历史数据</h2>
          <div className="monitor-toggles" role="tablist" aria-label="详细资料">
            <button type="button" className="ghost-button monitor-toggle" aria-pressed={archiveTab === "device"} onClick={() => setArchiveTab("device")}>
              设备信息
            </button>
            <button type="button" className="ghost-button monitor-toggle" aria-pressed={archiveTab === "query"} onClick={() => setArchiveTab("query")}>
              历史查询
            </button>
            <button type="button" className="ghost-button monitor-toggle" aria-pressed={archiveTab === "events"} onClick={() => setArchiveTab("events")}>
              已保存事件
            </button>
            <button type="button" className="ghost-button monitor-toggle" aria-pressed={archiveTab === "stats"} onClick={() => setArchiveTab("stats")}>
              统计参数
            </button>
          </div>
          {archiveTab === "device" && (
            <div className="cockpit-device-card">
              {selectedDevice ? (
                <div className="metric-grid">
                  <Metric label="设备编号" value={selectedDevice.device_id} />
                  <Metric label="设备类别" value={selectedDevice.equipment_category} />
                  <Metric label="记录数量" value={String(selectedDevice.record_count)} />
                  <Metric label="起始采集时间" value={selectedDevice.time_start ?? "无"} />
                  <Metric label="结束采集时间" value={selectedDevice.time_end ?? "无"} />
                  <Metric label="重复采集时间组数" value={String(selectedDevice.duplicate_timestamp_groups)} />
                </div>
              ) : (
                <p className="status">请先选择设备类别和设备编号。</p>
              )}
              {summary && (
                <ul className="category-list">
                  {summary.sheets.map((sheet) => (
                    <li key={sheet.sheet_name}>
                      <strong>{sheet.equipment_category}</strong>
                      <span>
                        {sheet.device_count} 台，{sheet.record_count} 条
                      </span>
                      <span>
                        {sheet.time_start ?? "无起始时间"} 至 {sheet.time_end ?? "无结束时间"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {archiveTab === "query" && (
            <>
        {!selectedRow && (
          <>
        <form
          className="monitor-form history-search-form"
          onSubmit={(event) => {
            event.preventDefault()
            setSearchPage(1)
            void runHistorySearch(1)
          }}
        >
          <label className="field-label" htmlFor="history-search">
            搜索
            <input
              id="history-search"
              className="text-input"
              value={searchText}
              placeholder="设备中文名称、设备编号或记录编号"
              onChange={(event) => setSearchText(event.target.value)}
            />
          </label>
          <label className="field-label" htmlFor="history-device">
            设备筛选
            <select
              id="history-device"
              className="text-input"
              value={deviceId}
              onChange={(event) => {
                const next = event.target.value
                const device = devices.find((item) => item.device_id === next)
                if (device) {
                  setCategory(device.equipment_category)
                }
                setDeviceId(next)
                haltPlayback()
                setSearchPage(1)
                void runHistorySearch(1, { deviceId: next })
              }}
            >
              <option value="">全部设备</option>
              {devices.map((device) => (
                <option key={device.device_id} value={device.device_id}>
                  {device.device_id} · {device.device_name || device.equipment_category}（{device.record_count} 条）
                </option>
              ))}
            </select>
          </label>
          <label className="field-label" htmlFor="record-start">
            开始采集时间
            <input
              id="record-start"
              className="text-input"
              value={startTime}
              placeholder="YYYY-MM-DD HH:MM:SS"
              onChange={(event) => {
                setStartTime(event.target.value)
                haltPlayback()
              }}
            />
          </label>
          <label className="field-label" htmlFor="record-end">
            结束采集时间
            <input
              id="record-end"
              className="text-input"
              value={endTime}
              placeholder="YYYY-MM-DD HH:MM:SS"
              onChange={(event) => {
                setEndTime(event.target.value)
                haltPlayback()
              }}
            />
          </label>
          <label className="field-label" htmlFor="history-status">
            状态筛选
            <select
              id="history-status"
              className="text-input"
              value={analysisStatusFilter}
              onChange={(event) => {
                const next = event.target.value as AnalysisStatusFilter
                setAnalysisStatusFilter(next)
                setSearchPage(1)
                void runHistorySearch(1, { status: next })
              }}
            >
              {ANALYSIS_STATUS_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <button type="submit" className="test-button" disabled={querying}>
            {querying ? "正在检索..." : "检索记录"}
          </button>
        </form>
        <p className="hint monitor-hint">
          历史查询按条件检索全部匹配记录并分页浏览；下方回放只演示当前设备已加载窗口，不等于全部历史。尚未分析的记录不会标为正常。
        </p>
        {queryError && (
          <p className="status error" role="alert">
            {queryError}
          </p>
        )}
        {searchResult?.status_filter_note && (
          <p className="monitor-note" role="status">{searchResult.status_filter_note}</p>
        )}
        {searchResult && !searchResult.found && (
          <p className="status" role="status">
            没有符合条件的历史记录。可改用设备筛选、缩短关键词或扩大时间范围。
          </p>
        )}
        {searchResult && searchResult.found && (
          <>
            <p className="monitor-note" role="status">
              共 {searchResult.matched_count} 条，当前第 {searchResult.page}/{Math.max(searchResult.total_pages, 1)} 页，每页 {searchResult.page_size} 条。
            </p>
            <div className="record-table-wrap">
              <table className="record-table">
                <caption>历史记录检索结果。点击查看会打开详情并保留各自的 record_id。</caption>
                <thead>
                  <tr>
                    <th>设备</th>
                    <th>采集时间</th>
                    <th>主要指标</th>
                    <th>分析状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {searchResult.records.map((record) => (
                    <tr key={record.record_id} aria-selected={record.record_id === selectedId}>
                      <td>
                        <div>{deviceDisplayName(record)}</div>
                        <div className="file-meta">{record.device_id}</div>
                      </td>
                      <td>
                        {record.collected_at}
                        {hasDuplicateTime(record) ? <div className="file-meta">同时段另有记录</div> : null}
                      </td>
                      <td>{primaryMetricText(record.values, PRIMARY_FIELDS)}</td>
                      <td>
                        <span className={isUnanalyzedStatus(record.analysis_status) ? "status-pill tone-warn" : "status-pill tone-warn"}>
                          {analysisStatusDisplay(record.analysis_status)}
                        </span>
                        {isUnanalyzedStatus(record.analysis_status) ? (
                          <div className="file-meta">尚未分析，不等于正常</div>
                        ) : (
                          <div className="file-meta">不是故障确诊</div>
                        )}
                      </td>
                      <td>
                        <button type="button" className="ghost-button" onClick={() => void openRecordFromList(record.record_id, "list", true)}>
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="monitor-toggles">
              <button
                type="button"
                className="ghost-button"
                disabled={querying || searchResult.page <= 1}
                onClick={() => {
                  const next = searchResult.page - 1
                  setSearchPage(next)
                  void runHistorySearch(next)
                }}
              >
                上一页
              </button>
              <button
                type="button"
                className="ghost-button"
                disabled={querying || searchResult.page >= searchResult.total_pages}
                onClick={() => {
                  const next = searchResult.page + 1
                  setSearchPage(next)
                  void runHistorySearch(next)
                }}
              >
                下一页
              </button>
            </div>
          </>
        )}

        {records && records.found && (
            <section className="playback-panel" aria-label="历史数据回放">
              <h2>历史数据回放</h2>
              <p className="disclaimer">{PLAYBACK_SCOPE_NOTE}</p>
              <p className="monitor-note">{DEMO_PLAYBACK_NOTE}</p>
              <p className="monitor-note">{playbackScopeText(records.returned_count, records.matched_count, records.truncated)}</p>
              <p className="file-meta">
                回放窗口：当前已加载 {records.returned_count} 条（接口上限 100）。
                设备共匹配 {records.matched_count} 条时，请用上方历史查询分页浏览，不要把回放窗口当成全部历史。
              </p>
              <div className="monitor-toggles">
                <button
                  type="button"
                  className="ghost-button"
                  disabled={records.records.length === 0 || playback.status === "playing" || playback.status === "paused"}
                  onClick={beginPlayback}
                >
                  开始回放
                </button>
                <button type="button" className="ghost-button" disabled={playback.status !== "playing"} onClick={pauseCurrentPlayback}>
                  暂停
                </button>
                <button type="button" className="ghost-button" disabled={playback.status !== "paused"} onClick={resumeCurrentPlayback}>
                  继续
                </button>
                <button type="button" className="ghost-button" onClick={haltPlayback}>
                  重置
                </button>
                <button type="button" className="ghost-button monitor-toggle" aria-pressed={playback.speed === 1} onClick={() => choosePlaybackSpeed(1)}>1×</button>
                <button type="button" className="ghost-button monitor-toggle" aria-pressed={playback.speed === 2} onClick={() => choosePlaybackSpeed(2)}>2×</button>
              </div>
              <p className="file-meta">
                当前进度：已回放 {playback.status === "idle" ? 0 : playback.index} / {records.records.length}
              </p>
              <p className="file-meta">
                演示状态：{monitorPhaseText(monitorPhase)}
                {monitorDetail ? `。${monitorDetail}` : ""}
              </p>
              {playbackRecord && (
                <>
                  <p className="file-meta">采集时间：{playbackRecord.collected_at}</p>
                  <div className="metric-grid">
                    {PLAYBACK_FIELDS.map((field) => (
                      <Metric key={field} label={field} value={formatCell(playbackRecord.values, field)} />
                    ))}
                  </div>
                  <button type="button" className="ghost-button" onClick={() => void openRecordFromList(playbackRecord.record_id, "list", true)}>
                    查看当前回放记录详情
                  </button>
                </>
              )}
            </section>
        )}
          </>
        )}

        {selectedRow && (
          <>
          <div className="monitor-toggles">
            <button type="button" className="ghost-button" onClick={closeRecordDetail}>
              {detailSource === "anomaly" ? "返回监测界面" : "返回记录列表"}
            </button>
          </div>
          <RecordDetail
            row={selectedRow}
            detail={detail}
            siblings={[
              ...(searchResult?.records ?? []),
              ...(records?.records ?? []),
            ].filter((record, index, list) =>
              record.collected_at === selectedRow.collected_at &&
              record.record_id !== selectedRow.record_id &&
              list.findIndex((item) => item.record_id === record.record_id) === index,
            )}
            loading={detailLoading}
            error={detailError}
            analyzing={analysisLoading}
            copyNotice={copyNotice}
            techOpen={techFactsOpen}
            onToggleTech={() => setTechFactsOpen((current) => !current)}
            onCopy={async (text) => {
              try {
                await copyText(text)
                setCopyNotice("已复制")
              } catch {
                setCopyNotice("复制失败")
              }
            }}
            onOpen={(recordId) => void openRecordFromList(recordId, "list", true)}
            onAnalyze={() => void analyzeRecord(selectedRow.record_id)}
          />
          <RecordAnalysis
            selectedId={selectedRow.record_id}
            analysis={analysis}
            loading={analysisLoading}
            error={analysisError}
            statsExpanded={statsExpanded}
            onToggleStats={() => setStatsExpanded((current) => !current)}
          />
          </>
        )}
            </>
          )}
          {archiveTab === "events" && (
            <HistoryEvents
              deviceId={deviceId}
              sessionId={autoTask?.device_id === deviceId ? autoTask.task_id : ""}
              events={historyEvents}
              note={historyNote}
              loaded={historyLoaded}
              error={historyError}
              loading={historyLoading}
              expandedEventId={expandedHistoryId}
              onToggle={(eventId) => setExpandedHistoryId((current) => (current === eventId ? "" : eventId))}
              onQueryDevice={() => void loadHistory("device")}
              onQuerySession={() => void loadHistory("session")}
              onRefresh={() => void loadHistory(historyScopeRef.current === "session" ? "session" : "device")}
            />
          )}
          {archiveTab === "stats" && (
            <section aria-label="统计参数">
              <p className="file-meta">{MONITOR_EVENT_NOTE}</p>
              <p className="file-meta">{EXPERIMENTAL_LIMIT_NOTE}</p>
              <p className="file-meta">温度指标：{TEMPERATURE_FIELDS.join("、")}。转速指标：{SPEED_FIELDS.join("、")}。只在记录里实际有数值时才会出现在趋势图中。</p>
              {focusEvent ? (
                <ul className="analysis-list">
                  {focusEvent.metrics.map((metric) => (
                    <li key={metric.field}>
                      <strong>{metric.field}</strong>
                      <span>当前值：{formatMeasurement(metric.current_value, metric.unit)}</span>
                      <span>历史中位数：{formatMeasurement(metric.median, metric.unit)}</span>
                      <span>MAD：{formatMeasurement(metric.mad, metric.unit)}</span>
                      <span>Robust Z-score：{formatAnalysisNumber(metric.robust_z)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="status">选择一条数据偏离记录后，这里显示它的统计参数。</p>
              )}
            </section>
          )}
        </section>
        <AnomalySummaryAlert
          open={alertSession?.summaryOpen === true && alertSession.deviceId === deviceId}
          summary={alertSummary}
          onOpenList={() => {
            setAlertSession((current) => (current ? dismissAlertSummary(current) : current))
            setAnomalyCenterOpen(true)
            window.requestAnimationFrame(() => {
              document.getElementById("anomaly-center")?.scrollIntoView({ block: "start" })
            })
          }}
          onDismiss={() => setAlertSession((current) => (current ? dismissAlertSummary(current) : current))}
        />
      </section>
    </main>
  )
}

function AnomalySummaryAlert({
  open,
  summary,
  onOpenList,
  onDismiss,
}: {
  open: boolean
  summary: ReturnType<typeof buildAlertSummary>
  onOpenList: () => void
  onDismiss: () => void
}) {
  useEffect(() => {
    if (!open) {
      return
    }
    function onKey(keyboardEvent: KeyboardEvent) {
      if (keyboardEvent.key === "Escape") {
        onDismiss()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onDismiss])
  if (!open) {
    return null
  }
  return (
    <div className="alert-backdrop">
      <section className="alert-dialog" role="dialog" aria-modal="true" aria-labelledby="anomaly-alert-title">
        <p className="cockpit-badge">已确认故障 · 告警</p>
        <h2 id="anomaly-alert-title">{FAULT_ALERT_TITLE}</h2>
        <p>涉及设备：{summary.deviceCount} 台</p>
        <p>确认故障告警：{summary.faultAlertCount} 条</p>
        <p>涉及指标：{summary.metricCount} 项</p>
        <p>最近一次异常时间：{summary.latestCollectedAt || "尚无"}</p>
        <p>本监测会话统计偏离记录：{summary.eventCount} 条（可在异常中心查看，不单独弹故障窗）</p>
        <p className="monitor-note">{FAULT_ALERT_NOTE}</p>
        <p className="file-meta">同一监测会话对确认故障最多自动弹一次；关闭后不会因统计偏离连续弹窗。</p>
        <div className="alert-actions">
          <button type="button" className="test-button" onClick={onOpenList}>
            查看异常列表
          </button>
          <button type="button" className="ghost-button" onClick={onDismiss}>
            我知道了
          </button>
        </div>
      </section>
    </div>
  )
}

function AnomalyCenter({
  deviceId,
  open,
  count,
  groups,
  metricOptions,
  metricFilter,
  startFilter,
  endFilter,
  expandedEpisodeId,
  activeId,
  onMetricFilter,
  onStartFilter,
  onEndFilter,
  onToggleEpisode,
  onOpen,
  onOpenRecord,
}: {
  deviceId: string
  open: boolean
  count: number
  groups: readonly EpisodeGroup[]
  metricOptions: readonly string[]
  metricFilter: string
  startFilter: string
  endFilter: string
  expandedEpisodeId: string
  activeId: number | null
  onMetricFilter: (value: string) => void
  onStartFilter: (value: string) => void
  onEndFilter: (value: string) => void
  onToggleEpisode: (id: string) => void
  onOpen: (eventId: number) => void
  onOpenRecord: (event: StoredMonitorEvent) => void
}) {
  if (!open) {
    return (
      <section className="cockpit-deviations" id="anomaly-center" aria-label="异常中心">
        <h2>异常中心</h2>
        <p className="status">开始监测并发现统计偏离后，这里汇总展示待核查的异常过程。</p>
      </section>
    )
  }
  return (
    <section className="cockpit-deviations" id="anomaly-center" aria-label="异常中心">
      <h2>异常中心</h2>
      <p className="file-meta">
        当前设备：{deviceId || "未选择"}。偏离记录 {count} 条，异常过程 {groups.length} 个。统计偏离可在此查询，但不会单独触发故障告警。
      </p>
      <div className="anomaly-filters">
        <label className="field-label" htmlFor="anomaly-metric-filter">
          异常指标
          <select id="anomaly-metric-filter" className="text-input" value={metricFilter} onChange={(event) => onMetricFilter(event.target.value)}>
            <option value="">全部指标</option>
            {metricOptions.map((field) => (
              <option key={field} value={field}>
                {field}
              </option>
            ))}
          </select>
        </label>
        <label className="field-label" htmlFor="anomaly-start-filter">
          开始时间
          <input id="anomaly-start-filter" className="text-input" value={startFilter} placeholder="YYYY-MM-DD HH:MM:SS" onChange={(event) => onStartFilter(event.target.value)} />
        </label>
        <label className="field-label" htmlFor="anomaly-end-filter">
          结束时间
          <input id="anomaly-end-filter" className="text-input" value={endFilter} placeholder="YYYY-MM-DD HH:MM:SS" onChange={(event) => onEndFilter(event.target.value)} />
        </label>
      </div>
      {groups.length === 0 ? (
        <p className="status">当前筛选条件下没有待核查的统计偏离过程。</p>
      ) : (
        <ul className="monitor-event-list">
          {groups.map((group) => {
            const lead = group.events[0]
            const expanded = expandedEpisodeId === group.id
            return (
              <li key={group.id} className="anomaly-episode">
                <button type="button" className="cockpit-event" aria-expanded={expanded} onClick={() => onToggleEpisode(group.id)}>
                  <strong>{group.deviceId}</strong>
                  <span>{group.status}</span>
                  <span>
                    时间 {group.startAt}
                    {group.endAt !== group.startAt ? ` ~ ${group.endAt}` : ""}
                  </span>
                  <span>指标 {group.metricFields.join("、") || "无"}</span>
                  <span>记录 {group.recordIds.length} 条</span>
                </button>
                {expanded && (
                  <ul className="anomaly-episode-records">
                    {group.events.map((item) => {
                      const event = item as StoredMonitorEvent
                      const metric = event.metrics?.[0]
                      return (
                        <li key={event.event_id}>
                          <button
                            type="button"
                            className="ghost-button anomaly-record-open"
                            aria-pressed={activeId === event.event_id}
                            onClick={() => onOpen(event.event_id)}
                          >
                            记录 {event.record_id} · {event.collected_at}
                            {metric
                              ? ` · ${metric.field} 当前 ${formatMeasurement(metric.current_value, metric.unit)} / 中位数 ${formatMeasurement(metric.median, metric.unit)}`
                              : ""}
                          </button>
                          <button type="button" className="ghost-button" onClick={() => onOpenRecord(event)}>
                            查看记录详情
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
                {lead && (
                  <p className="file-meta">
                    过程标识 ep-{group.leadEventId}
                    {" · "}
                    {diagnosisPhaseLabel(
                      typeof lead === "object" && lead && "diagnosis_phase" in lead
                        ? String((lead as { diagnosis_phase?: string }).diagnosis_phase || "")
                        : "",
                    )}
                    {typeof lead === "object" && "fault_alert_required" in lead && lead.fault_alert_required === true
                      ? " · 已确认故障告警"
                      : typeof lead === "object" && "reminder_required" in lead && lead.reminder_required === true
                        ? " · 统计过程起点"
                        : " · 持续异常归并"}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function DiagnosisBoard({
  event,
  techOpen,
  onToggleTech,
  onOpenRecord,
}: {
  event: StoredMonitorEvent | null
  techOpen: boolean
  onToggleTech: () => void
  onOpenRecord: (event: StoredMonitorEvent) => void
}) {
  const steps = diagnosisSteps(event)
  const ready = event !== null && diagnosisDetailAvailable(event)
  return (
    <section className="cockpit-diagnosis" id="diagnosis-panel" aria-label="智能诊断分析">
      <h2>智能诊断分析</h2>
      <ol className="cockpit-steps">
        {steps.map((step) => (
          <li key={step.title} data-done={step.done ? "yes" : "no"}>
            <strong>{step.done ? "已完成" : "未完成"} · {step.title}</strong>
            <span>{step.detail}</span>
          </li>
        ))}
      </ol>
      {event && (
        <div className="monitor-toggles">
          <button type="button" className="ghost-button" onClick={() => onOpenRecord(event)}>
            查看记录详情
          </button>
        </div>
      )}
      {event && <SupplementaryMeasurementPanel recordId={event.record_id} deviceId={event.device_id} collectedAt={event.collected_at} />}
      {!event && <p className="status">选择一条数据偏离记录后，这里显示已保存的诊断线索。</p>}
      {event && !ready && (
        <div className="diagnosis-block">
          <p className="monitor-note">{HISTORICAL_DIAGNOSIS_NOTE}</p>
          <p className="monitor-note">{ALERT_INSUFFICIENT_TEXT}</p>
          <p className="monitor-note">确诊情况：{NOT_CONFIRMED_TEXT}</p>
        </div>
      )}
      {event && ready && (
        <>
          <section className="diagnosis-block">
            <h3>发现的数据变化</h3>
            <ul className="analysis-list">
              {event.metrics.map((metric) => (
                <li key={metric.field}>
                  <strong>{metric.field}</strong>
                  <span>当前值 {formatMeasurement(metric.current_value, metric.unit)}</span>
                  <span>历史中位数 {formatMeasurement(metric.median, metric.unit)}</span>
                  {techOpen ? <span>Robust Z-score {formatAnalysisNumber(metric.robust_z)}</span> : null}
                </li>
              ))}
            </ul>
          </section>
          <section className="diagnosis-block">
            <h3>相关诊断线索</h3>
            <p className="file-meta">{eventDiagnosisLabel(event.diagnosis_level)}</p>
            {event.diagnostic_clues.length === 0 ? (
              <p className="status">接口没有返回诊断线索。</p>
            ) : (
              <ul className="analysis-list">
                {event.diagnostic_clues.map((clue) => (
                  <li key={`${clue.feature_code}-${clue.line_number}`}>
                    <strong>诊断线索</strong>
                    <span>知识章节：{clue.related_fault_section}</span>
                    <span>{clue.unverified_reason}</span>
                    {clueMeasurementLines(clue).map((line) => (
                      <span key={line}>已有测量：{line}</span>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="diagnosis-block">
            <h3>判断依据</h3>
            {(event.evidence_sources ?? []).length === 0 ? (
              <p className="status">接口没有返回知识来源。</p>
            ) : (
              <ul className="analysis-list">
                {(event.evidence_sources ?? []).map((source) => (
                  <li key={`${source.document}-${source.line_number}-${source.feature_code}`}>
                    <strong>{source.document}</strong>
                    <span>第{source.line_number}行</span>
                    <span>知识章节：{source.related_fault_section}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="diagnosis-block">
            <h3>还需补充的数据</h3>
            <p className="monitor-note">{MISSING_MEASUREMENT_TEXT}</p>
            {(event.missing_conditions ?? []).length === 0 ? (
              <p className="status">接口没有返回缺失条件。</p>
            ) : (
              <ul className="analysis-list">
                {(event.missing_conditions ?? []).map((item) => (
                  <li key={item}>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <p className="monitor-note">是否能够判断具体故障：不能。确诊情况：{NOT_CONFIRMED_TEXT}</p>
          {event.candidate_faults.length === 0 ? (
            <p className="monitor-note">{EMPTY_EVENT_FAULT_TEXT}</p>
          ) : (
            <ul className="analysis-list">
              {event.candidate_faults.map((candidate) => (
                <li key={candidate.fault}>
                  <strong>{candidate.fault}</strong>
                  <span>可能匹配，不是确诊</span>
                  <span>{candidate.note}</span>
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="ghost-button" aria-expanded={techOpen} onClick={onToggleTech}>
            {techOpen ? "收起技术依据" : "查看技术依据"}
          </button>
          {techOpen && (
            <div className="diagnosis-block">
              <p className="file-meta">诊断状态：{event.diagnosis_status}</p>
              <p className="file-meta">{event.event_note}</p>
              <h3>知识来源</h3>
              <h3>缺失条件</h3>
              {event.diagnostic_clues.map((clue) => (
                <p key={`${clue.document}-${clue.line_number}`} className="file-meta">
                  {clue.document} 第{clue.line_number}行。{clue.method || clue.unverified_reason}
                  {clue.official_condition ? ` 官方条件：${clue.official_condition}` : ""}
                </p>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}

function HistoryEvents({
  deviceId,
  sessionId,
  events,
  note,
  loaded,
  error,
  loading,
  expandedEventId,
  onToggle,
  onQueryDevice,
  onQuerySession,
  onRefresh,
}: {
  deviceId: string
  sessionId: string
  events: readonly StoredMonitorEvent[]
  note: string
  loaded: boolean
  error: string
  loading: boolean
  expandedEventId: string
  onToggle: (eventId: string) => void
  onQueryDevice: () => void
  onQuerySession: () => void
  onRefresh: () => void
}) {
  return (
    <section className="monitor-events" aria-label="历史监测事件">
      <h2>历史监测事件</h2>
      <p className="disclaimer">{MONITOR_EVENT_NOTE}</p>
      <p className="file-meta">列表来自已保存的历史记录。采集时间是 Excel 原始时间，服务器保存时间是写入数据库的时间。</p>
      <div className="monitor-toggles">
        <button type="button" className="ghost-button" disabled={!deviceId || loading} onClick={onQueryDevice}>
          查询当前设备
        </button>
        <button type="button" className="ghost-button" disabled={!sessionId || loading} onClick={onQuerySession}>
          查询当前自动监测任务
        </button>
        <button type="button" className="ghost-button" disabled={loading || (!deviceId && !sessionId)} onClick={onRefresh}>
          {loading ? "正在刷新..." : "刷新历史列表"}
        </button>
      </div>
      {error && (
        <p className="status error" role="alert">
          {error}
        </p>
      )}
      {note && <p className="file-meta">{note}</p>}
      {loaded && !loading && !error && events.length === 0 && <p className="status">暂无已保存的统计偏离事件。</p>}
      {events.length > 0 && (
        <ul className="monitor-event-list">
          {events.map((item) => (
            <SavedEventCard
              key={`${item.session_id}-${item.event_id}`}
              event={item}
              expanded={expandedEventId === String(item.event_id)}
              onToggle={() => onToggle(String(item.event_id))}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function SavedEventCard({ event, expanded, onToggle }: { event: StoredMonitorEvent; expanded: boolean; onToggle: () => void }) {
  const diagnosisReady = diagnosisDetailAvailable(event)
  return (
    <li className="monitor-event">
      <button type="button" className="ghost-button" aria-expanded={expanded} onClick={onToggle}>
        {expanded ? "收起诊断详情" : "查看诊断详情"}
      </button>
      <p>
        <strong>event_id {event.event_id}</strong> {event.event_type}
      </p>
      <p className="file-meta">设备编号：{event.device_id}</p>
      <p className="file-meta">记录编号：{event.record_id}</p>
      <p className="file-meta">采集时间：{event.collected_at}</p>
      <p className="file-meta">服务器保存时间：{event.processed_at}</p>
      <p className="file-meta">{diagnosisReady ? `诊断状态：${eventDiagnosisLabel(event.diagnosis_level)}` : HISTORICAL_DIAGNOSIS_NOTE}</p>
      <p className="file-meta">确诊情况：{NOT_CONFIRMED_TEXT}</p>
      {expanded && (
        <div className="diagnosis-detail">
          <section className="diagnosis-block">
            <h3>统计偏离指标</h3>
            <p className="file-meta">统计检测状态：{event.detection_status}</p>
            <p className="file-meta">数据来源：{event.data_source}</p>
            <p className="file-meta">{event.event_note}</p>
            {event.metrics.length === 0 ? (
              <p className="status">接口没有返回偏离指标。</p>
            ) : (
              <ul className="analysis-list">
                {event.metrics.map((metric) => (
                  <li key={metric.field}>
                    <strong>{metric.field}</strong>
                    <span>指标状态：{metric.metric_status}</span>
                    <span>当前值：{formatMeasurement(metric.current_value, metric.unit)}</span>
                    <span>历史中位数：{formatMeasurement(metric.median, metric.unit)}</span>
                    <span>MAD：{formatMeasurement(metric.mad, metric.unit)}</span>
                    <span>Robust Z-score：{formatAnalysisNumber(metric.robust_z)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <SupplementaryMeasurementPanel recordId={event.record_id} deviceId={event.device_id} collectedAt={event.collected_at} />
          {diagnosisReady ? (
            <EventDiagnosis event={event} />
          ) : (
            <section className="diagnosis-block">
              <h3>诊断详情</h3>
              <p className="monitor-note">{HISTORICAL_DIAGNOSIS_NOTE}</p>
              <p className="monitor-note">确诊情况：{NOT_CONFIRMED_TEXT}</p>
            </section>
          )}
        </div>
      )}
    </li>
  )
}

function EventDiagnosis({ event }: { event: StoredMonitorEvent }) {
  const sources = event.evidence_sources ?? []
  const missing = event.missing_conditions ?? []
  return (
    <>
      <section className="diagnosis-block">
        <h3>诊断状态</h3>
        <p className="deviation-status">{eventDiagnosisLabel(event.diagnosis_level)}</p>
        <p className="file-meta">{event.diagnosis_status}</p>
        <p className="monitor-note">确诊情况：{NOT_CONFIRMED_TEXT}</p>
      </section>
      <section className="diagnosis-block">
        <h3>诊断线索</h3>
        {event.diagnostic_clues.length === 0 ? (
          <p className="status">接口没有返回诊断线索。</p>
        ) : (
          <ul className="analysis-list">
            {event.diagnostic_clues.map((clue) => (
              <li key={`${clue.feature_code}-${clue.document}-${clue.line_number}`}>
                <strong>诊断线索</strong>
                <span>特征编号：{clue.feature_code}</span>
                <span>知识章节：{clue.related_fault_section}</span>
                <span>
                  {clue.document} 第{clue.line_number}行
                </span>
                <span>完整条件：{clue.complete_condition_met ? "已满足" : "未满足"}</span>
                <span>{clue.unverified_reason}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="diagnosis-block">
        <h3>知识来源</h3>
        {sources.length === 0 ? (
          <p className="status">接口没有返回知识来源。</p>
        ) : (
          <ul className="analysis-list">
            {sources.map((source) => (
              <li key={`${source.document}-${source.line_number}-${source.feature_code}`}>
                <strong>{source.document}</strong>
                <span>第{source.line_number}行</span>
                <span>特征编号：{source.feature_code}</span>
                <span>知识章节：{source.related_fault_section}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="diagnosis-block">
        <h3>缺失条件</h3>
        <p className="monitor-note">{MISSING_MEASUREMENT_TEXT}</p>
        {missing.length === 0 ? (
          <p className="status">接口没有返回缺失条件。</p>
        ) : (
          <ul className="analysis-list">
            {missing.map((item) => (
              <li key={item}>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="diagnosis-block">
        <h3>候选故障</h3>
        {event.candidate_faults.length === 0 ? (
          <p className="monitor-note">{EMPTY_EVENT_FAULT_TEXT}</p>
        ) : (
          <ul className="analysis-list">
            {event.candidate_faults.map((candidate) => (
              <li key={candidate.fault}>
                <strong>{candidate.fault}</strong>
                <span>可能匹配，不是确诊</span>
                <span>{candidate.note}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  )
}

function RecordDetail({
  row,
  detail,
  siblings,
  loading,
  error,
  analyzing,
  copyNotice,
  techOpen,
  onToggleTech,
  onCopy,
  onOpen,
  onAnalyze,
}: {
  row: DatasetRecord
  detail: DatasetRecord | null
  siblings: DatasetRecord[]
  loading: boolean
  error: string
  analyzing: boolean
  copyNotice: string
  techOpen: boolean
  onToggleTech: () => void
  onCopy: (text: string) => void
  onOpen: (recordId: string) => void
  onAnalyze: () => void
}) {
  const source = detail ?? row
  const metrics = visibleMetrics(source.values)
  return (
    <section className="record-detail" aria-live="polite">
      <h2>记录详情</h2>
      {hasDuplicateTime(source) && <p className="monitor-note">该设备在此时间存在多条不同记录，已按记录编号分开保留。</p>}
      <dl className="detail-facts">
        <div>
          <dt>记录编号</dt>
          <dd>
            {source.record_id}{" "}
            <button type="button" className="link-button" onClick={() => onCopy(source.record_id)}>复制</button>
          </dd>
        </div>
        <div>
          <dt>设备编号</dt>
          <dd>
            {source.device_id} · {deviceDisplayName(source)}{" "}
            <button type="button" className="link-button" onClick={() => onCopy(source.device_id)}>复制</button>
          </dd>
        </div>
        <div>
          <dt>采集时间</dt>
          <dd>{source.collected_at}</dd>
        </div>
        <div>
          <dt>数据来源</dt>
          <dd>官方历史数据集（只读）</dd>
        </div>
      </dl>
      {copyNotice && <p className="file-meta">{copyNotice}</p>}
      <button type="button" className="ghost-button" onClick={onToggleTech}>
        {techOpen ? "收起详细信息" : "展开详细信息"}
      </button>
      {techOpen && (
        <dl className="detail-facts">
          <div>
            <dt>原始表格行号</dt>
            <dd>{source.excel_row}</dd>
          </div>
          <div>
            <dt>工作表</dt>
            <dd>{source.sheet_name}</dd>
          </div>
          <div>
            <dt>同时段记录数</dt>
            <dd>{source.same_timestamp_count}</dd>
          </div>
        </dl>
      )}
      <button type="button" className="test-button" disabled={analyzing} onClick={onAnalyze}>
        {analyzing ? "正在分析..." : "分析此记录"}
      </button>
      <p className="file-meta">只会分析当前选中的这一条记录。统计偏离不是故障确诊。</p>
      {siblings.length > 0 && (
        <div className="sibling-list">
          <p>同一采集时间的其他记录：</p>
          {siblings.map((record) => (
            <button key={record.record_id} type="button" className="ghost-button" onClick={() => onOpen(record.record_id)}>
              {record.record_id}
            </button>
          ))}
        </div>
      )}
      {loading && <p className="status">正在按记录编号核对完整观测。</p>}
      {error && (
        <p className="status error" role="alert">
          {error}
        </p>
      )}
      {detail && <p className="file-meta">以下指标来自观测接口按记录编号核对的结果。</p>}
      <SupplementaryMeasurementPanel recordId={source.record_id} deviceId={source.device_id} collectedAt={source.collected_at} />
      <ul className="metric-list">
        {metrics.map(([key, value]) => (
          <li key={key}>
            <span>{key}</span>
            <strong>{value === null ? "无数据" : formatDisplayNumber(value)}</strong>
          </li>
        ))}
      </ul>
    </section>
  )
}

function RecordAnalysis({
  selectedId,
  analysis,
  loading,
  error,
  statsExpanded,
  onToggleStats,
}: {
  selectedId: string
  analysis: DatasetAnalysis | null
  loading: boolean
  error: string
  statsExpanded: boolean
  onToggleStats: () => void
}) {
  const current = analysis !== null && analysis.record_id === selectedId ? analysis : null
  if (!loading && !error && current === null) {
    return null
  }
  const deviation = current?.statistical_deviation
  const anomalous = deviation ? metricsForFields(deviation, deviation.anomalous_metrics) : []
  const unresolved = deviation ? unresolvedMetrics(deviation) : []
  return (
    <section className="record-analysis" aria-live="polite" aria-busy={loading}>
      <h2>记录分析</h2>
      <p className="disclaimer">{ANALYSIS_SCOPE_NOTE}</p>
      {loading && <p className="status">正在分析当前记录。</p>}
      {error && (
        <p className="status error" role="alert">
          {error}
        </p>
      )}
      {current && deviation && (
        <>
          <h3>观测记录</h3>
          <dl className="detail-facts">
            <div>
              <dt>设备编号</dt>
              <dd>{current.observation.device_id}</dd>
            </div>
            <div>
              <dt>记录编号</dt>
              <dd>{current.record_id}</dd>
            </div>
            <div>
              <dt>采集时间</dt>
              <dd>{current.observation.collected_at}</dd>
            </div>
            <div>
              <dt>数据来源</dt>
              <dd>{current.data_source === "official_dataset" ? "官方历史数据集" : current.data_source}</dd>
            </div>
          </dl>

          <h3>统计偏离</h3>
          <p className="deviation-status">结论：{deviation.detection_status}</p>
          <p className="monitor-note">统计偏离不是故障确诊。未分析或证据不足时不能写成设备正常。</p>
          {anomalous.length === 0 ? (
            <p className="status">本次没有指标被标为统计偏离。</p>
          ) : (
            <ul className="analysis-list">
              {anomalous.map((item, index) =>
                item ? (
                  <li key={item.field}>
                    <strong>{item.field}</strong>
                    <span>指标状态：{item.metric_status}</span>
                    <span>当前值：{formatMeasurement(item.current_value, item.unit)}</span>
                    {statsExpanded ? (
                      <>
                        <span>历史中位数：{formatAnalysisNumber(item.median)}</span>
                        <span>MAD：{formatAnalysisNumber(item.mad)}</span>
                        <span>Robust Z-score：{formatAnalysisNumber(item.robust_z)}</span>
                      </>
                    ) : null}
                  </li>
                ) : (
                  <li key={`${deviation.anomalous_metrics[index]}-${index}`}>
                    <strong>{deviation.anomalous_metrics[index]}</strong>
                    <span>接口未返回该指标的数值</span>
                  </li>
                ),
              )}
            </ul>
          )}
          <button type="button" className="ghost-button" onClick={onToggleStats}>
            {statsExpanded ? "收起完整统计信息" : "展开完整统计信息"}
          </button>
          {statsExpanded && (
            <>
              <p className="file-meta">历史样本数量：{deviation.history_sample_count}</p>
              <p className="monitor-note">{EXPERIMENTAL_LIMIT_NOTE}</p>
              {deviation.experimental_threshold.note && <p className="file-meta">{deviation.experimental_threshold.note}</p>}
              <p className="file-meta">
                {deviation.operating_condition.comparable ? "工况比较：接口标记为可比。" : "工况不可比。现有工况不能用来判断设备是否正常。"}
              </p>
            </>
          )}
          {unresolved.length > 0 && (
            <>
              <h3>未能判定的指标</h3>
              <ul className="analysis-list">
                {unresolved.map((item) => (
                  <li key={item.field}>
                    <strong>{item.field}</strong>
                    <span>指标状态：{item.metric_status}</span>
                    <span>{item.reason || "接口未返回原因"}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          <h3>诊断线索</h3>
          <p className="monitor-note">{analysisLevelText(current.diagnosis_level)}</p>
          {current.diagnostic_clues.length === 0 ? (
            <p className="status">接口没有返回诊断线索。</p>
          ) : (
            <ul className="analysis-list">
              {current.diagnostic_clues.map((clue) => (
                <li key={`${clue.feature_code}-${clue.line_number}`}>
                  <strong>{clue.partial_condition_met && !clue.complete_condition_met ? "部分线索" : clue.feature_code}</strong>
                  <span>官方章节：{clue.related_fault_section}</span>
                  <span>
                    来源：{clue.document} 第{clue.line_number}行
                  </span>
                  <span>完整诊断条件：{clue.complete_condition_met ? "已满足" : "未满足"}</span>
                  <span>{clue.unverified_reason}</span>
                </li>
              ))}
            </ul>
          )}

          <h3>诊断结论与限制</h3>
          {current.candidate_faults.length === 0 ? (
            <p className="monitor-note">{EMPTY_CANDIDATE_TEXT}</p>
          ) : (
            <ul className="analysis-list">
              {current.candidate_faults.map((candidate) => (
                <li key={candidate.fault}>
                  <strong>{candidate.fault}</strong>
                  <span>可能匹配，不是确诊</span>
                  <span>{candidate.note}</span>
                </li>
              ))}
            </ul>
          )}
          <h3>缺失数据</h3>
          <PlainList items={current.missing_data} empty="接口没有返回缺失数据。" />
          <h3>证据不足原因</h3>
          <PlainList items={current.insufficient_reasons} empty="接口没有返回证据不足原因。" />
          <h3>提示</h3>
          <PlainList items={current.warnings} empty="接口没有返回提示。" />
          {current.analysis_note && <p className="file-meta">{current.analysis_note}</p>}
        </>
      )}
    </section>
  )
}

function PlainList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="status">{empty}</p>
  }
  return (
    <ul className="analysis-list">
      {items.map((item, index) => (
        <li key={`${index}-${item}`}>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}
