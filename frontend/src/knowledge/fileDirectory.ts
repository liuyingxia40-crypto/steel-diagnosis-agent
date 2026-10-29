export type KnowledgeSourceType = "existing_knowledge" | "user_upload"
export type KnowledgeProcessingStatus =
  | "official_catalog"
  | "pending_cleaning"
  | "cleaning"
  | "cleaned"
  | "cleaning_failed"

export interface KnowledgeFileItem {
  file_id: string
  original_filename: string
  extension: string
  size_bytes: number
  sha256: string
  uploaded_at: string | null
  source_type: KnowledgeSourceType
  processing_status: KnowledgeProcessingStatus
  status_text: string
  error_message: string
  cleaned_at: string | null
  participates_in_qa: false
  in_graph: false
}

export interface CleanedPreview {
  file_id: string
  original_filename: string
  processing_status: KnowledgeProcessingStatus
  error_message: string
  in_graph: false
  notice: string
  section_count: number
  preview_truncated: boolean
  preview: {
    schema_version: string
    file_id: string
    source: {
      original_filename: string
      source_type: string
      extension: string
      sha256: string
    }
    sections: Array<{
      section_id: string
      heading: string
      text: string
      kind: string
      locator: Record<string, unknown>
    }>
    warnings: string[]
  } | null
}

const SOURCE_LABEL: Record<KnowledgeSourceType, string> = {
  existing_knowledge: "原有知识库",
  user_upload: "用户新增资料",
}

