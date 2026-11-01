import { useRef, type ReactNode } from "react"
import { equipmentOf, selectionSystem, type LineSnapshot, type SystemId, type ViewSelection } from "./model"
import { DRIVE_RATIO, FLOW_RATE, motionStates } from "./motion"
import { HEALTH_LABEL, relatedEquipment, type EquipmentHealth, type HealthStatus, type VisualizationMode } from "./situation"
import { useProcessMotion } from "./useProcessMotion"

const L = {
  supply: { x: 78, y: 56 },
  pump: { x: 250, y: 56 },
  pipe: { x: 500, y: 56 },
  spray: { x: 880, y: 116 },
  hv: { x: 52, y: 272 },
  motor: { x: 168, y: 272 },
  coupling: { x: 310, y: 272 },
  gear: { x: 470, y: 272 },
  shaft: { x: 660, y: 272 },
  mill: { x: 880, y: 272 },
  basin: { x: 1020, y: 430 },
  tank: { x: 78, y: 528 },
  oilPump: { x: 250, y: 528 },
  cooler: { x: 420, y: 528 },
  filter: { x: 590, y: 528 },
  motorBearing: { x: 168, y: 348 },
  gearBearing: { x: 470, y: 366 },
  oilRail: 424,
}

const COLOR: Record<SystemId, string> = {
  drive: "#3C4A5A",
  coolant: "#2F6F86",
  lube: "#7A6248",
}

function Prism({ x, y, w, h, d = 8, front = true }: { x: number; y: number; w: number; h: number; d?: number; front?: boolean }) {
  const dx = d * 0.7
  const dy = -d
  return (
    <g>
      <path className="ov-face-side" d={`M${x + w} ${y} l${dx} ${dy} V${y + h + dy} L${x + w} ${y + h} Z`} />
      <path className="ov-face-top" d={`M${x} ${y} l${dx} ${dy} H${x + w + dx} L${x + w} ${y} Z`} />
      {front ? <rect className="ov-face-front" x={x} y={y} width={w} height={h} /> : null}
    </g>
  )
}

function Gear({ r, teeth }: { r: number; teeth: number }) {
  return (
    <g>
      {Array.from({ length: teeth }, (_, index) => (
        <rect key={index} className="ov-face-side" x={-2.1} y={-r - 4} width={4.2} height={5} transform={`rotate(${(360 / teeth) * index})`} />
      ))}
      <circle className="ov-face-front" r={r} />
      <circle className="ov-cut-bg" r={r * 0.32} />
    </g>
  )
}

function Spin({
  speed,
  system,
  dir = 1,
  anim,
  children,
}: {
  speed: number
  system: SystemId
  dir?: 1 | -1
  anim: string
  children: ReactNode
}) {
  return (
    <g className="ov-spin" data-spin="" data-speed={speed} data-system={system} data-dir={dir} data-anim={anim}>
      {children}
    </g>
  )
}

function Reservoir({ percent, fluid }: { percent: number; fluid: "water" | "oil" }) {
  const top = -18
  const height = 36
  const inner = 26
  const fill = Math.max(4, inner * (percent / 100))
  const fillY = top + height - 5 - fill
  return (
    <g>
      <Prism x={-28} y={top} w={56} h={height} d={10} />
      <rect className={fluid === "water" ? "ov-water-fill" : "ov-oil-fill"} x={-22} y={fillY} width={44} height={fill} />
      <path className="ov-detail" d={`M-22 ${fillY} H22`} />
    </g>
  )
}

function MotorGlyph({ speed }: { speed: number }) {
  return (
    <g data-anim="motor-spin">
      <Prism x={-50} y={16} w={82} h={8} d={8} />
      <Prism x={-48} y={-20} w={74} h={36} d={12} />
      {[-32, -18, -4, 10, 24].map((x) => (
        <line key={x} className="ov-detail" x1={x} y1={-16} x2={x} y2={12} />
      ))}
      <ellipse className="ov-face-front" cx={-48} cy={-2} rx={7} ry={16} />
      <g transform="translate(-48 -2)">
        <Spin speed={speed} system="drive" anim="motor-spin">
          <path className="ov-detail" d="M0 -9 V9 M-9 0 H9 M-6 -6 L6 6 M-6 6 L6 -6" />
          <circle className="ov-face-top" r="2.2" />
        </Spin>
      </g>
      <Prism x={-8} y={-34} w={22} h={12} d={8} />
      <rect className="ov-metal" x={26} y={-5} width={24} height={10} />
    </g>
  )
}

