import { CanvasEvent, Graph, NodeEvent } from "@antv/g6"
import type { IElementEvent } from "@antv/g6"
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react"
import { fanPositions, type FanOccupant } from "../graph/fan"
import { labelSide } from "../graph/groupLayout"
import { lineDashOf, lineForEdge, type LineKind } from "../graph/hierarchyLine"
import { overviewPlacement, type SceneNode } from "../graph/networkScene"
import type { GraphEdge } from "../types/graph"

export interface GraphCanvasHandle {
  fitView: () => void
}

interface GraphCanvasProps {
  nodes: SceneNode[]
  edges: GraphEdge[]
  layout: "overview" | "local"
  layoutEpoch: number
  expandAnchorId: string | null
  selectedId: string | null
  onSelect: (id: string | null) => void
  onDeeper: (id: string) => void
}

function fullLabel(data: Record<string, unknown> | undefined, id: string): string {
  return typeof data?.label === "string" ? data.label : id
}

function shortLabel(data: Record<string, unknown> | undefined, id: string): string {
  return typeof data?.shortLabel === "string" ? data.shortLabel : fullLabel(data, id)
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
}

function fixedLayout() {
  return {
    type: "d3-force" as const,
    animation: false,
    iterations: 1,
    enableWorker: false,
    clustering: false,
    center: false as const,
    manyBody: false as const,
    link: false as const,
    collide: false as const,
    preventOverlap: false as const,
    x: false as const,
    y: false as const,
  }
}

function readPoint(graph: Graph, id: string, style: { x?: unknown; y?: unknown } | undefined): { x: number; y: number } | null {
  const position = graph.getElementPosition(id)
  if (Array.isArray(position) && position.length >= 2 && position.every((item) => typeof item === "number")) {
    return { x: position[0] ?? 0, y: position[1] ?? 0 }
  }
  if (typeof style?.x === "number" && typeof style.y === "number") {
    return { x: style.x, y: style.y }
  }
  return null
}

async function frameVisibleNodes(graph: Graph) {
  await graph.zoomTo(1, false)
  await graph.translateTo([0, 0], false)
  const ids = graph.getNodeData().map((node) => node.id)
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY
  for (const id of ids) {
    const bounds = graph.getElementRenderBounds(id)
    const min = bounds.min
    const max = bounds.max
    if (!min || !max) {
      continue
    }
    minX = Math.min(minX, min[0] ?? minX)
    minY = Math.min(minY, min[1] ?? minY)
    maxX = Math.max(maxX, max[0] ?? maxX)
    maxY = Math.max(maxY, max[1] ?? maxY)
  }
  const [width, height] = graph.getSize()
  if (!Number.isFinite(minX) || !(width > 0) || !(height > 0) || maxX <= minX || maxY <= minY) {
    return
  }
  const zoom = Math.min((width * 0.62) / (maxX - minX), (height * 0.6) / (maxY - minY), 1.35)
  const safe = Math.max(0.45, zoom)
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  await graph.zoomTo(safe, false)
  await graph.translateTo([width * 0.48 - cx * safe, height * 0.5 - cy * safe], false)
}

async function paintStates(graph: Graph, selectedId: string | null) {
  if (graph.destroyed || !graph.rendered) {
    return
  }
  const nodes = graph.getNodeData()
  const edges = graph.getEdgeData()
  const state: Record<string, string[]> = {}
  if (!selectedId || !nodes.some((node) => node.id === selectedId)) {
    for (const node of nodes) {
      state[node.id] = []
    }
    for (const edge of edges) {
      if (edge.id) {
        state[edge.id] = []
      }
    }
  } else {
    const neighbors = new Set(graph.getNeighborNodesData(selectedId).map((node) => node.id))
    const related = new Set(
      graph
        .getRelatedEdgesData(selectedId)
        .map((edge) => edge.id)
        .filter((id): id is string => typeof id === "string"),
    )
    for (const node of nodes) {
      if (node.id === selectedId) {
        state[node.id] = ["selected"]
      } else if (neighbors.has(node.id)) {
        state[node.id] = ["active"]
      } else {
        state[node.id] = ["dim"]
      }
    }
    for (const edge of edges) {
      if (!edge.id) {
        continue
      }
      state[edge.id] = related.has(edge.id) ? ["active"] : ["dim"]
    }
  }
  await graph.setElementState(state, false)
}

function readLineKind(value: unknown): LineKind {
  if (value === "solid" || value === "dashed" || value === "association") {
    return value
  }
  return "association"
}

