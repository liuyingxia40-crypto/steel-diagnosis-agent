import { readFileSync } from "node:fs"

import type { DatasetAnalysis, DatasetRecord } from "./src/types/api.ts"
import {
  MONITOR_EVENT_NOTE,
  applyPlaybackFailure,
  applyPlaybackSuccess,
  changePlaybackSpeed,
  deviationMetrics,
  initialPlayback,
  isDatasetAnalysis,
  pausePlayback,
  playbackAnalysisAction,
  playbackIntervalMs,
  resumePlayback,
  startPlayback,
  tickPlayback,
} from "./src/monitoring/view.ts"

const failures: string[] = []

function check(name: string, passed: boolean, detail = "") {
  const line = `${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`
  console.log(line)
  if (!passed) {
    failures.push(line)
  }
}

function drive(recordIds: readonly string[], speed: 1 | 2) {
  const records = recordIds.map((record_id) => ({ record_id }))
  let state = changePlaybackSpeed(startPlayback(initialPlayback(), records.length), speed)
  if (state.speed !== speed) {
    state = { ...state, speed }
  }
  const requested: string[] = []
  const processed: string[] = []
  let guard = 0
  while (guard < records.length * 4 + 5) {
    guard += 1
    const action = playbackAnalysisAction(state, records, processed, null)
    if (action.action === "analyze") {
      const current = records[state.index - 1]
      if (!current || current.record_id !== action.recordId || state.index !== requested.length + 1) {
        return { requested, processed, state, ordered: false }
      }
      requested.push(action.recordId)
      processed.push(action.recordId)
      continue
    }
    if (action.action === "wait") {
      if (action.waitMs !== playbackIntervalMs(speed) || action.recordId !== records[state.index - 1]?.record_id) {
        return { requested, processed, state, ordered: false }
      }
      state = tickPlayback(state, records.length)
      continue
    }
    break
  }
  return { requested, processed, state, ordered: true }
}

const hundred = Array.from({ length: 100 }, (_, index) => `REC-${index + 1}`)
const played = drive(hundred, 1)
const playedFast = drive(hundred, 2)
check(
  "A 已加载记录逐条请求且不提前分析",
  played.ordered &&
    played.requested.length === 100 &&
    played.requested.every((recordId, index) => recordId === hundred[index]) &&
    playedFast.ordered &&
    playedFast.requested.length === 100 &&
    playbackIntervalMs(1) === 1000 &&
    playbackIntervalMs(2) === 500 &&
    playbackAnalysisAction({ status: "playing", index: 1, speed: 1 }, hundred.map((record_id) => ({ record_id })), [], "REC-1").action === "hold",
)

const motorRecords = (await fetch("http://127.0.0.1:8000/api/dataset/records?device_id=MM-01&limit=100").then((response) => response.json())) as {
  records: DatasetRecord[]
  returned_count: number
  matched_count: number
  truncated: boolean
}
const motorDrive = drive(motorRecords.records.map((record) => record.record_id), 1)
check(
  "A MM-01 当前100条的回放计划",
  motorRecords.returned_count === 100 &&
    motorRecords.matched_count === 1030 &&
    motorRecords.truncated === true &&
    motorDrive.ordered &&
    motorDrive.requested.length === 100 &&
    motorDrive.requested[0] === motorRecords.records[0]?.record_id &&
    motorDrive.requested[99] === motorRecords.records[99]?.record_id,
  `first=${motorDrive.requested[0]} last=${motorDrive.requested[99]}`,
)

const targetRecords = (await fetch(
  "http://127.0.0.1:8000/api/dataset/records?device_id=MM-01&start_time=2026-04-03%2011:05:00&end_time=2026-04-03%2011:05:00&limit=100",
).then((response) => response.json())) as { records: DatasetRecord[] }
const target = targetRecords.records.find((record) => record.record_id === "REC-MA-20260403-00346")
const targetStarted = Date.now()
const targetBody: unknown = target
  ? await fetch(`http://127.0.0.1:8000/api/dataset/analysis/${target.record_id}`).then((response) => response.json())
  : null
const targetMs = Date.now() - targetStarted
const targetAnalysis = isDatasetAnalysis(targetBody) ? targetBody : null
const targetEvent = target && targetAnalysis
  ? applyPlaybackSuccess(
      {
        requestSessionId: 1,
        currentSessionId: 1,
        record: target,
        processedIds: [],
        events: [],
      },
      targetAnalysis,
    )
  : null
