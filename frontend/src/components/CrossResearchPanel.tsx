import { useEffect, useMemo, useState } from "react"
import { relationLabel } from "../graph/presentation"
import {
  checkStatusText,
  choiceReady,
  modelStateText,
  nameMatches,
  overallStatusText,
  researchIdentity,
  type NameMatch,
} from "../knowledge/crossResearch"
import type { ApiErrorResponse, DatasetDevices, DatasetRecords } from "../types/api"
import type { KnowledgeGraph } from "../types/graph"

interface SelectedNode {
  id: string
  label: string
}

interface Identity {
  node_id: string
  label: string
  entity_type: string
  scope: string
  parent_id: string
}

interface ResearchReport {
  entity_a: string
  entity_b: string
  research_status: string
  known_facts: Fact[]
  graph_connections: Connection[]
  research_hypotheses: Hypothesis[]
  supporting_evidence: Evidence[]
  contradictory_evidence: Evidence[]
  evidence_gaps: string[]
  verification_needed: string[]
  entity_a_matches: Match[]
  entity_b_matches: Match[]
  entity_a_identity: Identity | null
  entity_b_identity: Identity | null
  entity_a_other_matches: Identity[]
  entity_b_other_matches: Identity[]
  model_usage: ModelUsage
  causal_conclusion: boolean
  written_to_graph: boolean
  l2_physical_validation: string
}

interface Fact {
  statement: string
  kind: string
  causal_conclusion: boolean
}

interface Connection {
  hops: number
  causal_conclusion: boolean
  steps: Step[]
}

interface Step {
  relation: string
  source_label: string
  target_label: string
  traversal: string
  causal_conclusion: boolean
}

interface Hypothesis {
  cause_label: string
  effect_label: string
  basis: string
  uncertainty: string
  validation_status: string
  evidence_note: string
  causal_conclusion: boolean
  in_graph: boolean
}

interface Evidence {
  document: string
  section: string
  locator: string
  snippet: string
  direct_mention: boolean
}

interface Match {
  node_id: string
  label: string
  entity_type: string
  scope: string
}

interface ModelUsage {
  called: boolean
  call_count: number
  reason: string
  sent_evidence_ids: string[]
  authorized_grant_ids: string[]
  http_allow_external_honored: boolean
}

interface PhysicalCheck {
  check_id: string
  title: string
  status: string
  inputs: Record<string, unknown>
  basis: string
  limit: string
  computed: Record<string, unknown>
  supports_fault: boolean
  supports_causal_hypothesis: boolean
}

interface ValidationReport {
  record_id: string | null
  record_found: boolean
  record_message: string
  entity_a_identity: Identity | null
  entity_b_identity: Identity | null
  entity_a_other_matches: Identity[]
  entity_b_other_matches: Identity[]
  physical_checks: PhysicalCheck[]
  missing_measurements: string[]
  temporal_validation: { reason?: string; conflicts_merged?: boolean; causal_order?: string }
  overall_status: string
  causal_validation_status: string
  causal_conclusion: boolean
  validation_note: string
}

function isApiError(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return record.success === false && typeof record.message === "string"
}

function textOf(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function boolOf(value: unknown): boolean {
  return value === true
}

function numberOf(value: unknown): number {
  return typeof value === "number" ? value : 0
}

async function postJson(url: string, payload: object): Promise<unknown> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(isApiError(body) ? body.message : `请求失败，HTTP ${response.status}`)
  }
  if (isApiError(body)) {
    throw new Error(body.message)
  }
  return body
}

