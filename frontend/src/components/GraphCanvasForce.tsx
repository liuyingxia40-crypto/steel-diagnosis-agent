import { CanvasEvent, Graph, GraphEvent, NodeEvent } from "@antv/g6"
import type { IElementEvent } from "@antv/g6"
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react"
import { fanPositions, type FanOccupant } from "../graph/fan"
import { relationLabel } from "../graph/presentation"
import { layoutPartitions } from "../graph/regions"
import type { GraphEdge, GraphNode } from "../types/graph"

export interface GraphCanvasHandle {
  fitView: () => void
}

export interface CanvasNode extends GraphNode {
  depth: number
  hiddenCount: number
  categoryColor?: string
  important?: boolean
  uiGroup?: boolean
  hub?: boolean
  headline?: boolean
  region?: string
  presetX?: number
  presetY?: number
}

interface GraphCanvasProps {
  nodes: CanvasNode[]
  edges: GraphEdge[]
  layoutEpoch: number
  layoutKind?: "preset" | "force" | "partition"
  expandAnchorId: string | null
  selectedId: string | null
  locateId: string | null
  locateToken: number
  focusId?: string | null
  focusToken?: number
  onSelect: (id: string | null) => void
  onDeeper: (id: string) => void
}

const EXPAND_MS = 280

function nodeLabel(data: Record<string, unknown> | undefined, id: string): string {
  return typeof data?.label === "string" ? data.label : id
}

