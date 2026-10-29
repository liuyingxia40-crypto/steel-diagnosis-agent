import { useState } from "react"
import type { DatasetRecord, StoredMonitorEvent } from "../types/api"
import {
  SPEED_FIELDS,
  TEMPERATURE_FIELDS,
  VIBRATION_GROUPS,
  chartPoints,
  deviationMarkers,
  fieldsWithValues,
  formatAnalysisNumber,
  type VibrationGroupId,
} from "./view"

const SERIES_COLORS = ["#3dceb6", "#7eb6d6", "#e0c37a", "#d7d3c8"]

export function CockpitCharts({
  records,
  mode,
  events,
  group,
  onGroup,
  onMarker,
}: {
  records: readonly DatasetRecord[]
  mode: "history" | "progress" | "complete" | "waiting" | "outside"
  events: readonly StoredMonitorEvent[]
  group: VibrationGroupId
  onGroup: (group: VibrationGroupId) => void
  onMarker: (eventId: number) => void
}) {
  const selected = VIBRATION_GROUPS.find((item) => item.id === group) ?? VIBRATION_GROUPS[0]
  const vibrationFields = fieldsWithValues(records, selected.fields)
  const temperatureFields = fieldsWithValues(records, TEMPERATURE_FIELDS)
  const speedFields = fieldsWithValues(records, SPEED_FIELDS)
  const markers = deviationMarkers(records, events)
  const caption =
    mode === "history"
      ? "图中是本次查询到的历史观测，不是正在进行的分析结果。"
      : mode === "waiting"
        ? "正在等待第一条已处理记录。未处理记录不会画进图里。"
        : mode === "outside"
          ? "当前处理的记录不在本次查询结果中，所以这里不把这些记录画成已分析结果。"
          : mode === "complete"
            ? "本次任务已经处理完整段记录。图中只包含当前查询返回的观测。"
            : "图中只包含本次任务已经处理到的记录。"

  return (
    <section className="cockpit-visual" aria-label="运行数据">
      <div className="cockpit-section-head">
        <h2>运行数据</h2>
        <p>振动汇总指标随已处理记录展开。这不是连续原始波形，也不是频谱图。</p>
      </div>
      <p className="cockpit-caption">{caption}</p>
      <div className="monitor-toggles" role="group" aria-label="振动指标组">
        {VIBRATION_GROUPS.map((item) => (
          <button key={item.id} type="button" className="ghost-button monitor-toggle" aria-pressed={group === item.id} onClick={() => onGroup(item.id)}>
            {item.label}
            {item.unit ? `（${item.unit}）` : "（原表未标单位）"}
          </button>
        ))}
      </div>
      <CockpitTrend
        title="振动变化"
        records={records}
        fields={vibrationFields}
        unit={selected.unit}
        markers={markers}
        onMarker={onMarker}
        empty={mode === "waiting" || mode === "outside" ? caption : "当前设备在这组振动指标里没有可绘制的数值。"}
      />
      <div className="cockpit-aux">
        <CockpitTrend
          title="温度趋势"
          records={records}
          fields={temperatureFields}
          unit="°C"
          markers={markers}
          onMarker={onMarker}
          empty="当前设备没有可绘制的轴承温度。"
        />
        <CockpitTrend
          title="转速趋势"
          records={records}
          fields={speedFields}
          unit="rpm"
          markers={markers}
          onMarker={onMarker}
          empty="当前设备没有可绘制的转速。"
        />
      </div>
    </section>
  )
}