function CouplingGlyph({ speed }: { speed: number }) {
  return (
    <g>
      <rect className="ov-metal" x={-30} y={-4} width={14} height={8} />
      <rect className="ov-metal" x={16} y={-4} width={14} height={8} />
      <path className="ov-face-front" d="M-16 -11 h8 v22 h-8 Z" />
      <path className="ov-face-front" d="M8 -11 h8 v22 h-8 Z" />
      <Spin speed={speed} system="drive" anim="motor-spin">
        {[0, 60, 120, 180, 240, 300].map((angle) => (
          <ellipse key={angle} className="ov-face-side" cx="0" cy="-7" rx="3" ry="4.2" transform={`rotate(${angle})`} />
        ))}
        <circle className="ov-face-top" r="3" />
      </Spin>
    </g>
  )
}

function GearboxGlyph() {
  return (
    <g data-anim="gear-mesh">
      <Prism x={-58} y={-46} w={116} h={88} d={14} front={false} />
      <rect className="ov-cut-bg" x={-34} y={-22} width={68} height={44} />
      <g transform="translate(-14 6)">
        <Spin speed={DRIVE_RATIO.gearIn} system="drive" anim="gear-mesh">
          <Gear r={12} teeth={8} />
        </Spin>
      </g>
      <g transform="translate(16 -2)">
        <Spin speed={DRIVE_RATIO.gearOut} system="drive" dir={-1} anim="gear-mesh">
          <Gear r={18} teeth={12} />
        </Spin>
      </g>
      <path className="ov-face-front" fillRule="evenodd" d="M-58 -46 h116 v88 h-116 Z M-34 -22 h68 v44 h-68 Z" />
      <rect className="ov-detail" x={-34} y={-22} width={68} height={44} />
      <rect className="ov-metal" x={-74} y={-5} width={18} height={10} />
      <rect className="ov-metal" x={56} y={-5} width={20} height={10} />
      <rect className="ov-face-front" x={-18} y={-58} width={36} height={12} />
      <circle className="ov-face-top" cy={-52} r="3" />
    </g>
  )
}

function Cardan({ speed }: { speed: number }) {
  return (
    <Spin speed={speed} system="drive" anim="shaft-spin">
      <circle className="ov-face-front" r="10" />
      <rect className="ov-metal" x={-2} y={-12} width={4} height={24} />
      <rect className="ov-metal" x={-12} y={-2} width={24} height={4} />
    </Spin>
  )
}

function ShaftGlyph({ speed }: { speed: number }) {
  return (
    <g data-anim="shaft-spin">
      <rect className="ov-metal" x={-62} y={-3.5} width={124} height={7} />
      <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate={DRIVE_RATIO.shaft} d="M-52 0 H52" />
      <g transform="translate(-74 0)">
        <Cardan speed={speed} />
      </g>
      <g transform="translate(74 0)">
        <Cardan speed={speed} />
      </g>
    </g>
  )
}

function SupplyGlyph() {
  return (
    <g>
      <Prism x={-24} y={-22} w={48} h={42} d={8} />
      {[-12, 0, 12].map((x) => (
        <g key={x}>
          <line className="ov-detail" x1={x} y1={-22} x2={x} y2={-34} />
          <circle className="ov-face-top" cx={x} cy={-36} r="2.4" />
        </g>
      ))}
      <path className="ov-detail" d="M-12 2 H12 M-8 10 H8" />
    </g>
  )
}

function CoolerGlyph() {
  return (
    <g>
      <rect className="ov-metal" x={-36} y={-5} width={8} height={10} />
      <rect className="ov-metal" x={28} y={-5} width={8} height={10} />
      <Prism x={-28} y={-16} w={56} h={32} d={8} />
      <path className="ov-detail" d="M-16 -6 H16 M-16 2 H16 M-16 8 H16" />
    </g>
  )
}

