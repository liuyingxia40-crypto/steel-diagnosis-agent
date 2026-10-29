import { readFileSync } from "node:fs"
import { createHash } from "node:crypto"

import {
  applyPlaybackError,
  applyPlaybackProcess,
  createPlaybackSessionId,
  isMonitoringEventList,
  isMonitoringProcessResponse,
} from "./src/monitoring/view.ts"

const failures: string[] = []
const base = process.env.MONITOR_TEST_BASE ?? "http://127.0.0.1:18765"
const defaultDb = "D:\\工作\\steel-diagnosis-agent\\data\\runtime\\monitoring_events.sqlite3"

function check(name: string, passed: boolean, detail = "") {
  const line = `${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`
  console.log(line)
  if (!passed) {
    failures.push(line)
  }
}

function sha256(path: string): string {
  try {
    return createHash("sha256").update(readFileSync(path)).digest("hex")
  } catch {
    return ""
  }
}

async function post(sessionId: string, recordId: string) {
  const response = await fetch(`${base}/api/monitoring/process-record`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, record_id: recordId }),
  })
  const body: unknown = await response.json()
  return { status: response.status, body }
}

async function getEvents(params: Record<string, string>) {
  const query = new URLSearchParams(params)
  const response = await fetch(`${base}/api/monitoring/events?${query.toString()}`)
  const body: unknown = await response.json()
  return { status: response.status, body }
}

const beforeHash = sha256(defaultDb)
const sessionA = "pb-test-session-a"
const sessionB = "pb-test-session-b"
const target = "REC-MA-20260403-00346"
const early = "REC-MA-20260314-00001"
const firstId = createPlaybackSessionId()
const secondId = createPlaybackSessionId()
check(
  "会话标识格式",
  firstId !== secondId && /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(firstId) && firstId.startsWith("pb-"),
  firstId,
)

const first = await post(sessionA, target)
const firstBody = isMonitoringProcessResponse(first.body) ? first.body : null
const y = firstBody?.event?.metrics.find((item) => item.field === "Y向振动(mm/s)")
const record = firstBody
  ? {
      record_id: firstBody.analysis.observation.record_id,
      device_id: firstBody.analysis.observation.device_id,
      collected_at: firstBody.analysis.observation.collected_at,
    }
  : null
const shown = firstBody && record
  ? applyPlaybackProcess(
      {
        requestSessionId: 1,
        currentSessionId: 1,
        playbackSessionId: sessionA,
        record,
        processedIds: [],
        events: [],
      },
      firstBody,
    )
  : null
check(
  "A POST 保存并用 event_id 展示",
  first.status === 200 &&
    firstBody?.saved === true &&
    firstBody.duplicate === false &&
    firstBody.analysis.observation.device_id === "MM-01" &&
    y?.current_value === 3.017 &&
    y.robust_z === 3.923797970479703 &&
    y.metric_status === "偏离基线" &&
    firstBody.event?.event_type === "统计偏离" &&
    firstBody.event.candidate_faults.length === 0 &&
    firstBody.event.diagnosis_level === "diagnostic_clue" &&
    shown?.accepted === true &&
    shown.events.length === 1 &&
    shown.events[0]?.event_id === firstBody.event?.event_id &&
    !shown.statusText.includes("保存成功"),
  `status=${first.status} saved=${firstBody?.saved} id=${firstBody?.event?.event_id} y=${y?.current_value} z=${y?.robust_z}`,
)

const again = await post(sessionA, target)
const againBody = isMonitoringProcessResponse(again.body) ? again.body : null
const repeated = againBody && record && shown
  ? applyPlaybackProcess(
      {
        requestSessionId: 1,
        currentSessionId: 1,
        playbackSessionId: sessionA,
        record,
        processedIds: shown.processedIds,
        events: shown.events,
      },
      againBody,
    )
  : null
check(
  "B 重复提交不重复显示",
  again.status === 200 &&
    againBody?.duplicate === true &&
    againBody.saved === false &&
    againBody.event?.event_id === firstBody?.event?.event_id &&
    repeated?.accepted === false &&
    repeated.events.length === 1,
  `duplicate=${againBody?.duplicate} shown=${repeated?.events.length}`,
)

const other = await post(sessionB, target)
const otherBody = isMonitoringProcessResponse(other.body) ? other.body : null
const sessionListA = await getEvents({ session_id: sessionA, limit: "100" })
const sessionListB = await getEvents({ session_id: sessionB, limit: "100" })
const listA = isMonitoringEventList(sessionListA.body) ? sessionListA.body : null
const listB = isMonitoringEventList(sessionListB.body) ? sessionListB.body : null
check(
  "C 新会话可以区分",
  other.status === 200 &&
    otherBody?.saved === true &&
    otherBody.session_id === sessionB &&
    otherBody.event?.event_id !== firstBody?.event?.event_id &&
    listA?.count === 1 &&
    listA.events[0]?.session_id === sessionA &&
    listB?.count === 1 &&
    listB.events[0]?.session_id === sessionB,
  `a=${listA?.count} b=${listB?.count} ids=${firstBody?.event?.event_id},${otherBody?.event?.event_id}`,
)

