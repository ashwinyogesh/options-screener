/**
 * CSP capital allocator — pure, side-effect-free optimizer (ADR-0034).
 *
 * Two modes fill a capital budget from the CSP contracts on screen, subject to a
 * hard minimum-score gate and integer contract counts (collateral = strike × 100):
 *
 *   - 'edge'      — greedy value-density fill; each name contributes its single
 *                   best strike (symmetric-normalised csp_score / ROC blend).
 *   - 'optimized' — bounded-knapsack DP over *all* (name, strike) contracts that
 *                   jointly chooses the strike per name to maximise dollar-weighted
 *                   distance/ROC merit within budget (≤ 1 strike per name, per-name cap).
 *
 * Greedy is fast and explainable; the knapsack is exact for its objective and
 * lets the strike itself be a decision variable (e.g. pick $70 over $80).
 */
import type { CspResult } from '../types/csp'
import type {
  AllocationPick,
  AllocationResult,
  AllocatorCandidate,
  AllocatorSettings,
  SingleContractBaseline,
} from '../types/cspAllocator'
import { getSector } from '../constants/sectors'
import { computeHappyPrice } from './happyPrice'

/**
 * Volatility-normalised yield: per-trade return on collateral earned per 1% of the
 * stock's own expected move, both measured over the SAME horizon. Return (premium
 * ÷ strike) and expected move are single-period, so short-dated strikes are no
 * longer flattered by annualising only the numerator (ADR-0034 audit fix). Strips
 * the "high IV just means high premium" illusion. Falls back to an ~8% expected
 * move when EM is unavailable.
 */
function yieldEfficiency(premium: number, strike: number, price: number, expectedMove: number): number {
  const rocPct = strike > 0 ? (premium / strike) * 100 : 0   // return on collateral over the trade's life
  const emPct = price > 0 ? (expectedMove / price) * 100 : 0
  return rocPct / (emPct > 0 ? emPct : 8)
}

/** Flatten CSP results into per-contract candidates, keeping only eligible ones. */
function buildCandidates(
  results: CspResult[],
  settings: AllocatorSettings,
  capital: number,
): AllocatorCandidate[] {
  const raw: AllocatorCandidate[] = []
  for (const r of results) {
    for (const s of r.strikes) {
      const collateral = s.strike * 100
      const roc = s.roc_annualized ?? s.annualized_return
      if (s.premium <= 0) continue
      if (s.csp_score < settings.minScore) continue
      if (collateral <= 0 || collateral > capital) continue
      raw.push({
        symbol: r.symbol,
        sector: getSector(r.symbol),
        strike: s.strike,
        expiration: r.expiration,
        dte: r.dte,
        premium: s.premium,
        collateral,
        cspScore: s.csp_score,
        roc,
        otmPct: s.otm_pct,
        absDelta: Math.abs(s.delta),
        yieldEff: yieldEfficiency(s.premium, s.strike, r.price, r.expected_move),
        price: r.price,
        volSupport1: r.vol_support_126_1,
        volSupport2: r.vol_support_126_2,
        volSupport3: r.vol_support_126_3,
        expMoveLower: r.expected_move > 0 ? r.price - r.expected_move : r.price,
        happy: computeHappyPrice(r),
        value: 0, // filled in below
      })
    }
  }

  // Normalise both axes across the eligible set so equal slider weight gives
  // equal leverage — a fixed /100 safety scale would let min-max ROI dominate.
  const rocs = raw.map(c => c.roc)
  const minRoc = Math.min(...rocs)
  const maxRoc = Math.max(...rocs)
  const rocSpan = maxRoc - minRoc
  const scores = raw.map(c => c.cspScore)
  const minScore = Math.min(...scores)
  const maxScore = Math.max(...scores)
  const scoreSpan = maxScore - minScore
  const w = clamp01(settings.riskTolerance)
  for (const c of raw) {
    const safety = scoreSpan > 0 ? (c.cspScore - minScore) / scoreSpan : 0.5
    const roi = rocSpan > 0 ? (c.roc - minRoc) / rocSpan : 0.5
    c.value = (1 - w) * safety + w * roi
  }

  // Keep only the single best-value contract per name — stacking strikes of the
  // same stock is concentration, not diversification.
  const bestBySymbol = new Map<string, AllocatorCandidate>()
  for (const c of raw) {
    const existing = bestBySymbol.get(c.symbol)
    if (existing == null || betterCandidate(c, existing)) {
      bestBySymbol.set(c.symbol, c)
    }
  }
  return [...bestBySymbol.values()].sort(betterCandidateSort)
}

