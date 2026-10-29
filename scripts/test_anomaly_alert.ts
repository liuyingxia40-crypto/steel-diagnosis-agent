import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  ALERT_INSUFFICIENT_TEXT,
  FAULT_ALERT_TITLE,
  acceptPolledEvents,
  buildAlertSummary,
  createMonitorAlertSession,
  dedupeEventsById,
  dismissAlertSummary,
  filterEpisodeGroups,
  freshAlertEvents,
  groupEventsIntoEpisodes,
  ingestSessionEvents,
  mergeAlertQueue,
  needsFaultAlert,
  needsPopupReminder,
  openAlertSummary,
  readAlertCursor,
  rememberPrompted,
  writeAlertCursor,
  type AlertStorage,
  type DeviceAlertCursor,
} from "../frontend/src/monitoring/anomalyAlert.ts"
import { diagnosisDetailAvailable } from "../frontend/src/monitoring/view.ts"
import type { StoredMonitorEvent } from "../frontend/src/types/api.ts"

const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

function memoryStorage(): AlertStorage & { dump(): Map<string, string> } {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value)
    },
    dump: () => values,
  }
}

function memoryStorageFrom(source: Map<string, string>): AlertStorage {
  const values = new Map(source)
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value)
    },
  }
}

function event(
  id: number,
  deviceId = "CC-01",
  reminderRequired = true,
  options: {
    collectedAt?: string
    recordId?: string
    metrics?: StoredMonitorEvent["metrics"]
    faultAlert?: boolean
    phase?: string
  } = {},
): StoredMonitorEvent {
  return {
    event_id: id,
    session_id: "auto-view",
    record_id: options.recordId ?? `REC-${id}`,
    device_id: deviceId,
    collected_at: options.collectedAt ?? `2026-03-14 10:${String(id % 60).padStart(2, "0")}:00`,
    processed_at: "2026-10-28T12:00:00+08:00",
    event_type: "统计偏离",
    diagnosis_level: "statistical_deviation",
    diagnosis_available: false,
    diagnosis_status: "",
    detection_status: "偏离基线",
    data_source: "钢铁设备数据集_开发集.xlsx",
    event_scope: "official_development_set_history_replay",
    event_note: "实验性统计偏离",
    metrics:
      options.metrics ??
      [{ field: "Y向振动(mm/s)", unit: "mm/s", current_value: 3.2, median: 1, mad: 0.2, robust_z: 4, metric_status: "偏离基线" }],
    diagnostic_clues: [],
    evidence_sources: [],
    missing_conditions: [],
    candidate_faults: [],
    confirmed: false,
    reminder_required: reminderRequired,
    fault_alert_required: options.faultAlert === true,
    diagnosis_phase: options.phase ?? (options.faultAlert ? "confirmed_fault" : "statistical_deviation"),
  }
}

const storage = memoryStorage()
const first = acceptPolledEvents(null, [event(2), event(5)])
writeAlertCursor("CC-01", first.cursor, storage)
check("1 首次加载只建立基线，不把旧事件当新事件", first.baselinedNow && first.fresh.length === 0 && first.cursor.baselineMaxId === 5)

const second = acceptPolledEvents(readAlertCursor("CC-01", storage), [event(2), event(5), event(8, "CC-01", true, { faultAlert: true })])
check("2 新的故障告警 event_id 会进入提醒", second.fresh.map((item) => item.event_id).join(",") === "8")
const prompted = rememberPrompted(second.cursor, 8)
const repeated = acceptPolledEvents(prompted, [event(2), event(5), event(8, "CC-01", true, { faultAlert: true })])
check("3 同一 event_id 再次轮询不再进入提醒", repeated.fresh.length === 0)

writeAlertCursor("CC-01", prompted, storage)
const reloaded = readAlertCursor("CC-01", memoryStorageFrom(storage.dump()))
check("4 刷新后已提示事件不会再次出现", reloaded !== null && freshAlertEvents(reloaded as DeviceAlertCursor, [event(8, "CC-01", true, { faultAlert: true })]).length === 0)

