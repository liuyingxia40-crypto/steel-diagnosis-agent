import { useEffect, useMemo, useState } from "react"
import { EquipmentChain } from "./EquipmentChain"
import { equipmentOf, selectionSystem, type LineSnapshot, type SystemId, type ViewSelection } from "./model"
import {
  HEALTH_LABEL,
  aggregateHealth,
  equipmentHealth,
  productionEvents,
  type VisualizationMode,
} from "./situation"

const SYSTEMS: { id: SystemId; label: string }[] = [
  { id: "drive", label: "主传动链" },
  { id: "coolant", label: "冷却水回路" },
  { id: "lube", label: "稀油润滑" },
]

const MODES: { id: VisualizationMode; label: string }[] = [
  { id: "operation", label: "运行模式" },
  { id: "health", label: "健康模式" },
  { id: "replay", label: "回放模式" },
]

function motionPreferred() {
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

export function PhysicalSystemPanel({
  line,
  selection,
  onSelect,
}: {
  line: LineSnapshot
  selection: ViewSelection
  onSelect: (selection: ViewSelection) => void
}) {
  const motionOn = motionPreferred()
  const health = useMemo(() => equipmentHealth(line), [line])
  const events = useMemo(() => productionEvents(line), [line])
  const [pulseId, setPulseId] = useState<string | null>(null)
  const [mode, setMode] = useState<VisualizationMode>("operation")
  const [eventId, setEventId] = useState<string | null>(null)
  const motionGain = mode === "replay" ? 0 : mode === "health" ? 0.28 : 1
  const activeSystem = selectionSystem(line, selection)

  useEffect(() => {
    if (selection.type !== "equipment") {
      setPulseId(null)
      return
    }
    const equipment = equipmentOf(line, selection.id)
    if (!equipment || equipment.status !== "warn") {
      setPulseId(null)
      return
    }
    setPulseId(equipment.id)
    const timer = window.setTimeout(() => {
      setPulseId((current) => (current === equipment.id ? null : current))
    }, 1400)
    return () => window.clearTimeout(timer)
  }, [line, selection])

  function changeMode(next: VisualizationMode) {
    setMode(next)
    if (next !== "replay") {
      setEventId(null)
      return
    }
    const current = events.find((item) => item.id === eventId) ?? events[events.length - 1]
    if (!current) {
      return
    }
    setEventId(current.id)
    onSelect({ type: "equipment", id: current.equipmentId })
  }

  return (
    <section className="ov-physical is-workspace" aria-label="物理设备链动态视图">
      <div className="ov-physical-bar">
        <div className="ov-system-nav" role="group" aria-label="系统健康状态">
          {SYSTEMS.map((item) => {
            const rows = health.filter((entry) => equipmentOf(line, entry.equipmentId)?.systemId === item.id)
            const status = aggregateHealth(rows.map((entry) => entry.status))
            return (
              <button key={item.id} type="button" aria-pressed={activeSystem === item.id} onClick={() => onSelect({ type: "system", id: item.id })}>
                <span>{item.label}</span>
                <i className={`ov-status-dot is-${status}`} />
                <strong className={`is-${status}`}>{HEALTH_LABEL[status]}</strong>
              </button>
            )
          })}
        </div>
        <div className="ov-chain-tools">
          <div className="ov-focus" role="tablist" aria-label="可视化模式">
            {MODES.map((item) => (
              <button key={item.id} type="button" role="tab" aria-selected={mode === item.id} onClick={() => changeMode(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="ov-focus" role="tablist" aria-label="系统聚焦">
            <button type="button" role="tab" aria-selected={selection.type === "overview"} onClick={() => onSelect({ type: "overview" })}>
              产线总览
            </button>
          </div>
        </div>
      </div>
      <div className="ov-chain-scroll">
        <EquipmentChain
            line={line}
            selection={selection}
            motionOn={motionOn}
            motionGain={motionGain}
            mode={mode}
            health={health}
            pulseId={pulseId}
            onSelect={(id) => onSelect({ type: "equipment", id })}
            onBlank={() => onSelect({ type: "overview" })}
        />
      </div>
      <div className="ov-physical-caption">
        <h2>物理设备链动态视图</h2>
        <ul className="ov-legend">
          <li>
            <i className="ov-swatch ov-swatch-drive" />
            动力
          </li>
          <li>
            <i className="ov-swatch ov-swatch-water" />
            冷却水
          </li>
          <li>
            <i className="ov-swatch ov-swatch-oil" />
            稀油
          </li>
          <li>
            <i className="ov-swatch ov-swatch-return" />
            回水
          </li>
        </ul>
      </div>
    </section>
  )
}
