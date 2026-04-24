import { useMemo, useState } from 'react'
import { CspInput } from './components/CspInput'
import { CspFilterPanel } from './components/CspFilterPanel'
import { CspTable } from './components/CspTable'
import { CcInput } from './components/CcInput'
import { CcTable } from './components/CcTable'
import { CcFilterPanel } from './components/CcFilterPanel'
import { DitmInput } from './components/DitmInput'
import { DitmTable } from './components/DitmTable'
import { DitmFilterPanel } from './components/DitmFilterPanel'
import { MomentumFilterPanel } from './components/MomentumFilterPanel'
import { MomentumTable } from './components/MomentumTable'
import { MomentumInput } from './components/MomentumInput'
import { useCsp } from './hooks/useCsp'
import { useCc } from './hooks/useCc'
import { useDitm } from './hooks/useDitm'
import { useMomentum } from './hooks/useMomentum'
import type { CspFilterState, CspResult } from './types/csp'
import type { CcFilterState, CcResult } from './types/cc'
import type { DitmFilterState, DitmResult } from './types/ditm'
import type { MomentumFilterState, MomentumResult } from './types/momentum'

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
  minDelta: 0.65,
  maxExtrinsicPct: 0,
  smaRatioBullishOnly: false,
  maxSpreadPct: 0,
  excludeEarningsWithinDte: false,
}

const DEFAULT_MOMENTUM_FILTERS: MomentumFilterState = {
  minScore: 0,
  minRvol: 0,
  minRsi: 0,
  maxRsi: 100,
  minRoc21: 0,
  smaRatioBullishOnly: false,
  maxDistFrom52wHigh: 0,
}

