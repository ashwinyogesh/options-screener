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
import { SupplyChainView } from './components/SupplyChainView'
import { DcfView } from './components/DcfView'
import { useCsp } from './hooks/useCsp'
import { useCc } from './hooks/useCc'
import { useDitm } from './hooks/useDitm'
import type { CspFilterState, CspResult } from './types/csp'
import type { CcFilterState, CcResult } from './types/cc'
import type { DitmFilterState, DitmResult } from './types/ditm'

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

export default function App() {
  const [activeTab, setActiveTab] = useState<'csp' | 'cc' | 'ditm' | 'supply' | 'dcf'>('csp')

  // CSP state
  const { results: cspResults, errors: cspErrors, loading: cspLoading, symbolCount: cspSymbolCount, isScanMode: cspIsScanMode, errorMessage: cspErrorMessage, cachedAt: cspCachedAt, run: runCsp, scan: scanCsp } = useCsp()
  const [cspFilters, setCspFilters] = useState<CspFilterState>(DEFAULT_CSP_FILTERS)
  const filteredCsp = useMemo(() => applyCspFilters(cspResults, cspFilters), [cspResults, cspFilters])

  // CC state
  const { results: ccResults, errors: ccErrors, loading: ccLoading, symbolCount: ccSymbolCount, isScanMode: ccIsScanMode, errorMessage: ccErrorMessage, cachedAt: ccCachedAt, run: runCc, scan: scanCc } = useCc()
  const [ccFilters, setCcFilters] = useState<CcFilterState>(DEFAULT_CC_FILTERS)
  const filteredCc = useMemo(() => applyCcFilters(ccResults, ccFilters), [ccResults, ccFilters])

  // DITM state
  const { results: ditmResults, errors: ditmErrors, loading: ditmLoading, symbolCount: ditmSymbolCount, isScanMode: ditmIsScanMode, errorMessage: ditmErrorMessage, cachedAt: ditmCachedAt, macroPass, vixLevel, vix5dChange, spyAboveSma200, run: runDitm, scan: scanDitm } = useDitm()
  const [ditmFilters, setDitmFilters] = useState<DitmFilterState>(DEFAULT_DITM_FILTERS)
  const filteredDitm = useMemo(() => applyDitmFilters(ditmResults, ditmFilters), [ditmResults, ditmFilters])

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
            className={`tab-btn${activeTab === 'supply' ? ' tab-btn-active' : ''}`}
            onClick={() => setActiveTab('supply')}
          >
            Supply Chain
          </button>
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
              onScan={(topN, minDTE, maxDTE, universe) => scanCsp(topN, minDTE, maxDTE, universe)}
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
                  ? <p>Scanning <strong>selected universe</strong> in parallel &mdash; est. <strong>~25s</strong></p>
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
                {cspCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - cspCachedAt) / 60000)} min ago</span>
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
                  ? <p>Scanning <strong>selected universe</strong> in parallel &mdash; est. <strong>~25s</strong></p>
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
                {ccCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - ccCachedAt) / 60000)} min ago</span>
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
                  ? <p>Scanning <strong>selected universe</strong> in parallel &mdash; est. <strong>~30s</strong></p>
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
                {ditmCachedAt !== null && (
                  <span className="cache-notice"> · cached {Math.round((Date.now() - ditmCachedAt) / 60000)} min ago</span>
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

        {activeTab === 'supply' && <SupplyChainView />}
        {activeTab === 'dcf' && <DcfView />}
      </main>
    </div>
  )
}
