import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import {
  EMPTY_EVENT_FAULT_TEXT,
  HISTORICAL_DIAGNOSIS_NOTE,
  MISSING_MEASUREMENT_TEXT,
  NOT_CONFIRMED_TEXT,
  diagnosisDetailAvailable,
  eventDiagnosisLabel,
  isMonitoringEventList,
  isStoredMonitorEvent,
} from "../frontend/src/monitoring/view.ts"
import type { StoredMonitorEvent } from "../frontend/src/types/api.ts"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

function event(extra: Partial<StoredMonitorEvent> = {}): StoredMonitorEvent {
  return {
    event_id: 7,
    session_id: "auto-view",
    record_id: "REC-MA-20260403-00346",
    device_id: "MM-01",
    collected_at: "2026-04-03 11:05:00",
    processed_at: "2026-10-27T16:00:00+08:00",
    event_type: "统计偏离",
    diagnosis_level: "diagnostic_clue",
    detection_status: "偏离基线",
    data_source: "钢铁设备数据集_开发集.xlsx",
    event_scope: "official_development_set_history_replay",
    event_note: "这是官方开发集历史回放中的实验性统计偏离，不是正式设备报警，也不是故障确诊、故障概率或危险等级。",
    metrics: [
      {
        field: "Y向振动(mm/s)",
        unit: "mm/s",
        current_value: 3.017,
        median: 1.2,
        mad: 0.4,
        robust_z: 3.923797970479703,
        metric_status: "偏离基线",
      },
    ],
    diagnostic_clues: [
      {
        feature_code: "amplitude",
        related_fault_section: "转子不平衡 / 频谱特征",
        document: "KB-02_故障模式诊断知识库.md",
        line_number: 38,
        partial_condition_met: true,
        complete_condition_met: false,
        unverified_reason: "同一条记录满足 X向振动大于 Y向振动。当前数据没有轴向侧偏心测量。",
      },
    ],
    candidate_faults: [],
    ...extra,
  }
}

const ready = event({
  diagnosis_available: true,
  diagnosis_status: "已关联部分诊断线索。证据不足，无法确定具体故障原因。",
  evidence_sources: [
    {
      document: "KB-02_故障模式诊断知识库.md",
      line_number: 38,
      feature_code: "amplitude",
      related_fault_section: "转子不平衡 / 频谱特征",
    },
  ],
  missing_conditions: ["2X幅值", "相位", "证据不足，无法确定具体故障原因。"],
  confirmed: false,
})

const legacy = event()
const markedLegacy = event({
  diagnosis_available: false,
  diagnosis_status: "",
  evidence_sources: [],
  missing_conditions: [],
  confirmed: false,
  diagnostic_clues: ready.diagnostic_clues,
})

check("1 有诊断痕迹的事件可以展示线索", diagnosisDetailAvailable(ready) && eventDiagnosisLabel(ready.diagnosis_level) === "诊断线索" && isStoredMonitorEvent(ready))
check(
  "2 证据来源保留文档和行号",
  ready.evidence_sources?.[0]?.document === "KB-02_故障模式诊断知识库.md" && ready.evidence_sources[0].line_number === 38,
)
check("3 缺失条件原样保留", ready.missing_conditions?.includes("2X幅值") === true && ready.missing_conditions.includes("相位") && MISSING_MEASUREMENT_TEXT.includes("数据不足"))
check("4 confirmed 为 false 时使用尚未确诊", ready.confirmed === false && NOT_CONFIRMED_TEXT === "尚未确诊" && !NOT_CONFIRMED_TEXT.includes("已确诊故障"))
check("5 候选故障为空时不提供故障名称", ready.candidate_faults.length === 0 && EMPTY_EVENT_FAULT_TEXT === "没有候选故障，页面不会生成故障名称。" && !EMPTY_EVENT_FAULT_TEXT.includes("轴承"))
check(
  "6 旧事件缺少诊断字段时不视为已有诊断",
  !diagnosisDetailAvailable(legacy) && !diagnosisDetailAvailable(markedLegacy) && isStoredMonitorEvent(legacy) && HISTORICAL_DIAGNOSIS_NOTE === "该历史事件暂无诊断详情",
)
check(
  "7 事件列表结构仍可识别带诊断字段的记录",
  isMonitoringEventList({
    session_id: "auto-view",
    device_id: "MM-01",
    count: 1,
    limit: 100,
    events: [ready],
  }),
)

const page = readFileSync(resolve(root, "frontend/src/pages/EquipmentMonitoringPage.tsx"), "utf8")
const auto = readFileSync(resolve(root, "frontend/src/monitoring/autoMonitor.ts"), "utf8")
const app = readFileSync(resolve(root, "frontend/src/App.tsx"), "utf8")
const view = readFileSync(resolve(root, "frontend/src/monitoring/view.ts"), "utf8")
const cardStart = page.indexOf("function SavedEventCard")
const cardEnd = page.indexOf("function Metric")
const card = page.slice(cardStart, cardEnd)
const nav = app.slice(app.indexOf("<nav"), app.indexOf("</nav>"))
check(
  "8 事件详情和轮询不调用诊断或 process-record",
  !page.includes("process-record") &&
    !page.includes("DeepSeek") &&
    !page.includes("deepseek") &&
    !page.includes("detect_anomaly") &&
    !card.includes("fetch(") &&
    !card.includes("/api/") &&
    page.includes("/api/monitoring/events?") &&
    page.includes("/api/monitoring/auto/status") &&
    auto.includes("AUTO_POLL_MS = 2000"),
)
check(
  "9 顶部导航保留监测与图谱入口且无智能问答",
  nav.split('className="nav-button"').length - 1 === 2 &&
    !nav.includes("智能问答") &&
    nav.includes("知识图谱") &&
    nav.includes("设备运行监测") &&
    !nav.includes("振动数据分析") &&
    app.includes("<VibrationAnalysisPage />"),
)
check("10 页面文案不把统计偏离写成已确诊故障", !`${page}\n${view}\n${auto}`.includes("已确诊故障") && !page.includes("轴承磨损"))
check("11 诊断详情分区使用已保存字段", page.includes("诊断线索") && page.includes("知识来源") && page.includes("缺失条件") && page.includes("确诊情况：{NOT_CONFIRMED_TEXT}"))

if (results.some((item) => !item)) {
  console.log(`${results.filter(Boolean).length}/${results.length} passed`)
  process.exit(1)
}
console.log(`${results.length}/${results.length} passed`)
