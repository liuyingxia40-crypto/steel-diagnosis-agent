import { useState } from "react"
import { useRoute } from "../routing"
import { TREND_RANGES } from "./demoData"
import { equipmentProfile } from "./equipmentProfile"
import { activeAlarms, activeMetrics, equipmentOf, systemOf, type AlarmEvent, type LineSnapshot, type MetricPoint, type ViewSelection } from "./model"

const RANGE_MINUTES: Record<(typeof TREND_RANGES)[number]["id"], number> = {
  "15m": 15,
  "1h": 60,
  "8h": 480,
}

function directionText(direction: MetricPoint["direction"]): string {
  if (direction === "up") return "上升"
  if (direction === "down") return "下降"
  return "平稳"
}

function deviceName(line: LineSnapshot, id: string): string {
  return equipmentProfile(id)?.name ?? equipmentOf(line, id)?.name ?? "设备"
}

function sensorAlarms(alarms: AlarmEvent[]): AlarmEvent[] {
  return alarms.filter((item) => item.kind === "sensor").sort((left, right) => right.time.localeCompare(left.time))
}

function inferenceAlarms(alarms: AlarmEvent[]): AlarmEvent[] {
  return alarms.filter((item) => item.kind === "inference").sort((left, right) => right.time.localeCompare(left.time))
}

function focusMetric(metrics: MetricPoint[], alarms: AlarmEvent[]): MetricPoint | undefined {
  const numeric = metrics.filter((item) => Number.isFinite(Number(item.value)))
  const linked = numeric.find((item) => alarms.some((alarm) => alarm.kind === "sensor" && alarm.metricId === item.id))
  return linked ?? numeric.find((item) => item.direction !== "flat") ?? numeric[0]
}

function snapshotDate(updatedAt: string): Date | null {
  const match = updatedAt.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!match) return null
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]), Number(match[6] ?? 0))
}

function clock(date: Date): string {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`
}

function tickLabel(value: number): string {
  const abs = Math.abs(value)
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2
  return value.toFixed(digits)
}

function stampLabel(updatedAt: string, time: string): string {
  const day = updatedAt.slice(5, 10)
  return day ? `${day} ${time}` : time
}

function disposition(note: string): string | null {
  return note.includes("尚未确认") ? "尚未确认" : null
}

function diagnosisLines(line: LineSnapshot, alarms: AlarmEvent[]): string[] {
  const lines = [
    ...sensorAlarms(alarms).map((alarm) => `${deviceName(line, alarm.equipmentId)}：${alarm.title}。${alarm.note}`),
    ...inferenceAlarms(alarms).map((alarm) => `推断，尚未确认：${deviceName(line, alarm.equipmentId)}，${alarm.title}。${alarm.note}`),
  ]
  return lines.slice(0, 4)
}

function TrendChart({ metric, count, updatedAt, spanMinutes }: { metric: MetricPoint; count: number; updatedAt: string; spanMinutes: number }) {
  const series = metric.series.slice(-count)
  const width = 360
  const height = 128
  const padL = 46
  const padR = 10
  const padT = 16
  const padB = 22
  const end = snapshotDate(updatedAt)
  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const plotW = width - padL - padR
  const plotH = height - padT - padB
  const xAt = (index: number) => padL + (index / Math.max(series.length - 1, 1)) * plotW
  const yAt = (value: number) => padT + (1 - (value - min) / span) * plotH
  const points = series.map((value, index) => `${xAt(index)},${yAt(value)}`).join(" ")
  const mid = Math.floor((series.length - 1) / 2)
  const xTicks = [0, mid, series.length - 1].filter((value, index, list) => list.indexOf(value) === index)
  const yTicks = max === min ? [max] : [max, min]

  return (
    <svg className="ov-trend" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${metric.label}模拟趋势，单位 ${metric.unit || "无"}`}>
      <text className="ov-trend-unit" x={padL} y={11}>
        {metric.unit || "—"}
      </text>
      {yTicks.map((value) => (
        <g key={value}>
          <line x1={padL} y1={yAt(value)} x2={width - padR} y2={yAt(value)} className="ov-trend-grid" />
          <text className="ov-trend-tick" x={padL - 4} y={yAt(value) + 3} textAnchor="end">
            {tickLabel(value)}
          </text>
        </g>
      ))}
      <polyline points={points} className="ov-trend-line" />
      {xTicks.map((index) => {
        const minutesBack = series.length <= 1 || !end ? 0 : (spanMinutes * (series.length - 1 - index)) / (series.length - 1)
        const label = end ? clock(new Date(end.getTime() - minutesBack * 60_000)) : String(index + 1)
        return (
          <text key={index} className="ov-trend-tick" x={xAt(index)} y={height - 6} textAnchor={index === 0 ? "start" : index === series.length - 1 ? "end" : "middle"}>
            {label}
          </text>
        )
      })}
    </svg>
  )
}

