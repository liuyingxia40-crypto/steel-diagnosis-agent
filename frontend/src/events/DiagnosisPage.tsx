import { useEffect, useMemo, useState } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { equipmentOf } from "../overview/model"
import { relatedEquipment } from "../overview/situation"
import { useRoute } from "../routing"
import { auditsFor, loadAudit, statusAfter } from "./audit"
import { eventById, workEvents } from "./catalog"
import { STATUS_LABEL } from "./types"
import "./events.css"

export function DiagnosisPage() {
  const { route, navigate } = useRoute()
  const events = useMemo(() => workEvents(), [])
  const byTask = route.diagnosisId ? events.find((item) => item.diagnosisId === route.diagnosisId && item.type === "diagnosis") : undefined
  const byEvent = route.eventId ? eventById(route.eventId) : undefined
  const event = byTask ?? (byEvent?.type === "diagnosis" ? byEvent : undefined)
  const unrelated = !event && byEvent && byEvent.type !== "diagnosis"
  const [audits, setAudits] = useState(() => loadAudit())
  const links = event?.equipmentId ? relatedEquipment(event.equipmentId).diagnostic : []
  const review = event ? statusAfter(event.status, auditsFor(event.id, audits)) : undefined

  useEffect(() => {
    setAudits(loadAudit())
  }, [route.eventId, route.diagnosisId])

  return (
    <main className="diagnosis-page">
      <header className="ev-head">
        <div>
          <h1>智能诊断工作台</h1>
          <p>只展示已有推断记录。模拟数据，不是已确认故障，也没有接入诊断模型。</p>
        </div>
        <button type="button" className="ev-back" onClick={() => navigate({ page: "events", eventId: route.eventId, eventType: event ? "diagnosis" : undefined })}>
          返回事件与日志
        </button>
      </header>
      <section className="ev-inspector" aria-label="诊断任务">
        {!event ? (
          <p className="ev-empty">
            {unrelated
              ? `事件 ${byEvent.id} 不是诊断任务。没有改选其他记录。`
              : route.diagnosisId || route.eventId
                ? `未找到诊断任务 ${route.diagnosisId ?? route.eventId}。它可能已删除，或当前无权访问。`
                : "没有指定诊断任务。从事件详情或首页推断进入。"}
          </p>
        ) : (
          <div className="ev-body">
            <header>
              <span className="ev-tag is-attention">推断 · 未确认</span>
              <h2>{event.title}</h2>
              <p className="ev-tabular">
                {event.diagnosis?.taskId} · {event.id}
              </p>
            </header>
            <dl className="ev-spec">
              <dt>设备</dt>
              <dd>
                {event.equipmentName}
                {event.equipmentCode ? ` · ${event.equipmentCode}` : ""}
              </dd>
              <dt>关联事件</dt>
              <dd>{event.relatedEventIds.length > 0 ? event.relatedEventIds.join("、") : event.id}</dd>
              <dt>复核</dt>
              <dd>{review ? STATUS_LABEL[review] : "待复核"}</dd>
            </dl>
            <section>
              <h3>候选原因</h3>
              <ul className="ev-list">
                {event.diagnosis?.candidates.map((item) => (
                  <li key={item}>{item}（未确认）</li>
                ))}
              </ul>
            </section>
            <section>
              <h3>支持证据</h3>
              <ul className="ev-list">
                {event.diagnosis?.evidence.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              {links.map((item) => (
                <p key={item.id}>可能关联 {equipmentOf(DEMO_LINE, item.id)?.name ?? item.id}。{item.note}</p>
              ))}
            </section>
            <section>
              <h3>模型与算法</h3>
              <p>{event.diagnosis?.modelNote}</p>
            </section>
            <div className="ev-links">
              {event.equipmentId ? (
                <button type="button" onClick={() => navigate({ page: "monitor", equipmentId: event.equipmentId })}>
                  查看设备
                </button>
              ) : null}
              <button type="button" onClick={() => navigate({ page: "events", eventId: event.id, eventType: "diagnosis" })}>
                查看事件记录
              </button>
            </div>
          </div>
        )}
      </section>
    </main>
  )
}