function edgeFlags(edge: { source: string; target: string; data: object }, nodes: readonly SceneNode[]): Record<string, unknown> {
  const left = nodes.find((node) => node.id === edge.source)
  const right = nodes.find((node) => node.id === edge.target)
  const leftRegion = left?.region ?? ""
  const rightRegion = right?.region ?? ""
  const line = lineForEdge(edge.source, edge.target, nodes)
  return {
    ...edge.data,
    cross: Boolean(leftRegion && rightRegion && leftRegion !== rightRegion && leftRegion !== "local"),
    leaf: left?.role === "minor" || right?.role === "minor",
    lineKind: line.kind,
    childLevel: line.childLevel,
  }
}

function minorLabelPlacement(data: Record<string, unknown> | undefined): "bottom" | "left" | "right" | "top" {
  const side = data?.labelSide
  if (side === "left" || side === "right" || side === "top" || side === "bottom") {
    return side
  }
  return "right"
}

function nodeData(node: SceneNode): Record<string, unknown> {
  return {
    ...node.data,
    shortLabel: node.data.shortLabel,
    categoryColor: node.categoryColor,
    visualSize: node.visualSize,
    headline: node.headline,
    hub: node.hub,
    uiGroup: node.uiGroup,
    region: node.region,
    hiddenCount: node.hiddenCount,
    expandLevel: node.expandLevel,
    hiddenLabels: node.hiddenLabels,
  }
}

function plusBadge() {
  return {
    text: "+",
    placement: "right-top" as const,
    fontSize: 12,
    fontWeight: 700,
    fill: "#101820",
    backgroundFill: "#f4fbff",
    backgroundRadius: 8,
    padding: [0, 4] as [number, number],
    offsetX: -1,
    offsetY: 1,
    cursor: "pointer" as const,
  }
}

function hiddenNames(data: Record<string, unknown> | undefined): string[] {
  const value = data?.hiddenLabels
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === "string" && item.length > 0)
}

