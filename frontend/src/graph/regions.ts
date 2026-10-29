export interface PartitionNode {
  id: string
  region: string
  hub?: boolean
  radius: number
}

export interface PartitionEdge {
  source: string
  target: string
  relation: string
}

interface Point {
  x: number
  y: number
}

interface Body extends Point {
  id: string
  radius: number
  pin: boolean
}

const REGION_ORDER = ["equipment", "fault", "procedure", "case", "relation"] as const

function regionCenters(width: number, height: number) {
  const left = width * 0.04
  const right = width * 0.96
  const usableW = right - left
  const row1Y = height * 0.34
  const row2Y = height * 0.73
  const verticalGap = row2Y - row1Y
  const col = usableW / 3
  const row1Limit = Math.min((col - 48) / 2, (verticalGap - 56) / 2)
  const row2Gap = usableW * 0.36
  const row2Limit = Math.min((row2Gap - 48) / 2, (verticalGap - 56) / 2)
  return {
    equipment: { x: left + col * 0.5, y: row1Y, maxR: row1Limit },
    fault: { x: left + col * 1.5, y: row1Y, maxR: row1Limit },
    procedure: { x: left + col * 2.5, y: row1Y, maxR: row1Limit },
    case: { x: left + usableW * 0.32, y: row2Y, maxR: row2Limit },
    relation: { x: left + usableW * 0.68, y: row2Y, maxR: row2Limit },
  }
}

function layoutGroup(nodes: PartitionNode[], edges: PartitionEdge[], maxR: number): Map<string, Point> {
  const hub = nodes.find((node) => node.hub) ?? nodes[0]
  const others = nodes.filter((node) => node.id !== hub?.id).sort((a, b) => a.id.localeCompare(b.id))
  const wanted = 42 + 20 * Math.sqrt(Math.max(others.length, 1))
  const reach = Math.max(64, Math.min(maxR * 0.92, wanted))
  const bodies = new Map<string, Body>()
  if (hub) {
    bodies.set(hub.id, { id: hub.id, x: 0, y: 0, radius: hub.radius, pin: true })
  }
  others.forEach((node, index) => {
    const angle = index * 2.399963
    const ratio = others.length <= 1 ? 0.45 : 0.22 + 0.72 * Math.sqrt(index / others.length)
    bodies.set(node.id, {
      id: node.id,
      x: Math.cos(angle) * reach * ratio,
      y: Math.sin(angle) * reach * ratio,
      radius: node.radius,
      pin: false,
    })
  })
  const list = [...bodies.values()]
  const intra = edges.filter((edge) => bodies.has(edge.source) && bodies.has(edge.target))
  for (let step = 0; step < 90; step += 1) {
    const forceX = new Map<string, number>()
    const forceY = new Map<string, number>()
    const push = (id: string, x: number, y: number) => {
      forceX.set(id, (forceX.get(id) ?? 0) + x)
      forceY.set(id, (forceY.get(id) ?? 0) + y)
    }
    for (let left = 0; left < list.length; left += 1) {
      for (let right = left + 1; right < list.length; right += 1) {
        const a = list[left]
        const b = list[right]
        if (!a || !b) {
          continue
        }
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.hypot(dx, dy) || 0.01
        const min = a.radius + b.radius + 20
        const amount = dist < min ? (min - dist) / dist : 90 / (dist * dist)
        const ux = dx / dist
        const uy = dy / dist
        push(a.id, -ux * amount, -uy * amount)
        push(b.id, ux * amount, uy * amount)
      }
    }
    for (const edge of intra) {
      const a = bodies.get(edge.source)
      const b = bodies.get(edge.target)
      if (!a || !b) {
        continue
      }
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.hypot(dx, dy) || 0.01
      const preferred = edge.relation === "UI_GROUP" ? 86 : 62
      const pull = (dist - preferred) * 0.06
      const ux = dx / dist
      const uy = dy / dist
      push(a.id, ux * pull, uy * pull)
      push(b.id, -ux * pull, -uy * pull)
    }
    for (const body of list) {
      if (body.pin) {
        continue
      }
      push(body.id, -body.x * 0.015, -body.y * 0.015)
      body.x += (forceX.get(body.id) ?? 0) * 0.42
      body.y += (forceY.get(body.id) ?? 0) * 0.42
      const length = Math.hypot(body.x, body.y)
      if (length > reach) {
        body.x = (body.x / length) * reach
        body.y = (body.y / length) * reach
      }
    }
  }
  return new Map([...bodies.values()].map((body) => [body.id, { x: body.x, y: body.y }]))
}

/** 先按五个固定区域摆放，再只在各自范围内做局部力导向。同一批数据每次结果相同。 */
export function layoutPartitions(
  nodes: PartitionNode[],
  edges: PartitionEdge[],
  width = 1280,
  height = 720,
): Record<string, Point> {
  const centers = regionCenters(Math.max(width, 960), Math.max(height, 560))
  const placed: Record<string, Point> = {}
  for (const region of REGION_ORDER) {
    const group = nodes.filter((node) => node.region === region)
    const spot = centers[region]
    if (group.length === 0 || !spot) {
      continue
    }
    const local = layoutGroup(group, edges, spot.maxR)
    for (const [id, point] of local) {
      placed[id] = { x: Math.round(spot.x + point.x), y: Math.round(spot.y + point.y) }
    }
  }
  return placed
}
