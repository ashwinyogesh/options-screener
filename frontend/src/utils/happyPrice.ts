/**
 * Happy Price to Own (HPO) — v2, multi-family confluence (ADR-0034 follow-up).
 *
 * An objective "price I'd be happy to own the shares at," derived from the
 * support anchors a trader actually uses. v2 groups anchors into INDEPENDENT
 * families and measures agreement *across* families — three unrelated signals
 * pointing at the same level is real confluence; three flavours of the same
 * volume profile is not.
 *
 * Families (each contributes its real price level(s); structure counts once):
 *   - structure     : volume-support nodes (a single volume-profile family)
 *   - options       : put wall (largest scored put OI below spot)
 *   - trend         : 200-day moving average
 *   - institutional : anchored VWAP from the 52-week low (avg buyer cost basis)
 *   - volatility    : lower expected-move floor
 *
 * Output:
 *   - point   = median of the consensus (agreeing) anchors,
 *   - zone    = [min, max] of the consensus anchors,
 *   - spread% = zone width as % of the point,
 *   - confidence = how many INDEPENDENT families agree (≥3 high, 2 medium).
 *
 * Two guardrails:
 *   1. Downtrend silence — support anchors trail a falling price, so HPO goes
 *      quiet in a confirmed downtrend rather than print a comforting knife.
 *   2. Near-spot filter — an anchor within ~5% of spot is not a "discount to
 *      own"; only anchors at least that far below spot qualify.
 *
 * v2 is pure client-side from CspResult fields (backend now supplies sma_200,
 * avwap_52w_low and put_wall). A relative-value (valuation) leg — the only path
 * to a happy price *above* spot — remains deferred (needs fundamentals).
 */
import type { CspResult } from '../types/csp'

export type HappyConfidence = 'high' | 'medium' | 'low'

export type HappyFamily =
  | 'structure'
  | 'options'
  | 'trend'
  | 'institutional'
  | 'volatility'

export interface HappyAnchor {
  family: HappyFamily
  label: string
  price: number
  /** True when this family sits inside the consensus cluster. */
  inConsensus: boolean
}

export interface HappyPrice {
  /** Central happy price (median of consensus reps); null when suppressed. */
  point: number | null
  zoneLo: number | null
  zoneHi: number | null
  /** Zone width as % of the point — small = the agreeing families cluster tightly. */
  spreadPct: number | null
  confidence: HappyConfidence | null
  /** True when suppressed because the stock is in a confirmed downtrend. */
  downtrend: boolean
  /** Independent families in the consensus cluster (drives confidence). */
  familyCount: number
  /** Every qualifying anchor, tagged by family, ascending (for the tooltip). */
  anchors: HappyAnchor[]
}

/** An anchor must sit at least this far below spot to count as a discount to own. */
const NEAR_SPOT_PCT = 0.05
/** Families whose reps fall within ±this band of a center are treated as agreeing. */
const AGREE_BAND_PCT = 0.06

function median(sortedAsc: number[]): number {
  const n = sortedAsc.length
  const mid = Math.floor(n / 2)
  return n % 2 ? sortedAsc[mid] : (sortedAsc[mid - 1] + sortedAsc[mid]) / 2
}

const SUPPRESSED: HappyPrice = {
  point: null, zoneLo: null, zoneHi: null, spreadPct: null,
  confidence: null, downtrend: false, familyCount: 0, anchors: [],
}

function distinctFamilies(anchors: HappyAnchor[]): number {
  return new Set(anchors.map((a) => a.family)).size
}

/**
 * The densest cluster of anchors under a ±AGREE_BAND_PCT tolerance, scored by the
 * number of INDEPENDENT families it contains (not raw anchor count — several nodes
 * of one volume profile are still a single vote). Tries each anchor as the center
 * (mode-seeking), preferring more families, then a tighter spread, then a lower
 * center (the more conservative / deeper discount).
 */