const STATUS_LABEL: Record<KnowledgeProcessingStatus, string> = {
  official_catalog: "原有知识库",
  pending_cleaning: "待清洗",
  cleaning: "正在清洗",
  cleaned: "已清洗",
  cleaning_failed: "清洗失败",
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

export function sourceLabel(source: KnowledgeSourceType): string {
  return SOURCE_LABEL[source]
}

export function statusLabel(status: KnowledgeProcessingStatus): string {
  return STATUS_LABEL[status]
}

export function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function formatUploadedAt(value: string | null): string {
  if (!value) {
    return "无上传时间"
  }
  return value.replace("T", " ").slice(0, 19)
}

function isUserStatus(value: unknown): value is Exclude<KnowledgeProcessingStatus, "official_catalog"> {
  return value === "pending_cleaning" || value === "cleaning" || value === "cleaned" || value === "cleaning_failed"
}

function readFile(value: unknown): KnowledgeFileItem {
  if (!isRecord(value)) {
    throw new Error("文件记录格式不正确")
  }
  const source = value.source_type
  const status = value.processing_status
  if (source !== "existing_knowledge" && source !== "user_upload") {
    throw new Error("文件来源类别不正确")
  }
  if (status !== "official_catalog" && !isUserStatus(status)) {
    throw new Error("文件处理状态不正确")
  }
  if (source === "user_upload" && !isUserStatus(status)) {
    throw new Error("新增资料状态不正确")
  }
  if (source === "existing_knowledge" && status !== "official_catalog") {
    throw new Error("原有知识库状态不正确")
  }
  const filename = value.original_filename
  const extension = value.extension
  const fileId = value.file_id
  const sha256 = value.sha256
  if (
    typeof filename !== "string" ||
    !filename ||
    filename.includes("/") ||
    filename.includes("\\") ||
    typeof extension !== "string" ||
    !extension ||
    typeof fileId !== "string" ||
    !fileId ||
    fileId.includes("/") ||
    fileId.includes("\\") ||
    typeof sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(sha256) ||
    typeof value.size_bytes !== "number" ||
    value.participates_in_qa !== false ||
    value.in_graph !== false
  ) {
    throw new Error("文件记录缺少必要字段")
  }
  const uploadedAt = value.uploaded_at
  if (uploadedAt !== null && typeof uploadedAt !== "string") {
    throw new Error("上传时间格式不正确")
  }
  if (source === "user_upload" && !uploadedAt) {
    throw new Error("新增资料缺少上传时间")
  }
  return {
    file_id: fileId,
    original_filename: filename,
    extension,
    size_bytes: value.size_bytes,
    sha256,
    uploaded_at: uploadedAt,
    source_type: source,
    processing_status: status,
    status_text: statusLabel(status),
    error_message: typeof value.error_message === "string" ? value.error_message : "",
    cleaned_at: typeof value.cleaned_at === "string" ? value.cleaned_at : null,
    participates_in_qa: false,
    in_graph: false,
  }
}

export function parseCleanedPreview(value: unknown): CleanedPreview {
  if (!isRecord(value) || value.in_graph !== false || typeof value.notice !== "string") {
    throw new Error("清洗预览格式不正确")
  }
  const status = value.processing_status
  if (status !== "pending_cleaning" && status !== "cleaning" && status !== "cleaned" && status !== "cleaning_failed") {
    throw new Error("清洗状态不正确")
  }
  if (status !== "cleaned" && value.preview !== null) {
    throw new Error("未完成清洗时不能带有预览内容")
  }
  let preview: CleanedPreview["preview"] = null
  if (status === "cleaned") {
    if (!isRecord(value.preview) || !Array.isArray(value.preview.sections)) {
      throw new Error("已清洗文件缺少预览")
    }
    const source = value.preview.source
    if (!isRecord(source) || typeof source.original_filename !== "string" || typeof source.sha256 !== "string") {
      throw new Error("清洗预览缺少来源")
    }
    preview = {
      schema_version: String(value.preview.schema_version ?? ""),
      file_id: String(value.preview.file_id ?? ""),
      source: {
        original_filename: source.original_filename,
        source_type: String(source.source_type ?? ""),
        extension: String(source.extension ?? ""),
        sha256: source.sha256,
      },
      sections: value.preview.sections.flatMap((item) => {
        if (!isRecord(item) || typeof item.text !== "string" || typeof item.section_id !== "string") {
          return []
        }
        return [
          {
            section_id: item.section_id,
            heading: typeof item.heading === "string" ? item.heading : "",
            text: item.text,
            kind: typeof item.kind === "string" ? item.kind : "",
            locator: isRecord(item.locator) ? item.locator : {},
          },
        ]
      }),
      warnings: Array.isArray(value.preview.warnings)
        ? value.preview.warnings.filter((item): item is string => typeof item === "string")
        : [],
    }
  }
  return {
    file_id: String(value.file_id ?? ""),
    original_filename: String(value.original_filename ?? ""),
    processing_status: status,
    error_message: typeof value.error_message === "string" ? value.error_message : "",
    in_graph: false,
    notice: value.notice,
    section_count: typeof value.section_count === "number" ? value.section_count : 0,
    preview_truncated: value.preview_truncated === true,
    preview,
  }
}

export function parseKnowledgeFileList(value: unknown): KnowledgeFileItem[] {
  if (!isRecord(value) || !Array.isArray(value.files)) {
    throw new Error("文件目录数据缺少 files")
  }
  return value.files.map((item) => readFile(item))
}

export interface PipelineCounts {
  entity_count: number
  relation_count: number
  matched_node_count: number
  new_node_count: number
  new_relation_count: number
  duplicate_relation_count: number
  conflict_count: number
  review_count: number
  blocked_count: number
}

export interface PipelineStage {
  stage: string
  status: string
  reused: boolean
}

export interface PipelineView {
  process_id: string
  pipeline_status: string
  outcome: string
  message: string
  knowledge_added: boolean
  nothing_added: boolean
  pending_review: boolean
  counts: PipelineCounts
  stages: PipelineStage[]
  failure_stage: string
  error_message: string
  duration_ms: number
  in_graph: false
  notice: string
}

export interface ProcessPayload {
  file: KnowledgeFileItem
  pipeline: PipelineView
}

const COUNT_KEYS = [
  "entity_count",
  "relation_count",
  "matched_node_count",
  "new_node_count",
  "new_relation_count",
  "duplicate_relation_count",
  "conflict_count",
  "review_count",
  "blocked_count",
] as const

const STAGE_LABEL: Record<string, string> = {
  cleaning: "JSON清洗",
  extracting: "知识抽取",
  previewing: "入图预演",
  importing: "安全入图",
}

const STAGE_ORDER = ["cleaning", "extracting", "previewing", "importing"]

function readCount(record: Record<string, unknown>, key: (typeof COUNT_KEYS)[number]): number {
  const value = record[key]
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("处理统计缺少数量")
  }
  return value
}

