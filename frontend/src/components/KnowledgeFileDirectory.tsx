import { useEffect, useRef, useState } from "react"
import {
  formatBytes,
  parseKnowledgeTree,
  parseProcessPayload,
  parseTreePreview,
  pipelineLines,
  shouldRefreshGraph,
  type KnowledgeFileItem,
  type KnowledgeTreeNode,
  type KnowledgeTreePreview,
  type PipelineView,
} from "../knowledge/fileDirectory"
import type { ApiErrorResponse } from "../types/api"

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.pptx"
const OPEN_ON_LOAD = ["data", "data/processed", "data/processed/graph"]

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
  return "读取文件目录失败"
}

export function KnowledgeFileDirectory({
  open,
  onToggle,
  onKnowledgeAdded,
}: {
  open: boolean
  onToggle: () => void
  onKnowledgeAdded?: () => Promise<{ ok: boolean; message: string }>
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const busyRef = useRef(new Set<string>())
  const [roots, setRoots] = useState<KnowledgeTreeNode[]>([])
  const [missingRoots, setMissingRoots] = useState<string[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selectedPath, setSelectedPath] = useState("")
  const [preview, setPreview] = useState<KnowledgeTreePreview | null>(null)
  const [pipeline, setPipeline] = useState<PipelineView | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [detailError, setDetailError] = useState("")
  const [reloadToken, setReloadToken] = useState(0)

  async function loadTree() {
    const response = await fetch("/api/knowledge/tree")
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(isApiError(body) ? body.message : `知识目录请求失败，HTTP ${response.status}`)
    }
    const tree = parseKnowledgeTree(body)
    setRoots(tree.roots)
    setMissingRoots(tree.missing_roots)
  }

  useEffect(() => {
    const controller = new AbortController()
    async function run() {
      setLoading(true)
      setError("")
      try {
        const response = await fetch("/api/knowledge/tree", { signal: controller.signal })
        const body: unknown = await response.json().catch(() => null)
        if (!response.ok) {
          throw new Error(isApiError(body) ? body.message : `知识目录请求失败，HTTP ${response.status}`)
        }
        const tree = parseKnowledgeTree(body)
        if (!controller.signal.aborted) {
          setRoots(tree.roots)
          setMissingRoots(tree.missing_roots)
          setExpanded((current) => {
            const next = new Set(current)
            OPEN_ON_LOAD.forEach((path) => next.add(path))
            return next
          })
        }
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === "AbortError") {
          return
        }
        setError(readableError(loadError))
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      }
    }
    void run()
    return () => controller.abort()
  }, [reloadToken])

  async function applyPipeline(saved: KnowledgeFileItem, next: PipelineView) {
    setPipeline(next)
    const headline = pipelineLines(next).slice(0, 4).join("；")
    if (next.pipeline_status === "failed") {
      setMessage(`「${saved.original_filename}」已保存在目录中。${headline}`)
      setError(next.error_message || "处理失败")
      return
    }
    setError("")
    if (!shouldRefreshGraph(next) || !onKnowledgeAdded) {
      setMessage(`「${saved.original_filename}」${headline}`)
      return
    }
    const refreshed = await onKnowledgeAdded()
    if (!refreshed.ok) {
      setMessage(`「${saved.original_filename}」${headline}`)
      setError(refreshed.message)
      return
    }
    setMessage(`「${saved.original_filename}」${headline} ${refreshed.message}`)
  }

  async function acceptProcessResponse(response: Response, body: unknown, fallback: string) {
    const parsed = parseProcessPayload(body)
    if (parsed) {
      await loadTree()
      setExpanded((current) => {
        const next = new Set(current)
        next.add("data/runtime/knowledge_uploads")
        next.add("data/runtime/knowledge_cleaned")
        next.add("data/runtime/knowledge_extracted")
        return next
      })
      await applyPipeline(parsed.file, parsed.pipeline)
      return
    }
    if (!response.ok) {
      throw new Error(isApiError(body) ? body.message : `${fallback}，HTTP ${response.status}`)
    }
    throw new Error("处理结果无法读取。")
  }

  async function upload(file: File) {
    setUploading(true)
    setError("")
    setMessage("")
    try {
      if (file.size > 50 * 1024 * 1024) {
        throw new Error("单个文件不能超过 50MB。")
      }
      const response = await fetch(`/api/knowledge/files?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      })
      const body: unknown = await response.json().catch(() => null)
      if (response.status === 409) {
        setError(isApiError(body) ? body.message : "这个文件正在处理。")
        return
      }
      await acceptProcessResponse(response, body, "上传失败")
    } catch (uploadError) {
      setError(readableError(uploadError))
    } finally {
      setUploading(false)
      if (inputRef.current) {
        inputRef.current.value = ""
      }
    }
  }

  async function reprocess(fileId: string) {
    if (busyRef.current.has(fileId)) {
      return
    }
    busyRef.current.add(fileId)
    setBusyId(fileId)
    setError("")
    setMessage("")
    try {
      const response = await fetch(`/api/knowledge/files/${fileId}/process`, { method: "POST" })
      const body: unknown = await response.json().catch(() => null)
      if (response.status === 409) {
        setError(isApiError(body) ? body.message : "这个文件正在处理。")
        return
      }
      await acceptProcessResponse(response, body, "重新处理失败")
      if (selectedPath) {
        await openFile(selectedPath)
      }
    } catch (processErrorValue) {
      setError(readableError(processErrorValue))
    } finally {
      busyRef.current.delete(fileId)
      setBusyId((current) => (current === fileId ? null : current))
    }
  }

  function toggle(path: string) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }

  async function openFile(path: string) {
    setSelectedPath(path)
    setPreview(null)
    setPipeline(null)
    setDetailError("")
    try {
      const response = await fetch(`/api/knowledge/tree/preview?path=${encodeURIComponent(path)}`)
      const body: unknown = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(isApiError(body) ? body.message : `文件预览请求失败，HTTP ${response.status}`)
      }
      const parsed = parseTreePreview(body)
      setPreview(parsed)
      if (parsed.related_file_id) {
        const processResponse = await fetch(`/api/knowledge/files/${parsed.related_file_id}/process-result`)
        const processBody: unknown = await processResponse.json().catch(() => null)
        if (!processResponse.ok) {
          throw new Error(isApiError(processBody) ? processBody.message : `处理结果请求失败，HTTP ${processResponse.status}`)
        }
        const process = parseProcessPayload(processBody)
        setPipeline(process?.pipeline ?? null)
      }
    } catch (loadError) {
      setDetailError(readableError(loadError))
    }
  }

  return (
    <aside className={open ? "file-directory" : "file-directory collapsed"} aria-label="知识文件目录">
      <div className="file-directory-head">
        {open && <h2>知识文件</h2>}
        <button type="button" className="ghost-button" aria-expanded={open} onClick={onToggle}>
          {open ? "收起" : "目录"}
        </button>
      </div>
      {open && (
        <>
          <input
            ref={inputRef}
            className="file-picker"
            type="file"
            accept={ACCEPT}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                void upload(file)
              }
            }}
          />
          <button type="button" className="ghost-button file-add-button" disabled={uploading} onClick={() => inputRef.current?.click()}>
            {uploading ? "正在上传" : "添加文件"}
          </button>
          {error && (
            <p className="status error" role="alert">
              {error}
              <button type="button" onClick={() => setReloadToken((value) => value + 1)}>
                重试
              </button>
            </p>
          )}
          {message && <p className="file-directory-note">{message}</p>}
          {loading && <p className="file-directory-note">正在读取文件目录...</p>}
          {!loading && missingRoots.length > 0 && <p className="file-directory-note">这些目录当前不存在：{missingRoots.join("、")}</p>}
          <div className="file-tree" role="tree">
            {roots.map((node) => (
              <TreeRows
                key={node.path}
                nodes={[node]}
                depth={0}
                expanded={expanded}
                selectedPath={selectedPath}
                onToggle={toggle}
                onOpen={(path) => void openFile(path)}
              />
            ))}
          </div>
          {preview && (
            <FileDetail
              preview={preview}
              pipeline={pipeline}
              detailError={detailError}
              busy={busyId === preview.related_file_id}
              onReprocess={preview.related_file_id ? () => void reprocess(preview.related_file_id) : undefined}
            />
          )}
          {!preview && detailError && (
            <p className="status error" role="alert">
              {detailError}
            </p>
          )}
        </>
      )}
    </aside>
  )
}

function TreeRows({
  nodes,
  depth,
  expanded,
  selectedPath,
  onToggle,
  onOpen,
}: {
  nodes: KnowledgeTreeNode[]
  depth: number
  expanded: Set<string>
  selectedPath: string
  onToggle: (path: string) => void
  onOpen: (path: string) => void
}) {
  return nodes.map((node) => {
    const isDirectory = node.kind === "directory"
    const isOpen = expanded.has(node.path)
    return (
      <div key={node.path} role="treeitem" aria-expanded={isDirectory ? isOpen : undefined}>
        <button
          type="button"
          className={selectedPath === node.path ? "tree-row selected" : "tree-row"}
          style={{ paddingLeft: 6 + depth * 14 }}
          onClick={() => (isDirectory ? onToggle(node.path) : onOpen(node.path))}
        >
          {isDirectory ? <Chevron open={isOpen} /> : <span className="tree-chevron-spacer" />}
          {isDirectory ? <FolderIcon open={isOpen} /> : <FileGlyph extension={node.extension} />}
          <span className="tree-name">{node.name}</span>
        </button>
        {isDirectory && isOpen && node.children.length > 0 && (
          <TreeRows nodes={node.children} depth={depth + 1} expanded={expanded} selectedPath={selectedPath} onToggle={onToggle} onOpen={onOpen} />
        )}
      </div>
    )
  })
}

function FileDetail({
  preview,
  pipeline,
  detailError,
  busy,
  onReprocess,
}: {
  preview: KnowledgeTreePreview
  pipeline: PipelineView | null
  detailError: string
  busy: boolean
  onReprocess?: () => void
}) {
  return (
    <section className="file-detail" aria-label="文件详情">
      <h3>{preview.name}</h3>
      <p className="file-directory-note">{preview.path}</p>
      <p className="file-directory-note">
        {preview.extension ? preview.extension.toUpperCase() : "无扩展名"} · {formatBytes(preview.size_bytes)}
        {preview.modified_at ? ` · ${preview.modified_at.replace("T", " ").slice(0, 19)}` : ""}
      </p>
      {preview.related_filename && <p className="file-directory-note">对应上传文件：{preview.related_filename}</p>}
      {preview.notice && <p className="file-directory-note">{preview.notice}</p>}
      {preview.truncated && <p className="file-directory-note">预览已截断。</p>}
      {preview.text && <pre className="file-preview-text">{preview.text}</pre>}
      {detailError && (
        <p className="status error" role="alert">
          {detailError}
        </p>
      )}
      {pipeline && pipelineLines(pipeline).map((line) => <p key={line} className="file-directory-note">{line}</p>)}
      {onReprocess && (
        <button type="button" className="ghost-button file-add-button" disabled={busy} onClick={onReprocess}>
          {busy ? "正在处理" : "重新处理"}
        </button>
      )}
    </section>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={open ? "tree-chevron open" : "tree-chevron"} viewBox="0 0 12 12" aria-hidden="true">
      <path d="M4 2.5 8 6 4 9.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="tree-folder-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d={open ? "M1.5 4.5h5l1 1.5h7v7h-13z" : "M1.5 3.5h5l1 1.5h7v8h-13z"}
        fill={open ? "#e6c36a" : "#d7b15a"}
      />
    </svg>
  )
}

function FileGlyph({ extension }: { extension: string }) {
  const label = (extension || "file").slice(0, 4)
  return <span className={`tree-file-icon ext-${extension || "file"}`}>{label}</span>
}