const returned = freshAlertEvents(reloaded as DeviceAlertCursor, [
  event(8, "CC-01", true, { faultAlert: true }),
  event(9, "CC-01", true, { faultAlert: true }),
])
check("5 离开后新出现的故障告警仍能被发现", returned.map((item) => item.event_id).join(",") === "9")

const queued = mergeAlertQueue([], [event(11), event(10), event(11)])
check("6 兼容队列去重仍按 event_id", queued.map((item) => item.event_id).join(",") === "10,11")

check("7 查看详情使用 event_id 而不是记录编号", queued[0]?.event_id === 10 && queued[0]?.record_id === "REC-10")
check(
  "8 没有诊断详情时不生成故障结论",
  !diagnosisDetailAvailable(event(8)) && event(8).candidate_faults.length === 0 && ALERT_INSUFFICIENT_TEXT === "当前证据不足，尚未确诊",
)

const other = acceptPolledEvents(null, [event(4, "MM-01")])
writeAlertCursor("MM-01", other.cursor, storage)
check("9 不同设备的游标互不影响", readAlertCursor("CC-01", storage)?.baselineMaxId === 5 && readAlertCursor("MM-01", storage)?.baselineMaxId === 4)

check(
  "10b 统计 reminder 不等于故障告警",
  needsPopupReminder(event(21)) && !needsFaultAlert(event(21)) && needsFaultAlert(event(22, "CC-01", false, { faultAlert: true })),
)

check(
  "10c 缺少 reminder_required 的旧事件不弹窗",
  !needsPopupReminder({ reminder_required: undefined }),
)

const sessionA = createMonitorAlertSession("CC-01", "task-a")
const burst = Array.from({ length: 100 }, (_, index) =>
  event(1000 + index, "CC-01", index % 7 === 0, {
    collectedAt: `2026-04-01 12:${String(index % 60).padStart(2, "0")}:00`,
    faultAlert: false,
  }),
)
const firstIngest = ingestSessionEvents(sessionA, burst)
check(
  "11 一次监测 100 条统计偏离不自动弹故障窗",
  firstIngest.autoOpened === false &&
    firstIngest.session.summaryOpen === false &&
    firstIngest.session.seenEventIds.length === 100 &&
    firstIngest.newFaultAlertCount === 0,
  `auto=${firstIngest.autoOpened} faults=${firstIngest.newFaultAlertCount}`,
)

const withFault = ingestSessionEvents(firstIngest.session, [
  ...burst,
  event(2001, "CC-01", true, { faultAlert: true, phase: "confirmed_fault", collectedAt: "2026-04-01 13:00:00" }),
])
check(
  "12 首次确认故障才自动打开一次告警",
  withFault.autoOpened === true &&
    withFault.session.summaryOpen === true &&
    withFault.session.autoSummaryShown === true &&
    withFault.newFaultAlertCount === 1,
)

const afterDismiss = dismissAlertSummary(withFault.session)
const moreFault = ingestSessionEvents(afterDismiss, [
  event(2001, "CC-01", true, { faultAlert: true }),
  event(2002, "CC-01", true, { faultAlert: true, collectedAt: "2026-04-01 13:02:00" }),
])
check(
  "13 关闭后后续故障告警只更新列表，不自动再弹",
  moreFault.autoOpened === false &&
    moreFault.session.summaryOpen === false &&
    moreFault.newFaultAlertCount === 1 &&
    moreFault.session.faultAlertIds.includes(2002),
)

const pollAgain = ingestSessionEvents(moreFault.session, [
  event(2001, "CC-01", true, { faultAlert: true }),
  event(2002, "CC-01", true, { faultAlert: true }),
])
check(
  "14 同一故障告警被轮询多次不重复计数",
  pollAgain.newEventCount === 0 && pollAgain.newFaultAlertCount === 0,
)