export function parsePipeline(value: unknown): PipelineView {
  if (!isRecord(value)) {
    throw new Error("处理结果格式不正确")
  }
  if (value.in_graph !== false) {
    throw new Error("处理结果不能标成已在图谱中")
  }
  if (typeof value.knowledge_added !== "boolean" || typeof value.nothing_added !== "boolean" || typeof value.pending_review !== "boolean") {
    throw new Error("处理结果缺少入图结论")
  }
  if (!isRecord(value.counts)) {
    throw new Error("处理结果缺少统计")
  }
  const source = value.counts as Record<string, unknown>
  const counts: PipelineCounts = {
    entity_count: readCount(source, "entity_count"),
    relation_count: readCount(source, "relation_count"),
    matched_node_count: readCount(source, "matched_node_count"),
    new_node_count: readCount(source, "new_node_count"),
    new_relation_count: readCount(source, "new_relation_count"),
    duplicate_relation_count: readCount(source, "duplicate_relation_count"),
    conflict_count: readCount(source, "conflict_count"),
    review_count: readCount(source, "review_count"),
    blocked_count: readCount(source, "blocked_count"),
  }
  const stages = Array.isArray(value.stages)
    ? value.stages.flatMap((item) => {
        if (!isRecord(item) || typeof item.stage !== "string" || typeof item.status !== "string") {
          return []
        }
        return [{ stage: item.stage, status: item.status, reused: item.reused === true }]
      })
    : []
  return {
    process_id: typeof value.process_id === "string" ? value.process_id : "",
    pipeline_status: typeof value.pipeline_status === "string" ? value.pipeline_status : "",
    outcome: typeof value.outcome === "string" ? value.outcome : "",
    message: typeof value.message === "string" ? value.message : "",
    knowledge_added: value.knowledge_added,
    nothing_added: value.nothing_added,
    pending_review: value.pending_review,
    counts,
    stages,
    failure_stage: typeof value.failure_stage === "string" ? value.failure_stage : "",
    error_message: typeof value.error_message === "string" ? value.error_message : "",
    duration_ms: typeof value.duration_ms === "number" && Number.isFinite(value.duration_ms) ? value.duration_ms : 0,
    in_graph: false,
    notice: typeof value.notice === "string" ? value.notice : "",
  }
}

export function parseProcessPayload(value: unknown): ProcessPayload | null {
  if (!isRecord(value) || !isRecord(value.pipeline)) {
    return null
  }
  return { file: readFile(value), pipeline: parsePipeline(value.pipeline) }
}

export function shouldRefreshGraph(pipeline: PipelineView): boolean {
  return pipeline.pipeline_status === "completed" && pipeline.knowledge_added && (pipeline.counts.new_node_count > 0 || pipeline.counts.new_relation_count > 0)
}

export function pipelineCardLines(pipeline: PipelineView): string[] {
  return pipelineLines(pipeline).filter(
    (line) =>
      line.startsWith("JSON清洗") ||
      line.startsWith("知识抽取") ||
      line.startsWith("入图预演") ||
      line.startsWith("安全入图") ||
      line.startsWith("真正新增") ||
      line.startsWith("没有可安全") ||
      line.startsWith("存在待审核") ||
      line.startsWith("处理失败") ||
      line.startsWith("失败阶段") ||
      line.startsWith("失败原因") ||
      line.startsWith("新增节点") ||
      line.startsWith("耗时"),
  )
}

export function formatDuration(durationMs: number): string {
  if (durationMs < 1000) {
    return `${Math.round(durationMs)} 毫秒`
  }
  return `${(durationMs / 1000).toFixed(1)} 秒`
}

export function pipelineLines(pipeline: PipelineView): string[] {
  const lines: string[] = ["文件已保存"]
  if (pipeline.pipeline_status === "not_started") {
    lines.push("还没有处理记录")
    return lines
  }
  if (pipeline.pipeline_status === "running") {
    lines.push("正在处理")
  }
  for (const stage of STAGE_ORDER) {
    const found = pipeline.stages.find((item) => item.stage === stage)
    const label = STAGE_LABEL[stage] ?? stage
    if (!found) {
      lines.push(`${label}：未执行`)
      continue
    }
    if (found.status === "failed") {
      lines.push(`${label}：失败`)
      continue
    }
    if (found.status !== "completed") {
      lines.push(`${label}：${found.status || "未完成"}`)
      continue
    }
    if (stage === "cleaning") {
      lines.push(found.reused ? "JSON清洗成功（沿用已有结果）" : "JSON清洗成功")
    } else if (stage === "extracting") {
      lines.push("知识抽取成功")
    } else if (stage === "previewing") {
      lines.push("入图预演已完成")
    } else if (pipeline.knowledge_added && (pipeline.counts.new_node_count > 0 || pipeline.counts.new_relation_count > 0)) {
      lines.push(`安全入图已完成，新增 ${pipeline.counts.new_node_count} 个节点、${pipeline.counts.new_relation_count} 条关系`)
    } else {
      lines.push("安全入图已执行，没有新增知识")
    }
  }
  if (pipeline.pipeline_status === "completed") {
    lines.push("知识处理完成")
    if (pipeline.knowledge_added && (pipeline.counts.new_node_count > 0 || pipeline.counts.new_relation_count > 0)) {
      lines.push(`真正新增知识：${pipeline.counts.new_node_count} 个节点，${pipeline.counts.new_relation_count} 条关系`)
    } else {
      lines.push("没有可安全入图的知识")
    }
    if (pipeline.pending_review) {
      lines.push("存在待审核知识")
    }
  }
  if (pipeline.pipeline_status === "failed") {
    lines.push("处理失败")
    lines.push(`失败阶段：${STAGE_LABEL[pipeline.failure_stage] ?? (pipeline.failure_stage || "未标明")}`)
    if (pipeline.error_message) {
      lines.push(`失败原因：${pipeline.error_message}`)
    }
  }
  lines.push(
    `新增节点 ${pipeline.counts.new_node_count}，新增关系 ${pipeline.counts.new_relation_count}，重复关系 ${pipeline.counts.duplicate_relation_count}，冲突 ${pipeline.counts.conflict_count}，待审核 ${pipeline.counts.review_count}，阻止入图 ${pipeline.counts.blocked_count}`,
  )
  lines.push(`耗时 ${formatDuration(pipeline.duration_ms)}`)
  if (pipeline.message) {
    lines.push(pipeline.message)
  }
  return lines
}