function betterCandidate(a: AllocatorCandidate, b: AllocatorCandidate): boolean {
  return betterCandidateSort(a, b) < 0
}

/** Sort: value desc, then csp_score desc, then cheaper collateral first. */
function betterCandidateSort(a: AllocatorCandidate, b: AllocatorCandidate): number {
  if (b.value !== a.value) return b.value - a.value
  if (b.cspScore !== a.cspScore) return b.cspScore - a.cspScore
  return a.collateral - b.collateral
}

/**
 * Run the allocation. Returns an empty-but-valid result when no capital or no
 * eligible contracts exist.
 */
export function allocateCsp(
  results: CspResult[],
  capital: number,
  settings: AllocatorSettings,
): AllocationResult {
  const notes: string[] = []
  const empty = emptyResult(capital, notes)
  if (capital <= 0) {
    notes.push('Enter a capital amount to build a basket.')
    return empty
  }

  if (settings.mode === 'optimized') {
    return allocateOptimized(results, capital, settings, notes)
  }

  const candidates = buildCandidates(results, settings, capital)
  if (candidates.length === 0) {
    notes.push(
      `No contract clears the min score (${settings.minScore}) and fits in $${capital.toLocaleString()} of collateral. ` +
      'Lower the score gate, raise capital, or widen the on-screen results.',
    )
    return empty
  }

  let remaining = capital
  const picks: AllocationPick[] = []

  for (const c of candidates) {
    if (remaining < c.collateral) continue

    const n = Math.floor(remaining / c.collateral)
    if (n < 1) continue

    picks.push(makePick(c, n, capital))
    remaining -= n * c.collateral
  }

  if (picks.length === 0) {
    notes.push('No eligible contract fits in the available capital.')
    return empty
  }

  return summarise(picks, capital, candidates, notes)
}

/** Build one basket line item from a candidate and a contract count. */
function makePick(c: AllocatorCandidate, n: number, capital: number): AllocationPick {
  const deployed = n * c.collateral
  return {
    symbol: c.symbol,
    sector: c.sector,
    strike: c.strike,
    expiration: c.expiration,
    dte: c.dte,
    contracts: n,
    premiumPerContract: c.premium * 100,
    collateralPerContract: c.collateral,
    deployed,
    credit: n * c.premium * 100,
    cspScore: c.cspScore,
    roc: c.roc,
    otmPct: c.otmPct,
    absDelta: c.absDelta,
    yieldEff: c.yieldEff,
    price: c.price,
    volSupport1: c.volSupport1,
    volSupport2: c.volSupport2,
    volSupport3: c.volSupport3,
    expMoveLower: c.expMoveLower,
    happy: c.happy,
    capitalPct: deployed / capital,
  }
}

