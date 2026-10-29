export interface FanPoint {
  x: number
  y: number
}

export interface FanOccupant extends FanPoint {
  radius: number
}

function spanFor(count: number): number {
  if (count <= 1) {
    return 0
  }
  if (count <= 3) {
    return Math.PI * 0.42
  }
  if (count <= 6) {
    return Math.PI * 0.5
  }
  if (count <= 12) {
    return Math.PI * 0.92
  }
  return Math.PI * 1.12
}

function emptyAngle(origin: FanPoint, occupants: readonly FanOccupant[], bounds?: { width: number; height: number }): number {
  let bestAngle = -Math.PI / 2
  let bestScore = Number.NEGATIVE_INFINITY
  const inward = occupants.length
    ? Math.atan2(
        occupants.reduce((sum, item) => sum + item.y, 0) / occupants.length - origin.y,
        occupants.reduce((sum, item) => sum + item.x, 0) / occupants.length - origin.x,
      )
    : Math.PI / 2
  for (let step = 0; step < 36; step += 1) {
    const angle = -Math.PI + (step * 2 * Math.PI) / 36
    let score = -Math.cos(angle - inward) * 90
    for (const distance of [78, 140, 210]) {
      const x = origin.x + Math.cos(angle) * distance
      const y = origin.y + Math.sin(angle) * distance
      if (bounds && (x < 48 || y < 40 || x > bounds.width - 48 || y > bounds.height - 40)) {
        if (distance <= 78) {
          score = Number.NEGATIVE_INFINITY
          break
        }
        score -= 220
      }
      let nearest = 280
      for (const item of occupants) {
        nearest = Math.min(nearest, Math.hypot(item.x - x, item.y - y) - item.radius)
      }
      score += nearest
    }
    if (score > bestScore) {
      bestScore = score
      bestAngle = angle
    }
  }
  return bestAngle
}

function overlaps(point: FanPoint, blocked: readonly FanOccupant[], gap: number): boolean {
  return blocked.some((item) => Math.hypot(item.x - point.x, item.y - point.y) < item.radius + gap)
}

function clearPoint(origin: FanPoint, point: FanPoint, blocked: readonly FanOccupant[], gap: number): FanPoint {
  let angle = Math.atan2(point.y - origin.y, point.x - origin.x)
  let distance = Math.max(72, Math.hypot(point.x - origin.x, point.y - origin.y))
  for (let step = 0; step < 16; step += 1) {
    const candidate = {
      x: origin.x + Math.cos(angle) * distance,
      y: origin.y + Math.sin(angle) * distance,
    }
    if (!overlaps(candidate, blocked, gap)) {
      return candidate
    }
    distance += 22
    if (step % 3 === 2) {
      angle += 0.28
    }
  }
  return {
    x: origin.x + Math.cos(angle) * distance,
    y: origin.y + Math.sin(angle) * distance,
  }
}

/** 把新节点排在入口周围最空的一侧。不改动已有节点坐标。 */
export function fanPositions(
  origin: FanPoint,
  occupants: readonly FanOccupant[],
  count: number,
  nodeRadius: number,
  bounds?: { width: number; height: number },
): FanPoint[] {
  if (count <= 0) {
    return []
  }
  const gap = Math.max(46, nodeRadius * 2 + 26)
  const span = spanFor(count)
  const center = emptyAngle(origin, occupants, bounds)
  const blocked = occupants.map((item) => ({ ...item }))
  const placed: FanPoint[] = []
  let remaining = count
  let ring = 0
  while (remaining > 0) {
    const radius = 88 + ring * 64
    const capacity =
      span === 0 ? 1 : Math.max(1, Math.floor((span * radius) / gap))
    const take = Math.min(remaining, capacity)
    for (let index = 0; index < take; index += 1) {
      const offset = take === 1 ? 0.5 : index / (take - 1)
      const angle = center - span / 2 + offset * span
      const raw = {
        x: origin.x + Math.cos(angle) * radius,
        y: origin.y + Math.sin(angle) * radius,
      }
      const point = clearPoint(origin, raw, blocked, gap)
      if (bounds) {
        point.x = Math.min(bounds.width - 88, Math.max(88, point.x))
        point.y = Math.min(bounds.height - 64, Math.max(64, point.y))
      }
      placed.push(point)
      blocked.push({ ...point, radius: nodeRadius })
    }
    remaining -= take
    ring += 1
  }
  return placed
}