function KpiList({ metrics }: { metrics: MetricPoint[] }) {
  if (metrics.length === 0) {
    return <p className="ov-scada-note">当前范围没有可显示的参数。</p>
  }
  return (
    <ul className="ov-kpis">
      {metrics.map((item) => (
        <li key={item.id}>
          <span>{item.label}</span>
          <strong className="ov-tabular">
            {item.value}
            {item.unit ? <small>{item.unit}</small> : null}
          </strong>
          <em>{directionText(item.direction)}</em>
        </li>
      ))}
    </ul>
  )
}

function TrendBlock({ metrics, alarms, updatedAt }: { metrics: MetricPoint[]; alarms: AlarmEvent[]; updatedAt: string }) {
  const numeric = metrics.filter((item) => Number.isFinite(Number(item.value)))
  const preferred = focusMetric(metrics, alarms)?.id ?? ""
  const scopeKey = `${numeric.map((item) => item.id).join("|")}:${alarms.map((item) => item.metricId ?? "").join("|")}`
  const [scope, setScope] = useState(scopeKey)
  const [metricId, setMetricId] = useState(preferred)
  const [rangeId, setRangeId] = useState<(typeof TREND_RANGES)[number]["id"]>("1h")
  if (scope !== scopeKey) {
    setScope(scopeKey)
    setMetricId(preferred)
  }

  const metric = numeric.find((item) => item.id === metricId) ?? numeric[0]
  const range = TREND_RANGES.find((item) => item.id === rangeId) ?? TREND_RANGES[1]
  if (!metric) {
    return <p className="ov-scada-note">当前没有可绘制的数值趋势。</p>
  }

  return (
    <div className="ov-trend-block">
      <div className="ov-trend-bar">
        <h3>关键指标趋势</h3>
        <div>
          {numeric.map((item) => (
            <button key={item.id} type="button" aria-pressed={item.id === metric.id} onClick={() => setMetricId(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="ov-ranges">
        {TREND_RANGES.map((item) => (
          <button key={item.id} type="button" aria-pressed={item.id === range.id} onClick={() => setRangeId(item.id)}>
            {item.label}
          </button>
        ))}
      </div>
      <TrendChart metric={metric} count={range.count} updatedAt={updatedAt} spanMinutes={RANGE_MINUTES[range.id]} />
      <p className="ov-scada-note">趋势由模拟序列生成，坐标只对应当前指标，不是现场历史曲线。</p>
    </div>
  )
}

function AlarmCard({ alarm, line }: { alarm: AlarmEvent; line: LineSnapshot }) {
  const { navigate } = useRoute()
  const status = disposition(alarm.note)
  return (
    <li>
      <button type="button" className="ov-alarm-card" onClick={() => navigate({ page: "events", eventId: alarm.id, eventType: "alarm" })}>
        <span className="is-warn">警告</span>
        <strong>{alarm.title}</strong>
        <span>
          {deviceName(line, alarm.equipmentId)} · <span className="ov-tabular">{stampLabel(line.updatedAt, alarm.time)}</span>
        </span>
        {status ? <em>{status}</em> : null}
      </button>
    </li>
  )
}

function DiagnosisBlock({ line, alarms, split }: { line: LineSnapshot; alarms: AlarmEvent[]; split: boolean }) {
  const { navigate } = useRoute()
  const sensors = sensorAlarms(alarms)
  const inferences = inferenceAlarms(alarms)
  const lines = diagnosisLines(line, alarms)
  const visible = sensors.slice(0, 2)
  const inference = inferences[0]

  return (
    <section className={split ? "ov-diag-panel" : "ov-diag-stack"} aria-label="报警与智能诊断">
      <div className="ov-panel-head">
        <h2>报警与智能诊断</h2>
        <div className="ov-diag-tools">
          <span>待处理 {sensors.length}</span>
          <button type="button" onClick={() => navigate({ page: "events" })}>
            查看全部
          </button>
        </div>
      </div>

      <h3>重要报警</h3>
      {visible.length === 0 ? <p className="ov-scada-note">当前没有传感器报警。</p> : null}
      <ul className="ov-alarm-list">
        {visible.map((alarm) => (
          <AlarmCard key={alarm.id} alarm={alarm} line={line} />
        ))}
      </ul>

      <h3>AI 推断</h3>
      {inferences.length === 0 ? <p className="ov-scada-note">当前没有推断。</p> : null}
      <ul className="ov-alarm-list">
        {inferences.map((alarm) => (
          <li key={alarm.id}>
            <button type="button" className="ov-alarm-card" onClick={() => navigate({ page: "events", eventId: alarm.id, eventType: "diagnosis" })}>
              <span className="is-infer">推断</span>
              <strong>{alarm.title}</strong>
              <span>
                {deviceName(line, alarm.equipmentId)} · <span className="ov-tabular">{stampLabel(line.updatedAt, alarm.time)}</span>
              </span>
              <em>{alarm.note}</em>
            </button>
          </li>
        ))}
      </ul>

      <h3>诊断摘要</h3>
      {lines.length === 0 ? <p className="ov-scada-note">当前没有可展示的诊断结论。</p> : <div className="ov-summary">{lines.map((lineText) => <p key={lineText}>{lineText}</p>)}</div>}
      <button
        type="button"
        className="ov-text-link"
        onClick={() => navigate({ page: "diagnosis", eventId: inference?.id, diagnosisId: inference ? `dx-${inference.id}` : undefined })}
      >
        智能诊断工作台
      </button>
    </section>
  )
}

export function ScadaPanel({
  line,
  selection,
  onOverview,
}: {
  line: LineSnapshot
  selection: ViewSelection
  onOverview: () => void
}) {
  const { navigate } = useRoute()
  const metrics = activeMetrics(line, selection)
  const alarms = activeAlarms(line, selection)
  const equipment = selection.type === "equipment" ? equipmentOf(line, selection.id) : undefined
  const profile = equipment ? equipmentProfile(equipment.id) : undefined

  if (equipment) {
    return (
      <aside className="ov-scada is-detail" aria-label="设备详情">
        <header className="ov-scada-head">
          <button type="button" className="ov-back" onClick={onOverview}>
            返回产线总览
          </button>
          <h2>{profile?.name ?? equipment.name}</h2>
          <div className="ov-run-meta">
            <span>{equipment.code}</span>
            <span>模拟</span>
            <span className="ov-tabular">{line.updatedAt}</span>
            <button type="button" className="ov-text-link" onClick={() => navigate({ page: "health", equipmentId: equipment.id })}>
              查看健康档案
            </button>
          </div>
        </header>
        {profile ? (
          <section aria-label="设备基本信息">
            <h3>设备基本信息</h3>
            <dl className="ov-spec">
              <div>
                <dt>设备名称</dt>
                <dd>{profile.name}</dd>
              </div>
              <div>
                <dt>设备类型</dt>
                <dd>{profile.type}</dd>
              </div>
              <div>
                <dt>典型型号</dt>
                <dd>{profile.models}</dd>
              </div>
              <div>
                <dt>额定功率</dt>
                <dd>{profile.power}</dd>
              </div>
              <div>
                <dt>额定转速</dt>
                <dd>{profile.speed}</dd>
              </div>
            </dl>
          </section>
        ) : null}
        <section>
          <h3>设备参数</h3>
          <KpiList metrics={metrics} />
        </section>
        <TrendBlock metrics={metrics} alarms={alarms} updatedAt={line.updatedAt} />
        <DiagnosisBlock line={line} alarms={alarms} split={false} />
      </aside>
    )
  }

  const scope = selection.type === "system" ? systemOf(line, selection.id).name : null

  return (
    <aside className="ov-scada" aria-label="产线运行数据">
      <section className="ov-run-panel" aria-label="产线运行总览">
        <header className="ov-scada-head">
          <h2>产线运行总览</h2>
          <div className="ov-run-meta">
            <span>{line.name}</span>
            {scope ? <span>{scope}</span> : null}
            <span>模拟</span>
            <span className="ov-tabular">{line.updatedAt}</span>
          </div>
        </header>
        <KpiList metrics={metrics} />
        <TrendBlock metrics={metrics} alarms={alarms} updatedAt={line.updatedAt} />
      </section>
      <DiagnosisBlock line={line} alarms={alarms} split />
    </aside>
  )
}
