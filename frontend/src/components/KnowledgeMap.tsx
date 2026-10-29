import { CanvasEvent, Graph, NodeEvent } from "@antv/g6"
import type { IElementEvent } from "@antv/g6"
import { useEffect, useRef } from "react"
import type { KnowledgeView, ViewNode } from "../graph/knowledgeView"

interface KnowledgeMapProps {
  view: KnowledgeView
  selectedId: string | null
  locateId: string | null
  locateToken: number
  visible: boolean
  onSelect: (id: string | null) => void
}

const MODULE_COLOR: Record<string, string> = {
  root: "#1f4e79",
  "KB-01": "#2f6fed",
  "KB-02": "#e07a2f",
  "KB-03": "#2f9e6b",
  "KB-04": "#d45d73",
  "KB-05": "#7a5af5",
  "KB-06": "#1aa6a6",
}

const MODULE_TINT: Record<string, string> = {
  root: "#e7eef6",
  "KB-01": "#e8f1ff",
  "KB-02": "#fff1e6",
  "KB-03": "#e7f7ef",
  "KB-04": "#fdecef",
  "KB-05": "#f1edff",
  "KB-06": "#e6f8f8",
}

interface MapDatum {
  id: string
  data?: {
    label?: string
    module?: string
    depth?: number
    hasChildren?: boolean
  }
  style?: {
    collapsed?: boolean
  }
}

function clip(label: string): string {
  return label.length > 18 ? `${label.slice(0, 18)}…` : label
}

function textWidth(text: string): number {
  let width = 0
  for (const char of text) {
    width += char.charCodeAt(0) > 255 ? 14 : 8
  }
  return width
}

function nodeSize(label: string, depth: number, hasChildren: boolean): { width: number; height: number } {
  const shown = hasChildren ? `${clip(label)} +` : clip(label)
  return {
    width: Math.max(108, Math.min(280, textWidth(shown) + 36)),
    height: depth === 0 ? 46 : depth === 1 ? 40 : 34,
  }
}

function colors(module: string, depth: number): { fill: string; stroke: string; label: string } {
  const key = module || "root"
  const solid = MODULE_COLOR[key] ?? MODULE_COLOR.root
  if (depth <= 1) {
    return { fill: solid, stroke: solid, label: "#ffffff" }
  }
  return { fill: MODULE_TINT[key] ?? "#f4f7fb", stroke: solid, label: "#1c2836" }
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}

function childCounts(nodes: ViewNode[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const node of nodes) {
    if (!node.parent_id) {
      continue
    }
    counts.set(node.parent_id, (counts.get(node.parent_id) ?? 0) + 1)
  }
  return counts
}

