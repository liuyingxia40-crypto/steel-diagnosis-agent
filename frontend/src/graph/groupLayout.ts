export interface RegionSpec {
  id: string
  label: string
  color: string
  row: number
  col: number
  cols: number
}

export interface RegionBox extends RegionSpec {
  x: number
  y: number
  width: number
  height: number
}

export interface PlacementNode {
  id: string
  region: string
  hub: boolean
  label: string
  size: number
  order: number
  parentId?: string
}

export interface Point {
  x: number
  y: number
}

export interface Rect {
  left: number
  right: number
  top: number
  bottom: number
}

const GAP_X = 44
const GAP_Y = 52

const TARGET_WIDTH = 0.76
const TARGET_HEIGHT = 0.72

export function textWidth(text: string): number {
  let width = 0
  for (const char of text) {
    width += char.charCodeAt(0) > 255 ? 15 : 8.4
  }
  return width
}

export function regionBoxes(width: number, height: number, specs: readonly RegionSpec[]): RegionBox[] {
  const marginX = 32
  const marginTop = 12
  const marginBottom = 24
  const usableW = Math.max(width - marginX * 2, 320)
  const usableH = Math.max(height - marginTop - marginBottom, 280)
  const rows = [...new Set(specs.map((item) => item.row))].sort((a, b) => a - b)
  const rowGap = GAP_Y * Math.max(rows.length - 1, 0)
  const rowH = (usableH - rowGap) / Math.max(rows.length, 1)
  return specs.map((spec) => {
    const rowIndex = rows.indexOf(spec.row)
    const colGap = GAP_X * Math.max(spec.cols - 1, 0)
    const colW = (usableW - colGap) / Math.max(spec.cols, 1)
    return {
      ...spec,
      x: marginX + spec.col * (colW + GAP_X),
      y: marginTop + rowIndex * (rowH + GAP_Y),
      width: colW,
      height: rowH,
    }
  })
}

function clamp(value: number, min: number, max: number): number {
  if (max <= min) {
    return (min + max) / 2
  }
  return Math.min(max, Math.max(min, value))
}

interface FrameSlot {
  x: number
  y: number
  width: number
  height: number
}

/** 三上二下的目标区域。宽度约占画布 78%，高度约占 68%，并略向左避开图例。 */
function slotFor(regionId: string, width: number, height: number): FrameSlot {
  const frameW = width * TARGET_WIDTH
  const frameH = height * TARGET_HEIGHT
  const frameX = (width - frameW) / 2 - width * 0.02
  const frameY = (height - frameH) / 2 + height * 0.015
  const gapX = clamp(frameW * 0.035, 24, 48)
  const gapY = clamp(frameH * 0.055, 28, 52)
  const topH = (frameH - gapY) * 0.4
  const botH = frameH - gapY - topH
  const colW = (frameW - gapX * 2) / 3
  const botW = (frameW - gapX) / 2
  const slots: Record<string, FrameSlot> = {
    equipment: { x: frameX, y: frameY, width: colW, height: topH },
    fault: { x: frameX + colW + gapX, y: frameY, width: colW, height: topH },
    procedure: { x: frameX + (colW + gapX) * 2, y: frameY, width: colW, height: topH },
    case: { x: frameX, y: frameY + topH + gapY, width: botW, height: botH },
    relation: { x: frameX + botW + gapX, y: frameY + topH + gapY, width: botW, height: botH },
  }
  return slots[regionId] ?? { x: frameX, y: frameY, width: colW, height: topH }
}

/** 五个群组核心节点的画布坐标，随当前画布尺寸分布，不使用固定小画布。 */
export function hubCenter(regionId: string, width: number, height: number): Point {
  const slot = slotFor(regionId, width, height)
  return { x: slot.x + slot.width / 2, y: slot.y + slot.height / 2 }
}

export function clusterBoxes(width: number, height: number, specs: readonly RegionSpec[]): RegionBox[] {
  return specs.map((spec) => ({ ...spec, ...slotFor(spec.id, width, height) }))
}

