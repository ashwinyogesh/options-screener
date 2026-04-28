// Pure-function tests for the supply-chain layout helpers. No DOM, no
// React, no JSX — runs under vitest's default node environment.
import { describe, expect, it } from 'vitest'

import type { CompanyNode } from '../../../types/supplyChain'
import {
  COMP_HSPACE,
  FOCAL_X,
  LANE_HEADER_H,
  NODE_VSPACE,
  SOURCE_COLORS,
  buildColumnLayout,
  competitorRow,
  edgeStrokeFor,
  focalY,
  groupBySegment,
} from '../layout'

function node(overrides: Partial<CompanyNode> = {}): CompanyNode {
  return {
    name: 'Acme',
    ticker: null,
    relationship: 'supplier',
    revenue_pct: null,
    cost_pct: null,
    notes: '',
    source: '10-K',
    segment: null,
    confidence: null,
    ...overrides,
  }
}

describe('edgeStrokeFor', () => {
  it('returns a dashed amber stroke for industry sources', () => {
    const stroke = edgeStrokeFor('industry')

    expect(stroke).toEqual({ stroke: '#fbbf24', dash: '5 4' })
  })

  it('returns a solid blue stroke for 8-K sources', () => {
    const stroke = edgeStrokeFor('8-K')

    expect(stroke.stroke).toBe('#60a5fa')
    expect(stroke.dash).toBeUndefined()
  })

  it('returns a solid neutral stroke for 10-K sources', () => {
    const stroke = edgeStrokeFor('10-K')

    expect(stroke.stroke).toBe('#64748b')
    expect(stroke.dash).toBeUndefined()
  })
})

describe('SOURCE_COLORS', () => {
  it('maps each NodeSource to a CSS hex string', () => {
    expect(SOURCE_COLORS['10-K']).toMatch(/^#[0-9a-f]{6}$/i)
    expect(SOURCE_COLORS['8-K']).toMatch(/^#[0-9a-f]{6}$/i)
    expect(SOURCE_COLORS.industry).toMatch(/^#[0-9a-f]{6}$/i)
  })
})

describe('groupBySegment', () => {
  it('routes items to their declared segment when listed', () => {
    const items = [node({ name: 'A', segment: 'Cloud' }), node({ name: 'B', segment: 'Office' })]

    const grouped = groupBySegment(items, ['Cloud', 'Office'])

    expect(grouped.get('Cloud')?.map((n) => n.name)).toEqual(['A'])
    expect(grouped.get('Office')?.map((n) => n.name)).toEqual(['B'])
    expect(grouped.has('Cross-segment')).toBe(false)
  })

  it('routes items with unknown segments to Cross-segment', () => {
    const items = [
      node({ name: 'A', segment: 'Cloud' }),
      node({ name: 'B', segment: 'Mystery' }),
      node({ name: 'C', segment: null }),
    ]

    const grouped = groupBySegment(items, ['Cloud', 'Office'])

    expect(grouped.get('Cloud')?.map((n) => n.name)).toEqual(['A'])
    expect(grouped.get('Cross-segment')?.map((n) => n.name)).toEqual(['B', 'C'])
    expect(grouped.has('Office')).toBe(false) // empty lane removed
  })

  it('drops empty segment lanes from the output map', () => {
    const items = [node({ name: 'A', segment: 'Cloud' })]

    const grouped = groupBySegment(items, ['Cloud', 'Office', 'Devices'])

    expect([...grouped.keys()]).toEqual(['Cloud'])
  })
})

describe('buildColumnLayout', () => {
  it('uses single __all__ lane when fewer than 2 segments', () => {
    const items = [node({ name: 'A' }), node({ name: 'B' })]

    const layout = buildColumnLayout(items, [])

    expect(layout.useSegments).toBe(false)
    expect([...layout.byLane.keys()]).toEqual(['__all__'])
    expect(layout.laneInfo[0].yStart).toBeLessThan(layout.laneInfo[0].yEnd)
  })

  it('uses segment lanes when 2+ segments are provided', () => {
    const items = [
      node({ name: 'A', segment: 'Cloud' }),
      node({ name: 'B', segment: 'Office' }),
    ]

    const layout = buildColumnLayout(items, ['Cloud', 'Office'])

    expect(layout.useSegments).toBe(true)
    expect([...layout.byLane.keys()].sort()).toEqual(['Cloud', 'Office'])
  })

  it('reserves header height when segment lanes are active', () => {
    const items = [node({ name: 'A', segment: 'Cloud' })]

    const single = buildColumnLayout(items, [])
    const segmented = buildColumnLayout(items, ['Cloud', 'Office'])

    const singleH = single.laneInfo[0].yEnd - single.laneInfo[0].yStart
    const segH = segmented.laneInfo[0].yEnd - segmented.laneInfo[0].yStart
    expect(segH - singleH).toBe(LANE_HEADER_H)
  })

  it('lane height grows linearly with item count', () => {
    const one = buildColumnLayout([node()], [])
    const three = buildColumnLayout([node(), node(), node()], [])

    const oneH = one.laneInfo[0].yEnd - one.laneInfo[0].yStart
    const threeH = three.laneInfo[0].yEnd - three.laneInfo[0].yStart
    expect(threeH - oneH).toBe(2 * NODE_VSPACE)
  })
})

describe('focalY', () => {
  it('centres the focal node vertically over the taller column', () => {
    const supTotal = 800
    const cusTotal = 400

    const y = focalY(supTotal, cusTotal)

    // Math: FOCAL_Y_TOP (60) + 800/2 - 40 = 420
    expect(y).toBe(420)
  })

  it('clamps to a minimum lane height of 300', () => {
    const y = focalY(0, 0)

    // Math: 60 + 300/2 - 40 = 170
    expect(y).toBe(170)
  })
})

describe('competitorRow', () => {
  it('places competitors below the taller of the two columns', () => {
    const row = competitorRow({ competitors: [node(), node()] }, 800, 400)

    expect(row.y).toBe(840) // max(800, 400) + 40
  })

  it('centres competitors around FOCAL_X', () => {
    const competitors = [node(), node(), node()]

    const row = competitorRow({ competitors }, 0, 0)

    // 3 nodes spaced COMP_HSPACE apart should be centred around FOCAL_X+15
    const lastX = row.startX + (competitors.length - 1) * COMP_HSPACE
    const midpoint = (row.startX + lastX) / 2
    expect(midpoint).toBe(FOCAL_X + 90 - 75)
  })
})