export function KnowledgeMap({ view, selectedId, locateId, locateToken, visible, onSelect }: KnowledgeMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const onSelectRef = useRef(onSelect)
  const selectedRef = useRef(selectedId)
  const nodesRef = useRef(view.nodes)
  onSelectRef.current = onSelect
  selectedRef.current = selectedId
  nodesRef.current = view.nodes

  useEffect(() => {
    const container = containerRef.current
    if (!container) {
      return
    }
    const counts = childCounts(view.nodes)
    const byId = new Map(view.nodes.map((node) => [node.id, node]))
    const graph = new Graph({
      container,
      animation: false,
      background: "#f7f9fc",
      data: {
        nodes: view.nodes.map((node) => {
          const hasChildren = (counts.get(node.id) ?? 0) > 0
          const size = nodeSize(node.label, node.depth, hasChildren)
          return {
            id: node.id,
            data: {
              label: node.label,
              module: node.module,
              depth: node.depth,
              width: size.width,
              height: size.height,
              hasChildren,
            },
            style: {
              collapsed: hasChildren && node.id !== view.root_id,
            },
          }
        }),
        edges: view.nodes.flatMap((node) => {
          if (!node.parent_id) {
            return []
          }
          return [{ id: `link-${node.id}`, source: node.parent_id, target: node.id }]
        }),
      },
      layout: {
        type: "mindmap",
        direction: "LR",
        preLayout: true,
        getHeight: (node: { data?: { height?: number } }) => node.data?.height ?? 34,
        getWidth: (node: { data?: { width?: number } }) => node.data?.width ?? 140,
        getVGap: () => 12,
        getHGap: () => 48,
        getSide: () => "right",
      },
      node: {
        type: "rect",
        style: (datum: MapDatum) => {
          const depth = datum.data?.depth ?? 0
          const paint = colors(datum.data?.module ?? "", depth)
          const hasChildren = Boolean(datum.data?.hasChildren)
          const marker = hasChildren ? (datum.style?.collapsed ? " +" : " -") : ""
          const width = textWidth(`${clip(datum.data?.label ?? "")}${marker}`) + 36
          return {
            radius: 8,
            size: [Math.max(108, Math.min(280, width)), datum.data?.depth === 0 ? 46 : depth === 1 ? 40 : 34],
            fill: paint.fill,
            stroke: paint.stroke,
            lineWidth: depth <= 1 ? 0 : 1.5,
            labelText: `${clip(datum.data?.label ?? datum.id)}${marker}`,
            labelFill: paint.label,
            labelFontSize: depth <= 1 ? 14 : 13,
            labelFontWeight: depth <= 1 ? 600 : 400,
            labelPlacement: "center",
            cursor: "pointer",
            ports: [
              { key: "left", placement: "left" },
              { key: "right", placement: "right" },
            ],
          }
        },
        state: {
          selected: {
            lineWidth: 2.5,
            stroke: "#16324f",
          },
        },
      },
      edge: {
        type: "cubic-horizontal",
        style: {
          stroke: "#c5d0dc",
          lineWidth: 1,
          sourcePort: "right",
          targetPort: "left",
        },
      },
      plugins: [
        {
          type: "tooltip",
          key: "map-tip",
          trigger: "hover",
          enable: (event: IElementEvent) => event.targetType === "node",
          onOpenChange: () => undefined,
          style: {
            ".tooltip": {
              background: "#ffffff",
              color: "#1c2836",
              border: "1px solid #d5dde6",
              boxShadow: "0 6px 18px rgba(22, 50, 79, 0.08)",
            },
          },
          getContent: (_event: IElementEvent, items: Array<{ data?: { label?: string } }>) => {
            const name = items[0]?.data?.label ?? ""
            return `<div style="max-width:280px;line-height:1.5">${escapeHtml(name)}</div>`
          },
        },
      ],
      behaviors: ["drag-canvas", "zoom-canvas"],
    })
    graphRef.current = graph

    const toggle = (event: IElementEvent) => {
      const id = event.target.id
      const node = byId.get(id)
      onSelectRef.current(id)
      if (!node || (counts.get(id) ?? 0) === 0) {
        return
      }
      const datum = graph.getNodeData(id)
      if (datum.style?.collapsed) {
        void graph.expandElement(id, false)
      } else {
        void graph.collapseElement(id, false)
      }
    }
    graph.on(NodeEvent.CLICK, toggle)
    graph.on(CanvasEvent.CLICK, (event: IElementEvent) => {
      if (event.targetType === "node") {
        return
      }
      onSelectRef.current(null)
    })

    let cancelled = false
    void graph.render().then(async () => {
      if (cancelled) {
        return
      }
      await graph.fitView()
      const current = selectedRef.current
      if (current) {
        await graph.setElementState(current, ["selected"])
      }
    })

    const onResize = () => graph.resize()
    window.addEventListener("resize", onResize)
    return () => {
      cancelled = true
      window.removeEventListener("resize", onResize)
      graph.destroy()
      graphRef.current = null
    }
  }, [view])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph) {
      return
    }
    let cancelled = false
    const apply = async () => {
      try {
        const nodes = graph.getNodeData()
        if (cancelled) {
          return
        }
        const states = Object.fromEntries(nodes.map((node) => [node.id, node.id === selectedId ? ["selected"] : []]))
        await graph.setElementState(states, false)
      } catch {
        // 首次绘制完成前，选中状态会在渲染结束后再设置。
      }
    }
    void apply()
    return () => {
      cancelled = true
    }
  }, [selectedId])

  useEffect(() => {
    if (!visible) {
      return
    }
    graphRef.current?.resize()
  }, [visible])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph || !locateId) {
      return
    }
    const byId = new Map(nodesRef.current.map((node) => [node.id, node]))
    let cancelled = false
    const reveal = async () => {
      const chain: string[] = []
      let current = byId.get(locateId)
      const seen = new Set<string>()
      while (current?.parent_id && !seen.has(current.id)) {
        seen.add(current.id)
        chain.push(current.parent_id)
        current = byId.get(current.parent_id)
      }
      for (const ancestor of chain.reverse()) {
        if (cancelled) {
          return
        }
        const datum = graph.getNodeData(ancestor)
        if (datum.style?.collapsed) {
          await graph.expandElement(ancestor, false)
        }
      }
      if (!cancelled) {
        await graph.focusElement(locateId)
      }
    }
    void reveal()
    return () => {
      cancelled = true
    }
  }, [locateId, locateToken])

  return (
    <section className="map-stage">
      <div ref={containerRef} className="map-canvas" />
      <p className="map-hint">单击节点展开或收起下一层。单击具体知识后，在右侧查看原文。</p>
    </section>
  )
}
