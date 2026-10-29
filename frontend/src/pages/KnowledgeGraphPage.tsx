import { useEffect, useMemo, useRef, useState } from "react"
import { GraphCanvas, type GraphCanvasHandle } from "../components/GraphCanvas"
import { CrossResearchPanel } from "../components/CrossResearchPanel"
import { KnowledgeFileDirectory } from "../components/KnowledgeFileDirectory"
import { parseKnowledgeView, type KnowledgeView } from "../graph/knowledgeView"
import {
  VIEW_CONFIG,
  buildOverview,
  findGraphNode,
  growScene,
  openFurther,
  type SceneNode,
} from "../graph/networkScene"
import { importedDeviceNames, parseKnowledgeGraph, relationLabel, typeLabel } from "../graph/presentation"
import type { ApiErrorResponse } from "../types/api"
import type { GraphEdge, GraphNode, KnowledgeGraph } from "../types/graph"

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
  return "读取知识图谱失败"
}

function kindText(kind: string | undefined): string {
  if (kind === "rated_parameter") {
    return "额定参数"
  }
  if (kind === "operating_limit") {
    return "运行限值"
  }
  return ""
}

interface RelationItem {
  edge: GraphEdge
  other: GraphNode | undefined
  outbound: boolean
}

function relationsOf(nodeId: string, graph: KnowledgeGraph): RelationItem[] {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  return graph.edges.flatMap((edge) => {
    if (edge.data.relation === "SUPPORTED_BY" || edge.data.relation === "UI_GROUP") {
      return []
    }
    if (edge.source !== nodeId && edge.target !== nodeId) {
      return []
    }
    const outbound = edge.source === nodeId
    const other = nodes.get(outbound ? edge.target : edge.source)
    if (other?.data.type === "case" && !other.data.label.startsWith("EXP-2024-")) {
      return []
    }
    return [{ edge, other, outbound }]
  })
}