function placeRing(
  origin: Point,
  members: readonly PlacementNode[],
  placed: Map<string, Point>,
  canvasCenter: Point,
  rx: number,
  ry: number,
  twoLayers: boolean,
) {
  const innerCount = twoLayers ? Math.ceil(members.length / 2) : members.length
  const layers = twoLayers ? [members.slice(0, innerCount), members.slice(innerCount)] : [members]
  const outward = Math.atan2(origin.y - canvasCenter.y, origin.x - canvasCenter.x)
  layers.forEach((layer, layerIndex) => {
    const scale = !twoLayers ? 1 : layerIndex === 0 ? 0.68 : 1
    const widest = layer.reduce((best, item) => (item && best && textWidth(item.label) > textWidth(best.label) ? item : best), layer[0])
    const widestIndex = Math.max(0, layer.findIndex((item) => item?.id === widest?.id))
    const start = outward - (widestIndex / Math.max(layer.length, 1)) * Math.PI * 2
    layer.forEach((member, index) => {
      if (!member) {
        return
      }
      const stagger = layerIndex === 1 ? Math.PI / layer.length : 0
      const angle = start + stagger + (index / layer.length) * Math.PI * 2
      placed.set(member.id, {
        x: origin.x + Math.cos(angle) * rx * scale,
        y: origin.y + Math.sin(angle) * ry * scale,
      })
    })
  })
}

function placeWideCluster(origin: Point, members: readonly PlacementNode[], placed: Map<string, Point>, hubSize: number, box: RegionBox) {
  const top = members.slice(0, 2)
  const side = members.slice(2, 4)
  const bottom = members.slice(4)
  const placeRow = (row: readonly PlacementNode[], y: number) => {
    const widths = row.map((node) => textWidth(node.label) + 18)
    const natural = widths.reduce((sum, width) => sum + width, 0)
    const available = Math.max(box.width - 16, natural)
    const scale = Math.min(1, available / natural)
    let cursor = origin.x - (natural * scale) / 2
    const nextY = clamp(y, box.y + 28, box.y + box.height - 40)
    row.forEach((node, index) => {
      const width = (widths[index] ?? 80) * scale
      placed.set(node.id, {
        x: clamp(cursor + width / 2, box.x + width / 2, box.x + box.width - width / 2),
        y: nextY,
      })
      cursor += width
    })
  }
  const rowY = hubSize / 2 + 52
  placeRow(top, origin.y - rowY)
  side.forEach((node, index) => {
    const reach = hubSize / 2 + textWidth(node.label) / 2 + 14
    placed.set(node.id, {
      x: clamp(origin.x + (index === 0 ? -reach : reach), box.x + 70, box.x + box.width - 70),
      y: origin.y + 2,
    })
  })
  placeRow(bottom, origin.y + rowY)
}

function placeSatellites(
  parent: Point,
  hub: Point,
  parentNode: PlacementNode,
  children: readonly PlacementNode[],
  placed: Map<string, Point>,
) {
  const fromHub = Math.atan2(parent.y - hub.y, parent.x - hub.x)
  const gap = parentNode.size / 2 + 54
  children.forEach((child, index) => {
    const shift = children.length === 1 ? 0.4 : index === 0 ? -1.02 : 1.02
    const angle = fromHub + shift
    const ring = gap + child.size / 2
    const point = {
      x: parent.x + Math.cos(angle) * ring,
      y: parent.y + Math.sin(angle) * ring,
    }
    placed.set(child.id, point)
  })
}

