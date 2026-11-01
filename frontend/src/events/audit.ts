import type { AuditAction, AuditEntry, WorkStatus } from "./types"

const STORAGE_KEY = "steel-events-demo-audit"

/** 演示态处置记录。接入后端审计日志时替换 submitAudit 的写入实现。 */
export async function submitAudit(entry: Omit<AuditEntry, "id" | "persisted">): Promise<AuditEntry> {
  const saved: AuditEntry = {
    ...entry,
    id: `audit-${Date.now()}`,
    persisted: false,
  }
  const next = [...loadAudit(), saved]
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  return saved
}

export function loadAudit(): AuditEntry[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isAuditEntry)
  } catch {
    return []
  }
}

export function auditsFor(eventId: string, entries: AuditEntry[]): AuditEntry[] {
  return entries.filter((item) => item.eventId === eventId)
}

export function statusAfter(base: WorkStatus, entries: AuditEntry[]): WorkStatus {
  return entries.length > 0 ? entries[entries.length - 1].statusAfter : base
}

export function nextAction(status: WorkStatus, type: "alarm" | "diagnosis" | "operation" | "system"): { action: AuditAction; statusAfter: WorkStatus; label: string } | null {
  if (type === "alarm") {
    if (status === "pending") return { action: "acknowledge", statusAfter: "acknowledged", label: "确认报警" }
    if (status === "acknowledged") return { action: "investigate", statusAfter: "investigating", label: "开始调查" }
    if (status === "investigating") return { action: "resolve", statusAfter: "resolved", label: "标记已解决" }
    if (status === "resolved") return { action: "close", statusAfter: "closed", label: "关闭" }
    if (status === "closed") return { action: "reopen", statusAfter: "pending", label: "重新打开" }
  }
  if (type === "diagnosis" && status === "review") {
    return { action: "review", statusAfter: "reviewed", label: "标记已复核" }
  }
  return null
}

function isAuditEntry(value: unknown): value is AuditEntry {
  if (!value || typeof value !== "object") return false
  const item = value as AuditEntry
  return typeof item.id === "string" && typeof item.eventId === "string" && item.persisted === false
}