function parseResearch(value: unknown): ResearchReport {
  if (typeof value !== "object" || value === null) {
    throw new Error("研究结果不是有效对象。")
  }
  const record = value as Record<string, unknown>
  const usage = record.model_usage
  const model = typeof usage === "object" && usage !== null ? (usage as Record<string, unknown>) : {}
  return {
    entity_a: textOf(record.entity_a),
    entity_b: textOf(record.entity_b),
    research_status: textOf(record.research_status),
    known_facts: Array.isArray(record.known_facts) ? record.known_facts.filter(isRecord).map(parseFact) : [],
    graph_connections: Array.isArray(record.graph_connections) ? record.graph_connections.filter(isRecord).map(parseConnection) : [],
    research_hypotheses: Array.isArray(record.research_hypotheses) ? record.research_hypotheses.filter(isRecord).map(parseHypothesis) : [],
    supporting_evidence: Array.isArray(record.supporting_evidence) ? record.supporting_evidence.filter(isRecord).map(parseEvidence) : [],
    contradictory_evidence: Array.isArray(record.contradictory_evidence) ? record.contradictory_evidence.filter(isRecord).map(parseEvidence) : [],
    evidence_gaps: stringList(record.evidence_gaps),
    verification_needed: stringList(record.verification_needed),
    entity_a_matches: matchList(record.entity_a_matches),
    entity_b_matches: matchList(record.entity_b_matches),
    entity_a_identity: parseIdentity(record.entity_a_identity),
    entity_b_identity: parseIdentity(record.entity_b_identity),
    entity_a_other_matches: identityList(record.entity_a_other_matches),
    entity_b_other_matches: identityList(record.entity_b_other_matches),
    model_usage: {
      called: boolOf(model.called),
      call_count: numberOf(model.call_count),
      reason: textOf(model.reason),
      sent_evidence_ids: stringList(model.sent_evidence_ids),
      authorized_grant_ids: stringList(model.authorized_grant_ids),
      http_allow_external_honored: boolOf(model.http_allow_external_honored),
    },
    causal_conclusion: boolOf(record.causal_conclusion),
    written_to_graph: boolOf(record.written_to_graph),
    l2_physical_validation: textOf(record.l2_physical_validation),
  }
}

function parseValidation(value: unknown): ValidationReport {
  if (typeof value !== "object" || value === null) {
    throw new Error("校验结果不是有效对象。")
  }
  const record = value as Record<string, unknown>
  const temporal = isRecord(record.temporal_validation) ? record.temporal_validation : {}
  return {
    record_id: textOf(record.record_id) || null,
    record_found: record.record_found !== false,
    record_message: textOf(record.record_message),
    entity_a_identity: parseIdentity(record.entity_a_identity),
    entity_b_identity: parseIdentity(record.entity_b_identity),
    entity_a_other_matches: identityList(record.entity_a_other_matches),
    entity_b_other_matches: identityList(record.entity_b_other_matches),
    physical_checks: Array.isArray(record.physical_checks) ? record.physical_checks.filter(isRecord).map(parseCheck) : [],
    missing_measurements: stringList(record.missing_measurements),
    temporal_validation: {
      reason: textOf(temporal.reason),
      conflicts_merged: boolOf(temporal.conflicts_merged),
      causal_order: textOf(temporal.causal_order),
    },
    overall_status: textOf(record.overall_status),
    causal_validation_status: textOf(record.causal_validation_status),
    causal_conclusion: boolOf(record.causal_conclusion),
    validation_note: textOf(record.validation_note),
  }
}

function parseFact(item: Record<string, unknown>): Fact {
  return { statement: textOf(item.statement), kind: textOf(item.kind), causal_conclusion: boolOf(item.causal_conclusion) }
}

function parseConnection(item: Record<string, unknown>): Connection {
  const steps = Array.isArray(item.steps) ? item.steps.filter(isRecord).map(parseStep) : []
  return { hops: numberOf(item.hops), causal_conclusion: boolOf(item.causal_conclusion), steps }
}

function parseStep(item: Record<string, unknown>): Step {
  return {
    relation: textOf(item.relation),
    source_label: textOf(item.source_label),
    target_label: textOf(item.target_label),
    traversal: textOf(item.traversal),
    causal_conclusion: boolOf(item.causal_conclusion),
  }
}