export function KnowledgeGraphPage() {
  const canvasRef = useRef<GraphCanvasHandle>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [view, setView] = useState<KnowledgeView | null>(null)
  const [loadError, setLoadError] = useState("")
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState("")
  const [searchHint, setSearchHint] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [opened, setOpened] = useState<Record<string, number>>({})
  const [layoutEpoch, setLayoutEpoch] = useState(0)
  const [expandAnchorId, setExpandAnchorId] = useState<string | null>(null)
  const [directoryOpen, setDirectoryOpen] = useState(true)
  const [asideTab, setAsideTab] = useState<"detail" | "research">("detail")
  const [refreshError, setRefreshError] = useState("")
  const [refreshNote, setRefreshNote] = useState("")
  const graphRef = useRef(graph)
  graphRef.current = graph
  const openedRef = useRef(opened)
  openedRef.current = opened

  useEffect(() => {
    const element = viewportRef.current
    if (!element) {
      return
    }
    let width = element.clientWidth
    let height = element.clientHeight
    let primed = false
    const observer = new ResizeObserver(() => {
      if (!primed) {
        primed = true
        width = element.clientWidth
        height = element.clientHeight
        return
      }
      if (Object.keys(openedRef.current).length > 0) {
        return
      }
      if (Math.abs(element.clientWidth - width) < 48 && Math.abs(element.clientHeight - height) < 48) {
        return
      }
      width = element.clientWidth
      height = element.clientHeight
      setLayoutEpoch((value) => value + 1)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [loading])

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setLoading(true)
      setLoadError("")
      try {
        const [graphResponse, viewResponse] = await Promise.all([
          fetch("/api/knowledge/graph", { signal: controller.signal }),
          fetch("/api/knowledge/view", { signal: controller.signal }),
        ])
        const graphBody: unknown = await graphResponse.json().catch(() => null)
        const viewBody: unknown = await viewResponse.json().catch(() => null)
        if (!graphResponse.ok) {
          throw new Error(isApiError(graphBody) ? graphBody.message : `图谱请求失败，HTTP ${graphResponse.status}`)
        }
        if (!viewResponse.ok) {
          throw new Error(isApiError(viewBody) ? viewBody.message : `知识分类请求失败，HTTP ${viewResponse.status}`)
        }
        setGraph(parseKnowledgeGraph(graphBody))
        setView(parseKnowledgeView(viewBody))
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
  }, [])

  const overview = useMemo(() => (graph && view ? buildOverview(graph, view) : null), [graph, view])
  const scene = useMemo(() => {
    if (!overview || !graph || !view) {
      return overview
    }
    return growScene(graph, view, opened)
  }, [overview, graph, view, opened])

  const selected = scene?.nodes.find((node) => node.id === selectedId) ?? null
  const visibleIds = new Set(scene?.nodes.map((node) => node.id) ?? [])
  const business = selected && graph && !selected.uiGroup ? relationsOf(selected.id, graph) : []

  async function reloadMergedGraph(): Promise<{ ok: boolean; message: string }> {
    try {
      const response = await fetch("/api/knowledge/graph")
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        const message = isApiError(body) ? body.message : `图谱请求失败，HTTP ${response.status}`
        setRefreshError(message)
        return { ok: false, message }
      }
      const next = parseKnowledgeGraph(body)
      const current = graphRef.current
      const devices = current ? importedDeviceNames(current, next) : []
      setGraph(next)
      setRefreshError("")
      const located = devices.length > 0 ? `新增内容属于${devices.join("、")}。请双击对应设备展开，原有画面位置保持不变。` : "图谱已按后端结果更新。"
      setRefreshNote(located)
      const parent = devices.length > 0 ? next.nodes.find((node) => node.data.type === "device" && node.data.label === devices[0]) : undefined
      if (parent) {
        setSelectedId(parent.id)
      }
      return { ok: true, message: located }
    } catch (error) {
      const message = readableError(error)
      setRefreshError(message)
      return { ok: false, message }
    }
  }

  function resetOverview() {
    setOpened({})
    setExpandAnchorId(null)
    setSearchHint("")
    setLayoutEpoch((value) => value + 1)
  }

  function onDeeper(id: string) {
    if (!graph || !view) {
      return
    }
    const next = openFurther(graph, view, opened, id)
    setSelectedId(id)
    if (!next) {
      return
    }
    setExpandAnchorId(id)
    setOpened(next)
    setSearchHint("")
  }

  function runSearch() {
    const text = query.trim()
    if (!text || !graph || !scene) {
      return
    }
    const visible = scene.nodes.find(
      (node) => node.data.label.includes(text) || node.data.shortLabel.includes(text),
    )
    if (visible) {
      setSelectedId(visible.id)
      setSearchHint("")
      return
    }
    const found = findGraphNode(graph, text)
    setSearchHint(found ? "当前画面还没有这个节点。请从对应分类双击逐步展开。" : "没有找到这个名称。")
  }

  const realVisible = scene?.nodes.filter((node) => !node.uiGroup).length ?? 0

  return (
    <main className={directoryOpen ? "knowledge-network" : "knowledge-network files-collapsed"}>
      <KnowledgeFileDirectory open={directoryOpen} onToggle={() => setDirectoryOpen((value) => !value)} onKnowledgeAdded={reloadMergedGraph} />
      <section className="network-stage">
        <div className="network-toolbar">
          <label className="field-label" htmlFor="graph-search">
            搜索
          </label>
          <input
            id="graph-search"
            className="graph-search network-search"
            value={query}
            placeholder="设备、故障或案例"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                runSearch()
              }
            }}
          />
          <button type="button" className="ghost-button" onClick={runSearch}>
            定位
          </button>
          {Object.keys(opened).length > 0 && (
            <button type="button" className="ghost-button" onClick={resetOverview}>
              返回全局
            </button>
          )}
          <button type="button" className="ghost-button" onClick={() => canvasRef.current?.fitView()}>
            适应画布
          </button>
          <button type="button" className="ghost-button" onClick={() => setAsideTab("research")}>
            交叉研究
          </button>
          <p className="network-note">
            {graph ? `当前图谱 ${graph.nodes.length} 个节点，${graph.edges.length} 条关系` : "正在读取图谱"}
            {scene ? ` · 当前 ${realVisible} 个知识节点` : ""}
            {searchHint ? ` · ${searchHint}` : ""}
            {refreshNote ? ` · ${refreshNote}` : ""}
          </p>
        </div>
        <div ref={viewportRef} className="network-canvas">
          <div className="category-legend" aria-label="知识分类">
            {VIEW_CONFIG.categories.map((item) => (
              <span key={item.id}>
                <i style={{ background: item.color }} />
                {item.label}
              </span>
            ))}
            <span>
              <svg className="legend-stroke" viewBox="0 0 28 8" aria-hidden="true">
                <line x1="1" y1="4" x2="27" y2="4" stroke="#d5e6f2" strokeWidth="2" />
              </svg>
              实线：一级、二级层级连接
            </span>
            <span>
              <svg className="legend-stroke" viewBox="0 0 28 8" aria-hidden="true">
                <line x1="1" y1="4" x2="27" y2="4" stroke="#d5e6f2" strokeWidth="2" strokeDasharray="5 3" />
              </svg>
              虚线：三级及以下层级连接
            </span>
            <span>
              <svg className="legend-stroke" viewBox="0 0 28 8" aria-hidden="true">
                <line x1="1" y1="4" x2="27" y2="4" stroke="#d5e6f2" strokeWidth="2" strokeDasharray="2 2" />
              </svg>
              点线：非层级关联
            </span>
          </div>
          {loading && <p className="graph-empty">正在读取本机知识图谱...</p>}
          {refreshError && (
            <p className="status error" role="alert">
              {refreshError}
            </p>
          )}
          {!loading && loadError && (
            <p className="status error" role="alert">
              {loadError}
            </p>
          )}
          {!loading && !loadError && scene && scene.nodes.length > 0 && (
            <GraphCanvas
              ref={canvasRef}
              nodes={scene.nodes}
              edges={scene.edges}
              layout="overview"
              layoutEpoch={layoutEpoch}
              expandAnchorId={expandAnchorId}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onDeeper={onDeeper}
            />
          )}
        </div>
      </section>
      <aside className="graph-detail">
        <div className="detail-tabs">
          <button type="button" className="ghost-button" aria-pressed={asideTab === "detail"} onClick={() => setAsideTab("detail")}>
            节点详情
          </button>
          <button type="button" className="ghost-button" aria-pressed={asideTab === "research"} onClick={() => setAsideTab("research")}>
            交叉研究
          </button>
        </div>
        {asideTab === "detail" ? (
          <>
            <h2>节点详情</h2>
            {!selected && <p className="hint side-hint">单击节点查看关系和官方来源。双击在原位置外围展开真实关联，已有节点不会整图重排。实线连接一级和二级，虚线连接三级及以下。</p>}
            {selected && <Detail node={selected} relations={business} visibleIds={visibleIds} />}
          </>
        ) : (
          <>
            <h2>交叉研究</h2>
            {graph ? (
              <CrossResearchPanel graph={graph} selected={selected ? { id: selected.id, label: selected.data.label } : null} />
            ) : (
              <p className="hint side-hint">图谱还未载入，暂时不能选择节点。</p>
            )}
          </>
        )}
      </aside>
    </main>
  )
}