const multiMetric = event(3001, "CC-01", true, {
  recordId: "REC-MULTI",
  faultAlert: true,
  metrics: [
    { field: "Y向振动(mm/s)", unit: "mm/s", current_value: 3.2, median: 1, mad: 0.2, robust_z: 4, metric_status: "偏离基线" },
    { field: "X向振动(mm/s)", unit: "mm/s", current_value: 2.8, median: 1, mad: 0.2, robust_z: 3.5, metric_status: "偏离基线" },
  ],
})
const multiSession = ingestSessionEvents(createMonitorAlertSession("CC-01", "task-multi"), [multiMetric])
check(
  "15 同一记录多个指标仍是一条事件",
  multiSession.newEventCount === 1 && multiSession.newFaultAlertCount === 1 && buildAlertSummary([multiMetric]).metricCount === 2,
)

const episodeChain = [
  event(4001, "CC-01", true, { collectedAt: "2026-04-02 10:00:00", recordId: "R1" }),
  event(4002, "CC-01", false, { collectedAt: "2026-04-02 10:01:00", recordId: "R2" }),
  event(4003, "CC-01", false, { collectedAt: "2026-04-02 10:02:00", recordId: "R3" }),
]
const groups = groupEventsIntoEpisodes(episodeChain)
check(
  "16 同一异常过程连续记录归并",
  groups.length === 1 && groups[0]?.eventIds.join(",") === "4001,4002,4003",
)

const mixedDevices = groupEventsIntoEpisodes([
  event(5001, "CC-01", true),
  event(5002, "CC-01", false),
  event(5003, "MM-01", true),
  event(5004, "MM-01", false),
  event(5005, "CC-01", true),
])
check(
  "17 不同设备与不同异常过程不会错误合并",
  mixedDevices.length === 3 &&
    mixedDevices.map((item) => `${item.deviceId}:${item.eventIds.join("-")}`).join("|") === "CC-01:5001-5002|MM-01:5003-5004|CC-01:5005",
)

const upgrade = createMonitorAlertSession("CC-01", "task-upgrade")
const beforeDiag = ingestSessionEvents(upgrade, [event(6001, "CC-01", true, { faultAlert: false })])
const afterDiag = ingestSessionEvents(beforeDiag.session, [event(6001, "CC-01", true, { faultAlert: true, phase: "confirmed_fault" })])
check(
  "18 同事件诊断后升为故障告警可触发一次弹窗",
  beforeDiag.autoOpened === false && afterDiag.autoOpened === true && afterDiag.newFaultAlertCount === 1,
)

const restart = createMonitorAlertSession("CC-01", "task-restart")
check(
  "19 重新开始监测是新会话",
  restart.sessionKey !== upgrade.sessionKey && restart.faultAlertIds.length === 0 && restart.summaryOpen === false,
)

const filtered = filterEpisodeGroups(mixedDevices, { deviceId: "MM-01", metric: "Y向振动(mm/s)" })
check("20 异常中心可按设备与指标筛选", filtered.length === 1 && filtered[0]?.deviceId === "MM-01")

const manualOpen = openAlertSummary(dismissAlertSummary(withFault.session))
check("21 用户可主动再次打开告警，但不会自动连锁", manualOpen.summaryOpen === true && manualOpen.autoSummaryShown === true)

check("22 稳定 event_id 去重", dedupeEventsById([event(1), event(1), event(2)]).map((item) => item.event_id).join(",") === "1,2")

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const page = readFileSync(resolve(root, "frontend/src/pages/EquipmentMonitoringPage.tsx"), "utf8")
const alert = readFileSync(resolve(root, "frontend/src/monitoring/anomalyAlert.ts"), "utf8")
check(
  "23 页面仅对确认故障告警弹窗",
  page.includes(FAULT_ALERT_TITLE) &&
    page.includes("查看异常列表") &&
    page.includes("我知道了") &&
    page.includes("异常中心") &&
    page.includes("faultAlertIds") &&
    page.includes("ingestSessionEvents") &&
    !page.includes("关闭后继续查看") &&
    !page.includes("setAlertQueue(") &&
    alert.includes("needsFaultAlert") &&
    alert.includes("fault_alert_required") &&
    !`${page}\n${alert}`.includes("已确诊故障"),
)

check("24 详情仍可定位原始记录编号", page.includes("onOpenRecord") && groups[0]?.recordIds[0] === "R1")

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)
