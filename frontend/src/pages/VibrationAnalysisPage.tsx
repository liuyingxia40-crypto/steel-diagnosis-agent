import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react"
import type { ApiErrorResponse, VibrationAnalysisResponse } from "../types/api"
import {
  formatFileSize,
  formatMetric,
  isVibrationAnalysisResponse,
  vibrationInputError,
  vibrationSearchParams,
  type VibrationUnit,
} from "../vibration/upload"

type AnalysisState = "idle" | "loading" | "success" | "error"

const UNITS: VibrationUnit[] = ["未知", "mm/s", "m/s²"]

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return record.success === false && typeof record.message === "string"
}

function readableError(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "分析已取消。"
  }
  if (error instanceof Error) {
    return error.message === "Failed to fetch" ? "无法连接后端。请确认本机后端已启动。" : error.message
  }
  return "分析失败"
}

export function VibrationAnalysisPage() {
  const [file, setFile] = useState<File | null>(null)
  const [samplingRate, setSamplingRate] = useState("")
  const [column, setColumn] = useState("value")
  const [unit, setUnit] = useState<VibrationUnit>("未知")
  const [state, setState] = useState<AnalysisState>("idle")
  const [errorMessage, setErrorMessage] = useState("")
  const [result, setResult] = useState<VibrationAnalysisResponse | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      activeRequest.current?.abort()
    }
  }, [])

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null)
  }

  async function analyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (state === "loading") {
      return
    }
    const inputError = vibrationInputError(file, samplingRate, column)
    if (inputError || !file) {
      setState("error")
      setErrorMessage(inputError ?? "请选择 CSV 文件。")
      setResult(null)
      return
    }

    const controller = new AbortController()
    activeRequest.current?.abort()
    activeRequest.current = controller
    setState("loading")
    setErrorMessage("")
    setResult(null)

    try {
      const params = vibrationSearchParams(samplingRate, column, unit)
      const bytes = await file.arrayBuffer()
      const response = await fetch(`/api/vibration/analyze?${params.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "text/csv" },
        body: bytes,
        signal: controller.signal,
      })
      const body: unknown = await response.json().catch(() => null)
      if (!mounted.current || controller.signal.aborted) {
        return
      }
      if (!response.ok) {
        const message = isApiErrorResponse(body) ? body.message : `请求失败，HTTP ${response.status}`
        throw new Error(message)
      }
      if (!isVibrationAnalysisResponse(body)) {
        throw new Error("后端返回了无法识别的分析结果")
      }
      setResult(body)
      setState("success")
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) {
        return
      }
      setResult(null)
      setErrorMessage(readableError(error))
      setState("error")
    }
  }

  const peaks = result?.fft.peaks ?? []

  return (
    <main className="home">
      <section className="panel vibration-panel">
        <div className="mark" aria-hidden="true" />
        <h1>振动数据分析</h1>
        <p className="hint">上传 CSV 后，由后端计算信号指标。这里不判断设备故障。</p>
        <p className="disclaimer">分析结果仅反映上传信号的数学特征，不代表已确诊设备故障。</p>
        <form className="vibration-form" onSubmit={analyze}>
          <label className="field-label" htmlFor="vibration-file">
            CSV 文件
          </label>
          <input
            id="vibration-file"
            className="file-input"
            type="file"
            accept=".csv,text/csv"
            onChange={chooseFile}
          />
          <p className="file-meta">
            {file ? `${file.name}，${formatFileSize(file.size)}` : "尚未选择文件"}
          </p>

          <label className="field-label" htmlFor="sampling-rate">
            采样频率 sampling_rate（Hz，必填）
          </label>
          <input
            id="sampling-rate"
            className="text-input"
            inputMode="decimal"
            value={samplingRate}
            placeholder="例如 1000"
            onChange={(event) => setSamplingRate(event.target.value)}
          />

          <label className="field-label" htmlFor="value-column">
            数据列名 column
          </label>
          <input
            id="value-column"
            className="text-input"
            value={column}
            onChange={(event) => setColumn(event.target.value)}
          />

          <label className="field-label" htmlFor="signal-unit">
            原始信号单位 unit
          </label>
          <select
            id="signal-unit"
            className="text-input"
            value={unit}
            onChange={(event) => setUnit(event.target.value as VibrationUnit)}
          >
            {UNITS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>

          <button type="submit" className="test-button" disabled={state === "loading"}>
            {state === "loading" ? "正在分析..." : "开始分析"}
          </button>
        </form>

        {state === "loading" && <p className="status">正在分析，请稍候。</p>}
        {state === "error" && (
          <p className="status error" role="alert">
            {errorMessage}
          </p>
        )}

        {state === "success" && result && (
          <div aria-live="polite">
            <p className="file-meta">
              数据来源：{result.data_source}，列：{result.column}
            </p>
            <div className="metric-grid">
              <Metric label="样本数量" value={formatMetric(result.sample_count)} />
              <Metric label="采样频率" value={`${formatMetric(result.sampling_rate_hz)} Hz`} />
              <Metric label="采样时长" value={`${formatMetric(result.duration_seconds)} 秒`} />
              <Metric label="信号单位" value={result.unit || "未知"} />
              <Metric label="平均值" value={formatMetric(result.mean)} />
              <Metric label="RMS" value={formatMetric(result.rms)} />
              <Metric label="最大绝对峰值" value={formatMetric(result.peak_absolute)} />
              <Metric label="峰峰值" value={formatMetric(result.peak_to_peak)} />
              <Metric label="峰值因子" value={formatMetric(result.crest_factor)} />
              <Metric label="Pearson 峭度" value={formatMetric(result.kurtosis_pearson)} />
            </div>
            {result.notes.length > 0 && <p className="hint">{result.notes.join("")}</p>}
            <h2 className="peak-title">主要频率峰</h2>
            <p className="hint">{result.fft.amplitude_note}</p>
            {peaks.length === 0 ? (
              <p className="status">暂无有效频率峰</p>
            ) : (
              <ol className="peak-list">
                {peaks.map((peak, index) => (
                  <li key={`${peak.frequency_hz}-${peak.amplitude}`}>
                    <span>{index === 0 ? "幅值最高" : `第 ${index + 1} 个`}</span>
                    <strong>
                      {formatMetric(peak.frequency_hz)} Hz，幅值 {formatMetric(peak.amplitude)}
                    </strong>
                  </li>
                ))}
              </ol>
            )}
            <p className="disclaimer">{result.analysis_note}</p>
          </div>
        )}
      </section>
    </main>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  )
}