function CockpitTrend({
  title,
  records,
  fields,
  unit,
  markers,
  onMarker,
  empty,
}: {
  title: string
  records: readonly DatasetRecord[]
  fields: readonly string[]
  unit: string
  markers: ReadonlyArray<{ recordId: string; eventId: number }>
  onMarker: (eventId: number) => void
  empty: string
}) {
  const points = chartPoints(records, fields)
  const finite = points.flatMap((point) => point.values.filter((value): value is number => value !== null))
  const [hover, setHover] = useState<{ recordId: string; collectedAt: string; field: string; value: number } | null>(null)
  if (fields.length === 0 || finite.length === 0) {
    return (
      <figure className="cockpit-chart">
        <figcaption>{title}</figcaption>
        <p className="status">{empty}</p>
      </figure>
    )
  }
  const width = 920
  const height = title === "振动变化" ? 320 : 220
  const padLeft = 64
  const padRight = 16
  const padTop = 16
  const padBottom = 42
  const innerWidth = width - padLeft - padRight
  const innerHeight = height - padTop - padBottom
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  const span = max - min || 1
  const xAt = (index: number) => (points.length <= 1 ? padLeft + innerWidth / 2 : padLeft + (innerWidth * index) / (points.length - 1))
  const yAt = (value: number) => padTop + innerHeight - ((value - min) / span) * innerHeight
  const labelIndexes = points.length <= 3 ? points.map((_, index) => index) : [0, Math.floor((points.length - 1) / 2), points.length - 1]

  return (
    <figure className="cockpit-chart">
      <figcaption>
        {title}
        {unit ? ` · ${unit}` : " · 原表未标单位"}
      </figcaption>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}，每个点是一条记录，相同采集时间不合并`}>
        <line x1={padLeft} y1={padTop} x2={padLeft} y2={padTop + innerHeight} />
        <line x1={padLeft} y1={padTop + innerHeight} x2={width - padRight} y2={padTop + innerHeight} />
        <text x={padLeft - 8} y={padTop + 4} textAnchor="end">
          {formatAnalysisNumber(max)}
        </text>
        <text x={padLeft - 8} y={padTop + innerHeight} textAnchor="end">
          {formatAnalysisNumber(min)}
        </text>
        {fields.map((field, series) => {
          const commands: string[] = []
          points.forEach((point, index) => {
            const value = point.values[series]
            if (value === null) {
              return
            }
            const command = index > 0 && points[index - 1].values[series] !== null ? "L" : "M"
            commands.push(`${command} ${xAt(index)} ${yAt(value)}`)
          })
          return <path key={field} d={commands.join(" ")} fill="none" stroke={SERIES_COLORS[series] ?? SERIES_COLORS[0]} strokeWidth="2" />
        })}
        {markers.map((marker) => {
          const index = points.findIndex((point) => point.recordId === marker.recordId)
          if (index < 0) {
            return null
          }
          const x = xAt(index)
          return (
            <g key={marker.eventId}>
              <line x1={x} y1={padTop} x2={x} y2={padTop + innerHeight} stroke="#e0c37a" strokeDasharray="3 3" />
              <circle
                cx={x}
                cy={padTop + 8}
                r="6"
                fill="#e0c37a"
                role="button"
                tabIndex={0}
                onClick={() => onMarker(marker.eventId)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault()
                    onMarker(marker.eventId)
                  }
                }}
              >
                <title>{`统计偏离 ${marker.recordId}`}</title>
              </circle>
            </g>
          )
        })}
        {points.map((point, index) =>
          point.values.map((value, series) =>
            value === null ? null : (
              <circle
                key={`${point.recordId}-${fields[series]}`}
                cx={xAt(index)}
                cy={yAt(value)}
                r="3.5"
                fill={SERIES_COLORS[series] ?? SERIES_COLORS[0]}
                onMouseEnter={() => setHover({ recordId: point.recordId, collectedAt: point.collectedAt, field: fields[series], value })}
                onMouseLeave={() => setHover((current) => (current?.recordId === point.recordId && current.field === fields[series] ? null : current))}
              />
            ),
          ),
        )}
        {labelIndexes.map((index) => (
          <text key={points[index].recordId} x={xAt(index)} y={height - 12} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>
            {points[index].collectedAt}
          </text>
        ))}
      </svg>
      <div className="cockpit-legend">
        {fields.map((field, index) => (
          <span key={field}>
            <i style={{ background: SERIES_COLORS[index] ?? SERIES_COLORS[0] }} />
            {field}
          </span>
        ))}
        {markers.length > 0 && (
          <span>
            <i style={{ background: "#e0c37a" }} />
            已保存的统计偏离
          </span>
        )}
      </div>
      <p className="file-meta" role="status">
        {hover
          ? `采集时间 ${hover.collectedAt}；${hover.field} ${hover.value}${unit ? " " + unit : ""}；记录编号 ${hover.recordId}`
          : "把鼠标移到数据点上，可查看采集时间、指标数值、单位和记录编号。"}
      </p>
    </figure>
  )
}