function parseHypothesis(item: Record<string, unknown>): Hypothesis {
  return {
    cause_label: textOf(item.cause_label),
    effect_label: textOf(item.effect_label),
    basis: textOf(item.basis),
    uncertainty: textOf(item.uncertainty),
    validation_status: textOf(item.validation_status) || "unverified",
    evidence_note: textOf(item.evidence_note),
    causal_conclusion: boolOf(item.causal_conclusion),
    in_graph: boolOf(item.in_graph),
  }
}

function parseEvidence(item: Record<string, unknown>): Evidence {
  return {
    document: textOf(item.document),
    section: textOf(item.section),
    locator: textOf(item.locator),
    snippet: textOf(item.snippet),
    direct_mention: boolOf(item.direct_mention),
  }
}

function parseCheck(item: Record<string, unknown>): PhysicalCheck {
  return {
    check_id: textOf(item.check_id),
    title: textOf(item.title),
    status: textOf(item.status),
    inputs: isRecord(item.inputs) ? item.inputs : {},
    basis: textOf(item.basis),
    limit: textOf(item.limit),
    computed: isRecord(item.computed) ? item.computed : {},
    supports_fault: boolOf(item.supports_fault),
    supports_causal_hypothesis: boolOf(item.supports_causal_hypothesis),
  }
}

function parseIdentity(value: unknown): Identity | null {
  if (!isRecord(value) || !textOf(value.node_id)) {
    return null
  }
  return {
    node_id: textOf(value.node_id),
    label: textOf(value.label),
    entity_type: textOf(value.entity_type),
    scope: textOf(value.scope) || "归属未标明",
    parent_id: textOf(value.parent_id),
  }
}

function identityList(value: unknown): Identity[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.map(parseIdentity).filter((item): item is Identity => item !== null)
}

function requestIdentity(matches: NameMatch[], choiceId: string, idKey: "entity_a_id" | "entity_b_id", scopeKey: "entity_a_scope" | "entity_b_scope"): Record<string, string> {
  const chosen = researchIdentity(matches, choiceId)
  if (!chosen) {
    return {}
  }
  return chosen.scope ? { [idKey]: chosen.id, [scopeKey]: chosen.scope } : { [idKey]: chosen.id }
}

