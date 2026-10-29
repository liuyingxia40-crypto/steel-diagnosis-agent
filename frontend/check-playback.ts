import { readFileSync } from "node:fs"

import {
  PLAYBACK_SCOPE_NOTE,
  advancePlayback,
  initialPlayback,
  pausePlayback,
  playbackIntervalMs,
  playbackScopeText,
  resetPlayback,
  resumePlayback,
  startPlayback,
  tickPlayback,
  visiblePlaybackRecords,
} from "./src/monitoring/view.ts"

const failures: string[] = []

function check(name: string, passed: boolean, detail = "") {
  const line = `${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`
  console.log(line)
  if (!passed) {
    failures.push(line)
  }
}

function record(id: string, time: string, y: number) {
  return { record_id: id, collected_at: time, values: { "Y向振动(mm/s)": y } }
}

const loaded = [record("A", "2026-03-14 00:05:00", 1.2), record("B", "2026-03-14 00:10:00", 1.4), record("C", "2026-03-14 00:15:00", 1.6)]
let state = startPlayback(initialPlayback(), loaded.length)
check("A 开始", state.status === "playing" && state.index === 1 && visiblePlaybackRecords(loaded, state).length === 1)
state = pausePlayback(state)
const pausedIndex = state.index
state = advancePlayback(state, loaded.length, 5000)
check("A 暂停不前进", state.status === "paused" && state.index === pausedIndex)
state = resumePlayback(state, loaded.length)
state = tickPlayback(state, loaded.length)
check("A 继续后推进", state.status === "playing" && state.index === pausedIndex + 1)
state = resetPlayback(state)
check("A 重置", state.status === "idle" && state.index === 0 && visiblePlaybackRecords(loaded, state).length === loaded.length)

const oneX = advancePlayback(startPlayback({ status: "idle", index: 0, speed: 1 }, 20), 20, 2000)
const twoX = advancePlayback(startPlayback({ status: "idle", index: 0, speed: 2 }, 20), 20, 1000)
check(
  "B 播放速度",
  playbackIntervalMs(1) === 1000 &&
    playbackIntervalMs(2) === 500 &&
    oneX.index === 3 &&
    twoX.index === 3 &&
    oneX.status === "playing" &&
    twoX.status === "playing",
  `1x=${oneX.index} 2x=${twoX.index}`,
)

const played = visiblePlaybackRecords(loaded, { status: "playing", index: 2, speed: 1 })
check(
  "C/D 指针对应真实记录且不显示后续",
  played.length === 2 &&
    played[1]?.record_id === "B" &&
    played[1]?.values["Y向振动(mm/s)"] === 1.4 &&
    !played.some((item) => item.record_id === "C"),
)

const sameTime = [record("R1", "2026-03-14 00:15:00", 10.804), record("R2", "2026-03-14 00:15:00", 2.062)]
const first = visiblePlaybackRecords(sameTime, { status: "playing", index: 1, speed: 1 })
const second = visiblePlaybackRecords(sameTime, tickPlayback({ status: "playing", index: 1, speed: 1 }, sameTime.length))
check(
  "E 相同时间逐条播放",
  first.length === 1 &&
    first[0]?.record_id === "R1" &&
    second.length === 2 &&
    second[0]?.collected_at === second[1]?.collected_at &&
    second[0]?.record_id !== second[1]?.record_id &&
    second[1]?.values["Y向振动(mm/s)"] === 2.062,
)

const reset = resetPlayback({ status: "playing", index: 50, speed: 2 })
const nextDevice = [record("N1", "2026-03-14 00:00:00", 4)]
check(
  "F 重置后不沿用旧进度",
  reset.status === "idle" && reset.index === 0 && visiblePlaybackRecords(nextDevice, reset).length === 1 && reset.speed === 2,
)

let finished = startPlayback(initialPlayback(), 2)
finished = advancePlayback(finished, 2, 10000)
const afterEnd = advancePlayback(finished, 2, 10000)
check("结束不循环", finished.status === "finished" && finished.index === 2 && afterEnd.index === 2 && afterEnd.status === "finished")

const scope = playbackScopeText(100, 1030, true)
check("截断提示", scope.includes("仅回放当前加载的100条") && scope.includes("1030") && scope.includes("不是全部历史"))

const motor = await fetch("http://127.0.0.1:8000/api/dataset/records?device_id=MM-01&limit=100").then((response) => response.json())
const fan = await fetch(
  "http://127.0.0.1:8000/api/dataset/records?device_id=FF-01&start_time=2026-03-14%2000:15:00&end_time=2026-03-14%2000:15:00&limit=100",
).then((response) => response.json())
const motorPlayed = visiblePlaybackRecords(motor.records, { status: "playing", index: 1, speed: 1 })
const fanPlayed = visiblePlaybackRecords(fan.records, { status: "finished", index: fan.records.length, speed: 1 })
check(
  "接口记录顺序",
  motor.truncated === true &&
    motor.returned_count === 100 &&
    motor.matched_count === 1030 &&
    motorPlayed.length === 1 &&
    motorPlayed[0].record_id === motor.records[0].record_id &&
    motorPlayed[0].values["Y向振动(mm/s)"] === motor.records[0].values["Y向振动(mm/s)"] &&
    fan.returned_count === 2 &&
    fanPlayed.length === 2 &&
    fanPlayed[0].collected_at === fanPlayed[1].collected_at &&
    fanPlayed[0].record_id !== fanPlayed[1].record_id,
  `motor=${motor.records[0].record_id} fan=${fan.records.map((item: { record_id: string }) => item.record_id).join(",")}`,
)

const pageSource = readFileSync(new URL("./src/pages/EquipmentMonitoringPage.tsx", import.meta.url), "utf8")
const labels = ["设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度"]
const viewSource = readFileSync(new URL("./src/monitoring/view.ts", import.meta.url), "utf8")
check(
  "G/H 保留原功能且不展示答案标签",
  pageSource.includes("历史数据回放") &&
    pageSource.includes("PLAYBACK_SCOPE_NOTE") &&
    viewSource.includes(PLAYBACK_SCOPE_NOTE) &&
    pageSource.includes("分析此记录") &&
    pageSource.split("/api/dataset/analysis/").length - 1 === 1 &&
    !pageSource.includes("detect_anomaly") &&
    !pageSource.includes("analyze_equipment_record") &&
    !pageSource.includes("传感器已连接") &&
    pageSource.includes("clearPlaybackTimer()") &&
    pageSource.includes("haltPlayback()") &&
    labels.every((label) => !pageSource.includes(label)),
)

console.log(`SUMMARY ${failures.length === 0 ? "ok" : "failed"} ${failures.length}`)
if (failures.length > 0) {
  process.exit(1)
}