function settleSatellites(
  hub: PlacementNode,
  members: readonly PlacementNode[],
  placed: Map<string, Point>,
) {
  const leaves = members.filter((item) => item.parentId && item.parentId !== hub.id)
  const bodies = [hub, ...members]
  for (let pass = 0; pass < 28; pass += 1) {
    for (const leaf of leaves) {
      const point = placed.get(leaf.id)
      if (!point) {
        continue
      }
      let x = point.x
      let y = point.y
      for (const other of bodies) {
        if (other.id === leaf.id) {
          continue
        }
        const there = placed.get(other.id)
        if (!there) {
          continue
        }
        const dx = x - there.x
        const dy = y - there.y
        const distance = Math.hypot(dx, dy) || 0.1
        const needed = (leaf.size + other.size) / 2 + (other.hub ? 56 : 20)
        if (distance < needed) {
          x += (dx / distance) * (needed - distance)
          y += (dy / distance) * (needed - distance)
        }
        const preview = new Map(placed)
        preview.set(leaf.id, { x, y })
        const mine = labelRect({ x, y }, leaf.size, leaf.label, labelSide(leaf, preview))
        const theirs = labelRect(there, other.size, other.label, labelSide(other, preview))
        if (!hit(mine, theirs, 2)) {
          continue
        }
        const overlapX = Math.min(mine.right, theirs.right) - Math.max(mine.left, theirs.left)
        const overlapY = Math.min(mine.bottom, theirs.bottom) - Math.max(mine.top, theirs.top)
        if (overlapX < overlapY) {
          x += (x >= there.x ? 1 : -1) * (overlapX / 2 + 2)
        } else {
          y += (y >= there.y ? 1 : -1) * (overlapY / 2 + 2)
        }
      }
      placed.set(leaf.id, { x, y })
    }
  }
}

/** 每个分类中心周围的小型环绕网络。第3级贴在各自的上级节点外侧。 */
export function placeInRegion(
  box: RegionBox,
  hub: PlacementNode,
  members: readonly PlacementNode[],
  origin: Point,
  canvasCenter: Point,
): Map<string, Point> {
  const placed = new Map<string, Point>()
  placed.set(hub.id, origin)
  const cores = members.filter((item) => !item.parentId || item.parentId === hub.id)
  const extras = members.filter((item) => item.parentId && item.parentId !== hub.id)
  const left = origin.x - box.x
  const right = box.x + box.width - origin.x
  const top = origin.y - box.y
  const bottom = box.y + box.height - origin.y
  const roomX = Math.min(left, right)
  const roomY = Math.min(top, bottom)
  const widest = cores.reduce((max, item) => Math.max(max, textWidth(item.label)), 48)
  const nodeSize = cores.reduce((max, item) => Math.max(max, item.size), 32)
  const minR = hub.size / 2 + nodeSize / 2 + 52
  const maxRx = Math.max(minR, roomX - nodeSize / 2 - widest / 2 - 8)
  const maxRy = Math.max(minR, roomY - nodeSize / 2 - 28)
  const count = Math.max(cores.length, 1)
  const chordR = count <= 1 ? minR : (widest + 14) / (2 * Math.sin(Math.PI / count))
  const twoLayers = count >= 6 && (chordR > maxRx || chordR * 0.84 > maxRy)
  const layerCount = twoLayers ? Math.ceil(count / 2) : count
  const needed = layerCount <= 1 ? minR : (widest + 14) / (2 * Math.sin(Math.PI / layerCount))
  const rx = clamp(needed, minR, maxRx)
  const ry = clamp(needed * 0.88, minR, maxRy)
  if (cores.length >= 6) {
    placeWideCluster(origin, cores, placed, hub.size, box)
  } else if (cores.length > 0) {
    placeRing(origin, cores, placed, canvasCenter, rx, ry, twoLayers)
    relaxCluster(box, hub, cores, placed, origin)
  }
  const byParent = new Map<string, PlacementNode[]>()
  for (const extra of extras) {
    const key = extra.parentId ?? ""
    const list = byParent.get(key) ?? []
    list.push(extra)
    byParent.set(key, list)
  }
  for (const [parentId, children] of byParent) {
    const parentNode = cores.find((item) => item.id === parentId)
    const point = placed.get(parentId)
    if (!parentNode || !point) {
      continue
    }
    placeSatellites(point, origin, parentNode, children, placed)
  }
  settleSatellites(hub, members, placed)
  return placed
}

