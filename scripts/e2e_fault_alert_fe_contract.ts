/**
 * 前端接口契约：用后端联调导出的真实事件跑 ingestSessionEvents。
 * 接口级验收，不是浏览器自动化。
 */
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  createMonitorAlertSession,
  ingestSessionEvents,
  needsFaultAlert,
  needsPopupReminder,
  type AlertableEvent,
} from "../frontend/src/monitoring/anomalyAlert.ts"

const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

function main(): void {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
  const argPath = process.argv[2]
  if (!argPath) {
    console.error("用法: npx --yes tsx scripts/e2e_fault_alert_fe_contract.ts <fe_contract_events.json>")
    process.exit(2)
  }
  const payload = JSON.parse(readFileSync(resolve(argPath), "utf-8")) as {
    success_events: AlertableEvent[]
    stats_events: AlertableEvent[]
    dedupe_events: AlertableEvent[]
    device_id: string
    alert_event_id: number
  }

  const deviceId = payload.device_id
  let session = createMonitorAlertSession(deviceId)

  // 第一次轮询：过程起点可能还是 diagnosing（若事件已写回 confirmed 则直接告警）
  const first = ingestSessionEvents(session, payload.success_events)
  session = first.session
  const alertEvents = payload.success_events.filter((e) => needsFaultAlert(e))
  check(
    "FE1 仅 fault_alert_required 事件有告警资格",
    alertEvents.length >= 1 && alertEvents.every((e) => e.fault_alert_required === true),
    `count=${alertEvents.length}`,
  )
  check(
    "FE2 确认故障事件会自动弹窗一次",
    first.autoOpened === true && first.newFaultAlertCount >= 1 && session.autoSummaryShown === true,
    `autoOpened=${first.autoOpened} newFault=${first.newFaultAlertCount}`,
  )

  // 重复轮询同一批事件：不再弹窗
  const second = ingestSessionEvents(session, payload.success_events)
  session = second.session
  check(
    "FE3 重复轮询不重复弹同一告警",
    second.autoOpened === false && second.newFaultAlertCount === 0,
    `autoOpened=${second.autoOpened} newFault=${second.newFaultAlertCount}`,
  )

  // 统计偏离：reminder 可有，但不弹故障窗
  const statsDevice = payload.stats_events[0]?.device_id || "MM-01"
  let statsSession = createMonitorAlertSession(statsDevice)
  const statsIngest = ingestSessionEvents(statsSession, payload.stats_events)
  check(
    "FE4 统计偏离不自动弹故障告警",
    statsIngest.autoOpened === false
      && payload.stats_events.every((e) => !needsFaultAlert(e))
      && payload.stats_events.some((e) => needsPopupReminder(e) || e.detection_status === "偏离基线"),
    `autoOpened=${statsIngest.autoOpened}`,
  )

  // 100 条去重轮询：无故障告警弹窗
  let dedupeSession = createMonitorAlertSession("MM-01")
  const poll1 = ingestSessionEvents(dedupeSession, payload.dedupe_events)
  dedupeSession = poll1.session
  const poll2 = ingestSessionEvents(dedupeSession, payload.dedupe_events)
  check(
    "FE5 100 条偏离重复轮询不产生故障弹窗",
    poll1.autoOpened === false
      && poll2.autoOpened === false
      && poll1.newFaultAlertCount === 0
      && payload.dedupe_events.every((e) => !needsFaultAlert(e)),
    `p1=${poll1.autoOpened} p2=${poll2.autoOpened}`,
  )

  const lead = payload.success_events.find((e) => e.event_id === payload.alert_event_id)
  check(
    "FE6 告警详情关联设备/记录/阶段",
    !!lead
      && lead.device_id === deviceId
      && typeof lead.record_id === "string"
      && lead.record_id.length > 0
      && lead.diagnosis_phase === "confirmed_fault"
      && lead.fault_alert_required === true,
    lead ? `${lead.device_id}/${lead.record_id}/${lead.diagnosis_phase}` : "missing",
  )

  const failed = results.filter((x) => !x).length
  console.log(`TOTAL=${results.length} PASS=${results.length - failed} FAIL=${failed}`)
  if (failed) process.exit(1)
}

main()