const yMetric = targetEvent?.events[0]?.metrics.find((metric) => metric.field === "Y向振动(mm/s)")
const yRaw = targetAnalysis?.statistical_deviation.analyzed_metrics.find((metric) => metric.field === "Y向振动(mm/s)")
check(
  "B 自动记录 Y 向振动偏离且不生成故障名称",
  targetEvent?.accepted === true &&
    targetEvent.events.length === 1 &&
    targetEvent.events[0]?.category === "统计偏离" &&
    targetEvent.events[0]?.recordId === "REC-MA-20260403-00346" &&
    targetEvent.events[0]?.deviceId === "MM-01" &&
    yMetric?.currentText === "3.017 mm/s" &&
    yMetric.metricStatus === "偏离基线" &&
    yRaw?.robust_z === 3.923797970479703 &&
    yMetric.robustZText === "3.923797970479703" &&
    targetEvent.events[0]?.diagnosisLevel === "diagnostic_clue" &&
    targetEvent.events[0]?.levelLabel === "部分诊断线索" &&
    targetEvent.events[0]?.candidateFaults.length === 0 &&
    targetEvent.events[0]?.source === targetAnalysis?.data_source &&
    deviationMetrics(targetAnalysis as DatasetAnalysis).every((metric) => metric.metric_status === "偏离基线") &&
    !targetEvent.events[0]?.levelLabel.includes("转子不平衡") &&
    !targetEvent.events[0]?.levelLabel.includes("轴承磨损"),
  `ms=${targetMs} current=${yMetric?.currentText} z=${yMetric?.robustZText} events=${targetEvent?.events.length}`,
)

const duplicate = target && targetAnalysis && targetEvent
  ? applyPlaybackSuccess(
      {
        requestSessionId: 1,
        currentSessionId: 1,
        record: target,
        processedIds: targetEvent.processedIds,
        events: targetEvent.events,
      },
      targetAnalysis,
    )
  : null
let paused = pausePlayback({ status: "playing", index: 1, speed: 1 })
const pausedAction = playbackAnalysisAction(paused, [{ record_id: target?.record_id ?? "" }], targetEvent?.processedIds ?? [], null)
paused = resumePlayback(paused, 1)
const resumedAction = playbackAnalysisAction(paused, [{ record_id: target?.record_id ?? "" }], targetEvent?.processedIds ?? [], null)
const continued = playbackAnalysisAction(
  resumePlayback(pausePlayback({ status: "playing", index: 1, speed: 2 }), 3),
  [{ record_id: "A" }, { record_id: "B" }, { record_id: "C" }],
  ["A"],
  null,
)
const resetResult = target
  ? applyPlaybackSuccess(
      {
        requestSessionId: 1,
        currentSessionId: 2,
        record: target,
        processedIds: [],
        events: [],
      },
      targetAnalysis as DatasetAnalysis,
    )
  : null
check(
  "C 暂停继续和重置不重复生成事件",
  duplicate?.accepted === false &&
    duplicate?.events.length === 1 &&
    pausedAction.action === "hold" &&
    resumedAction.action === "done" &&
    continued.action === "wait" &&
    continued.recordId === "A" &&
    continued.waitMs === 500 &&
    resetResult?.accepted === false &&
    resetResult.events.length === 0,
)

const fanRecords = (await fetch(
  "http://127.0.0.1:8000/api/dataset/records?device_id=FF-01&start_time=2026-03-14%2000:15:00&end_time=2026-03-14%2000:15:00&limit=100",
).then((response) => response.json())) as { records: DatasetRecord[] }
const fanAnalyses = await Promise.all(
  fanRecords.records.map(async (record) => {
    const body: unknown = await fetch(`http://127.0.0.1:8000/api/dataset/analysis/${record.record_id}`).then((response) => response.json())
    return { record, analysis: isDatasetAnalysis(body) ? body : null }
  }),
)
let fanProcessed: string[] = []
let fanEvents = targetEvent?.events.slice(0, 0) ?? []
let fanSessionOk = true
for (const item of fanAnalyses) {
  if (!item.analysis) {
    fanSessionOk = false
    break
  }
  const applied = applyPlaybackSuccess(
    {
      requestSessionId: 4,
      currentSessionId: 4,
      record: item.record,
      processedIds: fanProcessed,
      events: fanEvents,
    },
    item.analysis,
  )
  fanSessionOk = fanSessionOk && applied.accepted
  fanProcessed = applied.processedIds
  fanEvents = applied.events
}
const fanPlan = drive(fanRecords.records.map((record) => record.record_id), 1)
check(
  "D 相同时间的两条记录分别分析",
  fanRecords.records.length === 2 &&
    fanRecords.records[0]?.collected_at === fanRecords.records[1]?.collected_at &&
    fanRecords.records[0]?.record_id !== fanRecords.records[1]?.record_id &&
    fanSessionOk &&
    fanProcessed.length === 2 &&
    fanProcessed[0] !== fanProcessed[1] &&
    fanPlan.requested.length === 2 &&
    fanPlan.requested[0] === fanRecords.records[0]?.record_id &&
    fanPlan.requested[1] === fanRecords.records[1]?.record_id,
  fanRecords.records.map((record) => record.record_id).join(","),
)