function relaxCluster(
  box: RegionBox,
  hub: PlacementNode,
  members: readonly PlacementNode[],
  placed: Map<string, Point>,
  center: Point,
) {
  const hubPoint = placed.get(hub.id) ?? center
  for (let step = 0; step < 42; step += 1) {
    const shift = new Map<string, Point>()
    const push = (id: string, x: number, y: number) => {
      const current = shift.get(id) ?? { x: 0, y: 0 }
      current.x += x
      current.y += y
      shift.set(id, current)
    }
    const bodies = [hub, ...members]
    for (let left = 0; left < bodies.length; left += 1) {
      for (let right = left + 1; right < bodies.length; right += 1) {
        const a = bodies[left]
        const b = bodies[right]
        if (!a || !b) {
          continue
        }
        const pa = placed.get(a.id)
        const pb = placed.get(b.id)
        if (!pa || !pb) {
          continue
        }
        const dx = pb.x - pa.x
        const dy = pb.y - pa.y
        const distance = Math.hypot(dx, dy) || 0.1
        const needed = (a.size + b.size) / 2 + 12
        if (distance < needed) {
          const amount = (needed - distance) / 2
          const ux = dx / distance
          const uy = dy / distance
          if (!a.hub) {
            push(a.id, -ux * amount, -uy * amount)
          }
          if (!b.hub) {
            push(b.id, ux * amount, uy * amount)
          }
        }
        const labelA = labelRect(pa, a.size, a.label, labelSide(a, placed))
        const labelB = labelRect(pb, b.size, b.label, labelSide(b, placed))
        const labelHitsCircle =
          circleHitsRect(pb, b.size, labelA, 4) || circleHitsRect(pa, a.size, labelB, 4)
        if (!hit(labelA, labelB, 5) && !labelHitsCircle) {
          continue
        }
        const overlapX = Math.min(labelA.right, labelB.right) - Math.max(labelA.left, labelB.left)
        const overlapY = Math.min(labelA.bottom, labelB.bottom) - Math.max(labelA.top, labelB.top)
        const horizontal = overlapX <= overlapY
        const amount = (horizontal ? overlapX : overlapY) / 2 + 2
        if (horizontal) {
          const sign = pa.x <= pb.x ? -1 : 1
          if (!a.hub) {
            push(a.id, sign * amount, 0)
          }
          if (!b.hub) {
            push(b.id, -sign * amount, 0)
          }
        } else if (!a.hub || !b.hub) {
          const sign = pa.y <= pb.y ? -1 : 1
          if (!a.hub) {
            push(a.id, 0, sign * amount)
          }
          if (!b.hub) {
            push(b.id, 0, -sign * amount)
          }
        }
      }
    }
    for (const member of members) {
      const point = placed.get(member.id)
      if (!point) {
        continue
      }
      const dx = point.x - hubPoint.x
      const dy = point.y - hubPoint.y
      const distance = Math.hypot(dx, dy) || 0.1
      const needed = hub.size / 2 + member.size / 2 + 16
      if (distance < needed) {
        push(member.id, (dx / distance) * (needed - distance), (dy / distance) * (needed - distance))
      }
    }
    for (const member of members) {
      const point = placed.get(member.id)
      const delta = shift.get(member.id)
      if (!point || !delta) {
        continue
      }
      const next = {
        x: clamp(point.x + delta.x * 0.55, box.x + member.size / 2 + textWidth(member.label) / 2 + 8, box.x + box.width - member.size / 2 - textWidth(member.label) / 2 - 8),
        y: clamp(point.y + delta.y * 0.55, box.y + member.size / 2 + 8, box.y + box.height - member.size / 2 - 32),
      }
      placed.set(member.id, next)
    }
  }
  const bodies = [hub, ...members]
  for (let pass = 0; pass < 8; pass += 1) {
    for (const member of members) {
      if (!member.parentId || member.parentId === hub.id) {
        continue
      }
      const point = placed.get(member.id)
      const parentPoint = placed.get(member.parentId)
      const parentNode = bodies.find((item) => item.id === member.parentId)
      if (!point || !parentPoint || !parentNode) {
        continue
      }
      const dx = point.x - parentPoint.x
      const dy = point.y - parentPoint.y
      const distance = Math.hypot(dx, dy) || 0.1
      const needed = parentNode.size / 2 + member.size / 2 + 24
      if (distance >= needed) {
        continue
      }
      const radial = {
        x: parentPoint.x + (dx / distance) * needed,
        y: parentPoint.y + (dy / distance) * needed,
      }
      const tx = -dy / distance
      const ty = dx / distance
      const tangent = {
        x: parentPoint.x + tx * needed,
        y: parentPoint.y + ty * needed,
      }
      const inside = (candidate: Point) =>
        candidate.x >= box.x + 12 &&
        candidate.x <= box.x + box.width - 12 &&
        candidate.y >= box.y + 12 &&
        candidate.y <= box.y + box.height - 22
      const chosen = inside(radial) ? radial : inside(tangent) ? tangent : radial
      placed.set(member.id, {
        x: clamp(chosen.x, box.x + member.size / 2 + 6, box.x + box.width - member.size / 2 - 6),
        y: clamp(chosen.y, box.y + member.size / 2 + 4, box.y + box.height - member.size / 2 - 18),
      })
    }
  }
}