const earlyResponse = await post(sessionA, early)
const earlyBody = isMonitoringProcessResponse(earlyResponse.body) ? earlyResponse.body : null
const earlyShown = earlyBody
  ? applyPlaybackProcess(
      {
        requestSessionId: 2,
        currentSessionId: 2,
        playbackSessionId: sessionA,
        record: {
          record_id: earlyBody.analysis.observation.record_id,
          device_id: earlyBody.analysis.observation.device_id,
          collected_at: earlyBody.analysis.observation.collected_at,
        },
        processedIds: [],
        events: [],
      },
      earlyBody,
    )
  : null
check(
  "D 基线不足不产生事件",
  earlyResponse.status === 200 &&
    earlyBody?.event === null &&
    earlyBody.saved === false &&
    earlyBody.analysis.statistical_deviation.detection_status === "基线不足" &&
    earlyShown?.events.length === 0 &&
    earlyShown.statusText.includes("基线不足") &&
    earlyShown.statusText.includes("不是设备正常") &&
    !earlyShown.statusText.includes("保存成功"),
  `status=${earlyResponse.status} detection=${earlyBody?.analysis.statistical_deviation.detection_status} events=${earlyShown?.events.length}`,
)

const deviceList = await getEvents({ device_id: "MM-01", limit: "100" })
const listed = isMonitoringEventList(deviceList.body) ? deviceList.body : null
const foundTarget = listed?.events.some((item) => item.record_id === target && item.event_id === firstBody?.event?.event_id)
check(
  "E 历史查询能读到已保存事件",
  deviceList.status === 200 &&
    listed !== null &&
    listed.limit === 100 &&
    foundTarget === true &&
    listed.events.every((item) => item.collected_at !== item.processed_at),
  `count=${listed?.count} limit=${listed?.limit}`,
)

const stale = firstBody && record
  ? applyPlaybackProcess(
      {
        requestSessionId: 1,
        currentSessionId: 9,
        playbackSessionId: sessionB,
        record,
        processedIds: [],
        events: [],
      },
      firstBody,
    )
  : null
const failed = applyPlaybackError(
  {
    requestSessionId: 3,
    currentSessionId: 3,
    playbackSessionId: sessionA,
    record: record ?? { record_id: target, device_id: "MM-01", collected_at: "2026-04-03 11:05:00" },
    processedIds: [],
    events: [],
  },
  "监测事件没有写入数据库。",
)
check(
  "F 旧会话结果不能写入新会话",
  stale?.accepted === false &&
    stale.events.length === 0 &&
    failed.accepted === true &&
    failed.events.length === 0 &&
    failed.statusText.includes("监测事件没有写入数据库") &&
    !failed.statusText.includes("保存成功"),
)

const beforeReadonly = listed?.count ?? -1
const readonly = await fetch(`${base}/api/dataset/analysis/${target}`)
const readonlyBody: unknown = await readonly.json()
const afterReadonly = await getEvents({ device_id: "MM-01", limit: "100" })
const afterList = isMonitoringEventList(afterReadonly.body) ? afterReadonly.body : null
const pageSource = readFileSync(new URL("./src/pages/EquipmentMonitoringPage.tsx", import.meta.url), "utf8")
const analysisUses = pageSource.split("/api/dataset/analysis/").length - 1
const processUses = pageSource.split("/api/monitoring/process-record").length - 1
check(
  "G 手动分析仍走只读接口",
  readonly.status === 200 &&
    afterList?.count === beforeReadonly &&
    analysisUses === 1 &&
    processUses === 1 &&
    pageSource.includes("function analyzeRecord") &&
    pageSource.includes("datasetAnalysisPath(recordId)") &&
    pageSource.includes('method: "POST"') &&
    !pageSource.slice(pageSource.indexOf("async function analyzeRecord"), pageSource.indexOf("async function analyzeRecord") + 900).includes("process-record"),
  `readonly=${readonly.status} rows=${beforeReadonly}->${afterList?.count} analysisUses=${analysisUses} processUses=${processUses}`,
)

const appSource = readFileSync(new URL("./src/App.tsx", import.meta.url), "utf8")
const summary = await fetch(`${base}/api/dataset/summary`)
const summaryBody = (await summary.json()) as { device_count?: number; record_count?: number }
check(
  "H 原有页面入口和数据查询仍在",
  !appSource.includes("智能问答") &&
    appSource.includes("知识图谱") &&
    appSource.includes("<VibrationAnalysisPage />") &&
    appSource.includes("设备运行监测") &&
    pageSource.includes("TrendChart") &&
    summary.status === 200 &&
    summaryBody.device_count === 46 &&
    summaryBody.record_count === 24000,
)

const labels = ["设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度"]
const payloads = [first.body, again.body, other.body, earlyResponse.body, deviceList.body, readonlyBody]
const leaked = payloads.some((item) => labels.some((label) => JSON.stringify(item).includes(`"${label}"`)))
check(
  "I 不展示答案标签字段",
  !leaked && labels.every((label) => !pageSource.includes(label)) && pageSource.includes("暂无已保存的统计偏离事件") && pageSource.includes("不代表数据库中的全部事件"),
)

const afterHash = sha256(defaultDb)
check("默认演示库没有被测试写入", beforeHash === afterHash, beforeHash.slice(0, 12))

console.log(`SUMMARY ${failures.length === 0 ? "ok" : "failed"} ${failures.length}`)
if (failures.length > 0) {
  process.exit(1)
}
