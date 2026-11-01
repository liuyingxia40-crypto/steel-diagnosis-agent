import { useEffect, useMemo, useState } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { equipmentOf, systemOf } from "../overview/model"
import { relatedEquipment } from "../overview/situation"
import { useRoute } from "../routing"
import { auditsFor, loadAudit, nextAction, statusAfter, submitAudit } from "./audit"
import { latestInstant, workEvents } from "./catalog"
import { SEVERITY_LABEL, SEVERITY_RANK, STATUS_LABEL, TYPE_LABEL, type AuditEntry, type WorkEvent, type WorkEventType, type WorkSeverity, type WorkStatus } from "./types"
import "./events.css"

type RangeId = "all" | "1h" | "8h" | "24h"
type SortId = "time-desc" | "time-asc" | "priority"

const RANGE_LABEL: Record<RangeId, string> = {
  all: "全部时间",
  "1h": "近 1 小时",
  "8h": "近 8 小时",
  "24h": "近 24 小时",
}

const OPEN_ALARM: WorkStatus[] = ["pending", "acknowledged", "investigating"]

function isRange(value: string | undefined): value is RangeId {
  return value === "1h" || value === "8h" || value === "24h" || value === "all"
}

function isType(value: string | undefined): value is WorkEventType {
  return value === "alarm" || value === "diagnosis" || value === "operation" || value === "system"
}

function formatStamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function inWindow(event: WorkEvent, range: RangeId, anchor: number): boolean {
  if (range === "all") return true
  const hours = range === "1h" ? 1 : range === "8h" ? 8 : 24
  const at = Date.parse(event.occurredAt)
  return at <= anchor && anchor - at <= hours * 60 * 60 * 1000
}

function Sparkline({ series }: { series: number[] }) {
  if (series.length < 2) return null
  const min = Math.min(...series)
  const max = Math.max(...series)
  const span = max - min || 1
  const points = series
    .map((value, index) => {
      const x = (index / (series.length - 1)) * 100
      const y = 28 - ((value - min) / span) * 24
      return `${x},${y}`
    })
    .join(" ")
  return (
    <svg className="ev-spark" viewBox="0 0 100 32" role="img" aria-label="模拟趋势">
      <polyline points={points} />
    </svg>
  )
}