function matchList(value: unknown): Match[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter(isRecord).map((item) => ({
    node_id: textOf(item.node_id),
    label: textOf(item.label),
    entity_type: textOf(item.entity_type),
    scope: textOf(item.scope) || "归属未标明",
  }))
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function locationText(item: Evidence): string {
  if (!item.document && !item.section && !item.locator) {
    return "没有真实原文定位。"
  }
  return `${item.document || "未记录文件"} · ${item.section || "未记录章节"} · ${item.locator || "未记录位置"}`
}

function valueText(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return ""
}

export function CrossResearchPanel({
  graph,
  selected,
}: {
  graph: KnowledgeGraph
  selected: SelectedNode | null
}) {
  const [entityA, setEntityA] = useState("")
  const [entityB, setEntityB] = useState("")
  const [choiceA, setChoiceA] = useState("")
  const [choiceB, setChoiceB] = useState("")
  const [recordId, setRecordId] = useState("")
  const [devices, setDevices] = useState<DatasetDevices["devices"]>([])
  const [deviceId, setDeviceId] = useState("")
  const [records, setRecords] = useState<DatasetRecords["records"]>([])
  const [research, setResearch] = useState<ResearchReport | null>(null)
  const [validation, setValidation] = useState<ValidationReport | null>(null)
  const [researchError, setResearchError] = useState("")
  const [validationError, setValidationError] = useState("")
  const [researching, setResearching] = useState(false)
  const [validating, setValidating] = useState(false)

  const matchesA = useMemo(() => nameMatches(graph, entityA), [graph, entityA])
  const matchesB = useMemo(() => nameMatches(graph, entityB), [graph, entityB])
  const ready = choiceReady(matchesA, choiceA) && choiceReady(matchesB, choiceB) && entityA.trim() !== "" && entityB.trim() !== ""

  useEffect(() => {
    const controller = new AbortController()
    void fetch("/api/dataset/devices", { signal: controller.signal })
      .then((response) => response.json())
      .then((body: unknown) => {
        if (isRecord(body) && Array.isArray(body.devices)) {
          setDevices(body.devices.filter(isRecord).map((item) => ({
            device_id: textOf(item.device_id),
            equipment_category: textOf(item.equipment_category),
            sheet_name: textOf(item.sheet_name),
            record_count: numberOf(item.record_count),
            time_start: textOf(item.time_start) || null,
            time_end: textOf(item.time_end) || null,
            duplicate_timestamp_groups: numberOf(item.duplicate_timestamp_groups),
          })))
        }
      })
      .catch(() => setDevices([]))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!deviceId) {
      return
    }
    const controller = new AbortController()
    const url = `/api/dataset/records?device_id=${encodeURIComponent(deviceId)}&limit=20`
    void fetch(url, { signal: controller.signal })
      .then((response) => response.json())
      .then((body: unknown) => {
        if (isRecord(body) && Array.isArray(body.records)) {
          setRecords(body.records.filter(isRecord).map((item) => ({
            record_id: textOf(item.record_id),
            excel_row: numberOf(item.excel_row),
            sheet_name: textOf(item.sheet_name),
            device_id: textOf(item.device_id),
            equipment_category: textOf(item.equipment_category),
            collected_at: textOf(item.collected_at),
            duplicate_timestamp: boolOf(item.duplicate_timestamp),
            same_timestamp_count: numberOf(item.same_timestamp_count),
            values: {},
          })))
        }
      })
      .catch(() => setRecords([]))
    return () => controller.abort()
  }, [deviceId])

  function assign(side: "a" | "b") {
    if (!selected) {
      return
    }
    const matches = nameMatches(graph, selected.label)
    if (side === "a") {
      setEntityA(selected.label)
      setChoiceA(matches.some((item) => item.id === selected.id) ? selected.id : "")
      return
    }
    setEntityB(selected.label)
    setChoiceB(matches.some((item) => item.id === selected.id) ? selected.id : "")
  }

  async function startResearch() {
    setResearching(true)
    setResearchError("")
    setResearch(null)
    setValidation(null)
    setValidationError("")
    try {
      const body = await postJson("/api/knowledge/research/reason", {
        entity_a: entityA.trim(),
        entity_b: entityB.trim(),
        ...requestIdentity(matchesA, choiceA, "entity_a_id", "entity_a_scope"),
        ...requestIdentity(matchesB, choiceB, "entity_b_id", "entity_b_scope"),
      })
      setResearch(parseResearch(body))
    } catch (error) {
      setResearchError(error instanceof Error ? error.message : "研究请求失败")
    } finally {
      setResearching(false)
    }
  }

  async function startValidation() {
    setValidating(true)
    setValidationError("")
    setValidation(null)
    try {
      const payload: { entity_a: string; entity_b: string; record_id?: string; entity_a_id?: string; entity_b_id?: string; entity_a_scope?: string; entity_b_scope?: string } = {
        entity_a: entityA.trim(),
        entity_b: entityB.trim(),
        ...requestIdentity(matchesA, choiceA, "entity_a_id", "entity_a_scope"),
        ...requestIdentity(matchesB, choiceB, "entity_b_id", "entity_b_scope"),
      }
      if (recordId.trim()) {
        payload.record_id = recordId.trim()
      }
      const body = await postJson("/api/knowledge/research/validate", payload)
      setValidation(parseValidation(body))
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : "校验请求失败")
    } finally {
      setValidating(false)
    }
  }

  return (
    <div className="research-panel">
      <p className="hint side-hint">页面不能授权资料外发，也不能把研究假设写入图谱。</p>
      <div className="research-actions">
        <button type="button" className="ghost-button" onClick={() => assign("a")} disabled={!selected}>
          当前节点作为A
        </button>
        <button type="button" className="ghost-button" onClick={() => assign("b")} disabled={!selected}>
          当前节点作为B
        </button>
      </div>
      <label className="field-label" htmlFor="research-entity-a">
        研究对象A
      </label>
      <input id="research-entity-a" className="graph-search" value={entityA} onChange={(event) => { setEntityA(event.target.value); setChoiceA("") }} />
      <ScopeChoices name="research-a" label={entityA} matches={matchesA} choice={choiceA} onChoose={setChoiceA} />
      <label className="field-label" htmlFor="research-entity-b">
        研究对象B
      </label>
      <input id="research-entity-b" className="graph-search" value={entityB} onChange={(event) => { setEntityB(event.target.value); setChoiceB("") }} />
      <ScopeChoices name="research-b" label={entityB} matches={matchesB} choice={choiceB} onChoose={setChoiceB} />
      <button type="button" className="ghost-button research-start" onClick={() => void startResearch()} disabled={!ready || researching}>
        {researching ? "正在研究" : "开始研究"}
      </button>
      {researchError && <p className="status error" role="alert">{researchError}</p>}
      {research && <ResearchResult report={research} localA={matchesA.find((item) => item.id === choiceA)} localB={matchesB.find((item) => item.id === choiceB)} />}
      <h3>L2观测校验</h3>
      <label className="field-label" htmlFor="research-device">
        观测设备
      </label>
      <select id="research-device" className="graph-search" value={deviceId} onChange={(event) => { setDeviceId(event.target.value); setRecords([]) }}>
        <option value="">不选择设备</option>
        {devices.map((item) => (
          <option key={item.device_id} value={item.device_id}>
            {item.device_id} · {item.equipment_category}
          </option>
        ))}
      </select>
      <label className="field-label" htmlFor="research-record">
        记录编号
      </label>
      <input id="research-record" className="graph-search" list="research-record-options" value={recordId} placeholder="可留空，或输入 REC- 记录编号" onChange={(event) => setRecordId(event.target.value)} />
      <datalist id="research-record-options">
        {records.map((item) => (
          <option key={item.record_id} value={item.record_id}>{item.collected_at}</option>
        ))}
      </datalist>
      <button type="button" className="ghost-button" onClick={() => void startValidation()} disabled={!entityA.trim() || !entityB.trim() || validating}>
        {validating ? "正在校验" : "校验观测"}
      </button>
      {validationError && <p className="status error" role="alert">{validationError}</p>}
      {validation && <ValidationResult report={validation} />}
    </div>
  )
}