function applyMomentumFilters(results: MomentumResult[], filters: MomentumFilterState): MomentumResult[] {
  return results.filter(r => {
    if (filters.minScore > 0 && r.momentum_score < filters.minScore) return false
    if (filters.minRvol > 0 && (r.rvol == null || r.rvol < filters.minRvol)) return false
    if (r.rsi != null && (r.rsi < filters.minRsi || r.rsi > filters.maxRsi)) return false
    if (filters.minRoc21 !== 0 && (r.roc_21 == null || r.roc_21 < filters.minRoc21)) return false
    if (filters.smaRatioBullishOnly && (r.sma_ratio == null || r.sma_ratio <= 1.0)) return false
    if (filters.maxDistFrom52wHigh > 0 && (r.dist_from_52w_high_pct == null || r.dist_from_52w_high_pct < -filters.maxDistFrom52wHigh)) return false
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
    if (best == null) return false
    if (best.delta < filters.minDelta) return false
    if (filters.maxExtrinsicPct > 0 && best.extrinsic_pct > filters.maxExtrinsicPct) return false
    if (filters.smaRatioBullishOnly && r.sma_ratio <= 1.0) return false
    if (filters.maxSpreadPct > 0 && (best.bid_ask_spread_pct == null || best.bid_ask_spread_pct > filters.maxSpreadPct)) return false
    if (filters.excludeEarningsWithinDte && r.earnings_within_dte) return false
    return true
  })
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'csp' | 'cc' | 'ditm' | 'momentum'>('csp')

  // CSP state
  const { results: cspResults, errors: cspErrors, loading: cspLoading, symbolCount: cspSymbolCount, isScanMode: cspIsScanMode, errorMessage: cspErrorMessage, run: runCsp, scan: scanCsp } = useCsp()
  const [cspFilters, setCspFilters] = useState<CspFilterState>(DEFAULT_CSP_FILTERS)
  const filteredCsp = useMemo(() => applyCspFilters(cspResults, cspFilters), [cspResults, cspFilters])

  // CC state
  const { results: ccResults, errors: ccErrors, loading: ccLoading, symbolCount: ccSymbolCount, isScanMode: ccIsScanMode, errorMessage: ccErrorMessage, run: runCc, scan: scanCc } = useCc()
  const [ccFilters, setCcFilters] = useState<CcFilterState>(DEFAULT_CC_FILTERS)
  const filteredCc = useMemo(() => applyCcFilters(ccResults, ccFilters), [ccResults, ccFilters])

  // DITM state
  const { results: ditmResults, errors: ditmErrors, loading: ditmLoading, symbolCount: ditmSymbolCount, isScanMode: ditmIsScanMode, errorMessage: ditmErrorMessage, run: runDitm, scan: scanDitm } = useDitm()
  const [ditmFilters, setDitmFilters] = useState<DitmFilterState>(DEFAULT_DITM_FILTERS)
  const filteredDitm = useMemo(() => applyDitmFilters(ditmResults, ditmFilters), [ditmResults, ditmFilters])

  // Momentum state
  const { results: momResults, errors: momErrors, loading: momLoading, symbolCount: momSymbolCount, isScanMode: momIsScanMode, errorMessage: momErrorMessage, run: runMomentum, scan: scanMomentum } = useMomentum()
  const [momFilters, setMomFilters] = useState<MomentumFilterState>(DEFAULT_MOMENTUM_FILTERS)
  const filteredMom = useMemo(() => applyMomentumFilters(momResults, momFilters), [momResults, momFilters])

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
            className={`tab-btn${activeTab === 'momentum' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('momentum')}
          >
            Momentum — Pre-Breakout
          </button>
        </div>
      </header>

      <main className="app-main">
        {activeTab === 'csp' && (
          <>
            <CspInput
              onScan={(topN, minDTE, maxDTE) => scanCsp(topN, minDTE, maxDTE)}
              onCustom={(symbols, minDTE, maxDTE) => runCsp({ symbols, minDTE, maxDTE })}
              loading={cspLoading}
            />
            {cspResults.length > 0 && (
              <CspFilterPanel filters={cspFilters} onChange={setCspFilters} />
            )}
            {cspLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {cspIsScanMode
                  ? <p>Scanning <strong>75 stocks</strong> in parallel &mdash; est. <strong>~20s</strong></p>
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
              </div>
            )}
            <CspTable data={filteredCsp} />
            {!cspLoading && cspResults.length === 0 && !cspErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>⚡ Scan Now</strong> to automatically find the top CSP opportunities, or switch to Custom Symbols.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'cc' && (
          <>
            <CcInput
              onScan={(topN, minDTE, maxDTE) => scanCc(topN, minDTE, maxDTE)}
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
                  ? <p>Scanning <strong>75 stocks</strong> in parallel &mdash; est. <strong>~20s</strong></p>
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
              </div>
            )}
            <CcTable data={filteredCc} />
            {!ccLoading && ccResults.length === 0 && !ccErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>⚡ Scan Now</strong> to automatically find top Covered Call opportunities, or switch to Custom Symbols.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'ditm' && (
          <>
            <DitmInput
              onScan={(topN, minDTE, maxDTE) => scanDitm(topN, minDTE, maxDTE)}
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
                  ? <p>Scanning <strong>75 stocks</strong> in parallel &mdash; est. <strong>~25s</strong></p>
                  : <p>Fetching <strong>{ditmSymbolCount}</strong> symbol{ditmSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(ditmSymbolCount / 5) * 4}s</strong></p>
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
              </div>
            )}
            <DitmTable data={filteredDitm} />
            {!ditmLoading && ditmResults.length === 0 && !ditmErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>⚡ Scan Now</strong> to find the top DITM Long Call opportunities, or switch to Custom Symbols.</p>
              </div>
            )}
          </>
        )}

        {activeTab === 'momentum' && (
          <>
            <MomentumInput
              onScan={(topN) => scanMomentum(topN)}
              onCustom={(symbols) => runMomentum({ symbols })}
              loading={momLoading}
            />
            {momResults.length > 0 && (
              <MomentumFilterPanel filters={momFilters} onChange={setMomFilters} />
            )}
            {momLoading && (
              <div className="loading-state">
                <div className="spinner" />
                {momIsScanMode
                  ? <p>Scanning <strong>75 stocks</strong> in parallel &mdash; est. <strong>~20s</strong></p>
                  : <p>Fetching <strong>{momSymbolCount}</strong> symbol{momSymbolCount !== 1 ? 's' : ''} in parallel
                      &nbsp;&mdash; est. <strong>~{Math.ceil(momSymbolCount / 5) * 4}s</strong></p>
                }
              </div>
            )}
            {momErrorMessage && (
              <div className="error-banner"><strong>Error:</strong> {momErrorMessage}</div>
            )}
            {momErrors.length > 0 && (
              <div className="error-summary">
                <strong>{momErrors.length} symbol{momErrors.length > 1 ? 's' : ''} failed:</strong>
                <ul>{momErrors.map(e => <li key={e.symbol}><strong>{e.symbol}</strong>: {e.reason}</li>)}</ul>
              </div>
            )}
            {!momLoading && momResults.length > 0 && (
              <div className="results-meta">
                Showing <strong>{filteredMom.length}</strong> of <strong>{momResults.length}</strong> results
                {filteredMom.length < momResults.length && ' (filters active)'}
              </div>
            )}
            <MomentumTable data={filteredMom} />
            {!momLoading && momResults.length === 0 && !momErrorMessage && (
              <div className="empty-state">
                <p>Click <strong>⚡ Scan Now</strong> to automatically find the top momentum breakout candidates.</p>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}

