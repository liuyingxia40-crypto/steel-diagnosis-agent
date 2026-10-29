/** 画布上已经记录的父子层级。层级是沿 parentId 向上的步数，不是颜色、边下标或创建顺序。 */

export type LineKind = "solid" | "dashed" | "association"

export interface HierarchyNode {
  id: string
  parentId: string
}

export interface LineDecision {
  kind: LineKind
  childLevel: number | null
}

export function hierarchyLevel(id: string, nodes: readonly HierarchyNode[]): number {
  const parentOf = new Map(nodes.map((node) => [node.id, node.parentId]))
  let level = 0
  let cursor = id
  const seen = new Set<string>()
  while (!seen.has(cursor)) {
    seen.add(cursor)
    const parent = parentOf.get(cursor) ?? ""
    if (!parent || !parentOf.has(parent)) {
      break
    }
    level += 1
    cursor = parent
  }
  return level
}

/** 只有一端的 parentId 明确指向另一端时，才按层级决定实线或虚线。 */
export function lineForEdge(source: string, target: string, nodes: readonly HierarchyNode[]): LineDecision {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const left = byId.get(source)
  const right = byId.get(target)
  const child =
    right && left && right.parentId === left.id ? right
    : left && right && left.parentId === right.id ? left
    : null
  if (!child) {
    return { kind: "association", childLevel: null }
  }
  const childLevel = hierarchyLevel(child.id, nodes)
  return { kind: childLevel >= 3 ? "dashed" : "solid", childLevel }
}

export function lineDashOf(kind: LineKind): number[] {
  if (kind === "dashed") {
    return [5, 4]
  }
  if (kind === "association") {
    return [2, 2]
  }
  return []
}
