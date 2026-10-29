import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react"
import {
  EVIDENCE_KINDS,
  METHOD_OPTIONS,
  REFERENCE_OPTIONS,
  UNIT_OPTIONS,
  applyEvidenceKindDefaults,
  evidenceLines,
  evidenceStatusLabel,
  evidenceValueText,
  isDiagnosticEvidence,
  kindLabel,
  methodLabel,
  referenceLabel,
  unitLabel,
  type DiagnosticEvidence,
} from "./diagnosticEvidence"
import {
  diagnosisLevelLabel,
  evidenceText,
  isDiagnosisSummary,
  timelineSummary,
  timelineText,
  type DiagnosisDetail,
  type DiagnosisSummary,
} from "./diagnosisTimeline"
import {
  UNSUPPORTED_UPLOAD,
  followupLines,
  followupSummary,
  isFollowupTask,
  type FollowupTask,
} from "./followupTask"
import {
  VIBRATION_UNIT_OPTIONS,
  apiErrorMessage,
  canVerifyLabel,
  conditionChanges,
  defaultMeasuredAt,
  describeSecondHarmonic,
  fileBaseName,
  groupByMeasurement,
  isSyncMeasurement,
  metricLines,
  originLabel,
  sourceKindLabel,
  splitChecks,
  statusLabel,
  syncPhaseLines,
  unitDisplay,
  type PhysicalCheck,
  type SupplementMeasurement,
  type SyncMeasurement,
} from "./supplementMeasurement"

type PanelTab = "followups" | "data" | "physics" | "history"
type AddFormKind = null | "single" | "synchronous" | "structured"

interface ReassessHypothesis {
  hypothesis_id?: string
  faults?: string[]
  version?: number
  verification_status?: string
  revision_reason?: string
  opposing_evidence?: { evidence_id?: string; detail?: string }[]
  supporting_evidence?: { evidence_id?: string; detail?: string }[]
}