function PipeSpoolGlyph() {
  return (
    <g>
      <rect className="ov-metal" x={-30} y={-9} width={6} height={18} />
      <rect className="ov-metal" x={24} y={-9} width={6} height={18} />
      <rect className="ov-face-front" x={-24} y={-5} width={48} height={10} />
    </g>
  )
}

function MillGlyph({ speed }: { speed: number }) {
  return (
    <g data-anim="roll-spin">
      <Prism x={-64} y={-98} w={16} h={180} d={10} />
      <Prism x={48} y={-98} w={16} h={180} d={10} />
      <Prism x={-64} y={-112} w={128} h={16} d={10} />
      <Prism x={-70} y={78} w={140} h={14} d={10} />
      <path className="ov-face-front" d="M-36 70 H46 L38 84 H-28 Z" />
      <rect className="ov-cut-bg" x={-42} y={-72} width={84} height={132} />
      <g transform="translate(0 -28)">
        <Spin speed={speed} system="drive" anim="roll-spin">
          <circle className="ov-metal" r="20" />
          <path className="ov-detail" d="M0 -15 V15 M-15 0 H15" />
          <circle className="ov-face-top" r="3.5" />
        </Spin>
      </g>
      <g transform="translate(0 26)">
        <Spin speed={speed} system="drive" dir={-1} anim="roll-spin">
          <circle className="ov-metal" r="20" />
          <path className="ov-detail" d="M0 -15 V15 M-15 0 H15" />
          <circle className="ov-face-top" r="3.5" />
        </Spin>
      </g>
      <rect className="ov-metal" x={-30} y={-124} width={8} height={14} />
      <rect className="ov-metal" x={22} y={-124} width={8} height={14} />
      <rect className="ov-metal" x={-90} y={-5} width={28} height={10} />
    </g>
  )
}

function PumpGlyph({ system, speed }: { system: "coolant" | "lube"; speed: number }) {
  return (
    <g>
      <rect className="ov-face-front" x={-18} y={16} width={40} height={7} />
      <ellipse className="ov-face-side" cx={5} cy={-3} rx={16} ry={16} />
      <circle className="ov-face-front" r="16" />
      <rect className="ov-metal" x={-30} y={-4} width={16} height={8} />
      <rect className="ov-metal" x={12} y={-5} width={18} height={10} />
      <Spin speed={speed} system={system} anim={system === "coolant" ? "coolant-flow" : "lube-flow"}>
        <path className="ov-detail" d="M0 -11 L2.2 -2.2 L11 0 L2.2 2.2 L0 11 L-2.2 2.2 L-11 0 L-2.2 -2.2 Z" />
        <circle className="ov-face-top" r="2.4" />
      </Spin>
    </g>
  )
}

function SprayGlyph() {
  return (
    <g data-anim="spray">
      <Prism x={-42} y={-8} w={84} h={12} d={8} />
      {[-27, -9, 9, 27].map((x) => (
        <path key={x} className="ov-face-front" d={`M${x - 3.2} 4 L${x} 12 L${x + 3.2} 4 Z`} />
      ))}
    </g>
  )
}

function FilterGlyph() {
  return (
    <g>
      <rect className="ov-metal" x={-7} y={-32} width={14} height={8} />
      <Prism x={-16} y={-24} w={32} h={44} d={8} />
      <path className="ov-detail" d="M-9 -12 H9 M-9 -2 H9 M-9 8 H9 M-9 16 H9" />
      <rect className="ov-metal" x={-7} y={20} width={14} height={8} />
    </g>
  )
}

function BearingGlyph({ stem = 40 }: { stem?: number }) {
  return (
    <g>
      <line className="ov-detail" x1={0} y1={-12 - stem} x2={0} y2={-12} />
      <rect className="ov-face-front" x={-16} y={-12} width={32} height={24} />
      <circle className="ov-face-front" r="7" />
      <circle className="ov-oil-fill" r="3" />
      <rect className="ov-metal" x={-3} y={12} width={6} height={12} />
    </g>
  )
}