function EventInspector({
  event,
  missingId,
  audits,
  narrow,
  onBack,
  onChanged,
}: {
  event?: WorkEvent
  missingId?: string
  audits: AuditEntry[]
  narrow: boolean
  onBack: () => void
  onChanged: () => void
}) {
  const { navigate } = useRoute()
  const [note, setNote] = useState("")
  const [message, setMessage] = useState("")
  const [busy, setBusy] = useState(false)
  const history = event ? auditsFor(event.id, audits) : []
  const status = event ? statusAfter(event.status, history) : "recorded"
  const action = event ? nextAction(status, event.type) : null

  useEffect(() => {
    setNote("")
    setMessage("")
  }, [event?.id])

  async function apply() {
    if (!event || !action) return
    setBusy(true)
    try {
      await submitAudit({
        eventId: event.id,
        action: action.action,
        at: new Date().toISOString(),
        operator: "演示用户",
        note: note.trim(),
        statusAfter: action.statusAfter,
      })
      setNote("")
      onChanged()
      setMessage("已记在本浏览器会话。演示模式，没有写入生产系统。")
    } catch {
      setMessage("演示记录没有保存成功。")
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ev-inspector" aria-label="事件详情">
      {narrow ? (
        <div className="ev-inspector-head">
          <button type="button" className="ev-back" onClick={onBack}>
            返回列表
          </button>
        </div>
      ) : null}
      {!event ? (
        <p className="ev-empty">{missingId ? `未找到事件 ${missingId}。它可能已删除，或当前无权访问。没有改选其他事件。` : "从左侧选择一条事件。"}</p>
      ) : (
        <div className="ev-body">
          <header>
            <span className={`ev-tag is-${event.severity}`}>{TYPE_LABEL[event.type]} · {SEVERITY_LABEL[event.severity]}</span>
            <h2>{event.title}</h2>
            <p className="ev-tabular">{event.id} · 模拟快照 {event.snapshotId}</p>
          </header>

          {event.type === "alarm" ? <AlarmBody event={event} status={status} /> : null}
          {event.type === "diagnosis" ? <DiagnosisBody event={event} status={status} /> : null}
          {event.type === "operation" || event.type === "system" ? <LogBody event={event} /> : null}

          <section>
            <h3>处置记录</h3>
            <ol className="ev-timeline">
              <li>
                <span className="ev-tabular">{formatStamp(event.occurredAt)}</span>
                <span>发生。事件时间，不等同于系统接收时间。</span>
              </li>
              <li>
                <span className="ev-tabular">{formatStamp(event.receivedAt)}</span>
                <span>接收。系统收到这条模拟记录。</span>
              </li>
              {history.map((item) => (
                <li key={item.id}>
                  <span className="ev-tabular">{formatStamp(item.at)}</span>
                  <span>
                    {item.operator} · {STATUS_LABEL[item.statusAfter]}
                    {item.note ? ` · ${item.note}` : ""}
                    {" · 未写入生产系统"}
                  </span>
                </li>
              ))}
            </ol>
            {action ? (
              <form
                className="ev-form"
                onSubmit={(submitEvent) => {
                  submitEvent.preventDefault()
                  void apply()
                }}
              >
                <p className="ev-note">演示模式。确认报警只表示已知晓，不表示故障已解决。复核推断也不表示故障已确认。</p>
                <input value={note} onChange={(input) => setNote(input.target.value)} placeholder="备注，可选" aria-label="处置备注" />
                <button type="submit" className="ev-action" disabled={busy}>
                  {action.label}
                </button>
              </form>
            ) : null}
            {message ? <p className="ev-note">{message}</p> : null}
          </section>

          <div className="ev-links">
            {event.equipmentId ? (
              <button type="button" onClick={() => navigate({ page: "monitor", equipmentId: event.equipmentId })}>
                查看设备
              </button>
            ) : null}
            {event.type === "alarm" || event.type === "diagnosis" ? (
              <button
                type="button"
                onClick={() => navigate({ page: "diagnosis", eventId: event.id, diagnosisId: event.diagnosisId })}
              >
                进入智能诊断工作台
              </button>
            ) : null}
          </div>
        </div>
      )}
    </section>
  )
}

function AlarmBody({ event, status }: { event: WorkEvent; status: WorkStatus }) {
  const links = event.equipmentId ? relatedEquipment(event.equipmentId).diagnostic : []
  return (
    <>
      <dl className="ev-spec">
        <dt>设备</dt>
        <dd>
          {event.equipmentName ?? "未关联设备"}
          {event.equipmentCode ? ` · ${event.equipmentCode}` : ""}
        </dd>
        <dt>发生</dt>
        <dd className="ev-tabular">{formatStamp(event.occurredAt)}</dd>
        <dt>严重程度</dt>
        <dd>{SEVERITY_LABEL[event.severity]}</dd>
        <dt>实际值</dt>
        <dd>{event.actualValue ?? "没有测点读数"}</dd>
        <dt>触发阈值</dt>
        <dd>{event.threshold}</dd>
        <dt>处理状态</dt>
        <dd>{STATUS_LABEL[status]}</dd>
      </dl>
      <section>
        <h3>关联测点</h3>
        <p>{event.metricLabel ?? "没有关联测点"}</p>
        {event.series ? <Sparkline series={event.series} /> : null}
        <p className="ev-note">趋势来自模拟序列，不是现场历史曲线。</p>
      </section>
      <section>
        <h3>诊断关联</h3>
        {event.diagnosisId ? <p>诊断任务 {event.diagnosisId}</p> : <p>没有关联的诊断任务。</p>}
        {links.map((item) => (
          <p key={item.id}>可能关联 {equipmentOf(DEMO_LINE, item.id)?.name ?? item.id}。{item.note}</p>
        ))}
      </section>
    </>
  )
}

function DiagnosisBody({ event, status }: { event: WorkEvent; status: WorkStatus }) {
  const task = event.diagnosis
  const links = event.equipmentId ? relatedEquipment(event.equipmentId).diagnostic : []
  return (
    <>
      <dl className="ev-spec">
        <dt>任务</dt>
        <dd>{task?.taskId ?? event.diagnosisId ?? event.id}</dd>
        <dt>设备</dt>
        <dd>
          {event.equipmentName ?? "未关联设备"}
          {event.equipmentCode ? ` · ${event.equipmentCode}` : ""}
          {event.systemId ? ` · ${systemOf(DEMO_LINE, event.systemId).name}` : ""}
        </dd>
        <dt>关联事件</dt>
        <dd>{event.relatedEventIds.length > 0 ? event.relatedEventIds.join("、") : "没有关联的其他事件"}</dd>
        <dt>复核</dt>
        <dd>{STATUS_LABEL[status]}</dd>
      </dl>
      <section>
        <h3>候选原因</h3>
        <ul className="ev-list">
          {(task?.candidates ?? [event.title]).map((item) => (
            <li key={item}>{item}（未确认）</li>
          ))}
        </ul>
      </section>
      <section>
        <h3>支持证据</h3>
        <ul className="ev-list">
          {(task?.evidence ?? [event.description]).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        {links.map((item) => (
          <p key={item.id}>可能关联 {equipmentOf(DEMO_LINE, item.id)?.name ?? item.id}。{item.note}</p>
        ))}
      </section>
      <section>
        <h3>模型与算法</h3>
        <p>{task?.modelNote}</p>
      </section>
    </>
  )
}

function LogBody({ event }: { event: WorkEvent }) {
  const log = event.log
  return (
    <dl className="ev-spec">
      <dt>级别</dt>
      <dd>{log?.level ?? SEVERITY_LABEL[event.severity]}</dd>
      <dt>模块</dt>
      <dd>{log?.module ?? event.source}</dd>
      <dt>时间</dt>
      <dd className="ev-tabular">{formatStamp(event.occurredAt)}</dd>
      <dt>消息</dt>
      <dd>{log?.message ?? event.description}</dd>
      <dt>错误码</dt>
      <dd>{log?.errorCode ?? "无"}</dd>
      <dt>任务</dt>
      <dd>{log?.taskId ?? "无"}</dd>
      {log?.detail ? (
        <>
          <dt>细节</dt>
          <dd>
            <details className="ev-details">
              <summary>展开技术细节</summary>
              <pre>{log.detail}</pre>
            </details>
          </dd>
        </>
      ) : null}
    </dl>
  )
}

export function EventsPage() {
  const { route, navigate } = useRoute()
  const events = useMemo(() => workEvents(), [])
  const anchor = useMemo(() => latestInstant(events), [events])
  const [audits, setAudits] = useState<AuditEntry[]>(() => loadAudit())
  const [severity, setSeverity] = useState<WorkSeverity | "all">("all")
  const [equipment, setEquipment] = useState("all")
  const [status, setStatus] = useState<WorkStatus | "all">("all")
  const [query, setQuery] = useState("")
  const [sort, setSort] = useState<SortId>("time-desc")
  const [narrow, setNarrow] = useState(false)
  const [listMode, setListMode] = useState(false)

  const range: RangeId = isRange(route.range) ? route.range : "all"
  const type: WorkEventType | "all" = isType(route.eventType) ? route.eventType : "all"
  const selected = route.eventId ? events.find((item) => item.id === route.eventId) : undefined
  const missingId = route.eventId && !selected ? route.eventId : undefined

  useEffect(() => {
    const media = window.matchMedia("(max-width: 980px)")
    const apply = () => setNarrow(media.matches)
    apply()
    media.addEventListener("change", apply)
    return () => media.removeEventListener("change", apply)
  }, [])

  useEffect(() => {
    setListMode(false)
    setAudits(loadAudit())
  }, [route.eventId])

  function currentStatus(event: WorkEvent): WorkStatus {
    return statusAfter(event.status, auditsFor(event.id, audits))
  }

  function windowed(event: WorkEvent): boolean {
    return inWindow(event, range, anchor)
  }

  const inTime = events.filter(windowed)
  const equipmentOptions = [...new Map(events.flatMap((item) => (item.equipmentId ? [[item.equipmentId, item] as const] : []))).values()]
  const filtered = inTime.filter((event) => {
    if (type !== "all" && event.type !== type) return false
    if (severity !== "all" && event.severity !== severity) return false
    if (equipment !== "all" && event.equipmentId !== equipment) return false
    if (status !== "all" && currentStatus(event) !== status) return false
    const needle = query.trim().toLowerCase()
    if (!needle) return true
    const haystack = [event.id, event.equipmentCode, event.equipmentName, event.title, event.description, event.diagnosisId].filter(Boolean).join(" ").toLowerCase()
    return haystack.includes(needle)
  })
  const sorted = [...filtered].sort((left, right) => {
    if (sort === "priority") {
      const rank = SEVERITY_RANK[right.severity] - SEVERITY_RANK[left.severity]
      return rank || right.occurredAt.localeCompare(left.occurredAt)
    }
    const order = left.occurredAt.localeCompare(right.occurredAt)
    return sort === "time-asc" ? order : -order
  })
  const outside = selected && !sorted.some((item) => item.id === selected.id) ? selected : undefined
  const rows = outside ? [outside, ...sorted] : sorted
  const pendingAlarms = inTime.filter((item) => item.type === "alarm" && OPEN_ALARM.includes(currentStatus(item))).length
  const severe = inTime.filter((item) => item.severity === "critical").length
  const pendingReviews = inTime.filter((item) => item.type === "diagnosis" && currentStatus(item) === "review").length
  const showList = !narrow || !route.eventId || listMode
  const showInspector = !narrow || Boolean(route.eventId && !listMode)

  function writeRoute(next: { eventId?: string; eventType?: WorkEventType | "all"; range?: RangeId }, mode: "push" | "replace") {
    const nextType = next.eventType ?? type
    const nextRange = next.range ?? range
    navigate(
      {
        page: "events",
        eventId: next.eventId,
        eventType: nextType === "all" ? undefined : nextType,
        range: nextRange === "all" ? undefined : nextRange,
      },
      mode,
    )
  }

  return (
    <main className="events-page">
      <header className="ev-head">
        <div>
          <h1>事件与日志</h1>
          <p>模拟快照 {DEMO_LINE.updatedAt}。时间窗以该时刻为截止，不是现场日志。</p>
        </div>
        <div className="ev-ranges" role="group" aria-label="时间筛选">
          {(Object.keys(RANGE_LABEL) as RangeId[]).map((item) => (
            <button key={item} type="button" aria-pressed={item === range} onClick={() => writeRoute({ eventId: route.eventId, range: item }, "replace")}>
              {RANGE_LABEL[item]}
            </button>
          ))}
        </div>
      </header>

      <section className="ev-stats" aria-label="事件统计">
        <div className="ev-stat">
          <span>待处理报警</span>
          <b>{pendingAlarms}</b>
          <small>未解决的传感器报警</small>
        </div>
        <div className="ev-stat">
          <span>严重事件</span>
          <b>{severe}</b>
          <small>严重等级</small>
        </div>
        <div className="ev-stat">
          <span>待复核推断</span>
          <b>{pendingReviews}</b>
          <small>尚未人工复核</small>
        </div>
        <div className="ev-stat">
          <span>近期事件</span>
          <b>{inTime.length}</b>
          <small>{RANGE_LABEL[range]}</small>
        </div>
      </section>

      <div className="ev-cats" role="group" aria-label="事件类别">
        <button type="button" aria-pressed={type === "all"} onClick={() => writeRoute({ eventId: route.eventId, eventType: "all" }, "replace")}>
          全部 {inTime.length}
        </button>
        {(["alarm", "diagnosis", "operation", "system"] as WorkEventType[]).map((item) => (
          <button key={item} type="button" aria-pressed={type === item} onClick={() => writeRoute({ eventId: route.eventId, eventType: item }, "replace")}>
            {TYPE_LABEL[item]} {inTime.filter((event) => event.type === item).length}
          </button>
        ))}
      </div>

      <div className="ev-filters">
        <select value={severity} onChange={(input) => setSeverity(input.target.value as WorkSeverity | "all")} aria-label="严重等级">
          <option value="all">全部等级</option>
          {(Object.keys(SEVERITY_LABEL) as WorkSeverity[]).map((item) => (
            <option key={item} value={item}>
              {SEVERITY_LABEL[item]}
            </option>
          ))}
        </select>
        <select value={equipment} onChange={(input) => setEquipment(input.target.value)} aria-label="关联设备">
          <option value="all">全部设备</option>
          {equipmentOptions.map((item) => (
            <option key={item.equipmentId} value={item.equipmentId}>
              {item.equipmentCode} {item.equipmentName}
            </option>
          ))}
        </select>
        <select value={status} onChange={(input) => setStatus(input.target.value as WorkStatus | "all")} aria-label="处理状态">
          <option value="all">全部状态</option>
          {(Object.keys(STATUS_LABEL) as WorkStatus[]).map((item) => (
            <option key={item} value={item}>
              {STATUS_LABEL[item]}
            </option>
          ))}
        </select>
        <select value={sort} onChange={(input) => setSort(input.target.value as SortId)} aria-label="排序">
          <option value="time-desc">时间新到旧</option>
          <option value="time-asc">时间旧到新</option>
          <option value="priority">优先级</option>
        </select>
        <input value={query} onChange={(input) => setQuery(input.target.value)} placeholder="事件 ID、设备编号或关键词" aria-label="搜索事件" />
        <span className="ev-note">显示 {filtered.length} / 窗口 {inTime.length}</span>
      </div>

      <div className="ev-split">
        {showList ? (
          <section className="ev-pane" aria-label="事件列表">
            <div className="ev-pane-head">
              <h2>事件列表</h2>
              <span>{outside ? "已选事件不在当前筛选中，仍保留选中。" : "选择一行查看详情"}</span>
            </div>
            {rows.length === 0 ? (
              <p className="ev-empty">没有符合筛选条件的事件。</p>
            ) : (
              <table className="ev-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>等级</th>
                    <th>事件名称</th>
                    <th>关联对象</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((event) => {
                    const current = currentStatus(event)
                    const open = OPEN_ALARM.includes(current) || current === "review"
                    return (
                      <tr
                        key={event.id}
                        aria-selected={event.id === route.eventId}
                        className={event.id === outside?.id ? "is-outside" : undefined}
                        tabIndex={0}
                        onClick={() => writeRoute({ eventId: event.id }, "push")}
                        onKeyDown={(keyEvent) => {
                          if (keyEvent.key === "Enter" || keyEvent.key === " ") {
                            keyEvent.preventDefault()
                            writeRoute({ eventId: event.id }, "push")
                          }
                        }}
                      >
                        <td className="ev-tabular">{formatStamp(event.occurredAt).slice(5)}</td>
                        <td>
                          <span className={`ev-tag is-${event.severity}`}>{SEVERITY_LABEL[event.severity]}</span>
                        </td>
                        <td>
                          <span className="ev-name">
                            <strong>{event.title}</strong>
                            <span>{TYPE_LABEL[event.type]} · {event.id}</span>
                          </span>
                        </td>
                        <td>{event.equipmentCode ? `${event.equipmentCode} ${event.equipmentName}` : event.log?.module ?? "系统"}</td>
                        <td>
                          <span className={open ? "ev-tag is-open" : "ev-tag is-done"}>{STATUS_LABEL[current]}</span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>
        ) : null}
        {showInspector ? (
          <EventInspector event={selected} missingId={missingId} audits={audits} narrow={narrow} onBack={() => setListMode(true)} onChanged={() => setAudits(loadAudit())} />
        ) : null}
      </div>
    </main>
  )
}
