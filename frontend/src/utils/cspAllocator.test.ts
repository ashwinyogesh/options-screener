import { describe, it, expect } from 'vitest'
import { allocateCsp } from './cspAllocator'
import type { CspResult, CspStrikeInfo } from '../types/csp'
import type { AllocatorSettings } from '../types/cspAllocator'

const BALANCED: AllocatorSettings = {
  mode: 'edge',
  riskTolerance: 0.5,
  minScore: 69,
  maxNamePct: 1,
  diversification: 0,
}

// Optimized tests default to no per-name cap; the cap is exercised explicitly.
const OPTIMIZED: AllocatorSettings = { ...BALANCED, mode: 'optimized', maxNamePct: 1 }

function strike(overrides: Partial<CspStrikeInfo> & Pick<CspStrikeInfo, 'strike' | 'premium' | 'csp_score'>): CspStrikeInfo {
  return {
    delta: -0.3,
    annualized_return: 20,
    bid_ask_spread_pct: 2,
    env_score: 70,
    strike_score: 70,
    env_detail: '',
    strike_detail: '',
    is_best: false,
    iv_fallback: false,
    stale_premium: false,
    iv_hv_ratio: 1.2,
    dist_pct: 5,
    em_buffer_pct: 10,
    otm_pct: 8,
    lq_count: 2000,
    roc_annualized: 22,
    iv_stale: false,
    ...overrides,
  }
}

function result(symbol: string, strikes: CspStrikeInfo[], overrides: Partial<CspResult> = {}): CspResult {
  return {
    symbol,
    price: 100,
    bb_upper: 110,
    bb_middle: 100,
    bb_lower: 90,
    sma_ratio: 1.1,
    rsi: 50,
    iv_rank: 50,
    iv_percentile: 50,
    earnings_date: null,
    earnings_within_dte: false,
    vol_support_126_1: null,
    vol_support_126_2: null,
    vol_support_126_3: null,
    dte: 35,
    expiration: '2026-10-16',
    strikes,
    best_csp_score: Math.max(...strikes.map(s => s.csp_score)),
    using_hv_fallback: false,
    expected_move: 8,
    dist_from_52w_high_pct: -5,
    chain_median_oi: 2000,
    sma_200: 0,
    avwap_52w_low: 0,
    put_wall: 0,
    ...overrides,
  }
}