function densestCluster(anchors: HappyAnchor[]): HappyAnchor[] {
  let best: HappyAnchor[] = []
  let bestFamilies = 0
  let bestSpread = Infinity
  let bestCenter = Infinity
  for (const center of anchors) {
    const lo = center.price * (1 - AGREE_BAND_PCT)
    const hi = center.price * (1 + AGREE_BAND_PCT)
    const members = anchors.filter((a) => a.price >= lo && a.price <= hi)
    const prices = members.map((m) => m.price)
    const spread = Math.max(...prices) - Math.min(...prices)
    const fams = distinctFamilies(members)
    const better =
      fams > bestFamilies ||
      (fams === bestFamilies && spread < bestSpread) ||
      (fams === bestFamilies && spread === bestSpread && center.price < bestCenter)
    if (better) {
      best = members
      bestFamilies = fams
      bestSpread = spread
      bestCenter = center.price
    }
  }
  return best
}

function sortAnchors(families: HappyAnchor[]): HappyAnchor[] {
  return [...families].sort((a, b) => a.price - b.price)
}

export function computeHappyPrice(r: CspResult): HappyPrice {
  // Guardrail 1 — downtrend silence: SMA50 < SMA200, below the 20-day mean, weak momentum.
  const downtrend = r.sma_ratio < 1 && r.price < r.bb_middle && r.rsi < 45
  if (downtrend) return { ...SUPPRESSED, downtrend: true }

  const spot = r.price
  if (!(spot > 0)) return SUPPRESSED

  // Guardrail 2 — near-spot filter: keep only anchors ≥ NEAR_SPOT_PCT below spot.
  const ceiling = spot * (1 - NEAR_SPOT_PCT)
  const keep = (v: number | null | undefined): v is number =>
    v != null && v > 0 && v <= ceiling

  // Each qualifying level is a real anchor tagged by its family. Volume-support
  // nodes keep their actual price (no synthetic median) — several nodes are still
  // one independent family, which the cluster scorer counts once.
  const anchors: HappyAnchor[] = []

  for (const v of [r.vol_support_126_1, r.vol_support_126_2, r.vol_support_126_3]) {
    if (keep(v)) anchors.push({ family: 'structure', label: 'Vol support', price: v, inConsensus: false })
  }
  if (keep(r.put_wall)) {
    anchors.push({ family: 'options', label: 'Put wall', price: r.put_wall, inConsensus: false })
  }
  if (keep(r.sma_200)) {
    anchors.push({ family: 'trend', label: '200-DMA', price: r.sma_200, inConsensus: false })
  }
  if (keep(r.avwap_52w_low)) {
    anchors.push({ family: 'institutional', label: 'AVWAP 52w-low', price: r.avwap_52w_low, inConsensus: false })
  }
  if (r.expected_move > 0) {
    const emFloor = spot - r.expected_move
    if (keep(emFloor)) {
      anchors.push({ family: 'volatility', label: 'EM floor', price: emFloor, inConsensus: false })
    }
  }

  // Need at least two independent families for confluence to mean anything.
  if (distinctFamilies(anchors) < 2) return { ...SUPPRESSED, anchors: sortAnchors(anchors) }

  const cluster = densestCluster(anchors)
  const familyCount = distinctFamilies(cluster)
  if (familyCount < 2) return { ...SUPPRESSED, anchors: sortAnchors(anchors) }

  const inCluster = new Set(cluster)
  for (const a of anchors) a.inConsensus = inCluster.has(a)

  const prices = cluster.map((a) => a.price).sort((a, b) => a - b)
  const zoneLo = prices[0]
  const zoneHi = prices[prices.length - 1]
  const point = median(prices)
  const spreadPct = point > 0 ? ((zoneHi - zoneLo) / point) * 100 : null
  const confidence: HappyConfidence = familyCount >= 3 ? 'high' : 'medium'

  return {
    point, zoneLo, zoneHi, spreadPct, confidence,
    downtrend: false, familyCount, anchors: sortAnchors(anchors),
  }
}

export type HappyBadge = 'below' | 'inside' | 'above' | 'none'

/** Where a break-even price sits relative to the happy zone. */
export function happyBadge(hp: HappyPrice, breakEven: number): HappyBadge {
  if (hp.zoneLo == null || hp.zoneHi == null) return 'none'
  if (breakEven <= hp.zoneLo) return 'below'   // below the whole support zone — safest
  if (breakEven <= hp.zoneHi) return 'inside'  // inside the support zone
  return 'above'                                // above support — owning above where buyers defended
}
