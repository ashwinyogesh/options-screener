import { describe, it, expect } from 'vitest'
import { computeHappyPrice, happyBadge } from './happyPrice'
import type { CspResult } from '../types/csp'

function mk(overrides: Partial<CspResult>): CspResult {
  return {
    symbol: 'TEST',
    price: 100,
    bb_upper: 110,
    bb_middle: 95,
    bb_lower: 80,
    sma_ratio: 1.1,
    rsi: 55,
    iv_rank: 50,
    iv_percentile: 50,
    earnings_date: null,
    earnings_within_dte: false,
    vol_support_126_1: null,
    vol_support_126_2: null,
    vol_support_126_3: null,
    dte: 35,
    expiration: '2026-10-16',
    strikes: [],
    best_csp_score: 0,
    using_hv_fallback: false,
    expected_move: 8,
    dist_from_52w_high_pct: -10,
    chain_median_oi: 1000,
    sma_200: 0,
    avwap_52w_low: 0,
    put_wall: 0,
    ...overrides,
  }
}

describe('computeHappyPrice', () => {
  it('goes silent in a confirmed downtrend', () => {
    const hp = computeHappyPrice(mk({ sma_ratio: 0.9, price: 90, bb_middle: 100, rsi: 40 }))
    expect(hp.downtrend).toBe(true)
    expect(hp.point).toBeNull()
  })

  it('is high confidence when three independent families agree', () => {
    const hp = computeHappyPrice(mk({
      price: 100,
      vol_support_126_1: 90,   // structure
      put_wall: 91,            // options
      avwap_52w_low: 92,       // institutional
      expected_move: 30,       // EM floor 70 — an outlier, excluded from consensus
    }))
    expect(hp.familyCount).toBe(3)
    expect(hp.point).toBe(91)
    expect(hp.zoneLo).toBe(90)
    expect(hp.zoneHi).toBe(92)
    expect(hp.confidence).toBe('high')
  })

  it('is medium confidence when exactly two families agree', () => {
    const hp = computeHappyPrice(mk({
      price: 100,
      vol_support_126_1: 90,   // structure
      expected_move: 12,       // volatility — EM floor 88, within band of 90
    }))
    expect(hp.familyCount).toBe(2)
    expect(hp.zoneLo).toBe(88)
    expect(hp.zoneHi).toBe(90)
    expect(hp.confidence).toBe('medium')
  })

  it('suppresses when families scatter with no cluster of two', () => {
    const hp = computeHappyPrice(mk({
      price: 100,
      vol_support_126_1: 95,   // structure (at the near-spot boundary)
      expected_move: 40,       // volatility — EM floor 60, far from 95
    }))
    expect(hp.point).toBeNull()
    expect(hp.downtrend).toBe(false)
  })

  it('suppresses when fewer than two families qualify', () => {
    const hp = computeHappyPrice(mk({ price: 100, expected_move: 8, vol_support_126_1: null }))
    expect(hp.point).toBeNull()
    expect(hp.downtrend).toBe(false)
  })

  it('drops anchors within 5% of spot (not a discount to own)', () => {
    const hp = computeHappyPrice(mk({
      price: 100,              // near-spot ceiling = 95
      expected_move: 0,
      vol_support_126_1: 98,   // dropped — inside 5% of spot
      vol_support_126_2: 90,   // structure keeps this one
      put_wall: 91,            // options
    }))
    expect(hp.zoneLo).toBe(90)
    expect(hp.zoneHi).toBe(91)
    expect(hp.anchors.some((a) => a.price === 98)).toBe(false)
    expect(hp.anchors.some((a) => a.family === 'options')).toBe(true)
  })

  it('shows volume support as real nodes, never a synthetic median (HOOD case)', () => {
    // spot 100 → near-spot ceiling 95; the nearest node (105) is dropped.
    const hp = computeHappyPrice(mk({
      price: 100,
      expected_move: 0,
      vol_support_126_1: 105,  // dropped — above the near-spot ceiling
      vol_support_126_2: 91,   // real structure node, joins consensus
      vol_support_126_3: 78,   // real structure node, outside consensus
      put_wall: 90,            // options
      avwap_52w_low: 92,       // institutional
      sma_200: 95,             // trend
    }))
    // structure counts once, but its node keeps its real price ($91, not median 84.5)
    expect(hp.familyCount).toBe(4)
    expect(hp.anchors.some((a) => a.price === 91)).toBe(true)
    expect(hp.anchors.some((a) => a.price === 78)).toBe(true)
    expect(hp.anchors.some((a) => Math.round(a.price) === 84)).toBe(false)
    expect(hp.zoneLo).toBe(90)
    expect(hp.zoneHi).toBe(95)
    expect(hp.confidence).toBe('high')
  })
})

describe('happyBadge', () => {
  // structure 90 + options 88 → consensus zone [88, 90]
  const hp = computeHappyPrice(mk({ price: 100, vol_support_126_1: 90, put_wall: 88, expected_move: 0 }))
  it('is below when break-even clears the whole zone', () => {
    expect(happyBadge(hp, 85)).toBe('below')
  })
  it('is inside when break-even is within the zone', () => {
    expect(happyBadge(hp, 89)).toBe('inside')
  })
  it('is above when break-even sits over the zone', () => {
    expect(happyBadge(hp, 95)).toBe('above')
  })
  it('is none when there is no zone', () => {
    const empty = computeHappyPrice(mk({ vol_support_126_1: null, expected_move: 0 }))
    expect(happyBadge(empty, 90)).toBe('none')
  })
})