function ScopeChoices({
  name,
  label,
  matches,
  choice,
  onChoose,
}: {
  name: string
  label: string
  matches: NameMatch[]
  choice: string
  onChoose: (id: string) => void
}) {
  if (!label.trim()) {
    return null
  }
  if (matches.length === 0) {
    return <p className="hint side-hint">当前图谱没有「{label.trim()}」。仍可按这个名称检索已保存文档，不会自动指定节点。</p>
  }
  if (matches.length === 1) {
    return <p className="hint side-hint">图谱中有一个「{matches[0].label}」，归属：{matches[0].scope}，节点 {matches[0].id}。</p>
  }
  return (
    <fieldset className="research-scopes">
      <legend>「{label.trim()}」有 {matches.length} 个同名节点，请先选择归属</legend>
      {matches.map((item) => (
        <label key={item.id}>
          <input type="radio" name={name} checked={choice === item.id} onChange={() => onChoose(item.id)} />
          {item.scope} · {item.typeText} · 节点 {item.id}
        </label>
      ))}
    </fieldset>
  )
}

function ResearchResult({
  report,
  localA,
  localB,
}: {
  report: ResearchReport
  localA: NameMatch | undefined
  localB: NameMatch | undefined
}) {
  const model = report.model_usage
  return (
    <div className="research-result">
      <p className={model.called ? "research-model called" : "research-model blocked"}>
        {modelStateText(model.called, model.call_count, model.reason)}
      </p>
      {model.authorized_grant_ids.length > 0 && <p className="hint side-hint">本次使用的服务器授权：{model.authorized_grant_ids.join("、")}</p>}
      <p className="hint side-hint">{report.causal_conclusion ? "服务端返回了因果标记。" : "没有已验证因果结论。"}研究状态：{report.research_status}。假设不会写入图谱。</p>
      {(localA || localB) && (
        <p className="hint side-hint">
          你确认的对象：{localA ? `A 为${localA.scope}的${localA.label}` : "A 未从同名节点中指定"}；
          {localB ? `B 为${localB.scope}的${localB.label}` : "B 未从同名节点中指定"}。服务端仍按名称返回全部匹配，没有改选。
        </p>
      )}
      <MatchList title="服务端返回的A匹配" matches={report.entity_a_matches} />
      <MatchList title="服务端返回的B匹配" matches={report.entity_b_matches} />
      <IdentityLine title="本次使用的A" identity={report.entity_a_identity} others={report.entity_a_other_matches} />
      <IdentityLine title="本次使用的B" identity={report.entity_b_identity} others={report.entity_b_other_matches} />
      <h3>已知图谱事实</h3>
      {report.known_facts.length === 0 ? <p className="hint side-hint">没有可展示的已知事实。</p> : (
        <ul className="research-list">
          {report.known_facts.map((item, index) => (
            <li key={`${item.kind}-${index}`}>{item.statement}{item.causal_conclusion ? "" : " 这不是因果结论。"}</li>
          ))}
        </ul>
      )}
      <h3>关联路径</h3>
      {report.graph_connections.length === 0 ? <p className="hint side-hint">没有已入图的关联路径。页面没有补写因果链。</p> : (
        <ul className="research-list">
          {report.graph_connections.map((path, index) => (
            <li key={`path-${index}`}>
              {path.steps.map((step, stepIndex) => (
                <p key={`${step.relation}-${stepIndex}`}>
                  {step.source_label || "未命名起点"} {step.traversal === "against_arrow" ? "←" : "→"} {relationLabel(step.relation)} {step.target_label || "未命名终点"}
                </p>
              ))}
              <p>{path.causal_conclusion ? "" : "这条路径不是因果链。"}</p>
            </li>
          ))}
        </ul>
      )}
      <h3>原始文档证据</h3>
      {report.supporting_evidence.length === 0 ? <p className="hint side-hint">没有带回文档定位。没有真实原文定位。</p> : (
        <ul className="research-list">
          {report.supporting_evidence.map((item, index) => (
            <li key={`evidence-${index}`}>
              <p>{locationText(item)}</p>
              {item.snippet ? <p>{item.snippet}</p> : <p>这条证据没有保存可核对原文。</p>}
              {item.direct_mention ? <p>原文直接出现查询名称。</p> : null}
            </li>
          ))}
        </ul>
      )}
      <h3>未验证因果假设</h3>
      {report.research_hypotheses.length === 0 ? <p className="hint side-hint">没有待验证因果假设。</p> : (
        <ul className="research-list">
          {report.research_hypotheses.map((item, index) => (
            <li key={`hypothesis-${index}`}>
              <p>未验证假设：{item.cause_label} → {item.effect_label}。这不是已验证关系。</p>
              <p>状态：{item.validation_status === "unverified" ? "未验证" : item.validation_status}。未入图。</p>
              {item.basis && <p>依据：{item.basis}</p>}
              {item.uncertainty && <p>不确定性：{item.uncertainty}</p>}
              {item.evidence_note && <p>{item.evidence_note}</p>}
            </li>
          ))}
        </ul>
      )}
      <h3>相反证据</h3>
      {report.contradictory_evidence.length === 0 ? <p className="hint side-hint">没有列出相反证据。</p> : (
        <ul className="research-list">
          {report.contradictory_evidence.map((item, index) => (
            <li key={`against-${index}`}>{locationText(item)} {item.snippet}</li>
          ))}
        </ul>
      )}
      <h3>缺失信息</h3>
      <StringList items={report.evidence_gaps} empty="没有额外缺失说明。" />
      <h3>建议继续采集或核对的内容</h3>
      <StringList items={report.verification_needed} empty="没有补充采集建议。" />
      {report.l2_physical_validation === "not_performed" && <p className="hint side-hint">这次研究还没有做 L2 校验。</p>}
    </div>
  )
}