function Detail({
  node,
  relations,
  visibleIds,
}: {
  node: SceneNode
  relations: RelationItem[]
  visibleIds: Set<string>
}) {
  const parameter = [node.data.value, node.data.unit].filter(Boolean).join(" ")
  return (
    <div className="detail-body">
      <p className="detail-label">{node.data.label}</p>
      <p className="detail-type">
        <span className="type-swatch" style={{ background: node.categoryColor }} />
        {node.uiGroup ? "知识分类" : typeLabel(node.data.type)}
        {kindText(node.data.kind) ? ` · ${kindText(node.data.kind)}` : ""}
      </p>
      {node.uiGroup && <p className="hint side-hint">这是知识分类，不会写入工业关系。</p>}
      {node.data.type === "ui-note" && <p className="hint side-hint">这是原文里的简短摘要，不是新的工业关系。</p>}
      {parameter && (
        <p className="detail-type">
          {parameter}
          {node.data.device_label ? ` · ${node.data.device_label}` : ""}
        </p>
      )}
      {node.summary && <p className="hint side-hint">{node.summary}</p>}
      {node.hiddenLabels.length > 0 && (
        <>
          <h3>下级内容：{node.hiddenLabels.length}项</h3>
          <ul className="child-list">
            {node.hiddenLabels.map((label, index) => (
              <li key={`${label}-${index}`}>{label}</li>
            ))}
          </ul>
          <p className="hint side-hint">双击节点展开下一层。</p>
        </>
      )}
      {!node.uiGroup && node.data.type !== "ui-note" && (
        <>
          <h3>业务关系</h3>
          {relations.length === 0 ? (
            <p className="hint side-hint">没有直接业务关系。</p>
          ) : (
            <ul className="relation-list">
              {relations.map((item) => (
                <li key={item.edge.id}>
                  <span>{relationLabel(item.edge.data.relation)}</span>
                  <strong>{item.outbound ? " → " : " ← "}</strong>
                  <span>{item.other?.data.label ?? "未知节点"}</span>
                  {item.other && !visibleIds.has(item.other.id) && <em className="off-canvas">未在当前画布中</em>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <h3>官方知识来源</h3>
      {node.data.source_refs.length === 0 ? (
        <p className="hint side-hint">该节点没有记录来源位置。</p>
      ) : (
        <ul className="source-list">
          {node.data.source_refs.map((ref, index) => (
            <li key={`${ref.document}-${ref.locator}-${index}`}>
              <p>{ref.document || "未记录文档"}</p>
              <p>{ref.section || "未记录章节"}</p>
              <p>{ref.locator || "未记录位置"}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
