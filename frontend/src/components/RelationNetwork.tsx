import { useEffect, useMemo, useRef, useState } from "react"
import { GraphCanvas, type CanvasNode, type GraphCanvasHandle } from "./GraphCanvasForce"
import {
  DEPTH_LEGEND,
  createExplore,
  depthLabel,
  expandNode,
  exploreView,
  hiddenNeighborCount,
  nodeDepths,
  type ExploreState,
} from "../graph/explore"
import { parseKnowledgeGraph, relationLabel, typeColor, typeLabel } from "../graph/presentation"
import type { ApiErrorResponse } from "../types/api"
import type { GraphEdge, GraphNode, KnowledgeGraph } from "../types/graph"

interface RelationNetworkProps {
  seedId: string
  onBack: () => void
}

function isApiError(value: unknown): value is ApiErrorResponse {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return record.success === false && typeof record.message === "string"
}

function readableError(error: unknown): string {
  if (error instanceof Error) {
    return error.message === "Failed to fetch" ? "无法连接后端。请确认本机后端已启动。" : error.message
  }
  return "读取正式图谱失败"
}

function kindLabel(kind: string): string {
  if (kind === "rated_parameter") {
    return "额定参数"
  }
  if (kind === "operating_limit") {
    return "运行限值"
  }
  return kind
}

interface RelationItem {
  edge: GraphEdge
  other: GraphNode | undefined
  outbound: boolean
}

function relationsOf(nodeId: string, graph: KnowledgeGraph): RelationItem[] {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  return graph.edges.flatMap((edge) => {
    if (edge.source !== nodeId && edge.target !== nodeId) {
      return []
    }
    const outbound = edge.source === nodeId
    return [{ edge, other: byId.get(outbound ? edge.target : edge.source), outbound }]
  })
}

function attributeRows(node: GraphNode): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = []
  if (node.data.kind) {
    rows.push({ label: "参数种类", value: kindLabel(node.data.kind) })
  }
  if (node.data.value) {
    const unit = node.data.unit && !node.data.value.includes(node.data.unit) ? ` ${node.data.unit}` : ""
    rows.push({ label: "数值", value: `${node.data.value}${unit}` })
  } else if (node.data.unit) {
    rows.push({ label: "单位", value: node.data.unit })
  }
  if (node.data.device_label) {
    rows.push({ label: "所属设备", value: node.data.device_label })
  }
  if (node.data.applicability) {
    rows.push({ label: "适用范围", value: node.data.applicability })
  }
  return rows
}

function businessTypesOf(graph: KnowledgeGraph): Set<string> {
  return new Set(graph.nodes.map((node) => node.data.type).filter((type) => type !== "source"))
}