describe('allocateCsp', () => {
  it('returns an empty basket with a note when capital is zero', () => {
    const res = allocateCsp([result('SOFI', [strike({ strike: 15, premium: 0.5, csp_score: 80 })])], 0, BALANCED)
    expect(res.picks).toHaveLength(0)
    expect(res.notes.length).toBeGreaterThan(0)
  })

  it('never deploys more than the available capital', () => {
    const results = [
      result('SOFI', [strike({ strike: 15, premium: 0.6, csp_score: 85 })]),
      result('UBER', [strike({ strike: 60, premium: 2.0, csp_score: 82 })]),
      result('NBIS', [strike({ strike: 100, premium: 3.5, csp_score: 80 })]),
    ]
    const res = allocateCsp(results, 15000, BALANCED)
    expect(res.totalDeployed).toBeLessThanOrEqual(15000)
    expect(res.totalDeployed + res.idleCash).toBeCloseTo(15000, 5)
    expect(res.numContracts).toBeGreaterThan(0)
  })

  it('excludes contracts below the min-score gate', () => {
    const results = [
      result('JUNK', [strike({ strike: 20, premium: 0.9, csp_score: 40 })]),
      result('SOFI', [strike({ strike: 15, premium: 0.6, csp_score: 85 })]),
    ]
    const res = allocateCsp(results, 10000, BALANCED)
    expect(res.picks.map(p => p.symbol)).not.toContain('JUNK')
    expect(res.picks.map(p => p.symbol)).toContain('SOFI')
  })

  it('allows a single name to exceed a third of capital (no concentration cap)', () => {
    const results = [
      result('SOFI', [strike({ strike: 15, premium: 0.6, csp_score: 90 })]),
      result('UBER', [strike({ strike: 60, premium: 2.0, csp_score: 80 })]),
    ]
    const res = allocateCsp(results, 15000, BALANCED)
    // With caps removed, the top-ranked name fills to the capital limit.
    expect(res.largestNamePct).toBeGreaterThan(0.35)
  })

  it('keeps only one strike per symbol (no same-name stacking)', () => {
    const results = [
      result('SOFI', [
        strike({ strike: 15, premium: 0.6, csp_score: 85 }),
        strike({ strike: 14, premium: 0.4, csp_score: 78 }),
      ]),
    ]
    const res = allocateCsp(results, 5000, BALANCED)
    const sofi = res.picks.filter(p => p.symbol === 'SOFI')
    expect(sofi).toHaveLength(1)
  })

  it('emits a note and empty basket when the only contract exceeds the capital', () => {
    // One contract needing $10,000 collateral against a $5,000 budget.
    const results = [result('NBIS', [strike({ strike: 100, premium: 3.5, csp_score: 85 })])]
    const res = allocateCsp(results, 5000, BALANCED)
    expect(res.picks).toHaveLength(0)
    expect(res.notes.join(' ')).toMatch(/collateral|capital/i)
  })

  it('builds a concentrated single-name baseline for comparison', () => {
    const results = [
      result('SOFI', [strike({ strike: 15, premium: 0.6, csp_score: 88 })]),
      result('UBER', [strike({ strike: 60, premium: 2.0, csp_score: 80 })]),
    ]
    const res = allocateCsp(results, 15000, BALANCED)
    expect(res.baseline).not.toBeNull()
    expect(res.baseline!.utilizationPct).toBeGreaterThan(0)
  })

  it('aggressive tolerance ranks higher-ROC contracts ahead of safer ones', () => {
    const results = [
      result('SAFE', [strike({ strike: 20, premium: 0.4, csp_score: 95, roc_annualized: 8 })]),
      result('YIELD', [strike({ strike: 20, premium: 1.2, csp_score: 72, roc_annualized: 40 })]),
    ]
    const aggressive = allocateCsp(results, 2000, { ...BALANCED, riskTolerance: 1 })
    // With one $2000-collateral slot and full-aggressive ranking, YIELD wins the top pick.
    expect(aggressive.picks[0]?.symbol).toBe('YIELD')
  })

  it('conservative tolerance ranks higher-score contracts ahead of higher-ROC ones', () => {
    const results = [
      result('SAFE', [strike({ strike: 20, premium: 0.4, csp_score: 95, roc_annualized: 8 })]),
      result('YIELD', [strike({ strike: 20, premium: 1.2, csp_score: 72, roc_annualized: 40 })]),
    ]
    const conservative = allocateCsp(results, 2000, { ...BALANCED, riskTolerance: 0 })
    // At full-conservative the safer, higher-score contract must take the single slot.
    expect(conservative.picks[0]?.symbol).toBe('SAFE')
  })

  it('the slider actually moves the pick between the two axes (symmetric scaling)', () => {
    // Same eligible set; only the slider changes. With symmetric min-max scaling
    // the winner must flip — this is the regression guard for the scale-bias bug.
    const results = [
      result('SAFE', [strike({ strike: 20, premium: 0.4, csp_score: 95, roc_annualized: 8 })]),
      result('YIELD', [strike({ strike: 20, premium: 1.2, csp_score: 72, roc_annualized: 40 })]),
    ]
    const opts = { ...BALANCED }
    const conservativeTop = allocateCsp(results, 2000, { ...opts, riskTolerance: 0 }).picks[0]?.symbol
    const aggressiveTop = allocateCsp(results, 2000, { ...opts, riskTolerance: 1 }).picks[0]?.symbol
    expect(conservativeTop).not.toBe(aggressiveTop)
  })
})

