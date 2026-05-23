import { useMemo, useState } from 'react'
import { CspInput } from './components/CspInput'
import { CspFilterPanel } from './components/CspFilterPanel'
import { CspTable } from './components/CspTable'
import { CcInput } from './components/CcInput'
import { CcTable } from './components/CcTable'
import { CcFilterPanel } from './components/CcFilterPanel'
import { DitmInput } from './components/DitmInput'
import { DitmFilterPanel } from './components/DitmFilterPanel'
import { DitmTable } from './components/DitmTable'
import { EmRankInput } from './components/EmRankInput'
import { EmRankTable } from './components/EmRankTable'
import { SupplyChainView } from './components/SupplyChainView'
import { DcfView } from './components/DcfView'
import { SwingInput } from './components/SwingInput'
import { SwingFilterPanel } from './components/SwingFilterPanel'
import { SwingTable } from './components/SwingTable'
import { NarrativeView } from './components/NarrativeView'
import { useCsp } from './hooks/useCsp'
import { useCc } from './hooks/useCc'
import { useDitm } from './hooks/useDitm'
import { useEmScan } from './hooks/useEmScan'
import { useSwing } from './hooks/useSwing'
import type { CspFilterState, CspResult } from './types/csp'
import type { CcFilterState, CcResult } from './types/cc'
import type { DitmFilterState, DitmResult } from './types/ditm'
import type { SwingFilterState, SwingResult } from './types/swing'
import type { UniverseKey } from './constants/universes'

const DEFAULT_CSP_FILTERS: CspFilterState = {
  smaRatioBullishOnly: false,
  maxSpreadPct: 0,
  excludeEarningsWithinDte: false,
  maxCollateral: 0,
}

const DEFAULT_CC_FILTERS: CcFilterState = {
  smaRatioBullishOnly: false,
  maxSpreadPct: 0,
  excludeEarningsWithinDte: false,
  maxCollateral: 0,
}

const DEFAULT_DITM_FILTERS: DitmFilterState = {
  smaRatioBullishOnly: false,
  maxSpreadPct: 0,
  excludeEarningsWithinDte: false,
  maxCapital: 0,
}

const DEFAULT_SWING_FILTERS: SwingFilterState = {
  setupType: 'all',
  minRR: 0,
  minScore: 50,
  excludeEarningsWarning: false,
  minPrice: 5,
  minAdvM: 5,
}

function applySwingFilters(
  results: SwingResult[],
  filters: SwingFilterState,
): SwingResult[] {
  return results.filter(r => {
    if (filters.setupType !== 'all' && r.setup_type !== filters.setupType) return false
    if (filters.minRR > 0 && r.rr < filters.minRR) return false
    if (filters.minScore > 0 && (r.composite_score ?? 0) < filters.minScore) return false
    if (filters.minPrice > 0 && r.price < filters.minPrice) return false
    if (filters.minAdvM > 0 && (r.adv_usd ?? 0) < filters.minAdvM * 1_000_000) return false
    if (filters.excludeEarningsWarning && r.earnings_warning) return false
    return true
  })
}

function applyCspFilters(results: CspResult[], filters: CspFilterState): CspResult[] {
  return results.filter(r => {
    const best = r.strikes.find(s => s.is_best) ?? r.strikes[0]
    if (filters.smaRatioBullishOnly && r.sma_ratio <= 1.0) return false
    if (filters.maxSpreadPct > 0 && (best == null || best.bid_ask_spread_pct == null || best.bid_ask_spread_pct > filters.maxSpreadPct)) return false
    if (filters.excludeEarningsWithinDte && r.earnings_within_dte) return false
    if (filters.maxCollateral > 0 && best != null && best.strike * 100 > filters.maxCollateral) return false
    return true
  })
}

function applyCcFilters(results: CcResult[], filters: CcFilterState): CcResult[] {
  return results.filter(r => {
    const best = r.strikes.find(s => s.is_best) ?? r.strikes[0]
    if (filters.smaRatioBullishOnly && r.sma_ratio <= 1.0) return false
    if (filters.maxSpreadPct > 0 && (best == null || best.bid_ask_spread_pct == null || best.bid_ask_spread_pct > filters.maxSpreadPct)) return false
    if (filters.excludeEarningsWithinDte && r.earnings_within_dte) return false
    if (filters.maxCollateral > 0 && best != null && best.strike * 100 > filters.maxCollateral) return false
    return true
  })
}