function Equip({
  id,
  x,
  y,
  name,
  code,
  system,
  selected,
  status,
  mode,
  linked,
  inferred,
  pulse,
  grouped,
  labelX = 0,
  labelDy = 52,
  labelAnchor = "middle",
  hit,
  ring,
  onSelect,
  children,
}: {
  id: string
  x: number
  y: number
  name: string
  code: string
  system: SystemId
  selected: boolean
  status: HealthStatus
  mode: VisualizationMode
  linked: boolean
  inferred: boolean
  pulse: boolean
  grouped: boolean
  labelX?: number
  labelDy?: number
  labelAnchor?: "middle" | "start" | "end"
  hit: { x: number; y: number; w: number; h: number }
  ring: { x: number; y: number; w: number; h: number }
  onSelect: (id: string) => void
  children: ReactNode
}) {
  const showText = mode !== "operation"
  return (
    <g
      className={["ov-equip", `ov-system-${system}`, `is-${status}`, selected ? "is-selected" : "", linked ? "is-linked" : "", inferred ? "is-inferred" : "", pulse ? "is-locating" : "", grouped ? "is-grouped" : ""].filter(Boolean).join(" ")}
      data-equipment-id={id}
      data-anim="equipment"
      data-system={system}
      data-health={status}
      transform={`translate(${x} ${y})`}
      role="button"
      tabIndex={0}
      style={{ color: COLOR[system] }}
      onClick={(event) => {
        event.stopPropagation()
        onSelect(id)
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onSelect(id)
        }
      }}
    >
      <rect className="ov-hit" x={hit.x} y={hit.y} width={hit.w} height={hit.h} />
      {grouped ? <rect className="ov-focus-ring" x={ring.x - 5} y={ring.y - 5} width={ring.w + 10} height={ring.h + 10} rx={10} /> : null}
      {selected || pulse ? <rect className="ov-select-ring" x={ring.x} y={ring.y} width={ring.w} height={ring.h} rx={8} /> : null}
      {linked ? <rect className="ov-link-ring" x={ring.x} y={ring.y} width={ring.w} height={ring.h} rx={8} /> : null}
      {inferred ? <rect className="ov-infer-ring" x={ring.x} y={ring.y} width={ring.w} height={ring.h} rx={8} /> : null}
      <g className="ov-glyph">{children}</g>
      <text className="ov-equip-name" x={labelX} y={labelDy} textAnchor={labelAnchor}>
        {name}
      </text>
      <text className="ov-equip-code" x={labelX} y={labelDy + 13} textAnchor={labelAnchor}>
        {code}
      </text>
      {inferred ? (
        <text className="ov-infer-label" x={labelX} y={labelDy + 26} textAnchor={labelAnchor}>
          可能关联
        </text>
      ) : null}
      {showText ? (
        <text className={`ov-health-label is-${status}`} x={labelX} y={labelDy + (inferred ? 39 : 26)} textAnchor={labelAnchor}>
          {HEALTH_LABEL[status]}
        </text>
      ) : null}
    </g>
  )
}

function Flow({
  system,
  links,
  hot,
  quiet,
  anim,
  children,
}: {
  system: SystemId
  links: string
  hot: boolean
  quiet: boolean
  anim: string
  children: ReactNode
}) {
  return (
    <g className={["ov-flow-group", hot ? "is-hot" : "", quiet ? "is-quiet" : ""].filter(Boolean).join(" ")} data-system={system} data-anim={anim} data-links={links}>
      {children}
    </g>
  )
}

function Particles({ path, system, rate, count }: { path: string; system: SystemId; rate: number; count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, index) => (
        <circle
          key={`${path}-${index}`}
          r="2.3"
          className={`ov-particle ov-particle-${system}`}
          data-particle=""
          data-system={system}
          data-path={path}
          data-rate={rate}
          data-delay={index * 1.15}
          data-priority={index === 0 ? "main" : "extra"}
        />
      ))}
    </>
  )
}

function level(line: LineSnapshot, equipmentId: string, metricId: string, fallback: number) {
  const value = Number(equipmentOf(line, equipmentId)?.metrics.find((item) => item.id === metricId)?.value)
  return Number.isFinite(value) ? value : fallback
}

const HIT = { x: -54, y: -46, w: 108, h: 104 }
const RING = { x: -48, y: -40, w: 96, h: 86 }