export interface KnowledgeTreeNode {
  kind: "directory" | "file"
  name: string
  path: string
  size_bytes: number
  extension: string
  modified_at: string
  related_file_id: string
  related_filename: string
  children: KnowledgeTreeNode[]
}

export interface KnowledgeTree {
  roots: KnowledgeTreeNode[]
  missing_roots: string[]
  notice: string
}

export interface KnowledgeTreePreview {
  path: string
  name: string
  extension: string
  size_bytes: number
  modified_at: string
  related_file_id: string
  related_filename: string
  preview_kind: "text" | "metadata" | "unsupported"
  text: string
  truncated: boolean
  notice: string
}

function readTreeNode(value: unknown): KnowledgeTreeNode {
  if (!isRecord(value) || (value.kind !== "directory" && value.kind !== "file")) {
    throw new Error("目录节点格式不正确")
  }
  if (typeof value.name !== "string" || !value.name || typeof value.path !== "string" || !value.path || value.path.includes("..")) {
    throw new Error("目录节点缺少路径")
  }
  const children = value.kind === "directory" && Array.isArray(value.children) ? value.children.map((item) => readTreeNode(item)) : []
  return {
    kind: value.kind,
    name: value.name,
    path: value.path,
    size_bytes: typeof value.size_bytes === "number" ? value.size_bytes : 0,
    extension: typeof value.extension === "string" ? value.extension : "",
    modified_at: typeof value.modified_at === "string" ? value.modified_at : "",
    related_file_id: typeof value.related_file_id === "string" ? value.related_file_id : "",
    related_filename: typeof value.related_filename === "string" ? value.related_filename : "",
    children,
  }
}

export function parseKnowledgeTree(value: unknown): KnowledgeTree {
  if (!isRecord(value) || !Array.isArray(value.roots)) {
    throw new Error("知识目录数据缺少 roots")
  }
  return {
    roots: value.roots.map((item) => readTreeNode(item)),
    missing_roots: Array.isArray(value.missing_roots) ? value.missing_roots.filter((item): item is string => typeof item === "string") : [],
    notice: typeof value.notice === "string" ? value.notice : "",
  }
}

export function parseTreePreview(value: unknown): KnowledgeTreePreview {
  if (!isRecord(value) || typeof value.path !== "string" || typeof value.name !== "string") {
    throw new Error("文件预览格式不正确")
  }
  const kind = value.preview_kind
  if (kind !== "text" && kind !== "metadata" && kind !== "unsupported") {
    throw new Error("文件预览类型不正确")
  }
  return {
    path: value.path,
    name: value.name,
    extension: typeof value.extension === "string" ? value.extension : "",
    size_bytes: typeof value.size_bytes === "number" ? value.size_bytes : 0,
    modified_at: typeof value.modified_at === "string" ? value.modified_at : "",
    related_file_id: typeof value.related_file_id === "string" ? value.related_file_id : "",
    related_filename: typeof value.related_filename === "string" ? value.related_filename : "",
    preview_kind: kind,
    text: typeof value.text === "string" ? value.text : "",
    truncated: value.truncated === true,
    notice: typeof value.notice === "string" ? value.notice : "",
  }
}