function applyDitmFilters(results: DitmResult[], filters: DitmFilterState): DitmResult[] {
  return results.filter(r => {
    const best = r.strikes.find(s => s.is_best) ?? r.strikes[0]
    if (filters.smaRatioBullishOnly && r.sma_ratio <= 1.0) return false
    if (filters.maxSpreadPct > 0 && (best == null || best.bid_ask_spread_pct == null || best.bid_ask_spread_pct > filters.maxSpreadPct)) return false
    if (filters.excludeEarningsWithinDte && r.earnings_within_dte) return false
    if (filters.maxCapital > 0 && best != null && best.mid * 100 > filters.maxCapital) return false
    return true
  })
}

// Narrative tab is shown by default (Phase 6). Set VITE_NARRATIVE_ENABLED=0 to hide.
const NARRATIVE_ENABLED = import.meta.env.VITE_NARRATIVE_ENABLED !== '0'

/** Format a backend ISO timestamp as a relative "Updated X min ago" badge label
 *  + a severity class so the user can see when the precomputed scan is stale.
 *
 *  Worker cadence (ADR-0024): every 15 min during RTH, every 4 h overnight.
 *  Thresholds reflect "expected" vs "concerning" vs "broken":
 *    <  30 min  → fresh         (.precomputed-badge)
 *    < 240 min  → aging         (.precomputed-badge--warn)
 *    >= 240 min → stale         (.precomputed-badge--stale)
 *  A 24h-old badge in muted green misleads operators into trusting data
 *  that may have already expired the underlying Cosmos TTL.
 */