export function EquipmentChain({
  line,
  selection,
  motionOn,
  motionGain,
  mode,
  health,
  pulseId,
  onSelect,
  onBlank,
}: {
  line: LineSnapshot
  selection: ViewSelection
  motionOn: boolean
  motionGain: number
  mode: VisualizationMode
  health: EquipmentHealth[]
  pulseId: string | null
  onSelect: (id: string) => void
  onBlank: () => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const focus = selectionSystem(line, selection)
  useProcessMotion(svgRef, motionOn && motionGain > 0, focus, motionGain)
  const motions = motionStates(line)
  const speed = (id: string, fallback: number) => motions.find((item) => item.equipmentId === id)?.speedNormalized ?? fallback
  const selectedId = selection.type === "equipment" ? selection.id : null
  const healthOf = new Map(health.map((item) => [item.equipmentId, item.status]))
  const relations = selectedId ? relatedEquipment(selectedId) : { physical: [], diagnostic: [] }
  const linked = new Set(relations.physical)
  const inferred = new Set(relations.diagnostic.map((item) => item.id))

  function mark(links: string, system: SystemId) {
    if (selection.type === "system" && selection.id === system) return true
    const ids = links.split(" ")
    if (selectedId && ids.includes(selectedId)) return true
    if (ids.some((id) => linked.has(id) || inferred.has(id))) return true
    return false
  }

  function quiet(system: SystemId) {
    return focus !== null && focus !== system
  }

  const node = (id: string, x: number, y: number, system: SystemId, extra: Partial<Parameters<typeof Equip>[0]>, glyph: ReactNode) => {
    const equipment = equipmentOf(line, id)
    return (
      <Equip
        id={id}
        x={x}
        y={y}
        name={equipment?.name ?? id}
        code={equipment?.code ?? ""}
        system={system}
        selected={selectedId === id}
        status={healthOf.get(id) ?? "unknown"}
        mode={mode}
        linked={linked.has(id)}
        inferred={inferred.has(id)}
        pulse={pulseId === id}
        grouped={focus === system}
        hit={HIT}
        ring={RING}
        onSelect={onSelect}
        {...extra}
      >
        {glyph}
      </Equip>
    )
  }

  return (
    <svg ref={svgRef} className={["ov-chain", motionOn && motionGain > 0 ? "is-motion" : "is-still", mode === "health" ? "is-health" : "", mode === "replay" ? "is-replay" : ""].filter(Boolean).join(" ")} viewBox="0 0 1100 600" data-mode="simulation" data-focus={focus ?? "overview"} data-visual={mode} aria-label="棒材产线物理设备链动态视图" onClick={onBlank}>
      <defs>
        <marker id="ov-mk-water" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0 0.6 L8 4 L0 7.4 Z" fill="#2F6F86" />
        </marker>
        <marker id="ov-mk-oil" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0 0.6 L8 4 L0 7.4 Z" fill="#7A6248" />
        </marker>
        <marker id="ov-mk-drive" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0 0.6 L8 4 L0 7.4 Z" fill="#3C4A5A" />
        </marker>
      </defs>

      <Flow system="coolant" links="cw-supply cw-pump" hot={mark("cw-supply cw-pump", "coolant")} quiet={quiet("coolant")} anim="coolant-flow">
        <path className="ov-pipe-body ov-pipe-water" d={`M${L.supply.x + 36} ${L.supply.y} H${L.pump.x - 32}`} />
        <path id="ov-water-suction" className="ov-flow ov-flow-water" data-flow="water" data-system="coolant" data-rate={FLOW_RATE.coolant} d={`M${L.supply.x + 36} ${L.supply.y} H${L.pump.x - 32}`} />
        <text className="ov-path-label" x={L.supply.x + 78} y={L.supply.y - 14}>吸入管路</text>
      </Flow>
      <Flow system="coolant" links="cw-pump cw-pipe cw-spray drv-mill" hot={mark("cw-pump cw-pipe cw-spray drv-mill", "coolant")} quiet={quiet("coolant")} anim="coolant-flow">
        <path className="ov-pipe-body ov-pipe-water" d={`M${L.pump.x + 32} ${L.supply.y} H${L.spray.x} V${L.spray.y}`} markerEnd="url(#ov-mk-water)" />
        <path id="ov-water-supply" className="ov-flow ov-flow-water" data-flow="water" data-system="coolant" data-rate={FLOW_RATE.coolant} d={`M${L.pump.x + 32} ${L.supply.y} H${L.spray.x} V${L.spray.y}`} />
        <Particles path="#ov-water-supply" system="coolant" rate={FLOW_RATE.coolant} count={3} />
        <text className="ov-path-label" x={L.pump.x + 90} y={L.supply.y - 14}>压力管路</text>
      </Flow>
      <Flow system="coolant" links="drv-mill cw-spray cw-return" hot={mark("drv-mill cw-spray cw-return", "coolant")} quiet={quiet("coolant")} anim="coolant-flow">
        <path className="ov-pipe-body ov-pipe-return" d={`M${L.mill.x + 58} ${L.mill.y + 88} V${L.basin.y} H${L.basin.x - 30}`} markerEnd="url(#ov-mk-water)" />
        <path id="ov-water-return" className="ov-flow ov-flow-water" data-flow="water" data-system="coolant" data-rate={FLOW_RATE.coolant} d={`M${L.mill.x + 58} ${L.mill.y + 88} V${L.basin.y} H${L.basin.x - 30}`} />
        <Particles path="#ov-water-return" system="coolant" rate={FLOW_RATE.coolant} count={2} />
        <text className="ov-path-label" x={L.mill.x + 70} y={L.basin.y - 12}>回水</text>
      </Flow>
      <Flow system="coolant" links="cw-spray drv-mill" hot={mark("cw-spray drv-mill", "coolant")} quiet={quiet("coolant")} anim="spray">
        {[-27, -9, 9, 27].map((dx) => (
          <path key={dx} className="ov-flow ov-flow-jet" data-flow="jet" data-system="coolant" data-rate="1" d={`M${L.spray.x + dx} ${L.spray.y + 12} V${L.mill.y - 36}`} />
        ))}
      </Flow>

      <Flow system="lube" links="lb-tank lb-pump" hot={mark("lb-tank lb-pump", "lube")} quiet={quiet("lube")} anim="lube-flow">
        <path className="ov-pipe-body ov-pipe-oil" d={`M${L.tank.x + 36} ${L.tank.y} H${L.oilPump.x - 32}`} />
        <path className="ov-flow ov-flow-oil" data-flow="oil" data-system="lube" data-rate={FLOW_RATE.lube} d={`M${L.tank.x + 36} ${L.tank.y} H${L.oilPump.x - 32}`} />
        <text className="ov-path-label" x={L.tank.x + 70} y={L.tank.y - 16}>吸入管路</text>
      </Flow>
      <Flow system="lube" links="lb-pump lb-cooler lb-filter" hot={mark("lb-pump lb-cooler lb-filter", "lube")} quiet={quiet("lube")} anim="lube-flow">
        <path className="ov-pipe-body ov-pipe-oil" d={`M${L.oilPump.x + 32} ${L.tank.y} H${L.filter.x - 24}`} markerEnd="url(#ov-mk-oil)" />
        <path id="ov-oil-feed" className="ov-flow ov-flow-oil" data-flow="oil" data-system="lube" data-rate={FLOW_RATE.lube} d={`M${L.oilPump.x + 32} ${L.tank.y} H${L.filter.x - 24}`} />
        <Particles path="#ov-oil-feed" system="lube" rate={FLOW_RATE.lube} count={2} />
        <text className="ov-path-label" x={L.cooler.x + 36} y={L.tank.y - 16}>压力管路</text>
      </Flow>

      <Flow system="drive" links="drv-hv drv-motor" hot={mark("drv-hv drv-motor", "drive")} quiet={quiet("drive")} anim="drive-flow">
        <path className="ov-pipe-body ov-pipe-power" d={`M${L.hv.x + 30} ${L.motor.y} H${L.motor.x - 52}`} markerEnd="url(#ov-mk-drive)" />
        <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate="1" d={`M${L.hv.x + 30} ${L.motor.y} H${L.motor.x - 52}`} />
      </Flow>
      <Flow system="drive" links="drv-motor drv-coupling" hot={mark("drv-motor drv-coupling", "drive")} quiet={quiet("drive")} anim="drive-flow">
        <path className="ov-pipe-body ov-pipe-power" d={`M${L.motor.x + 50} ${L.motor.y} H${L.coupling.x - 32}`} markerEnd="url(#ov-mk-drive)" />
        <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate="1" d={`M${L.motor.x + 50} ${L.motor.y} H${L.coupling.x - 32}`} />
      </Flow>
      <Flow system="drive" links="drv-coupling drv-gear" hot={mark("drv-coupling drv-gear", "drive")} quiet={quiet("drive")} anim="drive-flow">
        <path className="ov-pipe-body ov-pipe-power" d={`M${L.coupling.x + 32} ${L.motor.y} H${L.gear.x - 76}`} markerEnd="url(#ov-mk-drive)" />
        <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate="1" d={`M${L.coupling.x + 32} ${L.motor.y} H${L.gear.x - 76}`} />
      </Flow>
      <Flow system="drive" links="drv-gear drv-shaft" hot={mark("drv-gear drv-shaft", "drive")} quiet={quiet("drive")} anim="drive-flow">
        <path className="ov-pipe-body ov-pipe-power" d={`M${L.gear.x + 78} ${L.motor.y} H${L.shaft.x - 88}`} />
        <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate={DRIVE_RATIO.shaft} d={`M${L.gear.x + 78} ${L.motor.y} H${L.shaft.x - 88}`} />
      </Flow>
      <Flow system="drive" links="drv-shaft drv-mill" hot={mark("drv-shaft drv-mill", "drive")} quiet={quiet("drive")} anim="drive-flow">
        <path className="ov-pipe-body ov-pipe-power" d={`M${L.shaft.x + 90} ${L.motor.y} H${L.mill.x - 92}`} markerEnd="url(#ov-mk-drive)" />
        <path className="ov-flow ov-flow-power" data-flow="power" data-system="drive" data-rate={DRIVE_RATIO.shaft} d={`M${L.shaft.x + 90} ${L.motor.y} H${L.mill.x - 92}`} />
      </Flow>

      {node("cw-supply", L.supply.x, L.supply.y, "coolant", {}, <Reservoir percent={level(line, "cw-supply", "ws-level", 81)} fluid="water" />)}
      {node("cw-pump", L.pump.x, L.pump.y, "coolant", {}, <PumpGlyph system="coolant" speed={speed("cw-pump", FLOW_RATE.coolant)} />)}
      {node("cw-pipe", L.pipe.x, L.pipe.y, "coolant", { hit: { x: -40, y: -28, w: 80, h: 72 }, ring: { x: -36, y: -16, w: 72, h: 36 }, labelDy: -26 }, <PipeSpoolGlyph />)}
      {node("cw-return", L.basin.x, L.basin.y, "coolant", { labelDy: 40 }, <Reservoir percent={64} fluid="water" />)}
      {node("lb-tank", L.tank.x, L.tank.y, "lube", {}, <Reservoir percent={level(line, "lb-tank", "tk-level", 72)} fluid="oil" />)}
      {node("lb-pump", L.oilPump.x, L.oilPump.y, "lube", {}, <PumpGlyph system="lube" speed={speed("lb-pump", FLOW_RATE.lube)} />)}
      {node("lb-cooler", L.cooler.x, L.cooler.y, "lube", { hit: { x: -42, y: -32, w: 84, h: 90 }, ring: { x: -36, y: -24, w: 72, h: 52 } }, <CoolerGlyph />)}
      {node("lb-filter", L.filter.x, L.filter.y, "lube", { hit: { x: -28, y: -46, w: 56, h: 108 }, ring: { x: -24, y: -40, w: 48, h: 78 } }, <FilterGlyph />)}

      {node("drv-hv", L.hv.x, L.hv.y, "drive", { hit: { x: -32, y: -48, w: 64, h: 110 }, ring: { x: -28, y: -42, w: 56, h: 72 } }, <SupplyGlyph />)}
      {node("drv-motor", L.motor.x, L.motor.y, "drive", { hit: { x: -58, y: -48, w: 120, h: 80 }, ring: { x: -54, y: -40, w: 108, h: 68 }, labelX: -34, labelDy: -50 }, <MotorGlyph speed={speed("drv-motor", DRIVE_RATIO.motor)} />)}
      {node("drv-coupling", L.coupling.x, L.coupling.y, "drive", { hit: { x: -40, y: -40, w: 80, h: 100 }, ring: { x: -34, y: -24, w: 68, h: 48 } }, <CouplingGlyph speed={speed("drv-coupling", DRIVE_RATIO.coupling)} />)}
      {node("drv-gear", L.gear.x, L.gear.y, "drive", { hit: { x: -78, y: -72, w: 156, h: 118 }, ring: { x: -66, y: -64, w: 136, h: 110 }, labelX: -72, labelDy: -52, labelAnchor: "end" }, <GearboxGlyph />)}
      {node("drv-shaft", L.shaft.x, L.shaft.y, "drive", { hit: { x: -96, y: -36, w: 192, h: 96 }, ring: { x: -90, y: -24, w: 180, h: 52 }, labelDy: 46 }, <ShaftGlyph speed={speed("drv-shaft", DRIVE_RATIO.shaft)} />)}

      {node("cw-spray", L.spray.x, L.spray.y, "coolant", { hit: { x: -50, y: -28, w: 100, h: 56 }, ring: { x: -46, y: -16, w: 92, h: 36 }, labelX: 50, labelDy: 4, labelAnchor: "start" }, <SprayGlyph />)}
      {node("drv-mill", L.mill.x, L.mill.y, "drive", { hit: { x: -96, y: -132, w: 176, h: 250 }, ring: { x: -74, y: -120, w: 148, h: 220 }, labelX: 78, labelDy: -8, labelAnchor: "start" }, <MillGlyph speed={speed("drv-mill", DRIVE_RATIO.roll)} />)}
      <Flow system="lube" links="lb-filter lb-point lb-motor-bearing drv-gear drv-motor" hot={mark("lb-filter lb-point lb-motor-bearing drv-gear drv-motor", "lube")} quiet={quiet("lube")} anim="lube-flow">
        <path className="ov-pipe-body ov-pipe-oil" d={`M${L.filter.x} ${L.filter.y - 30} V${L.oilRail} H${L.motor.x}`} />
        <path className="ov-pipe-body ov-pipe-oil" d={`M${L.gear.x} ${L.oilRail} V${L.gearBearing.y + 24}`} markerEnd="url(#ov-mk-oil)" />
        <path className="ov-pipe-body ov-pipe-oil" d={`M${L.motor.x} ${L.oilRail} V${L.motorBearing.y + 24}`} markerEnd="url(#ov-mk-oil)" />
        <path id="ov-oil-bearings" className="ov-flow ov-flow-oil" data-flow="oil" data-system="lube" data-rate={FLOW_RATE.lube} d={`M${L.filter.x} ${L.filter.y - 30} V${L.oilRail} H${L.motor.x} V${L.motorBearing.y + 24}`} />
        <path id="ov-oil-gear-bearing" className="ov-flow ov-flow-oil" data-flow="oil" data-system="lube" data-rate={FLOW_RATE.lube} d={`M${L.gear.x} ${L.oilRail} V${L.gearBearing.y + 24}`} />
        <Particles path="#ov-oil-bearings" system="lube" rate={FLOW_RATE.lube} count={1} />
        <Particles path="#ov-oil-gear-bearing" system="lube" rate={FLOW_RATE.lube} count={1} />
      </Flow>
      {node("lb-point", L.gearBearing.x, L.gearBearing.y, "lube", { hit: { x: -22, y: -16, w: 78, h: 44 }, ring: { x: -20, y: -16, w: 40, h: 42 }, labelX: 24, labelDy: 2, labelAnchor: "start" }, <BearingGlyph />)}
      {node("lb-motor-bearing", L.motorBearing.x, L.motorBearing.y, "lube", { hit: { x: -22, y: -16, w: 78, h: 44 }, ring: { x: -20, y: -16, w: 40, h: 42 }, labelX: 24, labelDy: 2, labelAnchor: "start" }, <BearingGlyph />)}
    </svg>
  )
}
