import { useMemo, useState } from "react"
import { DEMO_LINE } from "../overview/demoData"
import { equipmentOf } from "../overview/model"
import { useRoute } from "../routing"
import { workEvents } from "../events/catalog"
import { HEALTH_AS_OF, assetById, equipmentAssets } from "./catalog"
import { equipmentStatus, latestPreventive, latestRecord, nextCareLabel, sameMonth, serviceYears, STATUS_RANK, planSignal } from "./status"
import { completePlan, createPlan, createRecord, deleteRecord, updatePlan, updateRecord, useHealthState, type PlanDraft, type RecordDraft } from "./store"
import { KIND_LABEL, STATUS_LABEL, type EquipmentAsset, type MaintenanceKind, type MaintenancePlan, type MaintenanceRecord, type MaintenanceStatus } from "./types"
import "./health.css"

type SortId = "urgency" | "next" | "years" | "last" | "name"
type TabId = "history" | "plan" | "health"
type DrawerMode = { kind: "record"; record?: MaintenanceRecord; plan?: MaintenancePlan } | { kind: "plan"; plan?: MaintenancePlan } | null

const PAGE_SIZE = 8

export function HealthPage() {
  const { route, navigate } = useRoute()
  const state = useHealthState()
  const assets = useMemo(() => equipmentAssets(), [])
  const selected = route.page === "health" && route.equipmentId ? assetById(route.equipmentId) : undefined
  const missing = route.page === "health" && Boolean(route.equipmentId) && !selected

  return (
    <main className="health-page">
      {selected ? (
        <AssetDetail asset={selected} onBack={() => navigate({ page: "health" })} />
      ) : missing ? (
        <section>
          <button type="button" className="hc-back" onClick={() => navigate({ page: "health" })}>
            返回设备健康中心
          </button>
          <p className="hc-empty">未找到设备 {route.equipmentId}。它可能不在当前设备台账中。没有改选其他设备。</p>
        </section>
      ) : (
        <AssetList assets={assets} records={state.records} plans={state.plans} onOpen={(id) => navigate({ page: "health", equipmentId: id })} />
      )}
    </main>
  )
}