function summarise(
  picks: AllocationPick[],
  capital: number,
  candidates: AllocatorCandidate[],
  notes: string[],
): AllocationResult {
  const totalDeployed = picks.reduce((a, p) => a + p.deployed, 0)
  const totalCredit = picks.reduce((a, p) => a + p.credit, 0)
  const numContracts = picks.reduce((a, p) => a + p.contracts, 0)
  const idleCash = capital - totalDeployed

  const weightedScore =
    totalDeployed > 0
      ? picks.reduce((a, p) => a + p.cspScore * p.deployed, 0) / totalDeployed
      : 0
  const weightedRocOnDeployed =
    totalDeployed > 0
      ? picks.reduce((a, p) => a + p.roc * p.deployed, 0) / totalDeployed
      : 0
  const weightedDelta =
    totalDeployed > 0
      ? picks.reduce((a, p) => a + p.absDelta * p.deployed, 0) / totalDeployed
      : 0
  const weightedYieldEff =
    totalDeployed > 0
      ? picks.reduce((a, p) => a + p.yieldEff * p.deployed, 0) / totalDeployed
      : 0
  const rocOnCapital = weightedRocOnDeployed * (totalDeployed / capital)

  const largestNamePct = picks.reduce((m, p) => Math.max(m, p.capitalPct), 0)

  const sectorMap = new Map<string, number>()
  for (const p of picks) {
    sectorMap.set(p.sector, (sectorMap.get(p.sector) ?? 0) + p.deployed)
  }
  const sectorBreakdown = [...sectorMap.entries()]
    .map(([sector, deployed]) => ({ sector, deployed, pct: deployed / capital }))
    .sort((a, b) => b.deployed - a.deployed)

  if (idleCash > 0) {
    const idlePct = (idleCash / capital) * 100
    notes.push(
      `$${Math.round(idleCash).toLocaleString()} (${idlePct.toFixed(0)}%) left idle — ` +
      'no remaining eligible contract fits in the leftover capital. Idle cash beats a sub-gate fill.',
    )
  }

  return {
    picks,
    capital,
    totalDeployed,
    idleCash,
    utilizationPct: (totalDeployed / capital) * 100,
    numNames: picks.length,
    numContracts,
    totalCredit,
    weightedScore,
    weightedRocOnDeployed,
    weightedDelta,
    weightedYieldEff,
    rocOnCapital,
    largestNamePct,
    sectorBreakdown,
    baseline: buildBaseline(candidates, capital),
    notes,
  }
}

/**
 * Concentrated single-name baseline: put the whole budget into the top-ranked
 * name (as many contracts as fit, no diversification cap). This is the
 * "one big position" the user is comparing against.
 */
function buildBaseline(
  candidates: AllocatorCandidate[],
  capital: number,
): SingleContractBaseline | null {
  const top = candidates[0]
  if (top == null) return null
  const contracts = Math.floor(capital / top.collateral)
  if (contracts < 1) return null
  const deployed = contracts * top.collateral
  return {
    symbol: top.symbol,
    strike: top.strike,
    contracts,
    deployed,
    credit: contracts * top.premium * 100,
    cspScore: top.cspScore,
    roc: top.roc,
    utilizationPct: (deployed / capital) * 100,
  }
}

function emptyResult(capital: number, notes: string[]): AllocationResult {
  return {
    picks: [],
    capital,
    totalDeployed: 0,
    idleCash: capital > 0 ? capital : 0,
    utilizationPct: 0,
    numNames: 0,
    numContracts: 0,
    totalCredit: 0,
    weightedScore: 0,
    weightedRocOnDeployed: 0,
    weightedDelta: 0,
    weightedYieldEff: 0,
    rocOnCapital: 0,
    largestNamePct: 0,
    sectorBreakdown: [],
    baseline: null,
    notes,
  }
}

function clamp01(x: number): number {
  if (x < 0) return 0
  if (x > 1) return 1
  return x
}

// ---------------------------------------------------------------------------
// Optimized mode — bounded-knapsack DP over (name, strike) contracts
// ---------------------------------------------------------------------------

/**
 * Flatten every eligible (name, strike) contract, scoring each on a blend of
 * distance-to-spot (safety, via otm_pct) and annualized ROC (return). Unlike
 * edge mode this does NOT collapse to one strike per name — the strike is a
 * decision variable the knapsack chooses.
 */