export function SupplementaryMeasurementPanel({
  recordId,
  deviceId,
  collectedAt,
}: {
  recordId: string
  deviceId: string
  collectedAt: string
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<PanelTab>("followups")
  const [addForm, setAddForm] = useState<AddFormKind>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [measuredAt, setMeasuredAt] = useState(collectedAt)
  const [sensorPosition, setSensorPosition] = useState("")
  const [samplingRate, setSamplingRate] = useState("")
  const [unit, setUnit] = useState("mm/s")
  const [dataOrigin, setDataOrigin] = useState("uploaded_csv")
  const [channelX, setChannelX] = useState("x")
  const [channelY, setChannelY] = useState("y")
  const [file, setFile] = useState<File | null>(null)
  const [measurements, setMeasurements] = useState<SupplementMeasurement[]>([])
  const [syncMeasurements, setSyncMeasurements] = useState<SyncMeasurement[]>([])
  const [checks, setChecks] = useState<PhysicalCheck[]>([])
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")
  const [hypotheses, setHypotheses] = useState<ReassessHypothesis[]>([])
  const [stopReason, setStopReason] = useState("")
  const [history, setHistory] = useState<DiagnosisSummary[]>([])
  const [selectedId, setSelectedId] = useState("")
  const [detail, setDetail] = useState<DiagnosisDetail | null>(null)
  const [followups, setFollowups] = useState<FollowupTask[]>([])
  const [evidenceRows, setEvidenceRows] = useState<DiagnosticEvidence[]>([])
  const [evidenceKind, setEvidenceKind] = useState("axial_eccentricity")
  const [evidenceValue, setEvidenceValue] = useState("")
  const [evidenceUnit, setEvidenceUnit] = useState("um")
  const [evidenceMethod, setEvidenceMethod] = useState("runout_measurement")
  const [evidenceReference, setEvidenceReference] = useState("shaft_axis")
  const [evidencePosition, setEvidencePosition] = useState("轴向探头")
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setMeasuredAt(defaultMeasuredAt(collectedAt))
    setChecks([])
    setHypotheses([])
    setStopReason("")
    setHistory([])
    setSelectedId("")
    setDetail(null)
    setFollowups([])
    setSyncMeasurements([])
    setEvidenceRows([])
    setError("")
    setNotice("")
    setFile(null)
    setAddForm(null)
    setTab("followups")
    setExpanded({})
  }, [recordId, deviceId, collectedAt])

  useEffect(() => {
    if (!open) {
      return
    }
    const controller = new AbortController()
    void loadList(controller.signal)
    void loadHistory(controller.signal)
    void loadFollowups(controller.signal)
    void loadEvidence(controller.signal)
    return () => controller.abort()
  }, [open, recordId])

  const pendingFollowups = useMemo(
    () => followups.filter((task) => task.status === "待补测" || task.status === "已校验但条件不足" || task.status === "已上传待校验").length,
    [followups],
  )
  const latestHistory = history[0]
  const conclusion = stopReason
    || latestHistory?.stop_reason
    || (latestHistory?.no_hypothesis ? "尚无故障假设" : "")
    || (latestHistory ? diagnosisLevelLabel(latestHistory.diagnosis_level) : "尚未重新评估")
  const confirmed = latestHistory?.confirmed === true

  async function loadList(signal: AbortSignal): Promise<void> {
    const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/supplementary-vibration`, { signal })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      setError(apiErrorMessage(body, "没有读到补充测量列表。"))
      return
    }
    const record = body && typeof body === "object" ? (body as { measurements?: unknown; synchronous_measurements?: unknown }) : {}
    setMeasurements(Array.isArray(record.measurements) ? record.measurements.filter(isMeasurement) : [])
    setSyncMeasurements(Array.isArray(record.synchronous_measurements) ? record.synchronous_measurements.filter(isSyncMeasurement) : [])
  }

  async function loadHistory(signal: AbortSignal): Promise<void> {
    const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/diagnosis-history`, { signal })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      setError(apiErrorMessage(body, "没有读到诊断历史。"))
      return
    }
    const rows = body && typeof body === "object" ? (body as { assessments?: unknown }).assessments : []
    setHistory(Array.isArray(rows) ? rows.filter(isDiagnosisSummary) : [])
  }

  async function loadEvidence(signal?: AbortSignal): Promise<void> {
    const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/diagnostic-evidence`, { signal })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      setError(apiErrorMessage(body, "没有读到其他测量证据。"))
      return
    }
    const rows = body && typeof body === "object" ? (body as { evidence?: unknown }).evidence : []
    setEvidenceRows(Array.isArray(rows) ? rows.filter(isDiagnosticEvidence) : [])
  }

  async function loadFollowups(signal?: AbortSignal): Promise<void> {
    const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/followup-tasks`, { signal })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      setError(apiErrorMessage(body, "没有读到补测任务。"))
      return
    }
    const rows = body && typeof body === "object" ? (body as { tasks?: unknown }).tasks : []
    setFollowups(Array.isArray(rows) ? rows.filter(isFollowupTask) : [])
  }

  async function onGenerateFollowups(): Promise<void> {
    setError("")
    setBusy("正在生成补测建议")
    try {
      const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/followup-tasks`, { method: "POST" })
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        setError(apiErrorMessage(body, "补测建议没有生成。"))
        return
      }
      const rows = body && typeof body === "object" ? (body as { tasks?: unknown }).tasks : []
      setFollowups(Array.isArray(rows) ? rows.filter(isFollowupTask) : [])
      setNotice("补测建议只来自当前线索、未验证假设和物理校验缺口。它不是故障确诊，也没有写入知识图谱。")
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  async function openAssessment(assessmentId: string): Promise<void> {
    if (selectedId === assessmentId) {
      setSelectedId("")
      setDetail(null)
      return
    }
    setError("")
    setBusy("正在读取证据")
    try {
      const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/diagnosis-history/${encodeURIComponent(assessmentId)}`)
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok || !isDiagnosisSummary(body)) {
        setError(apiErrorMessage(body, "没有读到这次评估的证据。"))
        return
      }
      setSelectedId(assessmentId)
      setDetail(body as DiagnosisDetail)
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  async function onRegisterEvidence(event: FormEvent): Promise<void> {
    event.preventDefault()
    setError("")
    setNotice("")
    if (measuredAt.trim() !== collectedAt.trim()) {
      setError("测量时间与这条记录的采集时间不一致，不能登记证据。")
      return
    }
    const payload: Record<string, unknown> = {
      device_id: deviceId,
      measured_at: measuredAt.trim(),
      evidence_kind: evidenceKind,
      sensor_position: evidencePosition.trim(),
      method: evidenceMethod.trim(),
      reference_basis: evidenceReference.trim(),
      data_origin: dataOrigin === "synthetic_test_signal" ? "synthetic_test_signal" : "uploaded_measurement",
      unit: evidenceUnit.trim(),
    }
    if (evidenceKind.startsWith("axial_")) {
      payload.value = Number(evidenceValue)
    } else if (evidenceKind === "load_1x_series") {
      payload.observations = [
        {
          load: Number(evidenceValue || "1"),
          load_unit: "%",
          amplitude_1x: Number(evidenceUnit === "mm/s" ? evidenceValue || "1" : "1"),
          amplitude_unit: "mm/s",
          phase_1x_deg: null,
          keyphasor_present: false,
        },
      ]
      payload.unit = "mm/s"
    } else {
      payload.peaks = [{ frequency_hz: Number(evidenceValue || "25"), amplitude: 1, label: "registered" }]
    }
    setBusy("正在登记证据")
    try {
      const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/diagnostic-evidence`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        setError(apiErrorMessage(body, "结构化证据没有保存。"))
        return
      }
      setNotice("结构化证据已登记。登记不等于条款已满足，也不等于故障确诊。")
      setAddForm(null)
      await loadEvidence()
      await loadFollowups()
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  async function onUpload(event: FormEvent): Promise<void> {
    event.preventDefault()
    setError("")
    setNotice("")
    if (!file) {
      setError("请选择振动 CSV。")
      return
    }
    if (!samplingRate.trim() || !measuredAt.trim()) {
      setError("请填写测量时间和采样频率。")
      return
    }
    if (addForm === "single" && !sensorPosition.trim()) {
      setError("请填写测量时间、传感器位置和采样频率。")
      return
    }
    if (measuredAt.trim() !== collectedAt.trim()) {
      setError("测量时间与这条记录的采集时间不一致。历史回放不能把其他时间的 CSV 绑定到该记录。")
      return
    }
    const params = new URLSearchParams({
      device_id: deviceId,
      measured_at: measuredAt.trim(),
      sampling_rate: samplingRate.trim(),
      unit,
      file_name: fileBaseName(file.name),
      data_origin: dataOrigin,
    })
    let path = `/api/dataset/records/${encodeURIComponent(recordId)}/supplementary-vibration`
    if (addForm === "single") {
      params.set("sensor_position", sensorPosition.trim())
      params.set("column", "value")
    } else {
      path = `/api/dataset/records/${encodeURIComponent(recordId)}/synchronous-vibration`
      params.set("sync_method", "shared_time_column")
      params.set("time_base", "relative_seconds")
      params.set(
        "channels",
        JSON.stringify([
          { column: channelX.trim() || "x", name: "X", direction: "X", sensor_position: "驱动端水平" },
          { column: channelY.trim() || "y", name: "Y", direction: "Y", sensor_position: "驱动端垂直" },
        ]),
      )
    }
    setBusy("正在上传")
    try {
      const response = await fetch(`${path}?${params.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body: await file.arrayBuffer(),
      })
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        setError(apiErrorMessage(body, addForm === "synchronous" ? "同步振动测量没有保存。" : "补充测量没有保存。"))
        return
      }
      const saved = body && typeof body === "object" ? (body as { duplicate?: boolean; measurement?: unknown }).measurement : null
      if (addForm === "synchronous" && isSyncMeasurement(saved)) {
        setNotice(
          body && typeof body === "object" && (body as { duplicate?: boolean }).duplicate
            ? "相同同步文件已存在，没有新增记录。"
            : "同步多通道测量已保存。通道间相位差不是键相绝对相位，也不是故障确诊。",
        )
      } else if (isMeasurement(saved)) {
        setNotice(body && typeof body === "object" && (body as { duplicate?: boolean }).duplicate ? "相同文件已存在，没有新增记录。" : "补充测量已保存。它不会覆盖官方记录。")
      }
      setAddForm(null)
      setFile(null)
      await loadList(new AbortController().signal)
      await loadFollowups()
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  async function onRevalidate(): Promise<void> {
    setError("")
    setBusy("正在校验")
    try {
      const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/physical-revalidation`, { method: "POST" })
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        setError(apiErrorMessage(body, "物理校验没有完成。"))
        return
      }
      const report = body && typeof body === "object" ? (body as { report?: { physical_checks?: unknown } }).report : null
      const rows = report?.physical_checks
      setChecks(Array.isArray(rows) ? rows.filter(isCheck) : [])
      await loadFollowups()
      setNotice("已重新执行物理校验。数值结果不是故障确诊。")
      setTab("physics")
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  async function onReassess(): Promise<void> {
    setError("")
    setBusy("正在评估")
    try {
      const response = await fetch(`/api/dataset/records/${encodeURIComponent(recordId)}/fault-reassessment`, { method: "POST" })
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        setError(apiErrorMessage(body, "故障重新评估没有完成。"))
        return
      }
      const record = body && typeof body === "object" ? (body as { hypotheses?: unknown; stop_reason?: unknown }) : {}
      setHypotheses(Array.isArray(record.hypotheses) ? record.hypotheses.filter(isHypothesis) : [])
      setStopReason(typeof record.stop_reason === "string" ? record.stop_reason : "")
      setNotice("重新评估只读取本地工具和已关联的补充测量，没有把 CSV 发给外部模型。结果不是确诊。")
      await loadHistory(new AbortController().signal)
      setTab("history")
    } catch {
      setError("无法连接后端。请确认本机后端已启动。")
    } finally {
      setBusy("")
    }
  }

  function toggleExpand(id: string): void {
    setExpanded((current) => ({ ...current, [id]: !current[id] }))
  }

  function openAddForm(kind: AddFormKind, evidenceTarget?: string): void {
    setTab("data")
    setAddForm(kind)
    setError("")
    setNotice("")
    if (kind === "structured") {
      const next = evidenceTarget || "axial_eccentricity"
      const defaults = applyEvidenceKindDefaults(next)
      setEvidenceKind(next)
      setEvidenceMethod(defaults.method)
      setEvidenceReference(defaults.reference)
      setEvidenceUnit(defaults.unit)
    }
  }

  const parts = splitChecks(checks)
  const changes = conditionChanges(checks)
  const harmonic = checks.length ? describeSecondHarmonic(checks, measurements) : null
  const groups = groupByMeasurement(metricLines(checks, measurements))
  const methodChoices = METHOD_OPTIONS[evidenceKind] || []
  const referenceChoices = REFERENCE_OPTIONS[evidenceKind] || []

  return (
    <section className="diagnosis-block supplement-panel" aria-label="补充测量">
      <div className="supplement-head">
        <h3>补充测量</h3>
        <button type="button" className="ghost-button" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
          {open ? "收起" : "展开"}
        </button>
      </div>
      <p className="monitor-note">历史数据回放不等于实时监测。测量时间必须与记录采集时间一致。</p>
      {open && (
        <>
          <div className="supplement-summary" aria-label="诊断摘要">
            <SummaryItem label="设备" value={deviceId} />
            <SummaryItem label="记录时间" value={collectedAt} />
            <SummaryItem label="记录编号" value={recordId} />
            <SummaryItem label="补测任务" value={`${followups.length} 项 / 待处理 ${pendingFollowups}`} />
            <SummaryItem label="补充数据" value={`${measurements.length + syncMeasurements.length + evidenceRows.length} 条`} />
            <SummaryItem label="当前结论" value={conclusion} />
            <SummaryItem label="确诊状态" value={confirmed ? "已确诊" : "故障尚未确诊"} />
          </div>

          <div className="supplement-tabs" role="tablist" aria-label="补充测量分区">
            <TabButton active={tab === "followups"} onClick={() => setTab("followups")}>建议补测</TabButton>
            <TabButton active={tab === "data"} onClick={() => setTab("data")}>测量数据</TabButton>
            <TabButton active={tab === "physics"} onClick={() => setTab("physics")}>物理校验</TabButton>
            <TabButton active={tab === "history"} onClick={() => setTab("history")}>诊断历史</TabButton>
          </div>

          {error && <p className="status error" role="alert">{error}</p>}
          {notice && <p className="monitor-note">{notice}</p>}

          {tab === "followups" && (
            <section className="supplement-section" aria-label="建议补测">
              <div className="supplement-toolbar">
                <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void onGenerateFollowups()}>
                  {busy === "正在生成补测建议" ? "正在生成..." : "根据当前证据生成补测建议"}
                </button>
              </div>
              {followups.length === 0 ? (
                <p className="status">还没有补测任务。没有依据时不会自动列出知识库中的全部测量项。</p>
              ) : (
                <div className="supplement-table-wrap">
                  <table className="supplement-table">
                    <thead>
                      <tr>
                        <th>测量项目</th>
                        <th>优先级</th>
                        <th>状态</th>
                        <th>缺失原因</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {followups.map((task) => {
                        const row = followupSummary(task)
                        return (
                          <tr key={task.task_id}>
                            <td>{row.item}</td>
                            <td>{row.priority}</td>
                            <td><StatusPill text={row.status} /></td>
                            <td className="cell-clip">{row.missing}</td>
                            <td>
                              <button type="button" className="link-button" onClick={() => toggleExpand(`fu-${task.task_id}`)}>
                                {expanded[`fu-${task.task_id}`] ? "收起详情" : "展开详情"}
                              </button>
                              {task.upload_supported ? (
                                <button
                                  type="button"
                                  className="link-button"
                                  onClick={() => {
                                    if (task.upload_mode === "structured") {
                                      openAddForm("structured", "axial_eccentricity")
                                      return
                                    }
                                    openAddForm(task.upload_mode === "synchronous" ? "synchronous" : "single")
                                  }}
                                >
                                  {task.upload_mode === "synchronous"
                                    ? "去上传同步多通道 CSV"
                                    : task.upload_mode === "structured"
                                      ? "去登记轴向偏心证据"
                                      : "去上传振动 CSV"}
                                </button>
                              ) : (
                                <span className="muted-inline">{UNSUPPORTED_UPLOAD}</span>
                              )}
                              {expanded[`fu-${task.task_id}`] && (
                                <div className="detail-fold">
                                  {followupLines(task).map((line) => (
                                    <p key={line}>{line}</p>
                                  ))}
                                </div>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {tab === "data" && (
            <section className="supplement-section" aria-label="测量数据">
              <div className="supplement-toolbar">
                <span className="toolbar-label">新增测量</span>
                <button type="button" className={`ghost-button${addForm === "single" ? " is-active" : ""}`} onClick={() => openAddForm(addForm === "single" ? null : "single")}>
                  单通道振动 CSV
                </button>
                <button type="button" className={`ghost-button${addForm === "synchronous" ? " is-active" : ""}`} onClick={() => openAddForm(addForm === "synchronous" ? null : "synchronous")}>
                  同步多通道 CSV
                </button>
                <button type="button" className={`ghost-button${addForm === "structured" ? " is-active" : ""}`} onClick={() => openAddForm(addForm === "structured" ? null : "structured")}>
                  其他测量证据
                </button>
              </div>

              {addForm === "single" || addForm === "synchronous" ? (
                <form className="supplement-form" onSubmit={(event) => void onUpload(event)}>
                  <label className="field-label">
                    振动 CSV
                    <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
                  </label>
                  <label className="field-label">
                    测量时间
                    <input value={measuredAt} onChange={(event) => setMeasuredAt(event.target.value)} />
                  </label>
                  {addForm === "single" ? (
                    <label className="field-label">
                      传感器位置
                      <input value={sensorPosition} onChange={(event) => setSensorPosition(event.target.value)} placeholder="例如：驱动端水平" />
                    </label>
                  ) : (
                    <>
                      <label className="field-label">
                        X 通道列名
                        <input value={channelX} onChange={(event) => setChannelX(event.target.value)} />
                      </label>
                      <label className="field-label">
                        Y 通道列名
                        <input value={channelY} onChange={(event) => setChannelY(event.target.value)} />
                      </label>
                      <p className="monitor-note form-span">同步 CSV 必须含时间列与两路通道。没有键相时只能算通道间相位差。</p>
                    </>
                  )}
                  <label className="field-label">
                    采样频率 Hz
                    <input value={samplingRate} inputMode="decimal" onChange={(event) => setSamplingRate(event.target.value)} />
                  </label>
                  <label className="field-label">
                    单位
                    <select value={unit} onChange={(event) => setUnit(event.target.value)}>
                      {VIBRATION_UNIT_OPTIONS.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    数据来源
                    <select value={dataOrigin} onChange={(event) => setDataOrigin(event.target.value)}>
                      <option value="uploaded_csv">用户补充数据</option>
                      <option value="synthetic_test_signal">模拟测试数据</option>
                    </select>
                  </label>
                  <div className="form-actions form-span">
                    <button type="submit" className="test-button" disabled={busy !== ""}>
                      {busy === "正在上传" ? "正在上传..." : addForm === "synchronous" ? "上传同步多通道" : "上传并关联"}
                    </button>
                    <button type="button" className="ghost-button" onClick={() => setAddForm(null)}>取消</button>
                  </div>
                </form>
              ) : null}

              {addForm === "structured" ? (
                <form className="supplement-form" onSubmit={(event) => void onRegisterEvidence(event)}>
                  <label className="field-label">
                    证据类型
                    <select
                      value={evidenceKind}
                      onChange={(event) => {
                        const next = event.target.value
                        const defaults = applyEvidenceKindDefaults(next)
                        setEvidenceKind(next)
                        setEvidenceMethod(defaults.method)
                        setEvidenceReference(defaults.reference)
                        setEvidenceUnit(defaults.unit)
                      }}
                    >
                      {EVIDENCE_KINDS.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    数值 / 频率 / 负荷
                    <input value={evidenceValue} onChange={(event) => setEvidenceValue(event.target.value)} />
                  </label>
                  <label className="field-label">
                    单位
                    <select value={evidenceUnit} onChange={(event) => setEvidenceUnit(event.target.value)}>
                      {UNIT_OPTIONS.filter((item, index, list) => list.findIndex((row) => row.label === item.label) === index).map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    测量方法
                    <select value={evidenceMethod} onChange={(event) => setEvidenceMethod(event.target.value)}>
                      {methodChoices.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    参考基准
                    <select value={evidenceReference} onChange={(event) => setEvidenceReference(event.target.value)}>
                      {referenceChoices.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    传感器位置
                    <input value={evidencePosition} onChange={(event) => setEvidencePosition(event.target.value)} />
                  </label>
                  <label className="field-label">
                    数据来源
                    <select value={dataOrigin} onChange={(event) => setDataOrigin(event.target.value)}>
                      <option value="uploaded_csv">用户补充数据</option>
                      <option value="synthetic_test_signal">模拟测试数据</option>
                    </select>
                  </label>
                  <label className="field-label">
                    测量时间
                    <input value={measuredAt} onChange={(event) => setMeasuredAt(event.target.value)} />
                  </label>
                  <div className="form-actions form-span">
                    <button type="submit" className="test-button" disabled={busy !== ""}>
                      {busy === "正在登记证据" ? "正在登记..." : "登记结构化证据"}
                    </button>
                    <button type="button" className="ghost-button" onClick={() => setAddForm(null)}>取消</button>
                  </div>
                  <p className="monitor-note form-span">
                    当前选择：{kindLabel(evidenceKind)} · {methodLabel(evidenceMethod)} · {referenceLabel(evidenceReference)} · {unitLabel(evidenceUnit)}。
                    口头「正常」不会被接受。
                  </p>
                </form>
              ) : null}

              {measuredAt.trim() !== collectedAt.trim() && addForm && (
                <p className="status error">当前填写的测量时间与原始采集时间不同，不能关联。</p>
              )}

              <div className="supplement-table-wrap">
                <table className="supplement-table">
                  <thead>
                    <tr>
                      <th>测量类型</th>
                      <th>文件或来源</th>
                      <th>传感器位置</th>
                      <th>时间</th>
                      <th>验证状态</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {measurements.length === 0 && syncMeasurements.length === 0 && evidenceRows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="empty-cell">这条记录还没有补充测量数据。</td>
                      </tr>
                    ) : null}
                    {measurements.map((item) => {
                      const origin = originLabel(item.data_origin, item.origin_note, item.file_name)
                      return (
                        <tr key={item.measurement_id}>
                          <td>单通道振动</td>
                          <td>
                            <div>{item.file_name}</div>
                            <OriginBadge text={origin} />
                          </td>
                          <td>{item.sensor_position}</td>
                          <td>{item.measured_at}</td>
                          <td><StatusPill text="已登记" /></td>
                          <td>
                            <button type="button" className="link-button" onClick={() => toggleExpand(`m-${item.measurement_id}`)}>
                              {expanded[`m-${item.measurement_id}`] ? "收起详情" : "展开详情"}
                            </button>
                            {expanded[`m-${item.measurement_id}`] && (
                              <div className="detail-fold">
                                <p>单位：{unitDisplay(item.unit)}</p>
                                <p>采样频率：{item.sampling_rate_hz} Hz</p>
                                <p>内部编号：{item.measurement_id}</p>
                                <p>SHA-256：{item.content_sha256}</p>
                                <p>{item.origin_note}</p>
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                    {syncMeasurements.map((item) => {
                      const origin = originLabel(item.data_origin, item.origin_note, item.file_name)
                      return (
                        <tr key={item.measurement_id}>
                          <td>同步多通道振动</td>
                          <td>
                            <div>{item.file_name}</div>
                            <OriginBadge text={origin} />
                          </td>
                          <td>{item.channels.map((channel) => channel.sensor_position).join(" / ")}</td>
                          <td>{item.measured_at}</td>
                          <td>
                            <StatusPill text={item.phase?.phase_reliable ? "已获得数值证据" : "仍然证据不足"} />
                          </td>
                          <td>
                            <button type="button" className="link-button" onClick={() => toggleExpand(`s-${item.measurement_id}`)}>
                              {expanded[`s-${item.measurement_id}`] ? "收起详情" : "展开详情"}
                            </button>
                            {expanded[`s-${item.measurement_id}`] && (
                              <div className="detail-fold">
                                {syncPhaseLines(item).map((line) => (
                                  <p key={line}>{line}</p>
                                ))}
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                    {evidenceRows.map((item) => (
                      <tr key={item.evidence_id}>
                        <td>{kindLabel(item.evidence_kind)}</td>
                        <td>
                          <div>{evidenceValueText(item)}</div>
                          <OriginBadge text={originLabel(item.data_origin || "", item.origin_note)} />
                        </td>
                        <td>{item.sensor_position}</td>
                        <td>{item.measured_at}</td>
                        <td><StatusPill text={evidenceStatusLabel(item.validation_status)} /></td>
                        <td>
                          <button type="button" className="link-button" onClick={() => toggleExpand(`e-${item.evidence_id}`)}>
                            {expanded[`e-${item.evidence_id}`] ? "收起详情" : "展开详情"}
                          </button>
                          {expanded[`e-${item.evidence_id}`] && (
                            <div className="detail-fold">
                              {evidenceLines(item).map((line) => (
                                <p key={line}>{line}</p>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {tab === "physics" && (
            <section className="supplement-section" aria-label="物理校验">
              <div className="supplement-toolbar">
                <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void onRevalidate()}>
                  {busy === "正在校验" ? "正在校验..." : "重新执行物理校验"}
                </button>
                <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void onReassess()}>
                  {busy === "正在评估" ? "正在评估..." : "重新评估故障"}
                </button>
              </div>
              {checks.length === 0 ? (
                <p className="status">尚未执行物理校验。点击上方按钮后，将按官方记录与补充数据分别展示结果。</p>
              ) : (
                <>
                  <div className="supplement-table-wrap">
                    <table className="supplement-table">
                      <thead>
                        <tr>
                          <th>检查项目</th>
                          <th>实际结果</th>
                          <th>数据来源</th>
                          <th>能否验证</th>
                          <th>详情</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...parts.official, ...parts.supplement].map((check) => (
                          <tr key={check.check_id}>
                            <td>{check.title}</td>
                            <td><StatusPill text={statusLabel(check.status)} /></td>
                            <td>{sourceKindLabel(check)}</td>
                            <td>{canVerifyLabel(check.status)}</td>
                            <td>
                              <button type="button" className="link-button" onClick={() => toggleExpand(`c-${check.check_id}`)}>
                                {expanded[`c-${check.check_id}`] ? "收起详情" : "展开详情"}
                              </button>
                              {expanded[`c-${check.check_id}`] && (
                                <div className="detail-fold">
                                  <p>{check.limit}</p>
                                  {check.condition_effect ? <p>{check.condition_effect}</p> : null}
                                  {check.supports_fault ? <p>该结果不能单独作为故障确诊。</p> : <p>数值结果不是故障确诊。</p>}
                                </div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="monitor-note">原来无法验证、现在可以计算：{changes.gained.length ? changes.gained.join("、") : "无"}</p>
                  <p className="monitor-note">仍然无法验证：{changes.still.length ? changes.still.join("、") : "无"}</p>
                  {harmonic && (
                    <div className="detail-fold always-open">
                      <p>{harmonic.officialLine}</p>
                      {harmonic.supplementLines.map((line) => (
                        <p key={line}>{line}</p>
                      ))}
                    </div>
                  )}
                  {groups.length > 0 && (
                    <div className="detail-fold always-open">
                      <p className="file-meta">补充计算结果（按测量分组）</p>
                      {groups.map((group) => (
                        <p key={group.measurementId}>
                          {group.measuredAt} · {group.sensorPosition} · {unitDisplay(group.unit)} ·
                          {group.lines.map((line) => `${line.label} ${line.value}`).join("；")}
                        </p>
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>
          )}

          {tab === "history" && (
            <section className="supplement-section" aria-label="诊断历史">
              <div className="supplement-toolbar">
                <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void onReassess()}>
                  {busy === "正在评估" ? "正在评估..." : "重新评估故障"}
                </button>
              </div>
              {(stopReason || hypotheses.length > 0) && (
                <div className="history-banner">
                  <p>最近一次停止原因：{stopReason || "未返回"}</p>
                  <p>确诊状态：故障尚未确诊。假设不是确诊，也没有写入知识图谱。</p>
                  {hypotheses.length === 0 ? (
                    <p>没有故障假设。数值一致不能生成故障名称。</p>
                  ) : (
                    hypotheses.map((item) => (
                      <p key={item.hypothesis_id || (item.faults || []).join("-")}>
                        {(item.faults || []).join("、")} · {item.verification_status} · 版本 {item.version ?? "—"}
                        {item.revision_reason ? ` · ${item.revision_reason}` : ""}
                      </p>
                    ))
                  )}
                </div>
              )}
              {history.length === 0 ? (
                <p className="status">还没有已保存的故障重新评估。不会生成空白诊断历史。</p>
              ) : (
                <div className="supplement-table-wrap">
                  <table className="supplement-table">
                    <thead>
                      <tr>
                        <th>评估时间</th>
                        <th>假设变化</th>
                        <th>版本变化</th>
                        <th>停止原因</th>
                        <th>详情</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((item) => {
                        const row = timelineSummary(item)
                        return (
                          <tr key={item.assessment_id}>
                            <td>{row.time}</td>
                            <td className="cell-clip">{row.change}</td>
                            <td>{row.version}</td>
                            <td className="cell-clip">{row.stopReason}</td>
                            <td>
                              <button type="button" className="link-button" onClick={() => void openAssessment(item.assessment_id)}>
                                {selectedId === item.assessment_id ? "收起证据" : "查看证据"}
                              </button>
                              <button type="button" className="link-button" onClick={() => toggleExpand(`h-${item.assessment_id}`)}>
                                {expanded[`h-${item.assessment_id}`] ? "收起摘要" : "展开详情"}
                              </button>
                              {!row.confirmed && <OriginBadge text="故障尚未确诊" />}
                              {expanded[`h-${item.assessment_id}`] && (
                                <div className="detail-fold">
                                  {timelineText(item).map((line) => (
                                    <p key={line}>{line}</p>
                                  ))}
                                </div>
                              )}
                              {selectedId === item.assessment_id && detail && detail.assessment_id === item.assessment_id && (
                                <AssessmentEvidence detail={detail} />
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </section>
  )
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-item">
      <span className="summary-label">{label}</span>
      <strong className="summary-value">{value}</strong>
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" role="tab" aria-selected={active} className={`supplement-tab${active ? " is-active" : ""}`} onClick={onClick}>
      {children}
    </button>
  )
}

function StatusPill({ text }: { text: string }) {
  const tone =
    text.includes("不足") || text.includes("待补测") || text.includes("待验证")
      ? "warn"
      : text.includes("确诊") || text.includes("矛盾")
        ? "danger"
        : text.includes("已获得") || text.includes("已登记") || text.includes("一致") || text.includes("条款")
          ? "ok"
          : "neutral"
  return <span className={`status-pill tone-${tone}`}>{text}</span>
}

function OriginBadge({ text }: { text: string }) {
  const tone = text.includes("模拟") ? "synth" : text.includes("官方") ? "official" : "user"
  return <span className={`origin-badge tone-${tone}`}>{text}</span>
}

function AssessmentEvidence({ detail }: { detail: DiagnosisDetail }) {
  const support = detail.added_support || []
  const oppose = detail.added_oppose || []
  const checks = detail.l2?.results || []
  return (
    <div className="detail-fold">
      <p className="file-meta">本次证据</p>
      {support.length === 0 && oppose.length === 0 ? (
        <p>没有新增的支持证据或反对证据。</p>
      ) : (
        <>
          {support.map((item, index) => (
            <p key={`support-${item.evidence_id || index}`}>新增支持证据 {evidenceText(item)}</p>
          ))}
          {oppose.map((item, index) => (
            <p key={`oppose-${item.evidence_id || index}`}>新增反对证据 {evidenceText(item)}</p>
          ))}
        </>
      )}
      {(detail.hypotheses_detail || []).map((item) => (
        <p key={(item.faults || []).join("-")}>
          {(item.faults || []).join("、")} {item.verification_status} V{item.version ?? ""}
          {(item.version_history || []).length > 0
            ? `；保留 ${item.version_history?.map((entry) => `${entry.verification_status} V${entry.version}`).join("、")}`
            : ""}
        </p>
      ))}
      {checks.map((item, index) => (
        <p key={`${item.overall_status || "l2"}-${index}`}>
          L2 {item.level || "记录"} {item.overall_status || "无状态"}
          {item.consistent_checks?.length ? `；一致 ${item.consistent_checks.join("、")}` : ""}
          {item.contradicted_checks?.length ? `；矛盾 ${item.contradicted_checks.join("、")}` : ""}
          {item.insufficient_checks?.length ? `；不足 ${item.insufficient_checks.join("、")}` : ""}
        </p>
      ))}
      <p>缺失测量：{detail.missing_measurements.length ? detail.missing_measurements.join("、") : "无"}</p>
    </div>
  )
}

function isMeasurement(value: unknown): value is SupplementMeasurement {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.measurement_id === "string" && typeof row.content_sha256 === "string" && typeof row.file_name === "string"
}

function isCheck(value: unknown): value is PhysicalCheck {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const row = value as Record<string, unknown>
  return typeof row.check_id === "string" && typeof row.status === "string" && typeof row.title === "string"
}

function isHypothesis(value: unknown): value is ReassessHypothesis {
  return typeof value === "object" && value !== null
}