function animateOut(graph: Graph, origin: { x: number; y: number }, targets: Array<{ id: string; x: number; y: number }>) {
  const started = performance.now()
  const step = () => {
    if (graph.destroyed) {
      return
    }
    const progress = Math.min(1, (performance.now() - started) / 280)
    const ease = 1 - (1 - progress) ** 3
    graph.updateNodeData(
      targets.map((target) => ({
        id: target.id,
        style: { x: origin.x + (target.x - origin.x) * ease, y: origin.y + (target.y - origin.y) * ease },
      })),
    )
    graph
      .draw()
      .then(() => {
        if (progress < 1) {
          requestAnimationFrame(step)
        }
      })
      .catch(() => undefined)
  }
  requestAnimationFrame(step)
}

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(function GraphCanvas(
  { nodes, edges, layout, layoutEpoch, expandAnchorId, selectedId, onSelect, onDeeper },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const onSelectRef = useRef(onSelect)
  const onDeeperRef = useRef(onDeeper)
  const selectedIdRef = useRef(selectedId)
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  const layoutRef = useRef(layout)
  const anchorRef = useRef(expandAnchorId)
  const readyEpoch = useRef(-1)
  const pickedAt = useRef(0)
  nodesRef.current = nodes
  edgesRef.current = edges
  layoutRef.current = layout
  anchorRef.current = expandAnchorId

  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    onDeeperRef.current = onDeeper
  }, [onDeeper])
  useEffect(() => {
    selectedIdRef.current = selectedId
  }, [selectedId])

  useImperativeHandle(ref, () => ({
    fitView() {
      const graph = graphRef.current
      if (!graph || graph.destroyed || !graph.rendered) {
        return
      }
      graph.fitView({ when: "always" }, false).catch(() => undefined)
    },
  }))

  useEffect(() => {
    const container = containerRef.current
    if (!container) {
      return
    }
    let disposed = false
    readyEpoch.current = -1
    const currentNodes = nodesRef.current
    const currentEdges = edgesRef.current
    const width = Math.max(container.clientWidth || 1100, 640)
    const height = Math.max(container.clientHeight || 640, 480)
    const overviewPoints = layoutRef.current === "overview" ? overviewPlacement(width, height, currentNodes) : null
    const graph = new Graph({
      container,
      autoResize: true,
      padding: 28,
      animation: false,
      background: "#101820",
      zoomRange: [0.35, 2.6],
      data: {
        nodes: currentNodes.map((node) => {
          const spot = overviewPoints?.get(node.id)
          const x = spot?.x ?? node.presetX
          const y = spot?.y ?? node.presetY
          return {
            id: node.id,
            data: {
              ...nodeData(node),
              labelSide: overviewPoints
                ? labelSide({ id: node.id, size: node.visualSize, parentId: node.parentId }, overviewPoints)
                : "bottom",
            },
            style: typeof x === "number" && typeof y === "number" ? { x, y } : undefined,
          }
        }),
        edges: currentEdges.map((edge) => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          data: edgeFlags(edge, currentNodes),
        })),
      },
      layout: fixedLayout(),
      node: {
        type: "circle",
        style(datum) {
          const data = datum.data
          const hub = data?.hub === true
          const headline = data?.headline === true || hub
          const size = typeof data?.visualSize === "number" ? data.visualSize : 18
          const hidden = typeof data?.hiddenCount === "number" ? data.hiddenCount : 0
          return {
            size,
            fill: typeof data?.categoryColor === "string" ? data.categoryColor : "#8ea0b3",
            fillOpacity: 1,
            stroke: hub ? "#ffffff" : "rgba(244, 250, 255, 0.78)",
            lineWidth: hub ? 2.5 : 1.2,
            lineDash: data?.uiGroup === true && !hub ? [4, 3] : [],
            cursor: "pointer" as const,
            labelText: headline ? shortLabel(data, datum.id) : "",
            labelFill: "#f7fbff",
            labelFontSize: hub ? 16 : size <= 20 ? 13 : 15,
            labelFontWeight: hub ? 700 : 600,
            labelPlacement: minorLabelPlacement(data),
            labelTextAlign: "center" as const,
            labelOffsetY: 8,
            labelWordWrap: false,
            labelBackground: true,
            labelBackgroundFill: "rgba(10, 16, 22, 0.9)",
            labelBackgroundRadius: 4,
            labelPadding: [2, 6] as [number, number],
            badge: hidden > 0,
            badges: hidden > 0 ? [plusBadge()] : [],
          }
        },
        state: {
          selected: {
            lineWidth: 3,
            stroke: "#ffffff",
            fillOpacity: 1,
            labelFill: "#f7fbff",
            labelFillOpacity: 1,
            halo: true,
            haloStroke: "rgba(255, 255, 255, 0.85)",
            haloLineWidth: 8,
          },
          active: {
            lineWidth: 2.4,
            stroke: "#ffffff",
            fillOpacity: 1,
            labelFill: "#f7fbff",
            labelFillOpacity: 1,
          },
          dim: {
            fillOpacity: 0.92,
            strokeOpacity: 0.9,
            opacity: 1,
            labelFill: "#f7fbff",
            labelFillOpacity: 1,
            labelOpacity: 1,
            badgeBackgroundOpacity: 1,
          },
        },
      },
      edge: {
        type: "line",
        style(datum) {
          const grouped = datum.data?.relation === "UI_GROUP"
          const cross = datum.data?.cross === true
          const kind = readLineKind(datum.data?.lineKind)
          const hiddenAssociation = kind === "association" && cross
          return {
            stroke: "rgba(186, 210, 226, 0.9)",
            strokeOpacity: hiddenAssociation ? 0 : kind === "association" ? 0.45 : 0.9,
            lineWidth: kind === "dashed" ? 1.15 : 1.05,
            lineDash: lineDashOf(kind),
            endArrow: !grouped,
            endArrowSize: 7,
            endArrowOffset: 10,
            labelText: "",
          }
        },
        state: {
          active: (datum: { data?: Record<string, unknown> }) => ({
            stroke: "#f4fbff",
            strokeOpacity: 1,
            lineWidth: 1.8,
            labelText: "",
            lineDash: lineDashOf(readLineKind(datum.data?.lineKind)),
          }),
          dim: (datum: { data?: Record<string, unknown> }) => ({
            strokeOpacity: datum.data?.lineKind === "association" && datum.data?.cross === true ? 0 : 0.2,
            opacity: 1,
          }),
        },
      },
      plugins: [
        {
          type: "tooltip",
          key: "node-tip",
          trigger: "hover",
          enable: (event: IElementEvent) => event.targetType === "node",
          style: {
            ".tooltip": {
              background: "#1a2430",
              color: "#f7fbff",
            },
          },
          getContent: (_event: IElementEvent, items: Array<{ data?: Record<string, unknown> }>) => {
            const data = items[0]?.data
            const name = escapeHtml(fullLabel(data, ""))
            const labels = hiddenNames(data)
            const level = typeof data?.expandLevel === "number" ? data.expandLevel : 0
            if (labels.length === 0 || level < 1) {
              return `<div style="max-width:280px;color:#f7fbff;line-height:1.5">${name}</div>`
            }
            const preview = labels.slice(0, 3).map((item) => escapeHtml(item)).join("、")
            return `<div style="max-width:300px;color:#f7fbff;line-height:1.5"><div>${name}</div><div style="margin-top:4px;color:#d7e8f6">双击展开第${level}级 · ${labels.length}个节点</div><div style="margin-top:2px">${preview}</div></div>`
          },
        },
      ],
      behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
    })
    graphRef.current = graph
    graph.on(NodeEvent.CLICK, (event: IElementEvent) => {
      pickedAt.current = Date.now()
      onSelectRef.current(event.target.id)
    })
    graph.on(NodeEvent.DBLCLICK, (event: IElementEvent) => {
      pickedAt.current = Date.now()
      onSelectRef.current(event.target.id)
      onDeeperRef.current(event.target.id)
    })
    graph.on(CanvasEvent.CLICK, () => {
      if (Date.now() - pickedAt.current < 400) {
        return
      }
      onSelectRef.current(null)
    })
    graph
      .render()
      .then(async () => {
        if (disposed || graph.destroyed) {
          return
        }
        readyEpoch.current = layoutEpoch
        await frameVisibleNodes(graph)
        await paintStates(graph, selectedIdRef.current)
      })
      .catch(() => undefined)
    return () => {
      disposed = true
      readyEpoch.current = -1
      if (!graph.destroyed) {
        graph.destroy()
      }
      if (graphRef.current === graph) {
        graphRef.current = null
      }
    }
  }, [layoutEpoch])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || graph.destroyed || !graph.rendered || readyEpoch.current !== layoutEpoch) {
      return
    }
    let cancelled = false
    const finals: Array<{ id: string; x: number; y: number }> = []
    const sync = async () => {
      const currentIds = new Set(graph.getNodeData().map((node) => node.id))
      const added = nodes.filter((node) => !currentIds.has(node.id))
      const currentEdgeIds = new Set(graph.getEdgeData().map((edge) => edge.id).filter((id): id is string => typeof id === "string"))
      const addedEdges = edges.filter((edge) => !currentEdgeIds.has(edge.id))
      graph.updateNodeData(nodes.filter((node) => currentIds.has(node.id)).map((node) => ({ id: node.id, data: nodeData(node) })))
      if (added.length === 0 && addedEdges.length === 0) {
        if (!cancelled && !graph.destroyed) {
          await graph.draw()
          await paintStates(graph, selectedIdRef.current)
        }
        return
      }
      const before = new Map(graph.getNodeData().map((node) => [node.id, readPoint(graph, node.id, node.style)]))
      if (added.length === 0) {
        graph.addEdgeData(addedEdges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, data: { ...edge.data } })))
        if (!cancelled && !graph.destroyed) {
          await graph.draw()
          await paintStates(graph, selectedIdRef.current)
        }
        return
      }
      const anchorId = anchorRef.current
      const origin = (anchorId ? before.get(anchorId) : null) ?? { x: 0, y: 0 }
      const occupants: FanOccupant[] = []
      for (const [id, point] of before) {
        if (!point) {
          continue
        }
        const datum = nodes.find((node) => node.id === id)
        occupants.push({ x: point.x, y: point.y, radius: (datum?.visualSize ?? 16) / 2 })
      }
      const nodeRadius = Math.max(12, ...added.map((node) => node.visualSize / 2))
      const size = graph.getSize()
      const targets = fanPositions(origin, occupants, added.length, nodeRadius, { width: size[0] ?? 0, height: size[1] ?? 0 })
      graph.addNodeData(
        added.map((node, index) => {
          const target = targets[index] ?? origin
          const dx = target.x - origin.x
          const dy = target.y - origin.y
          const canvasW = size[0] ?? 0
          const canvasH = size[1] ?? 0
          const side =
            target.x < 130 ? "right"
            : canvasW - target.x < 130 ? "left"
            : target.y < 90 ? "bottom"
            : canvasH - target.y < 90 ? "top"
            : Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? "right" : "left")
            : dy >= 0 ? "bottom" : "top"
          return {
            id: node.id,
            data: { ...nodeData(node), labelSide: side },
            style: { x: origin.x, y: origin.y },
          }
        }),
      )
      if (addedEdges.length > 0) {
        graph.addEdgeData(
          addedEdges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            data: edgeFlags(edge, nodes),
          })),
        )
      }
      added.forEach((node, index) => {
        finals.push({ id: node.id, x: targets[index]?.x ?? origin.x + 80, y: targets[index]?.y ?? origin.y })
      })
      if (cancelled || graph.destroyed) {
        return
      }
      await graph.draw()
      animateOut(graph, origin, finals)
      await paintStates(graph, selectedIdRef.current)
    }
    sync().catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [nodes, edges, layoutEpoch])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || graph.destroyed || !graph.rendered) {
      return
    }
    paintStates(graph, selectedId).catch(() => undefined)
  }, [selectedId])

  return <div ref={containerRef} className="graph-canvas" aria-label="知识图谱画布" />
})