function AssetList({
  assets,
  records,
  plans,
  onOpen,
}: {
  assets: EquipmentAsset[]
  records: MaintenanceRecord[]
  plans: MaintenancePlan[]
  onOpen: (id: string) => void
}) {
  const [query, setQuery] = useState("")
  const [system, setSystem] = useState("all")
  const [status, setStatus] = useState<MaintenanceStatus | "all">("all")
  const [sort, setSort] = useState<SortId>("urgency")
  const [windowDays, setWindowDays] = useState(30)
  const [drawer, setDrawer] = useState<DrawerMode>(null)
  const systems = [...new Set(assets.map((item) => item.systemName))]
  const views = assets.map((asset) => ({
    asset,
    status: equipmentStatus(plans, asset, HEALTH_AS_OF, windowDays),
    years: serviceYears(asset.commissioningDate),
    last: latestRecord(records, asset.id),
    next: nextCareLabel(plans, asset, HEALTH_AS_OF, windowDays),
  }))
  const dueCount = views.filter((item) => item.status === "due").length
  const overdueCount = views.filter((item) => item.status === "overdue").length
  const monthCount = records.filter((item) => sameMonth(item.performedAt) && item.result.includes("完成")).length
  const needle = query.trim().toLowerCase()
  const filtered = views.filter((item) => {
    if (system !== "all" && item.asset.systemName !== system) return false
    if (status !== "all" && item.status !== status) return false
    if (!needle) return true
    return `${item.asset.name} ${item.asset.code} ${item.asset.plateName ?? ""}`.toLowerCase().includes(needle)
  })
  const sorted = [...filtered].sort((left, right) => compareViews(left, right, sort))

  return (
    <>
      <header className="hc-head">
        <div>
          <h1>设备健康中心</h1>
          <p>设备履历 · 维修保养 · 生命周期管理。演示档案，统计截止 {DEMO_LINE.updatedAt}，未写入生产系统。</p>
        </div>
        <label className="hc-note">
          即将保养窗口
          <input
            aria-label="即将保养窗口天数"
            type="number"
            min={1}
            max={180}
            value={windowDays}
            onChange={(event) => setWindowDays(Math.min(180, Math.max(1, Number(event.target.value) || 30)))}
          />
          天
        </label>
      </header>
      <section className="hc-stats" aria-label="维护概览">
        <button type="button" className="hc-stat" aria-pressed={status === "all"} onClick={() => setStatus("all")}>
          <span>管理设备</span>
          <b>{assets.length}</b>
          <small>台账中的设备</small>
        </button>
        <button type="button" className="hc-stat" aria-pressed={status === "due"} onClick={() => setStatus("due")}>
          <span>即将保养</span>
          <b>{dueCount}</b>
          <small>{windowDays} 天内到期的设备</small>
        </button>
        <button type="button" className="hc-stat" aria-pressed={status === "overdue"} onClick={() => setStatus("overdue")}>
          <span>保养逾期</span>
          <b>{overdueCount}</b>
          <small>超过计划且未完成</small>
        </button>
        <button type="button" className="hc-stat" aria-pressed={false} onClick={() => setStatus("all")}>
          <span>本月已完成维护</span>
          <b>{monthCount}</b>
          <small>任务数，不是设备数</small>
        </button>
      </section>
      <div className="hc-toolbar">
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索设备名称或编号" aria-label="搜索设备" />
        <select value={system} onChange={(event) => setSystem(event.target.value)} aria-label="所属系统">
          <option value="all">全部系统</option>
          {systems.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <select value={status} onChange={(event) => setStatus(event.target.value as MaintenanceStatus | "all")} aria-label="维护状态">
          <option value="all">全部状态</option>
          {(Object.keys(STATUS_LABEL) as MaintenanceStatus[]).map((item) => (
            <option key={item} value={item}>
              {STATUS_LABEL[item]}
            </option>
          ))}
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value as SortId)} aria-label="排序">
          <option value="urgency">逾期和即将保养优先</option>
          <option value="next">下次保养时间</option>
          <option value="years">服役年限</option>
          <option value="last">最近保养时间</option>
          <option value="name">设备名称</option>
        </select>
        <span className="hc-spacer" />
        <button type="button" className="hc-primary" onClick={() => setDrawer({ kind: "record" })}>
          新增维护记录
        </button>
      </div>
      {sorted.length === 0 ? <p className="hc-empty">没有符合条件的设备。</p> : null}
      <section className="hc-grid" aria-label="设备卡片">
        {sorted.map((item) => (
          <button key={item.asset.id} type="button" className="hc-card" onClick={() => onOpen(item.asset.id)}>
            <span className="hc-card-top">
              <AssetMark type={item.asset.equipmentType} />
              <span className="hc-card-title">
                <strong>
                  {item.asset.name} {item.asset.code}
                </strong>
                <span>{item.asset.systemName}</span>
              </span>
              <em className={`hc-status is-${item.status}`}>{STATUS_LABEL[item.status]}</em>
            </span>
            <dl>
              <dt>服役</dt>
              <dd>{item.years}</dd>
              <dt>上次维护</dt>
              <dd>{item.last ? item.last.performedAt : "无记录"}</dd>
              <dt>下次保养</dt>
              <dd>{item.next}</dd>
            </dl>
            <span className="hc-more">详情</span>
          </button>
        ))}
      </section>
      {drawer ? <RecordDrawer assets={assets} mode={drawer} plans={plans} onClose={() => setDrawer(null)} /> : null}
    </>
  )
}

function compareViews(
  left: { asset: EquipmentAsset; status: MaintenanceStatus; years: string; last?: MaintenanceRecord; next: string },
  right: { asset: EquipmentAsset; status: MaintenanceStatus; years: string; last?: MaintenanceRecord; next: string },
  sort: SortId,
): number {
  if (sort === "name") return left.asset.name.localeCompare(right.asset.name, "zh")
  if (sort === "years") return numberOf(right.years) - numberOf(left.years)
  if (sort === "last") return (right.last?.performedAt ?? "").localeCompare(left.last?.performedAt ?? "")
  if (sort === "next") return left.next.localeCompare(right.next)
  return STATUS_RANK[left.status] - STATUS_RANK[right.status] || left.next.localeCompare(right.next)
}

function numberOf(value: string): number {
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : -1
}