function buildOptimizedCandidates(
  results: CspResult[],
  settings: AllocatorSettings,
  capital: number,
): AllocatorCandidate[] {
  const raw: AllocatorCandidate[] = []
  for (const r of results) {
    for (const s of r.strikes) {
      const collateral = s.strike * 100
      const roc = s.roc_annualized ?? s.annualized_return
      if (s.premium <= 0) continue
      if (s.csp_score < settings.minScore) continue
      if (Math.abs(s.delta) <= 0) continue   // no usable delta — the safety axis can't be trusted
      if (collateral <= 0 || collateral > capital) continue
      raw.push({
        symbol: r.symbol,
        sector: getSector(r.symbol),
        strike: s.strike,
        expiration: r.expiration,
        dte: r.dte,
        premium: s.premium,
        collateral,
        cspScore: s.csp_score,
        roc: s.roc_annualized ?? s.annualized_return,
        otmPct: s.otm_pct,
        absDelta: Math.abs(s.delta),
        yieldEff: yieldEfficiency(s.premium, s.strike, r.price, r.expected_move),
        price: r.price,
        volSupport1: r.vol_support_126_1,
        volSupport2: r.vol_support_126_2,
        volSupport3: r.vol_support_126_3,
        expMoveLower: r.expected_move > 0 ? r.price - r.expected_move : r.price,        happy: computeHappyPrice(r),        value: 0, // filled in below
      })
    }
  }

  // Merit blends safety (assignment probability via |delta|; lower = safer) and
  // return (vol-normalised yield efficiency), both min-max normalised so the
  // slider gives each equal leverage. |delta| is volatility-aware, and dividing
  // ROC by the expected move stops high IV masquerading as free edge.
  const ads = raw.map(c => c.absDelta)
  const minAD = Math.min(...ads)
  const maxAD = Math.max(...ads)
  const adSpan = maxAD - minAD
  const ys = raw.map(c => c.yieldEff)
  const minY = Math.min(...ys)
  const ySpan = Math.max(...ys) - minY
  const w = clamp01(settings.riskTolerance)
  for (const c of raw) {
    const safety = adSpan > 0 ? (maxAD - c.absDelta) / adSpan : 0.5
    const roi = ySpan > 0 ? (c.yieldEff - minY) / ySpan : 0.5
    c.value = (1 - w) * safety + w * roi
  }
  return raw
}

interface KnapsackChoice {
  cand: AllocatorCandidate
  count: number
}

/**
 * Total merit multiplier for holding `k` contracts of one name under concave
 * (diminishing-returns) merit: the j-th contract is worth `d^(j-1)` of the base,
 * so the sum is a geometric series. d = 1 is linear (k). Lower d spreads harder.
 */
function concaveSum(k: number, d: number): number {
  if (d >= 1) return k
  return (1 - Math.pow(d, k)) / (1 - d)
}

/**
 * Multiple-choice bounded knapsack via DP. Maximises total *dollar-weighted*
 * merit (Σ merit × deployed) subject to Σ collateral ≤ capital, ≤ 1 strike per
 * name, and ≤ maxNamePct of capital per name. Weighting merit by dollars (not
 * contract count) removes the cheap-stock bias — it ranks each *dollar* by
 * quality, which is what maximising the capital-weighted average distance/ROC
 * requires. Exact for the objective; capital is discretised to bound the table.
 */