export function placeOverview(
  width: number,
  height: number,
  specs: readonly RegionSpec[],
  nodes: readonly PlacementNode[],
): Map<string, Point> {
  const boxes = clusterBoxes(width, height, specs)
  const placed = new Map<string, Point>()
  for (const box of boxes) {
    const group = nodes.filter((node) => node.region === box.id).sort((a, b) => a.order - b.order)
    const hub = group.find((node) => node.hub)
    if (!hub) {
      continue
    }
    const members = group.filter((node) => !node.hub)
    const origin = hubCenter(box.id, width, height)
    for (const [id, point] of placeInRegion(box, hub, members, origin, { x: width / 2, y: height / 2 })) {
      placed.set(id, point)
    }
  }
  separateLabels(nodes, placed, boxes)
  clearCrossHits(nodes, placed)
  tightenGroups(nodes, placed)
  liftOffLabels(nodes, placed)
  return placed
}

function liftOffLabels(nodes: readonly PlacementNode[], placed: Map<string, Point>) {
  const leaves = nodes.filter((node) => node.size <= 20)
  for (let pass = 0; pass < 14; pass += 1) {
    for (const leaf of leaves) {
      const point = placed.get(leaf.id)
      if (!point) {
        continue
      }
      let x = point.x
      let y = point.y
      for (const other of nodes) {
        if (other.id === leaf.id || other.region !== leaf.region) {
          continue
        }
        const there = placed.get(other.id)
        if (!there) {
          continue
        }
        const here = { x, y }
        const preview = new Map(placed)
        preview.set(leaf.id, here)
        const leafLabel = labelRect(here, leaf.size, leaf.label, labelSide(leaf, preview))
        const otherLabel = labelRect(there, other.size, other.label, labelSide(other, preview))
        const crowded =
          Math.hypot(x - there.x, y - there.y) < (leaf.size + other.size) / 2 + 8 ||
          hit(leafLabel, otherLabel, 2) ||
          circleHitsRect(here, leaf.size, otherLabel, 1) ||
          circleHitsRect(there, other.size, leafLabel, 1)
        if (!crowded) {
          continue
        }
        const dx = x - there.x
        const dy = y - there.y
        const distance = Math.hypot(dx, dy) || 0.1
        x += (dx / distance) * 7
        y += (dy / distance) * 7
      }
      placed.set(leaf.id, { x, y })
    }
  }
}

function crowds(leaf: PlacementNode, here: Point, other: PlacementNode, there: Point, placed: ReadonlyMap<string, Point>): boolean {
  const preview = new Map(placed)
  preview.set(leaf.id, here)
  const leafLabel = labelRect(here, leaf.size, leaf.label, labelSide(leaf, preview))
  const otherLabel = labelRect(there, other.size, other.label, labelSide(other, preview))
  return (
    hit(leafLabel, otherLabel, 4) ||
    circleHitsRect(here, leaf.size, otherLabel, 3) ||
    circleHitsRect(there, other.size, leafLabel, 3) ||
    Math.hypot(here.x - there.x, here.y - there.y) < (leaf.size + other.size) / 2 + (other.hub ? 28 : 26)
  )
}

