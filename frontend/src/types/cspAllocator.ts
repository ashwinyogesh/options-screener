/**
 * Types for the CSP capital allocator (ADR-0034). Given a capital budget and
 * the CSP contracts currently on screen, the allocator proposes an integer
 * basket of contracts that maximises risk-adjusted edge under concentration
 * caps. Pure client-side computation — see utils/cspAllocator.ts.
 */
import type { HappyPrice } from '../utils/happyPrice'

export type AllocatorMode = 'edge' | 'optimized'

export interface AllocatorSettings {
  /** Which selection strategy to run. */
  mode: AllocatorMode
  /** 0 = Conservative, 1 = Aggressive. In 'edge' mode blends csp_score vs ROC; in 'optimized' mode blends distance-to-spot vs ROC. */
  riskTolerance: number
  /** Minimum composite csp_score a contract must clear. */
  minScore: number
  /** Optimized mode: max fraction of capital in any single name (0–1; ≥1 = no cap). */
  maxNamePct: number
  /**
   * Optimized mode: concave-merit strength (0–1). 0 = linear (off); higher makes
   * each additional contract of the same name worth less, so the book spreads in
   * proportion to the quality gap between names. A soft nudge; the cap is the hard cap.
   */
  diversification: number
}

/** A single eligible contract flattened out of the CSP results. */
export interface AllocatorCandidate {
  symbol: string
  sector: string
  strike: number
  expiration: string
  dte: number
  premium: number          // per-share credit ($)
  collateral: number       // strike × 100 ($ tied up per contract)
  cspScore: number         // 0–100 composite
  roc: number              // annualized ROC (%) — roc_annualized ?? annualized_return
  otmPct: number           // % below spot
  absDelta: number         // |put delta| ≈ assignment probability (lower = safer)
  yieldEff: number         // ROC per 1% of the stock's expected move (vol-normalised yield)
  price: number            // current spot
  volSupport1: number | null
  volSupport2: number | null
  volSupport3: number | null
  expMoveLower: number     // spot − expected_move (lower bound of the 1σ range)
  happy: HappyPrice        // happy-price-to-own confluence
  value: number            // blended objective used for ranking (0–1)
}

/** One line item in the proposed basket. */
export interface AllocationPick {
  symbol: string
  sector: string
  strike: number
  expiration: string
  dte: number
  contracts: number
  premiumPerContract: number   // premium × 100
  collateralPerContract: number
  deployed: number             // contracts × collateral
  credit: number               // contracts × premium × 100
  cspScore: number
  roc: number
  otmPct: number               // % below spot (distance-to-spot safety proxy)
  absDelta: number             // |put delta| ≈ assignment probability (lower = safer)
  yieldEff: number             // ROC per 1% of the stock's expected move (vol-normalised yield)
  price: number                // current spot
  volSupport1: number | null
  volSupport2: number | null
  volSupport3: number | null
  expMoveLower: number         // spot − expected_move (lower bound of the 1σ range)
  happy: HappyPrice            // happy-price-to-own confluence
  capitalPct: number           // deployed / capital
}

/** A concentrated single-name baseline for the "is diversifying worth it?" comparison. */
export interface SingleContractBaseline {
  symbol: string
  strike: number
  contracts: number
  deployed: number
  credit: number
  cspScore: number
  roc: number
  utilizationPct: number
}

export interface AllocationResult {
  picks: AllocationPick[]
  capital: number
  totalDeployed: number
  idleCash: number
  utilizationPct: number
  numNames: number
  numContracts: number
  totalCredit: number
  /** Capital-weighted mean csp_score across deployed collateral. */
  weightedScore: number
  /** Capital-weighted annualized ROC on deployed collateral (%). */
  weightedRocOnDeployed: number
  /** Capital-weighted mean |put delta| across deployed collateral (assignment-prob proxy). */
  weightedDelta: number
  /** Capital-weighted mean vol-normalised yield (ROC per 1% expected move). */
  weightedYieldEff: number
  /** Effective annualized ROC on the full budget, incl. idle-cash drag (%). */
  rocOnCapital: number
  /** Largest single-name share of capital (0–1). */
  largestNamePct: number
  sectorBreakdown: Array<{ sector: string; deployed: number; pct: number }>
  baseline: SingleContractBaseline | null
  /** Human-readable reasons the allocator could not fully deploy, if any. */
  notes: string[]
}
