// Pure layout helpers for the supply-chain graph view.
//
// No React imports here — these functions are tested directly in
// [layout.test.ts](./__tests__/layout.test.ts) without a DOM or JSX
// runtime. Anything that returns JSX lives in `nodes.tsx`; anything
// that consumes data + builds a layout description lives here.
import type { CompanyNode, NodeSource, SupplyChainData } from '../../types/supplyChain'

// ---------------------------------------------------------------- constants
export const FOCAL_X = 600
export const FOCAL_Y_TOP = 60
export const SUPPLIER_X = 100
export const CUSTOMER_X = 1100
export const NODE_VSPACE = 78
export const COMP_HSPACE = 180
export const LANE_GAP = 60
export const LANE_HEADER_H = 28

export const SOURCE_COLORS: Record<NodeSource, string> = {
  '10-K': '#94a3b8',
  '8-K': '#60a5fa',
  industry: '#fbbf24',
}

// ----------------------------------------------------------------- edges
export interface EdgeStroke {
  stroke: string
  dash?: string
}

export function edgeStrokeFor(source: NodeSource): EdgeStroke {
  if (source === 'industry') return { stroke: '#fbbf24', dash: '5 4' }
  if (source === '8-K') return { stroke: '#60a5fa' }
  return { stroke: '#64748b' }
}

// --------------------------------------------------------------- segments
const CROSS_SEGMENT_KEY = 'Cross-segment'

export function groupBySegment(
  items: CompanyNode[],
  segments: string[],
): Map<string, CompanyNode[]> {
  const map = new Map<string, CompanyNode[]>()
  for (const seg of segments) map.set(seg, [])
  map.set(CROSS_SEGMENT_KEY, [])
  for (const it of items) {
    const key = it.segment && segments.includes(it.segment) ? it.segment : CROSS_SEGMENT_KEY
    map.get(key)!.push(it)
  }
  for (const [k, v] of map) if (v.length === 0) map.delete(k)
  return map
}

// ------------------------------------------------------------------ lanes
export interface LaneInfo {
  segment: string
  yStart: number
  yEnd: number
}

export interface ColumnLayout {
  laneInfo: LaneInfo[]
  totalH: number
  byLane: Map<string, CompanyNode[]>
  useSegments: boolean
}

/**
 * Build per-column lane info for one side (suppliers or customers) of the
 * graph. ``segments.length >= 2`` is the trigger to draw segment lanes;
 * single-segment companies fall back to a single ``__all__`` lane with no
 * header band.
 */
export function buildColumnLayout(
  list: CompanyNode[],
  segments: string[],
): ColumnLayout {
  const useSegments = segments.length >= 2
  const byLane = useSegments
    ? groupBySegment(list, segments)
    : new Map([['__all__', list]])

  let y = FOCAL_Y_TOP + 80
  const laneInfo: LaneInfo[] = []
  for (const [seg, items] of byLane) {
    const yStart = y
    const headerOffset = useSegments ? LANE_HEADER_H : 0
    const h = headerOffset + Math.max(items.length, 1) * NODE_VSPACE
    laneInfo.push({ segment: seg, yStart, yEnd: yStart + h })
    y += h + LANE_GAP
  }
  return { laneInfo, totalH: y, byLane, useSegments }
}

// ---------------------------------------------------- focal-node positioning
export function focalY(supTotalH: number, cusTotalH: number): number {
  const maxLaneH = Math.max(supTotalH, cusTotalH, 300)
  return FOCAL_Y_TOP + maxLaneH / 2 - 40
}

// ---------------------------------------------------- competitor row layout
export function competitorRow(
  d: Pick<SupplyChainData, 'competitors'>,
  supTotalH: number,
  cusTotalH: number,
): { y: number; startX: number } {
  const y = Math.max(supTotalH, cusTotalH) + 40
  const startX =
    FOCAL_X - ((d.competitors.length - 1) * COMP_HSPACE) / 2 + 90 - 75
  return { y, startX }
}