/** 第三级如果压到其他分类，就改放到上级节点周围的空位，不缩小节点。 */
function clearCrossHits(nodes: readonly PlacementNode[], placed: Map<string, Point>) {
  const leaves = nodes.filter((node) => node.size <= 20 && node.parentId)
  for (const leaf of leaves) {
    const parent = placed.get(leaf.parentId ?? "")
    const parentNode = nodes.find((node) => node.id === leaf.parentId)
    const current = placed.get(leaf.id)
    if (!parent || !parentNode || !current) {
      continue
    }
    const foreign = nodes.filter((node) => node.region !== leaf.region)
    const hitsForeign = foreign.some((node) => {
      const there = placed.get(node.id)
      return there ? crowds(leaf, current, node, there, placed) : false
    })
    if (!hitsForeign) {
      continue
    }
    const others = nodes.filter((node) => node.id !== leaf.id)
    let best = current
    let bestScore = Number.POSITIVE_INFINITY
    for (let step = 0; step < 16; step += 1) {
      const angle = (Math.PI * 2 * step) / 16
      for (const extra of [48, 72, 96]) {
        const candidate = {
          x: parent.x + Math.cos(angle) * (parentNode.size / 2 + leaf.size / 2 + extra),
          y: parent.y + Math.sin(angle) * (parentNode.size / 2 + leaf.size / 2 + extra),
        }
        let score = 0
        for (const other of others) {
          const there = placed.get(other.id)
          if (!there) {
            continue
          }
          if (crowds(leaf, candidate, other, there, placed)) {
            score += other.region === leaf.region ? 2 : 5
          }
        }
        if (score < bestScore) {
          best = candidate
          bestScore = score
        }
      }
    }
    placed.set(leaf.id, best)
  }
}

function clonePoints(placed: ReadonlyMap<string, Point>): Map<string, Point> {
  return new Map([...placed].map(([id, point]) => [id, { x: point.x, y: point.y }]))
}

function groupsCross(nodes: readonly PlacementNode[], placed: ReadonlyMap<string, Point>): boolean {
  for (let left = 0; left < nodes.length; left += 1) {
    for (let right = left + 1; right < nodes.length; right += 1) {
      const a = nodes[left]
      const b = nodes[right]
      if (!a || !b || a.region === b.region) {
        continue
      }
      const pa = placed.get(a.id)
      const pb = placed.get(b.id)
      if (!pa || !pb) {
        continue
      }
      if (Math.hypot(pa.x - pb.x, pa.y - pb.y) < (a.size + b.size) / 2 + 18) {
        return true
      }
      const labelA = labelRect(pa, a.size, a.label, labelSide(a, placed))
      const labelB = labelRect(pb, b.size, b.label, labelSide(b, placed))
      if (hit(labelA, labelB, 4) || circleHitsRect(pa, a.size, labelB, 1) || circleHitsRect(pb, b.size, labelA, 1)) {
        return true
      }
    }
  }
  return false
}

/** 把五个群组朝整体中心收拢约 22%，组内相对位置不变。发生遮挡时退回到最近的安全距离。 */
function tightenGroups(nodes: readonly PlacementNode[], placed: Map<string, Point>) {
  const base = clonePoints(placed)
  const apply = (factor: number) => {
    const hubs = nodes.filter((node) => node.hub && base.has(node.id))
    if (hubs.length === 0) {
      return
    }
    let cx = 0
    let cy = 0
    for (const hub of hubs) {
      const point = base.get(hub.id)
      if (!point) {
        continue
      }
      cx += point.x
      cy += point.y
    }
    cx /= hubs.length
    cy /= hubs.length
    const shift = new Map<string, Point>()
    for (const hub of hubs) {
      const point = base.get(hub.id)
      if (!point) {
        continue
      }
      shift.set(hub.region, {
        x: cx + (point.x - cx) * factor - point.x,
        y: cy + (point.y - cy) * factor - point.y,
      })
    }
    for (const node of nodes) {
      const point = base.get(node.id)
      const delta = shift.get(node.region)
      if (!point || !delta) {
        continue
      }
      placed.set(node.id, { x: point.x + delta.x, y: point.y + delta.y })
    }
  }
  apply(0.78)
  if (!groupsCross(nodes, placed)) {
    return
  }
  let tight = 0.78
  let safe = 1
  for (let step = 0; step < 8; step += 1) {
    const factor = (safe + tight) / 2
    apply(factor)
    if (groupsCross(nodes, placed)) {
      tight = factor
    } else {
      safe = factor
    }
  }
  apply(safe)
}