function optimizeKnapsack(
  cands: AllocatorCandidate[],
  capital: number,
  maxNamePct: number,
  diversification: number,
): KnapsackChoice[] {
  if (cands.length === 0) return []

  // Concave-merit decay: 0 diversification = linear (d=1); 1 = strong spread (d=0.4).
  const d = diversification > 0 ? 1 - Math.min(diversification, 1) * 0.6 : 1

  // Discretise capital; scale the unit up for very large budgets to cap work.
  const MAX_CAP_UNITS = 2000
  let unit = 25
  if (Math.floor(capital / unit) > MAX_CAP_UNITS) {
    unit = Math.ceil(capital / MAX_CAP_UNITS / 25) * 25
  }
  const capUnits = Math.floor(capital / unit)
  if (capUnits <= 0) return []

  const nameCapDollars = maxNamePct > 0 && maxNamePct < 1 ? maxNamePct * capital : capital

  const byName = new Map<string, AllocatorCandidate[]>()
  for (const c of cands) {
    const list = byName.get(c.symbol)
    if (list) list.push(c)
    else byName.set(c.symbol, [c])
  }
  const names = [...byName.keys()]

  interface Option { wUnits: number; merit: number; cand: AllocatorCandidate; count: number }

  let prev = new Float64Array(capUnits + 1)
  const rows: Array<{ options: Option[]; pick: number[] }> = []

  for (const name of names) {
    const options: Option[] = []
    for (const cand of byName.get(name)!) {
      const wS = Math.ceil(cand.collateral / unit)
      if (wS <= 0 || wS > capUnits) continue
      const maxByCapital = Math.floor(capUnits / wS)
      const maxByName = Math.floor(nameCapDollars / cand.collateral)
      const maxK = Math.min(maxByCapital, maxByName)
      for (let k = 1; k <= maxK; k++) {
        // Objective weights merit by dollars deployed, with concave returns in count.
        options.push({ wUnits: k * wS, merit: cand.value * cand.collateral * concaveSum(k, d), cand, count: k })
      }
    }
    const next = Float64Array.from(prev)
    const pick = new Array<number>(capUnits + 1).fill(-1) // option index, or -1 = skip name
    for (let c = 0; c <= capUnits; c++) {
      for (let oi = 0; oi < options.length; oi++) {
        const o = options[oi]
        if (o.wUnits <= c) {
          const v = prev[c - o.wUnits] + o.merit
          if (v > next[c]) {
            next[c] = v
            pick[c] = oi
          }
        }
      }
    }
    rows.push({ options, pick })
    prev = next
  }

  // Best capacity (idle cash allowed → search all).
  let bestC = 0
  let bestV = -1
  for (let c = 0; c <= capUnits; c++) {
    if (prev[c] > bestV) { bestV = prev[c]; bestC = c }
  }

  const selected: KnapsackChoice[] = []
  let c = bestC
  for (let i = names.length - 1; i >= 0; i--) {
    const oi = rows[i].pick[c]
    if (oi >= 0) {
      const o = rows[i].options[oi]
      selected.push({ cand: o.cand, count: o.count })
      c -= o.wUnits
    }
  }
  return selected
}

function allocateOptimized(
  results: CspResult[],
  capital: number,
  settings: AllocatorSettings,
  notes: string[],
): AllocationResult {
  const cands = buildOptimizedCandidates(results, settings, capital)
  if (cands.length === 0) {
    notes.push(
      `No contract clears the min score (${settings.minScore}) and fits in $${capital.toLocaleString()} of collateral. ` +
      'Lower the score gate, raise capital, or widen the on-screen results.',
    )
    return emptyResult(capital, notes)
  }

  const selected = optimizeKnapsack(cands, capital, settings.maxNamePct, settings.diversification)
  if (selected.length === 0) {
    const capPct = Math.round(settings.maxNamePct * 100)
    notes.push(
      settings.maxNamePct < 1
        ? `No contract fits within the per-name cap (${capPct}% of capital). Raise the cap or capital.`
        : 'No eligible contract fits in the available capital.',
    )
    return emptyResult(capital, notes)
  }

  const picks = selected
    .map(({ cand, count }) => makePick(cand, count, capital))
    .sort((a, b) => b.deployed - a.deployed)
  const rankedForBaseline = [...cands].sort(betterCandidateSort)
  return summarise(picks, capital, rankedForBaseline, notes)
}