function ValidationResult({ report }: { report: ValidationReport }) {
  return (
    <div className="research-result">
      <p className="hint side-hint">{overallStatusText(report.overall_status)}。因果校验状态：{report.causal_validation_status || "unverified"}。{report.causal_conclusion ? "" : "数值结果不构成因果证明。"}</p>
      <IdentityLine title="校验使用的A" identity={report.entity_a_identity} others={report.entity_a_other_matches} />
      <IdentityLine title="校验使用的B" identity={report.entity_b_identity} others={report.entity_b_other_matches} />
      {report.record_message && <p className="hint side-hint">{report.record_message}</p>}
      {report.validation_note && <p className="hint side-hint">{report.validation_note}</p>}
      {report.temporal_validation.reason && <p className="hint side-hint">{report.temporal_validation.reason}</p>}
      {report.physical_checks.length === 0 ? <p className="hint side-hint">没有返回数值检查。</p> : (
        <ul className="research-list">
          {report.physical_checks.map((item) => (
            <li key={item.check_id}>
              <p>{item.title || item.check_id}：{checkStatusText(item.status)}</p>
              {item.status === "consistent" && <p>这条数值一致只说明当前读数符合该约束，不是故障确诊。</p>}
              {item.basis && <p>依据：{item.basis}</p>}
              {item.limit && <p>限制：{item.limit}</p>}
              <ValueList title="输入" value={item.inputs} />
              <ValueList title="计算" value={item.computed} />
              {item.supports_fault ? null : <p>不据此认定故障。</p>}
            </li>
          ))}
        </ul>
      )}
      <h3>缺少的测量</h3>
      <StringList items={report.missing_measurements} empty="没有列出缺失测量。" />
    </div>
  )
}