function _formatPrecomputedAge(isoTimestamp: string): { label: string; className: string } {
  try {
    const updated = new Date(isoTimestamp)
    const ageMin = Math.round((Date.now() - updated.getTime()) / 60_000)
    let className = 'precomputed-badge'
    if (ageMin >= 240) className = 'precomputed-badge precomputed-badge--stale'
    else if (ageMin >= 30) className = 'precomputed-badge precomputed-badge--warn'

    let label: string
    if (ageMin < 1) label = 'Updated just now'
    else if (ageMin === 1) label = 'Updated 1 min ago'
    else if (ageMin < 60) label = `Updated ${ageMin} min ago`
    else {
      const h = Math.floor(ageMin / 60)
      const m = ageMin % 60
      label = m === 0 ? `Updated ${h}h ago` : `Updated ${h}h ${m}m ago`
    }
    if (ageMin >= 240) label = `⚠ ${label} (stale)`
    return { label, className }
  } catch {
    return { label: 'Updated recently', className: 'precomputed-badge' }
  }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'csp' | 'cc' | 'ditm' | 'swing' | 'em-rank' | 'supply' | 'dcf' | 'narrative'>('csp')

  // CSP state
  const { results: cspResults, errors: cspErrors, loading: cspLoading, symbolCount: cspSymbolCount, isScanMode: cspIsScanMode, errorMessage: cspErrorMessage, cachedAt: cspCachedAt, lastUpdatedAt: cspLastUpdatedAt, vixLevel: cspVixLevel, vixPercentile: cspVixPercentile, volRegime: cspVolRegime, run: runCsp, scan: scanCsp } = useCsp()
  const [cspFilters, setCspFilters] = useState<CspFilterState>(DEFAULT_CSP_FILTERS)
  const filteredCsp = useMemo(() => applyCspFilters(cspResults, cspFilters), [cspResults, cspFilters])

  // CC state
  const { results: ccResults, errors: ccErrors, loading: ccLoading, symbolCount: ccSymbolCount, isScanMode: ccIsScanMode, errorMessage: ccErrorMessage, cachedAt: ccCachedAt, lastUpdatedAt: ccLastUpdatedAt, vixLevel: ccVixLevel, vixPercentile: ccVixPercentile, volRegime: ccVolRegime, run: runCc, scan: scanCc } = useCc()
  const [ccFilters, setCcFilters] = useState<CcFilterState>(DEFAULT_CC_FILTERS)
  const filteredCc = useMemo(() => applyCcFilters(ccResults, ccFilters), [ccResults, ccFilters])

  // DITM state
  const { results: ditmResults, errors: ditmErrors, loading: ditmLoading, symbolCount: ditmSymbolCount, isScanMode: ditmIsScanMode, errorMessage: ditmErrorMessage, cachedAt: ditmCachedAt, lastUpdatedAt: ditmLastUpdatedAt, macroPass, vixLevel, vix5dChange, spyAboveSma200, run: runDitm, scan: scanDitm } = useDitm()
  const [ditmFilters, setDitmFilters] = useState<DitmFilterState>(DEFAULT_DITM_FILTERS)
  const filteredDitm = useMemo(() => applyDitmFilters(ditmResults, ditmFilters), [ditmResults, ditmFilters])

  // EM Rank state
  const { results: emResults, errors: emErrors, loading: emLoading, symbolCount: emSymbolCount, isScanMode: emIsScanMode, errorMessage: emErrorMessage, cachedAt: emCachedAt, run: runEm, scan: scanEm } = useEmScan()

  // Swing state
  const { results: swingResults, regime: swingRegime, loading: swingLoading, isScanMode: swingIsScanMode, gatesBypassed: swingGatesBypassed, errorMessage: swingErrorMessage, cachedAt: swingCachedAt, lastUpdatedAt: swingLastUpdatedAt, scan: scanSwing, run: runSwing } = useSwing()
  const [swingFilters, setSwingFilters] = useState<SwingFilterState>(DEFAULT_SWING_FILTERS)
  const filteredSwing = useMemo(() => applySwingFilters(swingResults, swingFilters), [swingResults, swingFilters])

  return (
    <div className="app">
      <header className="app-header">
        <h1>Options Screener</h1>
        <div className="tab-bar">
          <button
            className={`tab-btn${activeTab === 'csp' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('csp')}
          >
            CSP — Cash Secured Put
          </button>
          <button
            className={`tab-btn${activeTab === 'cc' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('cc')}
          >
            CC — Covered Call
          </button>
          <button
            className={`tab-btn${activeTab === 'ditm' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('ditm')}
          >
            DITM — Long Call
          </button>
          <button
            className={`tab-btn${activeTab === 'em-rank' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('em-rank')}
          >
            EM Rank
          </button>
          <button
            className={`tab-btn${activeTab === 'swing' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('swing')}
          >
            Swing
          </button>
          <button
            className={`tab-btn${activeTab === 'supply' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('supply')}
          >
            Supply Chain
          </button>
          {NARRATIVE_ENABLED && (
            <button
              className={`tab-btn${activeTab === 'narrative' ? ' tab-btn-active' : ''}`}
              onClick={() => setActiveTab('narrative')}
            >
              Narrative
            </button>
          )}
          {/* DCF tab hidden — verdict calibration in progress.
          <button
            className={`tab-btn${activeTab === 'dcf' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('dcf')}
          >
            DCF Valuation
          </button>
          */}
        </div>
      </header>

      <main className="app-main">
        {activeTab === 'csp' && (
          <>
            <CspInput
              onScan={(topN, minDTE, maxDTE, universe, maxCapital) => scanCsp(topN, minDTE, maxDTE, universe, maxCapital)}
              onCustom={(symbols, minDTE, maxDTE, maxCapital) => runCsp({ symbols, minDTE, maxDTE, ...(maxCapital !== undefined && { maxCapital }) })}
              loading={cspLoading}
            />
            {cspResults.length > 0 && (
              <CspFilterPanel filters={cspFilters} onChange={setCspFilters} />
            )}
            {cspLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {cspIsScanMode
                  ? <p>Loading precomputed results&hellip;</p>
                  : <p>Fetching <strong>{cspSymbolCount}</strong> symbol{cspSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(cspSymbolCount / 5) * 4}s</strong></p>
                }
              </div>
            )}
            {cspErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {cspErrorMessage}</div>
            )}
            {cspErrors.length > 0 && (
              <div className="error-summary">
                <strong>{cspErrors.length} symbol{cspErrors.length > 1 ? 's' : ''} failed:</strong>
                <ul>{cspErrors.map(e => <li key={e.symbol}><strong>{e.symbol}</strong>: {e.reason}</li>)}</ul>
              </div>
            )}
            {!cspLoading && cspResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{filteredCsp.length}</strong> of <strong>{cspResults.length}</strong> results
                {filteredCsp.length < cspResults.length && ' (filters active)'}
                {cspIsScanMode && cspLastUpdatedAt && (() => {
                  const { label, className } = _formatPrecomputedAge(cspLastUpdatedAt)
                  return <span className={className}> · {label}</span>
                })()}
                {!cspIsScanMode && cspCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - cspCachedAt) / 60000) < 1 ? '< 1' : Math.round((Date.now() - cspCachedAt) / 60000)} min ago</span>
                )}
                {cspVixLevel != null && (
                  <span style={{ marginLeft: 8, color: '#64748b', fontSize: 12 }}>
                    {' '}· <strong>VIX</strong> {cspVixLevel.toFixed(1)} ({cspVixPercentile?.toFixed(0)}p · {cspVolRegime})
                  </span>
                )}
              </div>
            )}
            <CspTable data={filteredCsp} />
            {!cspLoading && cspResults.length === 0 && !cspErrorMessage && (
              <div className="empty-state">
                <p>Enter symbols and click <strong>🚀 Run</strong> to screen CSP opportunities, or switch to <strong>⚡ Auto Scan</strong> for the curated universe.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'cc' && (
          <>
            <CcInput
              onScan={(topN, minDTE, maxDTE, universe) => scanCc(topN, minDTE, maxDTE, universe)}
              onCustom={(symbols, minDTE, maxDTE) => runCc({ symbols, minDTE, maxDTE })}
              loading={ccLoading}
            />
            {ccResults.length > 0 && (
              <CcFilterPanel filters={ccFilters} onChange={setCcFilters} />
            )}
            {ccLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {ccIsScanMode
                  ? <p>Loading precomputed results&hellip;</p>
                  : <p>Fetching <strong>{ccSymbolCount}</strong> symbol{ccSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(ccSymbolCount / 5) * 4}s</strong></p>
                }
              </div>
            )}
            {ccErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {ccErrorMessage}</div>
            )}
            {ccErrors.length > 0 && (
              <div className="error-summary">
                <strong>{ccErrors.length} symbol{ccErrors.length > 1 ? 's' : ''} failed:</strong>
                <ul>{ccErrors.map(e => <li key={e.symbol}><strong>{e.symbol}</strong>: {e.reason}</li>)}</ul>
              </div>
            )}
            {!ccLoading && ccResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{filteredCc.length}</strong> of <strong>{ccResults.length}</strong> results
                {filteredCc.length < ccResults.length && ' (filters active)'}
                {ccIsScanMode && ccLastUpdatedAt && (() => {
                  const { label, className } = _formatPrecomputedAge(ccLastUpdatedAt)
                  return <span className={className}> · {label}</span>
                })()}
                {!ccIsScanMode && ccCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - ccCachedAt) / 60000) < 1 ? '< 1' : Math.round((Date.now() - ccCachedAt) / 60000)} min ago</span>
                )}
                {ccVixLevel != null && (
                  <span style={{ marginLeft: 8, color: '#64748b', fontSize: 12 }}>
                    {' '}· <strong>VIX</strong> {ccVixLevel.toFixed(1)} ({ccVixPercentile?.toFixed(0)}p · {ccVolRegime})
                  </span>
                )}
              </div>
            )}
            <CcTable data={filteredCc} />
            {!ccLoading && ccResults.length === 0 && !ccErrorMessage && (
              <div className="empty-state">
                <p>Enter symbols and click <strong>🚀 Run</strong> to screen Covered Call opportunities, or switch to <strong>⚡ Auto Scan</strong> for the curated universe.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'ditm' && (
          <>
            <DitmInput
              onScan={(topN, minDTE, maxDTE, universe) => scanDitm(topN, minDTE, maxDTE, universe)}
              onCustom={(symbols, minDTE, maxDTE) => runDitm({ symbols, minDTE, maxDTE })}
              loading={ditmLoading}
            />
            {ditmResults.length > 0 && (
              <DitmFilterPanel filters={ditmFilters} onChange={setDitmFilters} />
            )}
            {ditmLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {ditmIsScanMode
                  ? <p>Loading precomputed results&hellip;</p>
                  : <p>Fetching <strong>{ditmSymbolCount}</strong> symbol{ditmSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(ditmSymbolCount / 5) * 5}s</strong></p>
                }
              </div>
            )}
            {ditmErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {ditmErrorMessage}</div>
            )}
            {ditmErrors.length > 0 && (
              <div className="error-summary">
                <strong>{ditmErrors.length} symbol{ditmErrors.length > 1 ? 's' : ''} failed:</strong>
                <ul>{ditmErrors.map(e => <li key={e.symbol}><strong>{e.symbol}</strong>: {e.reason}</li>)}</ul>
              </div>
            )}
            {!ditmLoading && ditmResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{filteredDitm.length}</strong> of <strong>{ditmResults.length}</strong> results
                {filteredDitm.length < ditmResults.length && ' (filters active)'}
                {ditmIsScanMode && ditmLastUpdatedAt && (() => {
                  const { label, className } = _formatPrecomputedAge(ditmLastUpdatedAt)
                  return <span className={className}> · {label}</span>
                })()}
                {!ditmIsScanMode && ditmCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - ditmCachedAt) / 60000) < 1 ? '< 1' : Math.round((Date.now() - ditmCachedAt) / 60000)} min ago</span>
                )}
              </div>
            )}
            <DitmTable data={filteredDitm} macroPass={macroPass} vixLevel={vixLevel} vix5dChange={vix5dChange} spyAboveSma200={spyAboveSma200} />
            {!ditmLoading && ditmResults.length === 0 && !ditmErrorMessage && (
              <div className="empty-state">
                <p>Enter symbols and click <strong>🚀 Run</strong> to screen DITM Long Call opportunities, or switch to <strong>⚡ Auto Scan</strong> for the curated universe.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'em-rank' && (
          <>
            <EmRankInput
              onScan={(topN, minDTE, maxDTE, universe, maxCapital) => scanEm(topN, minDTE, maxDTE, universe as UniverseKey, maxCapital)}
              onCustom={(symbols, minDTE, maxDTE, maxCapital) => runEm({ symbols, minDTE, maxDTE, ...(maxCapital !== undefined && { maxCapital }) })}
              loading={emLoading}
            />
            {emLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {emIsScanMode
                  ? <p>Scanning <strong>selected universe</strong> in parallel &mdash; est. <strong>~25s</strong></p>
                  : <p>Fetching <strong>{emSymbolCount}</strong> symbol{emSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(emSymbolCount / 5) * 4}s</strong></p>
                }
              </div>
            )}
            {emErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {emErrorMessage}</div>
            )}
            {emErrors.length > 0 && (
              <div className="error-summary">
                <strong>{emErrors.length} symbol{emErrors.length > 1 ? 's' : ''} failed:</strong>
                <ul>{emErrors.map(e => <li key={e.symbol}><strong>{e.symbol}</strong>: {e.reason}</li>)}</ul>
              </div>
            )}
            {!emLoading && emResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{emResults.length}</strong> result{emResults.length !== 1 ? 's' : ''}
                {emCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - emCachedAt) / 60000) < 1 ? '< 1' : Math.round((Date.now() - emCachedAt) / 60000)} min ago</span>
                )}
              </div>
            )}
            <EmRankTable data={emResults} />
            {!emLoading && emResults.length === 0 && !emErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>⚡ Scan Now</strong> to rank the universe by ROC at the 1σ EM strike, or enter custom symbols.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'supply' && <SupplyChainView />}
        {activeTab === 'dcf' && <DcfView />}
        {activeTab === 'narrative' && NARRATIVE_ENABLED && <NarrativeView />}

        {activeTab === 'swing' && (
          <>
            <SwingInput
              onScan={(universe) => scanSwing(universe)}
              onCustom={(symbols, bypassGates) => runSwing(symbols, bypassGates)}
              loading={swingLoading}
            />
            {swingResults.length > 0 && (
              <SwingFilterPanel
                filters={swingFilters}
                onChange={setSwingFilters}
              />
            )}
            {swingLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {swingIsScanMode
                  ? <p>Scanning <strong>swing-eligible universe</strong> &mdash; est. <strong>~45s</strong> (includes AI commentary for top 3)</p>
                  : <p>Analyzing custom symbols &mdash; est. <strong>~20s</strong></p>
                }
              </div>
            )}
            {swingErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {swingErrorMessage}</div>
            )}
            {!swingLoading && swingResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{filteredSwing.length}</strong> of <strong>{swingResults.length}</strong> qualified setup{swingResults.length !== 1 ? 's' : ''}
                {filteredSwing.length < swingResults.length && ' (filters active)'}
                {swingCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - swingCachedAt) / 60000) < 1 ? '< 1' : Math.round((Date.now() - swingCachedAt) / 60000)} min ago</span>
                )}
                {swingIsScanMode && swingLastUpdatedAt && (() => {
                  const { label, className } = _formatPrecomputedAge(swingLastUpdatedAt)
                  return <span className={className}> · {label}</span>
                })()}
              </div>
            )}
            {!swingLoading && swingRegime && (
              <div
                className="regime-banner"
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 12,
                  padding: '10px 14px',
                  margin: '8px 0 10px',
                  background:
                    swingRegime.regime_label === 'risk_on'  ? '#0f1f14' :
                    swingRegime.regime_label === 'risk_off' ? '#2a0f0f' :
                                                              '#1a1d2a',
                  border:
                    '1px solid ' + (
                      swingRegime.regime_label === 'risk_on'  ? '#16a34a' :
                      swingRegime.regime_label === 'risk_off' ? '#dc2626' :
                                                                '#475569'
                    ),
                  borderRadius: 6,
                  fontSize: 12,
                  color: '#cbd5e1',
                }}
              >
                <span style={{
                  padding: '2px 10px',
                  borderRadius: 4,
                  fontWeight: 700,
                  letterSpacing: 0.5,
                  textTransform: 'uppercase',
                  background:
                    swingRegime.regime_label === 'risk_on'  ? '#16a34a' :
                    swingRegime.regime_label === 'risk_off' ? '#dc2626' :
                                                              '#475569',
                  color: '#fff',
                }}>
                  {swingRegime.regime_label.replace('_', '-')}
                </span>
                <span><strong>Score:</strong> {swingRegime.risk_on_score.toFixed(0)}/100</span>
                <span><strong>R:R gate:</strong> {swingRegime.rr_gate.toFixed(1)}</span>
                <span><strong>Multiplier:</strong> ×{swingRegime.multiplier.toFixed(2)}</span>
                <span><strong>SPY:</strong> {swingRegime.index_trend}</span>
                <span><strong>VIX:</strong> {swingRegime.vix.toFixed(1)} ({swingRegime.vix_percentile.toFixed(0)}p, {swingRegime.vol_regime})</span>
                <span><strong>Breadth:</strong> {swingRegime.breadth_pct.toFixed(0)}% &gt; EMA50</span>
                <span><strong>IWM/SPY:</strong> {swingRegime.risk_appetite.toFixed(3)}</span>
                {swingRegime.disable_setups.length > 0 && (
                  <span style={{ color: '#fbbf24' }}>
                    <strong>Disabled:</strong> {swingRegime.disable_setups.join(', ')}
                  </span>
                )}
                {swingRegime.degraded && (
                  <span style={{ color: '#fbbf24' }} title={swingRegime.drivers.join(' · ')}>
                    ⚠ degraded data — using neutral defaults
                  </span>
                )}
              </div>
            )}
            <SwingTable data={filteredSwing} gatesBypassed={swingGatesBypassed} />
            {!swingLoading && swingResults.length === 0 && !swingErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>🚀 Run</strong> to scan the swing-eligible universe for Breakout, Momentum, Reversion, and Retest setups. Hard gates: R:R ≥ 2.5, setup score ≥ 40. Top 3 receive AI commentary.</p>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}