function separateLabels(
  nodes: readonly PlacementNode[],
  placed: Map<string, Point>,
  boxes: readonly RegionBox[],
) {
  const boxOf = new Map(boxes.map((box) => [box.id, box]))
  const ordered = [...nodes].sort((a, b) => a.size - b.size)
  for (let pass = 0; pass < 20; pass += 1) {
    let moved = false
    for (let left = 0; left < ordered.length; left += 1) {
      for (let right = left + 1; right < ordered.length; right += 1) {
        const a = ordered[left]
        const b = ordered[right]
        if (!a || !b || a.hub) {
          continue
        }
        const pa = placed.get(a.id)
        const pb = placed.get(b.id)
        if (!pa || !pb) {
          continue
        }
        const circles = Math.hypot(pa.x - pb.x, pa.y - pb.y) < (a.size + b.size) / 2 + 8
        const labels = hit(
          labelRect(pa, a.size, a.label, labelSide(a, placed)),
          labelRect(pb, b.size, b.label, labelSide(b, placed)),
          3,
        )
        if (!circles && !labels) {
          continue
        }
        const dx = pa.x - pb.x
        const dy = pa.y - pb.y
        const distance = Math.hypot(dx, dy) || 0.1
        const box = a.size > 20 ? boxOf.get(a.region) : undefined
        const next = {
          x: pa.x + (dx / distance) * 7,
          y: pa.y + (dy / distance) * 7,
        }
        if (box) {
          next.x = clamp(next.x, box.x + a.size / 2 + 4, box.x + box.width - a.size / 2 - 4)
          next.y = clamp(next.y, box.y + a.size / 2 + 4, box.y + box.height - a.size / 2 - 16)
        }
        if (next.x !== pa.x || next.y !== pa.y) {
          placed.set(a.id, next)
          moved = true
          continue
        }
        const slide = {
          x: box ? clamp(pa.x - (dy / distance) * 10, box.x + a.size / 2 + 4, box.x + box.width - a.size / 2 - 4) : pa.x,
          y: box ? clamp(pa.y + (dx / distance) * 10, box.y + a.size / 2 + 4, box.y + box.height - a.size / 2 - 16) : pa.y,
        }
        if (slide.x !== pa.x || slide.y !== pa.y) {
          placed.set(a.id, slide)
          moved = true
        }
      }
    }
    if (!moved) {
      break
    }
  }
}

export function placeLocal(width: number, height: number, focus: PlacementNode, neighbors: readonly PlacementNode[]): Map<string, Point> {
  const placed = new Map<string, Point>()
  const cx = width / 2
  const cy = height / 2
  placed.set(focus.id, { x: cx, y: cy })
  const rowGap = 84
  const colGap = 210
  const perCol = Math.max(1, Math.floor((height - 120) / rowGap))
  neighbors.forEach((node, index) => {
    const col = Math.floor(index / perCol)
    const row = index % perCol
    const countInCol = Math.min(perCol, neighbors.length - col * perCol)
    const side = col % 2 === 0 ? 1 : -1
    const depth = Math.floor(col / 2) + 1
    const rawX = cx + side * depth * colGap
    const rawY = cy + (row - (countInCol - 1) / 2) * rowGap
    placed.set(node.id, {
      x: clamp(rawX, 90, width - 90),
      y: clamp(rawY, 48, height - 48),
    })
  })
  return placed
}