const failed = applyPlaybackFailure(
  {
    requestSessionId: 5,
    currentSessionId: 5,
    record: { record_id: "REC-FAIL", device_id: "MM-01", collected_at: "2026-03-14 00:05:00" },
    processedIds: [],
    events: [],
  },
  "请求失败，HTTP 500",
)
const failedAgain = applyPlaybackFailure(
  {
    requestSessionId: 5,
    currentSessionId: 5,
    record: { record_id: "REC-FAIL", device_id: "MM-01", collected_at: "2026-03-14 00:05:00" },
    processedIds: failed.processedIds,
    events: failed.events,
  },
  "请求失败，HTTP 500",
)
check(
  "E 分析失败单独记录",
  failed.accepted &&
    failed.events.length === 1 &&
    failed.events[0]?.category === "分析失败" &&
    failed.events[0]?.metrics.length === 0 &&
    failed.events[0]?.analysis === null &&
    failed.statusText === "分析失败：请求失败，HTTP 500" &&
    !failed.statusText.includes("未发现异常") &&
    !failed.statusText.includes("无异常") &&
    !failed.statusText.includes("正常") &&
    failedAgain.accepted === false &&
    failedAgain.events.length === 1,
)

const stale = target
  ? applyPlaybackSuccess(
      {
        requestSessionId: 1,
        currentSessionId: 9,
        record: { record_id: target.record_id, device_id: "FF-01", collected_at: target.collected_at },
        processedIds: [],
        events: [],
      },
      targetAnalysis as DatasetAnalysis,
    )
  : null
check("F 旧会话或另一台设备的结果被拒绝", stale?.accepted === false && stale?.events.length === 0 && stale?.processedIds.length === 0)

const pageSource = readFileSync(new URL("./src/pages/EquipmentMonitoringPage.tsx", import.meta.url), "utf8")
const viewSource = readFileSync(new URL("./src/monitoring/view.ts", import.meta.url), "utf8")
const appSource = readFileSync(new URL("./src/App.tsx", import.meta.url), "utf8")
const labels = ["设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度"]
check(
  "G/H 页面保留原功能且不展示答案标签",
  pageSource.includes("监测事件") &&
    pageSource.includes("正在分析") &&
    pageSource.includes("inspectCurrent") &&
    pageSource.includes("datasetAnalysisPath") &&
    pageSource.split("/api/dataset/analysis/").length - 1 === 1 &&
    pageSource.includes("分析此记录") &&
    pageSource.includes("MONITOR_EVENT_NOTE") &&
    viewSource.includes(MONITOR_EVENT_NOTE) &&
    viewSource.includes("正在分析") &&
    pageSource.includes("stopMonitorSession()") &&
    pageSource.includes("autoAbortRef.current?.abort()") &&
    !pageSource.includes("advancePlayback") &&
    !pageSource.includes("detect_anomaly") &&
    !pageSource.includes("轴承磨损") &&
    !pageSource.includes("传感器已连接") &&
    labels.every((label) => !pageSource.includes(label)) &&
    !appSource.includes("智能问答") &&
    appSource.includes("知识图谱") &&
    appSource.includes("<VibrationAnalysisPage />") &&
    appSource.includes("设备运行监测"),
)

console.log(`SUMMARY ${failures.length === 0 ? "ok" : "failed"} ${failures.length}`)
if (failures.length > 0) {
  process.exit(1)
}