function shorten(label: string, max = 12): string {
  const text = label.split("\n")[0] ?? label
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function nodeSize(node: { hub?: boolean; uiGroup?: boolean; important?: boolean; data?: { type?: string } }): number {
  if (node.hub) {
    return 48
  }
  if (node.uiGroup) {
    return 26
  }
  if (node.important && node.data?.type === "device") {
    return 30
  }
  if (node.important) {
    return 22
  }
  return 15
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

function pointOf(style: { x?: unknown; y?: unknown } | undefined): { x: number; y: number } | null {
  if (typeof style?.x !== "number" || typeof style.y !== "number") {
    return null
  }
  return { x: style.x, y: style.y }
}

function readPoint(graph: Graph, id: string, style: { x?: unknown; y?: unknown } | undefined) {
  const fromStyle = pointOf(style)
  if (fromStyle) {
    return fromStyle
  }
  try {
    const position = graph.getElementPosition(id)
    if (typeof position[0] === "number" && typeof position[1] === "number") {
      return { x: position[0], y: position[1] }
    }
  } catch {
    return null
  }
  return null
}

function forceLayout(count: number, sizeOf: Map<string, number>) {
  const dense = count > 40
  const distance = dense ? 74 : 118
  const charge = dense ? -34 : -150
  const padding = dense ? 11 : 20
  const size = (node: { id?: string }) => sizeOf.get(String(node.id)) ?? 15
  return {
    type: "d3-force" as const,
    animation: false,
    iterations: dense ? 220 : 180,
    enableWorker: false,
    clustering: false,
    alphaDecay: 0.05,
    alphaMin: 0.002,
    velocityDecay: 0.58,
    center: {
      strength: dense ? 0.035 : 0.05,
    },
    manyBody: {
      strength: (node: { id?: string }) => charge * (size(node) / 18),
      distanceMin: 8,
      distanceMax: dense ? 150 : 380,
    },
    link: {
      distance,
      strength: dense ? 0.7 : 0.4,
    },
    collide: {
      radius: (node: { id?: string }) => size(node) / 2 + padding,
      strength: 1,
      iterations: 2,
    },
    nodeSize: (node: { id?: string }) => size(node),
  }
}

function presetLayout() {
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

function countBadge(count: number) {
  return {
    text: `+${count}`,
    placement: "right" as const,
    fontSize: 8,
    fontWeight: 600,
    fill: "#d5e2ee",
    backgroundFill: "rgba(16, 24, 32, 0.55)",
    backgroundRadius: 3,
    padding: [0, 2] as [number, number],
    offsetX: 2,
    cursor: "pointer" as const,
  }
}

function nodePayload(node: CanvasNode, size: number) {
  return {
    ...node.data,
    depth: node.depth,
    hiddenCount: node.hiddenCount,
    categoryColor: node.categoryColor ?? "#8b9bab",
    important: node.important ?? false,
    uiGroup: node.uiGroup ?? false,
    hub: node.hub ?? false,
    headline: node.headline ?? false,
    region: node.region ?? "",
    showMinor: false,
    labelOn: false,
    visualSize: size,
  }
}

async function paintStates(graph: Graph, selectedId: string | null, hoverId: string | null) {
  if (graph.destroyed || !graph.rendered) {
    return
  }
  const nodes = graph.getNodeData()
  const edges = graph.getEdgeData()
  const pivot = hoverId && nodes.some((node) => node.id === hoverId) ? hoverId : selectedId
  const state: Record<string, string[]> = {}
  if (!pivot || !nodes.some((node) => node.id === pivot)) {
    for (const node of nodes) {
      state[node.id] = []
    }
    for (const edge of edges) {
      if (edge.id) {
        state[edge.id] = []
      }
    }
  } else {
    const neighborIds = new Set(graph.getNeighborNodesData(pivot).map((node) => node.id))
    const relatedEdgeIds = new Set(
      graph
        .getRelatedEdgesData(pivot)
        .map((edge) => edge.id)
        .filter((id): id is string => typeof id === "string"),
    )
    for (const node of nodes) {
      if (node.id === selectedId) {
        state[node.id] = node.id === pivot || neighborIds.has(node.id) ? ["selected", "active"] : ["selected"]
      } else if (node.id === pivot || neighborIds.has(node.id)) {
        state[node.id] = ["active"]
      } else {
        state[node.id] = ["dim"]
      }
    }
    for (const edge of edges) {
      if (!edge.id) {
        continue
      }
      state[edge.id] = relatedEdgeIds.has(edge.id) ? ["active"] : ["dim"]
    }
  }
  const named = new Set<string>()
  if (selectedId && nodes.some((node) => node.id === selectedId)) {
    named.add(selectedId)
    for (const neighbor of graph.getNeighborNodesData(selectedId)) {
      named.add(neighbor.id)
    }
  }
  graph.updateNodeData(
    nodes.map((node) => ({
      id: node.id,
      data: { ...(node.data ?? {}), labelOn: named.has(node.id) },
    })),
  )
  await graph.setElementState(state, false)
}

function animateOut(
  graph: Graph,
  origin: { x: number; y: number },
  targets: ReadonlyArray<{ id: string; x: number; y: number }>,
  cancelled: () => boolean,
) {
  const started = performance.now()
  const step = () => {
    if (cancelled() || graph.destroyed) {
      return
    }
    const progress = Math.min(1, (performance.now() - started) / EXPAND_MS)
    const ease = 1 - (1 - progress) ** 3
    graph.updateNodeData(
      targets.map((target) => ({
        id: target.id,
        style: {
          x: origin.x + (target.x - origin.x) * ease,
          y: origin.y + (target.y - origin.y) * ease,
        },
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
  {
    nodes,
    edges,
    layoutEpoch,
    layoutKind = "force",
    expandAnchorId,
    selectedId,
    locateId,
    locateToken,
    focusId = null,
    focusToken = 0,
    onSelect,
    onDeeper,
  },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const onSelectRef = useRef(onSelect)
  const onDeeperRef = useRef(onDeeper)
  const selectedIdRef = useRef(selectedId)
  const hoverRef = useRef<string | null>(null)
  const locateIdRef = useRef(locateId)
  const pendingLocate = useRef(false)
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  const anchorRef = useRef(expandAnchorId)
  const layoutKindRef = useRef(layoutKind)
  const focusIdRef = useRef(focusId)
  const readyEpoch = useRef(-1)
  nodesRef.current = nodes
  edgesRef.current = edges
  anchorRef.current = expandAnchorId
  layoutKindRef.current = layoutKind
  focusIdRef.current = focusId

  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    onDeeperRef.current = onDeeper
  }, [onDeeper])
  useEffect(() => {
    selectedIdRef.current = selectedId
  }, [selectedId])
  useEffect(() => {
    locateIdRef.current = locateId
  }, [locateId])

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
    hoverRef.current = null
    const currentNodes = nodesRef.current
    const currentEdges = edgesRef.current
    const sizeOf = new Map(currentNodes.map((node) => [node.id, nodeSize(node)]))
    const partitioned = layoutKindRef.current === "partition"
    const regionOf = new Map(currentNodes.map((node) => [node.id, node.region ?? ""]))
    const partitionPos = partitioned
      ? layoutPartitions(
          currentNodes
            .filter((node) => node.region)
            .map((node) => ({
              id: node.id,
              region: node.region ?? "",
              hub: node.hub,
              radius: (sizeOf.get(node.id) ?? 15) / 2,
            })),
          currentEdges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            relation: edge.data.relation,
          })),
          Math.max(container.clientWidth || 1280, 1100),
          Math.max(container.clientHeight || 680, 620),
        )
      : null
    const graph = new Graph({
      container,
      autoResize: true,
      autoFit: "view",
      padding: [36, 210, 28, 24],
      animation: false,
      background: "#101820",
      zoomRange: [0.12, 3],
      data: {
        nodes: currentNodes.map((node) => ({
          id: node.id,
          data: nodePayload(node, sizeOf.get(node.id) ?? 15),
          style: (() => {
            const spot = partitionPos?.[node.id]
            if (spot) {
              return { x: spot.x, y: spot.y }
            }
            if (typeof node.presetX === "number" && typeof node.presetY === "number") {
              return { x: node.presetX, y: node.presetY }
            }
            return undefined
          })(),
        })),
        edges: currentEdges.flatMap((edge) => {
          const left = regionOf.get(edge.source) ?? ""
          const right = regionOf.get(edge.target) ?? ""
          const cross = Boolean(left && right && left !== right)
          if (cross && edge.data.relation === "UI_GROUP") {
            return []
          }
          return [
            {
              id: edge.id,
              source: edge.source,
              target: edge.target,
              data: { ...edge.data, cross },
            },
          ]
        }),
      },
      layout: partitioned || layoutKindRef.current === "preset" ? presetLayout() : forceLayout(currentNodes.length, sizeOf),
      node: {
        type: "circle",
        style(datum) {
          const important = datum.data?.important === true
          const uiGroup = datum.data?.uiGroup === true
          const hub = datum.data?.hub === true
          const headline = datum.data?.headline === true || hub
          const named = datum.data?.labelOn === true
          const showMinor = datum.data?.showMinor === true
          const hidden = typeof datum.data?.hiddenCount === "number" ? datum.data.hiddenCount : 0
          const fullName = nodeLabel(datum.data, datum.id)
          const size = typeof datum.data?.visualSize === "number" ? datum.data.visualSize : 15
          const fill = typeof datum.data?.categoryColor === "string" ? datum.data.categoryColor : "#9aa8b8"
          return {
            size,
            fill,
            fillOpacity: 1,
            stroke: hub ? "#ffffff" : "rgba(244, 250, 255, 0.72)",
            strokeOpacity: 1,
            lineWidth: hub ? 2.4 : important ? 1.6 : 1,
            lineDash: uiGroup && !hub ? [4, 3] : [],
            cursor: "pointer" as const,
            labelText: headline || named || showMinor ? shorten(fullName, hub ? 8 : headline ? 14 : 12) : "",
            labelFill: "#ffffff",
            labelFillOpacity: 1,
            labelFontSize: hub ? 15 : 13,
            labelFontWeight: 650,
            labelPlacement: "bottom" as const,
            labelTextAlign: "center" as const,
            labelOffsetY: 6,
            labelBackground: true,
            labelBackgroundFill: "rgba(10, 16, 22, 0.82)",
            labelBackgroundOpacity: 1,
            labelBackgroundRadius: 3,
            labelPadding: [1, 4] as [number, number],
            badge: hidden > 0,
            badges: hidden > 0 ? [countBadge(hidden)] : [],
          }
        },
        state: {
          selected: {
            lineWidth: 3,
            stroke: "#ffffff",
            fillOpacity: 1,
            labelFillOpacity: 1,
            halo: true,
            haloStroke: "rgba(255, 255, 255, 0.72)",
            haloLineWidth: 8,
            opacity: 1,
          },
          active: {
            lineWidth: 2,
            stroke: "#ffffff",
            fillOpacity: 1,
            labelFillOpacity: 1,
            opacity: 1,
          },
          dim: {
            fillOpacity: 0.92,
            strokeOpacity: 0.9,
            labelFill: "#ffffff",
            labelFillOpacity: 1,
            labelOpacity: 1,
            badgeBackgroundOpacity: 0.9,
            opacity: 1,
          },
        },
      },
      edge: {
        type: "line",
        style(datum) {
          const grouped = datum.data?.relation === "UI_GROUP"
          const cross = datum.data?.cross === true
          const name = grouped ? "" : relationLabel(typeof datum.data?.relation === "string" ? datum.data.relation : "")
          return {
            stroke: grouped ? "rgba(176, 206, 226, 0.4)" : cross ? "rgba(150, 176, 198, 0.95)" : "rgba(186, 214, 234, 0.95)",
            strokeOpacity: grouped ? 0.45 : cross ? 0.26 : 0.82,
            lineWidth: cross ? 0.8 : 1.15,
            lineDash: grouped ? [5, 4] : [],
            endArrow: !grouped,
            endArrowSize: 6,
            endArrowOffset: 8,
            labelText: name,
            labelFill: "rgba(255, 255, 255, 0)",
            labelFontSize: 11,
            labelFontWeight: 600,
            labelBackground: false,
            labelPadding: [2, 4] as [number, number],
          }
        },
        state: {
          active: {
            stroke: "#e8f3fb",
            strokeOpacity: 1,
            lineWidth: 1.6,
            opacity: 1,
            labelFill: "#ffffff",
            labelOpacity: 1,
            labelBackground: true,
            labelBackgroundFill: "rgba(12, 18, 24, 0.92)",
          },
          dim: {
            opacity: 1,
            labelOpacity: 0,
          },
        },
      },
      plugins: [
        {
          type: "tooltip",
          key: "node-tip",
          trigger: "hover",
          enable: (event: IElementEvent) => event.targetType === "node",
          onOpenChange: () => undefined,
          style: {
            ".tooltip": {
              background: "#1a2430",
              color: "#e7eef4",
            },
          },
          getContent: (_event: IElementEvent, items: Array<{ data?: Record<string, unknown> }>) => {
            const name = nodeLabel(items[0]?.data, "")
            return `<div style="max-width:260px;line-height:1.5">${escapeHtml(name)}</div>`
          },
        },
      ],
      behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
    })
    graphRef.current = graph

    const handleNodeClick = (event: IElementEvent) => {
      onSelectRef.current(event.target.id)
    }
    const handleNodeDoubleClick = (event: IElementEvent) => {
      const id = event.target.id
      onSelectRef.current(id)
      onDeeperRef.current(id)
    }
    const handleCanvasClick = () => {
      onSelectRef.current(null)
    }
    const handleEnter = (event: IElementEvent) => {
      hoverRef.current = event.target.id
      paintStates(graph, selectedIdRef.current, hoverRef.current).catch(() => undefined)
    }
    const handleLeave = () => {
      hoverRef.current = null
      paintStates(graph, selectedIdRef.current, null).catch(() => undefined)
    }
    graph.on(NodeEvent.CLICK, handleNodeClick)
    graph.on(NodeEvent.DBLCLICK, handleNodeDoubleClick)
    graph.on(CanvasEvent.CLICK, handleCanvasClick)
    graph.on(NodeEvent.POINTER_ENTER, handleEnter)
    graph.on(NodeEvent.POINTER_LEAVE, handleLeave)
    let minorVisible: boolean | null = null
    const syncMinorLabels = () => {
      if (disposed || graph.destroyed || !graph.rendered) {
        return
      }
      const showMinor = graph.getZoom() >= 1.25
      if (minorVisible === showMinor) {
        return
      }
      minorVisible = showMinor
      graph.updateNodeData(
        graph.getNodeData().map((node) => ({
          id: node.id,
          data: { ...(node.data ?? {}), showMinor },
        })),
      )
      graph.draw().catch(() => undefined)
    }
    graph.on(GraphEvent.AFTER_TRANSFORM, syncMinorLabels)

    graph
      .render()
      .then(() => {
        if (disposed || graph.destroyed) {
          return
        }
        readyEpoch.current = layoutEpoch
        syncMinorLabels()
        const entered = focusIdRef.current
        if (entered && graph.getNodeData().some((node) => node.id === entered)) {
          graph.focusElement(entered, false).catch(() => undefined)
        }
        const target = locateIdRef.current
        if (pendingLocate.current && target) {
          pendingLocate.current = false
          graph.focusElement(target, false).catch(() => undefined)
        }
        return paintStates(graph, selectedIdRef.current, null)
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
    let finals: Array<{ id: string; x: number; y: number }> = []
    const sync = async () => {
      const currentIds = new Set(graph.getNodeData().map((node) => node.id))
      const nextIds = new Set(nodes.map((node) => node.id))
      const removedNodes = [...currentIds].filter((id) => !nextIds.has(id))
      const currentEdgeIds = new Set(
        graph
          .getEdgeData()
          .map((edge) => edge.id)
          .filter((id): id is string => typeof id === "string"),
      )
      const added = nodes.filter((node) => !currentIds.has(node.id))
      const addedEdges = edges.filter((edge) => !currentEdgeIds.has(edge.id))
      const removedEdges = [...currentEdgeIds].filter((id) => !edges.some((edge) => edge.id === id))
      if (added.length === 0 && addedEdges.length === 0 && removedNodes.length === 0 && removedEdges.length === 0) {
        graph.updateNodeData(
          nodes.map((node) => ({
            id: node.id,
            data: nodePayload(node, nodeSize(node)),
          })),
        )
        if (!cancelled && !graph.destroyed) {
          await graph.draw()
          await paintStates(graph, selectedIdRef.current, hoverRef.current)
        }
        return
      }
      const before = new Map(
        graph.getNodeData().map((node) => [node.id, readPoint(graph, node.id, node.style)]),
      )
      if (removedEdges.length > 0) {
        graph.removeEdgeData(removedEdges)
      }
      if (removedNodes.length > 0) {
        graph.removeNodeData(removedNodes)
      }
      graph.updateNodeData(
        nodes
          .filter((node) => currentIds.has(node.id))
          .map((node) => ({
            id: node.id,
            data: nodePayload(node, nodeSize(node)),
          })),
      )
      if (added.length === 0) {
        if (addedEdges.length > 0) {
          graph.addEdgeData(
            addedEdges.map((edge) => ({
              id: edge.id,
              source: edge.source,
              target: edge.target,
              data: { ...edge.data },
            })),
          )
        }
        if (!cancelled && !graph.destroyed) {
          await graph.draw()
          await paintStates(graph, selectedIdRef.current, hoverRef.current)
        }
        return
      }
      const anchorId = anchorRef.current
      const anchor = anchorId ? before.get(anchorId) ?? null : null
      const origin = anchor ?? { x: 0, y: 0 }
      const occupants: FanOccupant[] = []
      for (const [id, point] of before) {
        if (!point || !nextIds.has(id)) {
          continue
        }
        const datum = nodes.find((node) => node.id === id)
        occupants.push({
          x: point.x,
          y: point.y,
          radius: nodeSize(datum ?? { data: { type: "" } }) / 2,
        })
      }
      const targets = fanPositions(origin, occupants, added.length, 11)
      graph.addNodeData(
        added.map((node) => ({
          id: node.id,
          data: nodePayload(node, nodeSize(node)),
          style: { x: origin.x, y: origin.y },
        })),
      )
      if (addedEdges.length > 0) {
        graph.addEdgeData(
          addedEdges.map((edge) => ({
            id: edge.id,
            source: edge.source,
            target: edge.target,
            data: { ...edge.data },
          })),
        )
      }
      finals = added.map((node, index) => ({
        id: node.id,
        x: targets[index]?.x ?? origin.x,
        y: targets[index]?.y ?? origin.y,
      }))
      if (cancelled || graph.destroyed) {
        return
      }
      await graph.draw()
      if (cancelled || graph.destroyed) {
        return
      }
      animateOut(graph, origin, finals, () => cancelled || graph.destroyed)
      await paintStates(graph, selectedIdRef.current, hoverRef.current)
    }
    sync().catch(() => undefined)
    return () => {
      cancelled = true
      if (finals.length === 0 || graph.destroyed) {
        return
      }
      graph.updateNodeData(finals.map((point) => ({ id: point.id, style: { x: point.x, y: point.y } })))
      graph.draw().catch(() => undefined)
    }
  }, [nodes, edges, layoutEpoch])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || graph.destroyed || !graph.rendered) {
      return
    }
    paintStates(graph, selectedId, hoverRef.current).catch(() => undefined)
  }, [selectedId])

  useEffect(() => {
    if (!focusId || focusToken === 0) {
      return
    }
    const graph = graphRef.current
    if (!graph || graph.destroyed || !graph.rendered) {
      return
    }
    graph.focusElement(focusId, false).catch(() => undefined)
  }, [focusId, focusToken])

  useEffect(() => {
    if (!locateId || locateToken === 0) {
      return
    }
    pendingLocate.current = true
    const graph = graphRef.current
    if (!graph || graph.destroyed || !graph.rendered) {
      return
    }
    let exists = false
    try {
      exists = graph.getNodeData(locateId).id === locateId
    } catch {
      exists = false
    }
    if (!exists) {
      return
    }
    pendingLocate.current = false
    graph.focusElement(locateId, false).catch(() => undefined)
  }, [locateId, locateToken])

  return <div ref={containerRef} className="graph-canvas" aria-label="知识图谱画布" />
})