export function labelSide(node: Pick<PlacementNode, "size" | "parentId" | "id">, positions: ReadonlyMap<string, Point>): "bottom" | "left" | "right" | "top" {
  if (!node.parentId || node.size > 20) {
    return "bottom"
  }
  const parent = positions.get(node.parentId)
  const self = positions.get(node.id)
  if (!parent || !self) {
    return "right"
  }
  const dx = self.x - parent.x
  const dy = self.y - parent.y
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? "right" : "left"
  }
  return dy >= 0 ? "bottom" : "top"
}

export function labelRect(point: Point, size: number, text: string, side: "bottom" | "left" | "right" | "top" = "bottom"): Rect {
  const scale = size >= 50 ? 16 / 15 : size <= 20 ? 13 / 15 : 1
  const width = textWidth(text) * scale + 12
  if (side === "left") {
    const right = point.x - size / 2 - 4
    const top = point.y - 8
    return { left: right - width, right, top, bottom: top + 26 }
  }
  if (side === "right") {
    const left = point.x + size / 2 + 4
    const top = point.y - 10
    return { left, right: left + width, top, bottom: top + 26 }
  }
  if (side === "top") {
    const bottom = point.y - size / 2 - 6
    return { left: point.x - width / 2, right: point.x + width / 2, top: bottom - 26, bottom }
  }
  const top = point.y + size / 2 + 8
  return { left: point.x - width / 2, right: point.x + width / 2, top, bottom: top + 26 }
}

function circleHitsRect(center: Point, size: number, rect: Rect, gap: number): boolean {
  const x = clamp(center.x, rect.left, rect.right)
  const y = clamp(center.y, rect.top, rect.bottom)
  return Math.hypot(center.x - x, center.y - y) < size / 2 + gap
}

function hit(a: Rect, b: Rect, gap: number): boolean {
  return a.left - gap < b.right && a.right + gap > b.left && a.top - gap < b.bottom && a.bottom + gap > b.top
}

export function auditPositions(
  nodes: readonly PlacementNode[],
  positions: ReadonlyMap<string, Point>,
  boxes: readonly RegionBox[] = [],
): string[] {
  const errors: string[] = []
  const boxOf = new Map(boxes.map((box) => [box.id, box]))
  for (let index = 0; index < boxes.length; index += 1) {
    for (let other = index + 1; other < boxes.length; other += 1) {
      const a = boxes[index]
      const b = boxes[other]
      if (!a || !b) {
        continue
      }
      const overlap = a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
      if (overlap) {
        errors.push(`区域重叠 ${a.id} ${b.id}`)
      }
    }
  }
  for (let left = 0; left < nodes.length; left += 1) {
    for (let right = left + 1; right < nodes.length; right += 1) {
      const a = nodes[left]
      const b = nodes[right]
      if (!a || !b) {
        continue
      }
      const pa = positions.get(a.id)
      const pb = positions.get(b.id)
      if (!pa || !pb) {
        errors.push(`缺少坐标 ${a.id} ${b.id}`)
        continue
      }
      const gap = Math.hypot(pa.x - pb.x, pa.y - pb.y)
      if (gap < (a.size + b.size) / 2 + 8) {
        errors.push(`圆重叠 ${a.label} ${b.label}`)
      }
      const sideA = labelSide(a, positions)
      const sideB = labelSide(b, positions)
      const labelA = labelRect(pa, a.size, a.label, sideA)
      const labelB = labelRect(pb, b.size, b.label, sideB)
      if (hit(labelA, labelB, 2) || circleHitsRect(pa, a.size, labelB, 0) || circleHitsRect(pb, b.size, labelA, 0)) {
        errors.push(`文字重叠 ${a.label} ${b.label}`)
      }
    }
    const node = nodes[left]
    const point = node ? positions.get(node.id) : undefined
    const box = node ? boxOf.get(node.region) : undefined
    if (node && point && box) {
      const label = labelRect(point, node.size, node.label, labelSide(node, positions))
      for (const other of boxes) {
        if (other.id === box.id) {
          continue
        }
        const otherRect = { left: other.x, right: other.x + other.width, top: other.y, bottom: other.y + other.height }
        if (hit(label, otherRect, 0)) {
          errors.push(`文字进入其他分区 ${node.label} -> ${other.id}`)
        }
      }
    }
  }
  return errors
}