export function RelationNetwork({ seedId, onBack }: RelationNetworkProps) {
  const canvasRef = useRef<GraphCanvasHandle>(null)
  const visibleRef = useRef<Set<string>>(new Set())
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [loadError, setLoadError] = useState("")
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState("")
  const [matchCount, setMatchCount] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(seedId)
  const [explore, setExplore] = useState<ExploreState | null>(null)
  const [layoutEpoch, setLayoutEpoch] = useState(0)
  const [expandAnchorId, setExpandAnchorId] = useState<string | null>(null)
  const [legendOpen, setLegendOpen] = useState(false)
  const [locateId, setLocateId] = useState<string | null>(null)
  const [locateToken, setLocateToken] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setLoading(true)
      setLoadError("")
      try {
        const response = await fetch("/api/knowledge/graph", { signal: controller.signal })
        const body: unknown = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(isApiError(body) ? body.message : `请求失败，HTTP ${response.status}`)
        }
        const parsed = parseKnowledgeGraph(body)
        if (!parsed.nodes.some((node) => node.id === seedId)) {
          throw new Error("正式图谱中没有这个节点，没有生成新的关系。")
        }
        setGraph(parsed)
        setSelectedId(seedId)
        setExplore(createExplore(seedId))
        setLayoutEpoch((current) => current + 1)
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return
        }
        setLoadError(readableError(error))
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    }
    void load()
    return () => controller.abort()
  }, [seedId])

  const businessTypes = useMemo(() => (graph ? businessTypesOf(graph) : new Set<string>()), [graph])
  const view = useMemo(() => {
    if (!graph || !explore) {
      return null
    }
    return exploreView(graph, explore, businessTypes, false)
  }, [graph, explore, businessTypes])
  const depths = useMemo(() => {
    if (!graph || !explore || !view) {
      return new Map<string, number>()
    }
    return nodeDepths(graph, explore, view.visibleIds)
  }, [graph, explore, view])
  const visibleIds = view?.visibleIds ?? new Set<string>()
  visibleRef.current = visibleIds

  const canvasNodes = useMemo<CanvasNode[]>(() => {
    if (!graph || !view) {
      return []
    }
    return view.nodes.map((node) => ({
      ...node,
      depth: depths.get(node.id) ?? 1,
      hiddenCount: hiddenNeighborCount(graph, view.visibleIds, node.id, false, businessTypes),
    }))
  }, [graph, view, depths, businessTypes])
  const canvasEdges = view?.edges ?? []
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId) ?? null
  const seedNode = graph?.nodes.find((node) => node.id === seedId) ?? null
  const selectedCanvas = canvasNodes.find((node) => node.id === selectedId)
  const selectedRelations = selectedNode && graph ? relationsOf(selectedNode.id, graph) : []
  const businessRelations = selectedRelations.filter((item) => item.edge.data.relation !== "SUPPORTED_BY")
  const sourceRelations = selectedRelations.filter((item) => item.edge.data.relation === "SUPPORTED_BY")

  useEffect(() => {
    if (!graph) {
      return
    }
    const timer = window.setTimeout(() => {
      const text = query.trim().toLowerCase()
      if (!text) {
        setMatchCount(0)
        return
      }
      const matches = graph.nodes.filter(
        (node) => node.data.type !== "source" && node.data.label.toLowerCase().includes(text),
      )
      setMatchCount(matches.length)
      const visibleMatch = matches.find((node) => visibleRef.current.has(node.id))
      const target = visibleMatch ?? matches[0]
      if (!target) {
        return
      }
      setSelectedId(target.id)
      setLocateId(target.id)
      setLocateToken((current) => current + 1)
      if (!visibleRef.current.has(target.id)) {
        setExplore(createExplore(target.id))
        setExpandAnchorId(null)
        setLayoutEpoch((current) => current + 1)
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [query, graph])

  function goDeeper(id: string) {
    if (!explore) {
      return
    }
    const next = expandNode(explore, id)
    setSelectedId(id)
    if (next === explore) {
      return
    }
    setExpandAnchorId(id)
    setExplore(next)
  }

  return (
    <section className="graph-page network-page">
      <div className="graph-stage">
        <div className="graph-viewport">
          <div className="graph-toolbar">
            <button type="button" className="graph-back" onClick={onBack}>
              返回知识结构
            </button>
            <input
              className="graph-float-search"
              value={query}
              placeholder="在正式图谱中搜索"
              aria-label="在正式图谱中搜索"
              onChange={(event) => setQuery(event.target.value)}
            />
            {matchCount > 0 && <span className="graph-match">找到 {matchCount} 个</span>}
            {seedNode && <span className="graph-match">当前入口：{seedNode.data.label}</span>}
          </div>
          <aside className="depth-legend" aria-label="展示深度图例">
            <ul>
              {DEPTH_LEGEND.map((item) => (
                <li key={item.level}>
                  <span className="type-swatch" style={{ background: item.color }} />
                  <span>
                    <strong>
                      {item.level}：{item.name}
                    </strong>
                    <em>{item.hint}</em>
                  </span>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="legend-more"
              aria-expanded={legendOpen}
              onClick={() => setLegendOpen((open) => !open)}
            >
              {legendOpen ? "收起说明" : "关系与操作"}
            </button>
            {legendOpen && (
              <div className="legend-extra">
                <p>这里只显示 graph.json 里已经记录的业务关系。知识目录的父子层级不会画成工业关系。</p>
                <p>单击查看节点。双击从该节点向外展开下一层已有关系。</p>
              </div>
            )}
          </aside>
          {loading && <p className="graph-empty">正在读取正式图谱...</p>}
          {!loading && loadError && (
            <p className="status error" role="alert">
              {loadError}
            </p>
          )}
          {!loading && !loadError && canvasNodes.length > 0 && (
            <GraphCanvas
              ref={canvasRef}
              nodes={canvasNodes}
              edges={canvasEdges}
              layoutEpoch={layoutEpoch}
              expandAnchorId={expandAnchorId}
              selectedId={selectedId}
              locateId={locateId}
              locateToken={locateToken}
              onSelect={setSelectedId}
              onDeeper={goDeeper}
            />
          )}
        </div>
      </div>
      <aside className="graph-detail">
        <h2>正式关系</h2>
        {!selectedNode && <p className="hint side-hint">单击节点查看这条业务关系的依据。</p>}
        {selectedNode && (
          <div className="detail-body">
            <p className="detail-label">{selectedNode.data.label}</p>
            <p className="detail-type">
              <span className="type-swatch" style={{ background: typeColor(selectedNode.data.type) }} />
              {typeLabel(selectedNode.data.type)}
            </p>
            <p className="detail-type">展示深度：{depthLabel(selectedCanvas?.depth ?? 1)}</p>
            {selectedCanvas && selectedCanvas.hiddenCount > 0 && (
              <p className="hint side-hint">还有 {selectedCanvas.hiddenCount} 个直接关联未展开。双击后从该节点周围展开。</p>
            )}
            {attributeRows(selectedNode).length > 0 && (
              <ul className="relation-list">
                {attributeRows(selectedNode).map((row) => (
                  <li key={row.label}>
                    <span>{row.label}</span>
                    <strong>：</strong>
                    <span>{row.value}</span>
                  </li>
                ))}
              </ul>
            )}
            <h3>直接关联</h3>
            {businessRelations.length === 0 ? (
              <p className="hint side-hint">没有直接业务关系。</p>
            ) : (
              <ul className="relation-list">
                {businessRelations.map((item) => (
                  <li key={item.edge.id}>
                    <span>{relationLabel(item.edge.data.relation)}</span>
                    <strong>{item.outbound ? " → " : " ← "}</strong>
                    <span>{item.other?.data.label ?? "未知节点"}</span>
                    {item.other && <span> · {typeLabel(item.other.data.type)}</span>}
                  </li>
                ))}
              </ul>
            )}
            <h3>来源关系</h3>
            {sourceRelations.length === 0 ? (
              <p className="hint side-hint">没有来源支撑关系。</p>
            ) : (
              <ul className="relation-list">
                {sourceRelations.map((item) => (
                  <li key={item.edge.id}>
                    <span>{relationLabel(item.edge.data.relation)}</span>
                    <strong>{item.outbound ? " → " : " ← "}</strong>
                    <span>{item.other?.data.label ?? "未知节点"}</span>
                  </li>
                ))}
              </ul>
            )}
            <h3>官方知识来源</h3>
            {selectedNode.data.source_refs.length === 0 ? (
              <p className="hint side-hint">该节点没有记录来源位置。</p>
            ) : (
              <ul className="source-list">
                {selectedNode.data.source_refs.map((ref, index) => (
                  <li key={`${ref.document}-${ref.locator}-${index}`}>
                    <p>{ref.document || "未记录文档"}</p>
                    <p>{ref.section || "未记录章节"}</p>
                    <p>{ref.locator || "未记录位置"}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>
    </section>
  )
}