function AssetDetail({ asset, onBack }: { asset: EquipmentAsset; onBack: () => void }) {
  const { navigate } = useRoute()
  const state = useHealthState()
  const [tab, setTab] = useState<TabId>("history")
  const [page, setPage] = useState(0)
  const [drawer, setDrawer] = useState<DrawerMode>(null)
  const [windowDays] = useState(30)
  const records = state.records.filter((item) => item.equipmentId === asset.id)
  const plans = state.plans.filter((item) => item.equipmentId === asset.id)
  const status = equipmentStatus(state.plans, asset, HEALTH_AS_OF, windowDays)
  const events = workEvents().filter((item) => item.equipmentId === asset.id)
  const node = equipmentOf(DEMO_LINE, asset.id)
  const pages = Math.max(1, Math.ceil(records.length / PAGE_SIZE))
  const visible = records.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const relatedIds = new Set([...records.map((item) => item.id), ...plans.map((item) => item.id)])
  const audits = state.audits.filter((item) => relatedIds.has(item.targetId))

  return (
    <article className="hc-detail">
      <header className="hc-detail-head">
        <button type="button" className="hc-back" onClick={onBack}>
          返回设备健康中心
        </button>
        <div className="hc-links">
          <button type="button" className="hc-ghost" onClick={() => navigate({ page: "monitor", equipmentId: asset.id })}>
            查看运行监测
          </button>
          <button type="button" className="hc-ghost" onClick={() => navigate({ page: "portal" })}>
            返回产线总览
          </button>
        </div>
      </header>
      <h2>
        {asset.name} {asset.code}
      </h2>
      <p className="hc-note">
        {asset.systemName} · {asset.equipmentType}
        {asset.plateName && asset.plateName !== asset.name ? ` · 铭牌 ${asset.plateName}` : ""} · 模拟档案
      </p>
      <p>
        <em className={`hc-status is-${status}`}>{STATUS_LABEL[status]}</em>
      </p>
      <section className="hc-kpis" aria-label="设备维护摘要">
        <div>
          <span>服役年限</span>
          <b>{serviceYears(asset.commissioningDate)}</b>
        </div>
        <div>
          <span>累计运行小时</span>
          <b>{asset.operatingHours == null ? "未知" : `${asset.operatingHours} h`}</b>
        </div>
        <div>
          <span>上次定期保养</span>
          <b>{latestPreventive(state.records, asset.id)?.performedAt ?? "无"}</b>
        </div>
        <div>
          <span>下次保养</span>
          <b>{nextCareLabel(state.plans, asset)}</b>
        </div>
      </section>
      <p className="hc-note">服役年限按投运日期 {asset.commissioningDate ?? "未知"} 计算到 {HEALTH_AS_OF}。维护状态来自维护计划，不由报警代替。基础资料来自产线设备台账，这里没有第二套设备编号。</p>
      <div className="hc-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "history"} onClick={() => setTab("history")}>
          维修保养履历
        </button>
        <button type="button" role="tab" aria-selected={tab === "plan"} onClick={() => setTab("plan")}>
          维护计划
        </button>
        <button type="button" role="tab" aria-selected={tab === "health"} onClick={() => setTab("health")}>
          健康记录
        </button>
      </div>
      {tab === "history" ? (
        <section className="hc-panel" aria-label="维修保养履历">
          <div className="hc-actions" style={{ padding: "10px 12px" }}>
            <button type="button" className="hc-primary" onClick={() => setDrawer({ kind: "record" })}>
              新增维护记录
            </button>
            <span className="hc-note">共 {records.length} 条。故障维修不会自动完成定期保养。</span>
          </div>
          {visible.length === 0 ? <p className="hc-empty">还没有维护记录。</p> : null}
          {visible.map((record) => (
            <article key={record.id} className="hc-record">
              <header>
                <strong className="ev-tabular">{record.performedAt}</strong>
                <span>{KIND_LABEL[record.type]}</span>
                <em className="hc-status">{record.result}</em>
                <span className="hc-note">{record.dataMode === "simulated" ? "模拟档案" : "本机会话，未写入生产系统"}</span>
              </header>
              <p>{record.description}</p>
              <p className="hc-note">
                执行人：{record.operator || "未记录"}
                {record.replacedComponents && record.replacedComponents.length > 0 ? ` · 更换 ${record.replacedComponents.join("、")}` : ""}
                {record.attachmentNames && record.attachmentNames.length > 0 ? ` · 附件 ${record.attachmentNames.join("、")}` : ""}
              </p>
              {record.relatedEventIds && record.relatedEventIds.length > 0 ? (
                <div className="hc-links">
                  {record.relatedEventIds.map((id) => (
                    <button key={id} type="button" className="hc-ghost" onClick={() => navigate({ page: "events", eventId: id })}>
                      事件 {id}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="hc-note">没有关联事件。</p>
              )}
              {record.diagnosisId ? (
                <button type="button" className="hc-ghost" onClick={() => navigate({ page: "diagnosis", diagnosisId: record.diagnosisId, eventId: record.relatedEventIds?.[0] })}>
                  诊断 {record.diagnosisId}
                </button>
              ) : null}
              {record.revisions.map((revision) => (
                <p key={revision.at} className="hc-note">
                  {revision.at.slice(0, 16).replace("T", " ")} {revision.operator} 修改：{revision.summary}
                </p>
              ))}
              <div className="hc-actions">
                <button type="button" className="hc-ghost" onClick={() => setDrawer({ kind: "record", record })}>
                  编辑
                </button>
                <button type="button" className="hc-ghost" onClick={() => deleteRecord(record.id, "演示用户")}>
                  删除
                </button>
              </div>
            </article>
          ))}
          {pages > 1 ? (
            <div className="hc-pager" style={{ padding: 10 }}>
              <button type="button" className="hc-ghost" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>
                上一页
              </button>
              <span className="hc-note">
                {page + 1} / {pages}
              </span>
              <button type="button" className="hc-ghost" disabled={page + 1 >= pages} onClick={() => setPage((value) => value + 1)}>
                下一页
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
      {tab === "plan" ? (
        <section className="hc-panel" aria-label="维护计划">
          <div className="hc-actions" style={{ padding: "10px 12px" }}>
            <button type="button" className="hc-primary" onClick={() => setDrawer({ kind: "plan" })}>
              新增维护计划
            </button>
            <span className="hc-note">同时配置时间和运行小时时，任一条件到达即到期。完成一项不会重置整台设备的服役年限。</span>
          </div>
          {plans.length === 0 ? <p className="hc-empty">还没有维护计划，因此维护状态是未制定计划。</p> : null}
          {plans.map((plan) => (
            <article key={plan.id} className="hc-plan">
              <header>
                <strong>{plan.title}</strong>
                <span>{KIND_LABEL[plan.type]}</span>
                <em className={`hc-status is-${planSignal(plan, asset)}`}>{STATUS_LABEL[plan.enabled ? planSignal(plan, asset) : "ok"]}</em>
              </header>
              <p>
                {plan.intervalDays ? `每 ${plan.intervalDays} 天` : "无固定天数"}
                {plan.intervalOperatingHours ? ` · 每 ${plan.intervalOperatingHours} 运行小时` : ""}
                {plan.intervalDays && plan.intervalOperatingHours ? " · 任一到达即触发" : ""}
              </p>
              <p className="hc-note">
                上次 {plan.lastCompletedAt ?? "无"}
                {plan.lastCompletedOperatingHours != null ? ` / ${plan.lastCompletedOperatingHours} h` : ""} · 下次 {plan.nextDueAt ?? "无日期"}
                {plan.nextDueOperatingHours != null ? ` / ${plan.nextDueOperatingHours} h` : ""} · 负责人 {plan.assignedTo || "未指定"}
              </p>
              <div className="hc-actions">
                <button type="button" className="hc-ghost" onClick={() => setDrawer({ kind: "plan", plan })}>
                  编辑
                </button>
                <button type="button" className="hc-primary" onClick={() => setDrawer({ kind: "record", plan })}>
                  标记执行完成
                </button>
              </div>
            </article>
          ))}
        </section>
      ) : null}
      {tab === "health" ? (
        <section className="hc-panel" aria-label="健康记录">
          <h3 style={{ margin: "10px 12px 0", fontSize: 13 }}>历史异常与诊断</h3>
          {events.length === 0 ? <p className="hc-empty">暂无关联的事件记录。</p> : null}
          {events.map((item) => (
            <article key={item.id} className="hc-record">
              <header>
                <strong>{item.occurredAt.slice(0, 16).replace("T", " ")}</strong>
                <span>{item.title}</span>
              </header>
              <p>{item.description}</p>
              <div className="hc-links">
                <button type="button" className="hc-ghost" onClick={() => navigate({ page: "events", eventId: item.id, eventType: item.type })}>
                  查看事件
                </button>
                {item.diagnosisId ? (
                  <button type="button" className="hc-ghost" onClick={() => navigate({ page: "diagnosis", eventId: item.id, diagnosisId: item.diagnosisId })}>
                    查看诊断
                  </button>
                ) : null}
              </div>
            </article>
          ))}
          <h3 style={{ margin: "10px 12px 0", fontSize: 13 }}>关键监测读数</h3>
          <p className="hc-note" style={{ padding: "0 12px" }}>
            以下是模拟快照 {DEMO_LINE.updatedAt} 的当前读数，不是长期历史曲线，也不能单独证明保养是否逾期。
          </p>
          {node && node.metrics.length > 0 ? (
            <dl className="hc-spec">
              {node.metrics.map((metric) => (
                <span key={metric.id} style={{ display: "contents" }}>
                  <dt>{metric.label}</dt>
                  <dd>
                    {metric.value} {metric.unit}
                  </dd>
                </span>
              ))}
            </dl>
          ) : (
            <p className="hc-empty">没有监测读数。</p>
          )}
          <h3 style={{ margin: "0 12px", fontSize: 13 }}>健康评估</h3>
          <p className="hc-empty">暂无健康评估记录。没有根据报警或维护记录自动生成健康结论，也没有维修前后对比。</p>
        </section>
      ) : null}
      {audits.length > 0 ? (
        <section>
          <h3 style={{ fontSize: 13 }}>操作记录</h3>
          {audits.slice(0, 6).map((item) => (
            <p key={item.id} className="hc-note">
              {item.at.slice(0, 19).replace("T", " ")} {item.operator} · {item.note} · 未写入生产系统
            </p>
          ))}
        </section>
      ) : null}
      {drawer ? <RecordDrawer assets={[asset]} mode={drawer} plans={state.plans} onClose={() => setDrawer(null)} /> : null}
    </article>
  )
}

function RecordDrawer({
  assets,
  mode,
  plans,
  onClose,
}: {
  assets: EquipmentAsset[]
  mode: Exclude<DrawerMode, null>
  plans: MaintenancePlan[]
  onClose: () => void
}) {
  if (mode.kind === "plan") {
    return <PlanForm assets={assets} plan={mode.plan} onClose={onClose} />
  }
  return <RecordForm assets={assets} record={mode.record} plan={mode.plan} plans={plans} onClose={onClose} />
}

function RecordForm({
  assets,
  record,
  plan,
  plans,
  onClose,
}: {
  assets: EquipmentAsset[]
  record?: MaintenanceRecord
  plan?: MaintenancePlan
  plans: MaintenancePlan[]
  onClose: () => void
}) {
  const [equipmentId, setEquipmentId] = useState(record?.equipmentId ?? plan?.equipmentId ?? assets[0]?.id ?? "")
  const [type, setType] = useState<MaintenanceKind>(record?.type ?? plan?.type ?? "preventive")
  const [performedAt, setPerformedAt] = useState(record?.performedAt ?? HEALTH_AS_OF)
  const [description, setDescription] = useState(record?.description ?? (plan ? `完成${plan.title}` : ""))
  const [operator, setOperator] = useState(record?.operator ?? "演示用户")
  const [result, setResult] = useState(record?.result ?? "已完成")
  const [parts, setParts] = useState(record?.replacedComponents?.join("、") ?? "")
  const [events, setEvents] = useState<string[]>(record?.relatedEventIds ?? [])
  const [diagnosisId, setDiagnosisId] = useState(record?.diagnosisId ?? "")
  const [files, setFiles] = useState<string[]>(record?.attachmentNames ?? [])
  const [planId, setPlanId] = useState(record?.maintenancePlanId ?? plan?.id ?? "")
  const [error, setError] = useState("")
  const related = workEvents().filter((item) => item.equipmentId === equipmentId)
  const diagnoses = related.filter((item) => item.type === "diagnosis")
  const equipmentPlans = plans.filter((item) => item.equipmentId === equipmentId)

  function save() {
    if (!equipmentId || !description.trim() || !operator.trim() || !result.trim() || !performedAt) {
      setError("请填写设备、日期、工作内容、执行人和结果。")
      return
    }
    const draft: RecordDraft = {
      equipmentId,
      maintenancePlanId: planId || undefined,
      type,
      performedAt,
      description,
      operator,
      result,
      replacedComponents: parts.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
      relatedEventIds: events,
      diagnosisId: diagnosisId || undefined,
      attachmentNames: files,
    }
    if (!record && (plan || planId)) completePlan(plan?.id || planId, draft)
    else if (record) updateRecord(record.id, draft, "更正维护记录，原描述已保留在修改历史")
    else createRecord(draft)
    onClose()
  }

  return (
    <div className="hc-drawer-back" onClick={onClose}>
      <form
        className="hc-drawer hc-form"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        <h2>{record ? "编辑维护记录" : plan ? "完成维护计划" : "新增维护记录"}</h2>
        <p className="hc-note">演示模式只保存在本浏览器会话，不会同步到生产系统。附件只保留文件名。</p>
        <label>
          关联设备
          <select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)} disabled={Boolean(plan)}>
            {assets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} {item.code}
              </option>
            ))}
          </select>
        </label>
        <label>
          作业类型
          <select value={type} onChange={(event) => setType(event.target.value as MaintenanceKind)}>
            {(Object.keys(KIND_LABEL) as MaintenanceKind[]).map((item) => (
              <option key={item} value={item}>
                {KIND_LABEL[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          维护日期
          <input type="date" value={performedAt} onChange={(event) => setPerformedAt(event.target.value)} />
        </label>
        <label>
          工作内容
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <label>
          执行人员
          <input value={operator} onChange={(event) => setOperator(event.target.value)} />
        </label>
        <label>
          维护结果
          <input value={result} onChange={(event) => setResult(event.target.value)} />
        </label>
        <label>
          更换部件
          <input value={parts} onChange={(event) => setParts(event.target.value)} placeholder="多项用顿号分隔，可空" />
        </label>
        <label>
          关联计划
          <select value={planId} onChange={(event) => setPlanId(event.target.value)} disabled={Boolean(plan)}>
            <option value="">不关联计划</option>
            {equipmentPlans.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <div>
          <span className="hc-note">关联事件</span>
          <div className="hc-checks">
            {related.length === 0 ? <span className="hc-note">这台设备没有可关联的事件。</span> : null}
            {related.map((item) => (
              <label key={item.id}>
                <input
                  type="checkbox"
                  checked={events.includes(item.id)}
                  onChange={(event) => setEvents((current) => (event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id)))}
                />
                {item.id} {item.title}
              </label>
            ))}
          </div>
        </div>
        <label>
          关联诊断
          <select value={diagnosisId} onChange={(event) => setDiagnosisId(event.target.value)}>
            <option value="">不关联</option>
            {diagnoses.map((item) => (
              <option key={item.id} value={item.diagnosisId}>
                {item.diagnosisId} {item.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          附件
          <input
            type="file"
            multiple
            onChange={(event) => setFiles([...(event.target.files ? [...event.target.files].map((file) => file.name) : [])])}
          />
        </label>
        {files.length > 0 ? <p className="hc-note">将保存文件名：{files.join("、")}</p> : null}
        {error ? <p className="is-error">{error}</p> : null}
        <div className="hc-actions">
          <button type="submit" className="hc-primary">
            保存
          </button>
          <button type="button" className="hc-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </div>
  )
}

function PlanForm({ assets, plan, onClose }: { assets: EquipmentAsset[]; plan?: MaintenancePlan; onClose: () => void }) {
  const [equipmentId, setEquipmentId] = useState(plan?.equipmentId ?? assets[0]?.id ?? "")
  const [title, setTitle] = useState(plan?.title ?? "")
  const [type, setType] = useState<MaintenanceKind>(plan?.type ?? "preventive")
  const [days, setDays] = useState(plan?.intervalDays ? String(plan.intervalDays) : "")
  const [hours, setHours] = useState(plan?.intervalOperatingHours ? String(plan.intervalOperatingHours) : "")
  const [nextDueAt, setNextDueAt] = useState(plan?.nextDueAt ?? "")
  const [nextHours, setNextHours] = useState(plan?.nextDueOperatingHours != null ? String(plan.nextDueOperatingHours) : "")
  const [assignedTo, setAssignedTo] = useState(plan?.assignedTo ?? "演示维护班")
  const [enabled, setEnabled] = useState(plan?.enabled ?? true)
  const [error, setError] = useState("")

  function save() {
    const intervalDays = days.trim() ? Number(days) : undefined
    const intervalOperatingHours = hours.trim() ? Number(hours) : undefined
    const nextDueOperatingHours = nextHours.trim() ? Number(nextHours) : undefined
    if (!title.trim() || (!intervalDays && !intervalOperatingHours)) {
      setError("请填写项目，并至少设置时间周期或运行小时周期。")
      return
    }
    if (!nextDueAt && nextDueOperatingHours == null) {
      setError("请填写下次计划日期或下次运行小时。")
      return
    }
    const draft: PlanDraft = {
      equipmentId,
      title: title.trim(),
      type,
      intervalDays,
      intervalOperatingHours,
      nextDueAt: nextDueAt || undefined,
      nextDueOperatingHours,
      assignedTo,
      enabled,
    }
    if (plan) updatePlan(plan.id, draft)
    else createPlan(draft)
    onClose()
  }

  return (
    <div className="hc-drawer-back" onClick={onClose}>
      <form
        className="hc-drawer hc-form"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        <h2>{plan ? "编辑维护计划" : "新增维护计划"}</h2>
        <p className="hc-note">演示模式只保存在本浏览器会话。两种周期都填写时，任一到达即到期。</p>
        <label>
          设备
          <select value={equipmentId} onChange={(event) => setEquipmentId(event.target.value)} disabled={Boolean(plan)}>
            {assets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} {item.code}
              </option>
            ))}
          </select>
        </label>
        <label>
          维护项目
          <input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          维护类型
          <select value={type} onChange={(event) => setType(event.target.value as MaintenanceKind)}>
            {(Object.keys(KIND_LABEL) as MaintenanceKind[]).map((item) => (
              <option key={item} value={item}>
                {KIND_LABEL[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          时间周期（天）
          <input value={days} onChange={(event) => setDays(event.target.value)} inputMode="numeric" />
        </label>
        <label>
          运行小时周期
          <input value={hours} onChange={(event) => setHours(event.target.value)} inputMode="numeric" />
        </label>
        <label>
          下次计划日期
          <input type="date" value={nextDueAt} onChange={(event) => setNextDueAt(event.target.value)} />
        </label>
        <label>
          下次运行小时
          <input value={nextHours} onChange={(event) => setNextHours(event.target.value)} inputMode="numeric" />
        </label>
        <label>
          负责人
          <input value={assignedTo} onChange={(event) => setAssignedTo(event.target.value)} />
        </label>
        <label>
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          启用
        </label>
        {error ? <p className="is-error">{error}</p> : null}
        <div className="hc-actions">
          <button type="submit" className="hc-primary">
            保存
          </button>
          <button type="button" className="hc-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </div>
  )
}

function AssetMark({ type }: { type: string }) {
  const pen = { fill: "none", stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round" as const, strokeLinejoin: "round" as const }
  return (
    <svg className="hc-mark" viewBox="0 0 32 32" aria-hidden="true">
      {type === "电机" ? (
        <>
          <rect {...pen} x="6" y="10" width="14" height="12" rx="2" />
          <path {...pen} d="M20 13h6v6h-6M9 10V7h8" />
        </>
      ) : null}
      {type === "减速机" ? (
        <>
          <circle {...pen} cx="12" cy="16" r="5" />
          <circle {...pen} cx="20" cy="16" r="3.5" />
          <path {...pen} d="M12 11V8M20 12.5V9" />
        </>
      ) : null}
      {type === "泵" ? (
        <>
          <circle {...pen} cx="14" cy="16" r="6" />
          <path {...pen} d="M14 10v12M20 16h6M8 16H6" />
        </>
      ) : null}
      {type === "油箱" || type === "冷却器" ? <rect {...pen} x="7" y="9" width="18" height="14" rx="2" /> : null}
      {type === "轧机" ? (
        <>
          <path {...pen} d="M8 8v16M24 8v16M8 12h16M8 20h16" />
          <circle {...pen} cx="16" cy="16" r="2.2" />
        </>
      ) : null}
      {type !== "电机" && type !== "减速机" && type !== "泵" && type !== "油箱" && type !== "冷却器" && type !== "轧机" ? (
        <rect {...pen} x="8" y="8" width="16" height="16" rx="2" />
      ) : null}
    </svg>
  )
}