function IdentityLine({ title, identity, others }: { title: string; identity: Identity | null; others: Identity[] }) {
  if (!identity && others.length === 0) {
    return null
  }
  return (
    <>
      <h3>{title}</h3>
      {identity ? (
        <p className="hint side-hint">{identity.label} · 归属 {identity.scope} · 节点 {identity.node_id}</p>
      ) : (
        <p className="hint side-hint">没有唯一节点。同名节点没有自动选择。</p>
      )}
      {others.length > 0 && (
        <ul className="research-list">
          {others.map((item) => (
            <li key={item.node_id}>未使用的同名节点：{item.label} · {item.scope} · {item.node_id}。没有合并。</li>
          ))}
        </ul>
      )}
    </>
  )
}

function MatchList({ title, matches }: { title: string; matches: Match[] }) {
  return (
    <>
      <h3>{title}</h3>
      {matches.length === 0 ? <p className="hint side-hint">没有同名图谱节点。</p> : (
        <ul className="research-list">
          {matches.map((item) => (
            <li key={item.node_id || `${item.label}-${item.scope}`}>{item.label} · {item.scope}{matches.length > 1 ? "。同名节点没有自动合并或改选。" : ""}</li>
          ))}
        </ul>
      )}
    </>
  )
}

function StringList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="hint side-hint">{empty}</p>
  }
  return (
    <ul className="research-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}

function ValueList({ title, value }: { title: string; value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, item]) => valueText(item) !== "")
  if (entries.length === 0) {
    return null
  }
  return (
    <p>
      {title}：{entries.map(([key, item]) => `${key} ${valueText(item)}`).join("；")}
    </p>
  )
}