describe('allocateCsp — optimized (knapsack) mode', () => {
  it('never deploys more than the available capital', () => {
    const results = [
      result('AAA', [
        strike({ strike: 70, premium: 1.5, csp_score: 90, otm_pct: 17 }),
        strike({ strike: 80, premium: 2.5, csp_score: 85, otm_pct: 6 }),
      ]),
      result('BBB', [strike({ strike: 40, premium: 1.0, csp_score: 82, otm_pct: 12 })]),
    ]
    const res = allocateCsp(results, 20000, OPTIMIZED)
    expect(res.totalDeployed).toBeLessThanOrEqual(20000)
    expect(res.totalDeployed + res.idleCash).toBeCloseTo(20000, 5)
    expect(res.numContracts).toBeGreaterThan(0)
  })

  it('prefers the deeper (safer) strike at full-conservative', () => {
    // Same name, two strikes: $70 is lower |delta| (safer), $80 richer premium.
    const results = [
      result('AAA', [
        strike({ strike: 70, premium: 1.5, csp_score: 88, delta: -0.15 }),
        strike({ strike: 80, premium: 3.0, csp_score: 88, delta: -0.35 }),
      ]),
      result('BBB', [strike({ strike: 30, premium: 0.5, csp_score: 80, delta: -0.25 })]),
    ]
    const res = allocateCsp(results, 20000, { ...OPTIMIZED, riskTolerance: 0 })
    const aaa = res.picks.find(p => p.symbol === 'AAA')
    expect(aaa?.strike).toBe(70)
  })

  it('prefers the higher-ROC strike at full-aggressive', () => {
    const results = [
      result('AAA', [
        strike({ strike: 70, premium: 1.5, csp_score: 88, delta: -0.15, roc_annualized: 12 }),
        strike({ strike: 80, premium: 3.0, csp_score: 88, delta: -0.35, roc_annualized: 40 }),
      ]),
      result('BBB', [strike({ strike: 30, premium: 0.5, csp_score: 80, delta: -0.25, roc_annualized: 8 })]),
    ]
    const res = allocateCsp(results, 20000, { ...OPTIMIZED, riskTolerance: 1 })
    const aaa = res.picks.find(p => p.symbol === 'AAA')
    expect(aaa?.strike).toBe(80)
  })

  it('keeps at most one strike per name', () => {
    const results = [
      result('AAA', [
        strike({ strike: 70, premium: 1.5, csp_score: 88, delta: -0.15 }),
        strike({ strike: 75, premium: 2.0, csp_score: 88, delta: -0.25 }),
        strike({ strike: 80, premium: 3.0, csp_score: 88, delta: -0.35 }),
      ]),
    ]
    const res = allocateCsp(results, 30000, OPTIMIZED)
    expect(res.picks.filter(p => p.symbol === 'AAA')).toHaveLength(1)
  })

  it('still enforces the min-score gate', () => {
    const results = [result('JUNK', [strike({ strike: 40, premium: 1.0, csp_score: 40, otm_pct: 15 })])]
    const res = allocateCsp(results, 20000, OPTIMIZED)
    expect(res.picks).toHaveLength(0)
  })

  it('concave merit spreads the book vs the linear (diversification off) basket', () => {
    // One dominant name (best delta + yield) and two weaker ones, no cap.
    const results = [
      result('SOFI', [strike({ strike: 20, premium: 1.0, csp_score: 88, delta: -0.15, roc_annualized: 25 })], { price: 100, expected_move: 10 }),
      result('HOOD', [strike({ strike: 20, premium: 1.0, csp_score: 84, delta: -0.22, roc_annualized: 20 })], { price: 100, expected_move: 10 }),
      result('PLTR', [strike({ strike: 20, premium: 1.0, csp_score: 80, delta: -0.30, roc_annualized: 15 })], { price: 100, expected_move: 10 }),
    ]
    const linear = allocateCsp(results, 10000, { ...OPTIMIZED, diversification: 0 })
    const concave = allocateCsp(results, 10000, { ...OPTIMIZED, diversification: 1 })
    // Linear pours into the single best name; concave holds fewer of it and adds another.
    expect(concave.numNames).toBeGreaterThan(linear.numNames)
    expect(concave.largestNamePct).toBeLessThan(linear.largestNamePct)
  })

  it('vol-normalises return so high-IV is not mistaken for free edge', () => {
    // Same headline ROC and delta; LOWVOL has a smaller expected move, so its
    // yield-per-move is richer and it wins the aggressive (return-weighted) pick.
    const results = [
      result('LOWVOL', [strike({ strike: 50, premium: 1.0, csp_score: 85, delta: -0.25, roc_annualized: 20 })], { price: 100, expected_move: 5 }),
      result('HIVOL', [strike({ strike: 50, premium: 1.0, csp_score: 85, delta: -0.25, roc_annualized: 20 })], { price: 100, expected_move: 20 }),
    ]
    const res = allocateCsp(results, 10000, { ...OPTIMIZED, riskTolerance: 1 })
    expect(res.picks[0].symbol).toBe('LOWVOL')
  })

  it('caps any single name at maxNamePct of capital', () => {
    // Four cheap names; a 30% cap must keep every name at or below 30% of capital.
    const results = [
      result('AAA', [strike({ strike: 10, premium: 0.6, csp_score: 88, otm_pct: 14 })]),
      result('BBB', [strike({ strike: 10, premium: 0.5, csp_score: 85, otm_pct: 13 })]),
      result('CCC', [strike({ strike: 10, premium: 0.4, csp_score: 82, otm_pct: 12 })]),
      result('DDD', [strike({ strike: 10, premium: 0.3, csp_score: 80, otm_pct: 11 })]),
    ]
    const res = allocateCsp(results, 10000, { ...OPTIMIZED, maxNamePct: 0.3 })
    for (const p of res.picks) {
      expect(p.capitalPct).toBeLessThanOrEqual(0.3 + 1e-9)
    }
    expect(res.largestNamePct).toBeLessThanOrEqual(0.3 + 1e-9)
  })

  it('dollar-weights merit so an expensive high-merit name beats cheap ones (no cheap-stock bias)', () => {
    // At balanced, merit rises with both safety (low |delta|) and ROC. NBIS is the
    // highest-merit name; count-weighted sum would favour buying many cheap SOFI, but
    // dollar-weighting must select the expensive high-merit NBIS as the largest holding.
    const results = [
      result('SOFI', [strike({ strike: 15, premium: 0.5, csp_score: 80, delta: -0.35, roc_annualized: 10 })]),
      result('MID', [strike({ strike: 50, premium: 1.5, csp_score: 80, delta: -0.25, roc_annualized: 15 })]),
      result('NBIS', [strike({ strike: 170, premium: 6.0, csp_score: 80, delta: -0.15, roc_annualized: 20 })]),
    ]
    const res = allocateCsp(results, 18000, OPTIMIZED)
    expect(res.picks[0].symbol).toBe('NBIS')
  })

  it('excludes strikes whose delta failed to compute (delta = 0 is not "safest")', () => {
    // BADDELTA has the higher score but no usable delta; the guard must drop it so
    // its |delta| = 0 can't normalize to maximum safety and win the slot.
    const results = [
      result('BADDELTA', [strike({ strike: 20, premium: 1.0, csp_score: 92, delta: 0 })]),
      result('GOOD', [strike({ strike: 20, premium: 0.8, csp_score: 85, delta: -0.2 })]),
    ]
    const res = allocateCsp(results, 10000, OPTIMIZED)
    expect(res.picks.map(p => p.symbol)).not.toContain('BADDELTA')
    expect(res.picks.map(p => p.symbol)).toContain('GOOD')
  })
})
