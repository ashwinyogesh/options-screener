import { useCallback, useEffect, useMemo, useState } from 'react'
import { AIAssistPanel } from './AIAssistPanel'
import { DataCardPanel } from './DataCardPanel'
import { FinanceTerm } from './FinanceTerm'
import { useDdCoach } from '../../hooks/useDdCoach'
import type {
  Answers,
  CompStructure,
  DataCard,
  DDEntry,
  FilingLinks,
  FlagAcknowledgment,
  GuidedValuationInput,
  GuidedValuationResult,
  GuidedValuationSave,
  InsiderActivity,
  LeadershipCheck,
  PathResult,
  PathToTarget,
  Realism,
  StomachAnswer,
  UserCall,
} from '../../types/ddCoach'

// ---------------------------------------------------------------------------
// Screen registry — labels are user-facing copy (no finance jargon).
// ---------------------------------------------------------------------------

const SCREENS = [
  'The Business',
  'What They Sell',
  'The Market',
  'The Moat',
  'Leadership',
  'Path to Target',
  'The Risks',
  'Why Now',
  'Bear Case',
  'Fair Price',       // V3 — user-driven guided valuation
  'Decision & Plan',
] as const

type ScreenIdx = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pctFmt(decimal: number | null | undefined, digits = 0): string {
  if (decimal == null) return '—'
  return `${(decimal * 100).toFixed(digits)}%`
}

function multFmt(m: number | null | undefined): string {
  if (m == null) return '—'
  return `${m.toFixed(1)}×`
}

const REALISM_LABEL: Record<Realism, string> = {
  easy: 'Easy — already in line with history',
  plausible: 'Plausible — within peer norms',
  stretch: 'Stretch — would be unusual',
  unrealistic: 'Unrealistic — rarely happens',
}

function deriveFairEps(card: DataCard, pathResult: PathToTarget | null): number | null {
  if (pathResult?.cash_per_share != null && pathResult.cash_per_share > 0) {
    return pathResult.cash_per_share
  }
  if (
    card.fcf_ttm != null
    && card.market_cap != null
    && card.spot_price != null
    && card.market_cap > 0
  ) {
    return (card.fcf_ttm * card.spot_price) / card.market_cap
  }
  return null
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function DdCoachView() {
  const coach = useDdCoach()

  const [ticker, setTicker] = useState('')
  const [activeTicker, setActiveTicker] = useState<string | null>(null)
  const [card, setCard] = useState<DataCard | null>(null)
  const [entry, setEntry] = useState<DDEntry | null>(null)
  const [filings, setFilings] = useState<FilingLinks | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [screenIdx, setScreenIdx] = useState<ScreenIdx>(0)
  const [completed, setCompleted] = useState(false)

  // Wizard form state
  const [answers, setAnswers] = useState<Answers>({})
  const [userCall, setUserCall] = useState<UserCall | null>(null)
  const [plannedDollars, setPlannedDollars] = useState<number>(0)
  const [stomach, setStomach] = useState<StomachAnswer | null>(null)
  const [finalDollars, setFinalDollars] = useState<number>(0)

  // V2 plan-pre-commit state
  const [portfolioPct, setPortfolioPct] = useState<number>(0)
  const [sellTarget, setSellTarget] = useState<number>(0)
  const [addMorePrice, setAddMorePrice] = useState<number>(0)
  const [bailOutTrigger, setBailOutTrigger] = useState<string>('')
  const [commitmentAck, setCommitmentAck] = useState<boolean>(false)

  // Path-to-Target state (Screen 6)
  const [targetPrice, setTargetPrice] = useState<number>(0)
  const [pathResult, setPathResult] = useState<PathToTarget | null>(null)
  const [pathError, setPathError] = useState<string | null>(null)

  // V3 guided valuation — Fair Price screen (Screen 9)
  const [growthBear, setGrowthBear] = useState<number>(0)
  const [growthBase, setGrowthBase] = useState<number>(0)
  const [growthBull, setGrowthBull] = useState<number>(0)
  const [peBear, setPeBear] = useState<number>(0)
  const [peBase, setPeBase] = useState<number>(0)
  const [peBull, setPeBull] = useState<number>(0)
  const [requiredReturn, setRequiredReturn] = useState<number>(0.12)
  const [requiredMos, setRequiredMos] = useState<number>(0.25)
  const [guidedValResult, setGuidedValResult] = useState<GuidedValuationResult | null>(null)
  const [guidedValError, setGuidedValError] = useState<string | null>(null)
  const [guidedSeeded, setGuidedSeeded] = useState<boolean>(false)

  // ---- start a new entry ----
  const startEntry = useCallback(async () => {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    setError(null)
    setCompleted(false)
    setEntry(null); setCard(null); setFilings(null)
    setAnswers({}); setUserCall(null)
    setPlannedDollars(0); setStomach(null); setFinalDollars(0)
    setPortfolioPct(0); setSellTarget(0); setAddMorePrice(0)
    setBailOutTrigger(''); setCommitmentAck(false)
    setTargetPrice(0); setPathResult(null); setPathError(null)
    setGrowthBear(0); setGrowthBase(0); setGrowthBull(0)
    setPeBear(0); setPeBase(0); setPeBull(0)
    setRequiredReturn(0.12); setRequiredMos(0.25)
    setGuidedValResult(null); setGuidedValError(null); setGuidedSeeded(false)
    setScreenIdx(0)

    const [cardRes, entryRes, filingsRes] = await Promise.all([
      coach.fetchDataCard(t),
      coach.createEntry(t),
      coach.fetchFilings(t),
    ])
    if (cardRes.error) { setError(cardRes.error.detail); return }
    if (entryRes.error) { setError(entryRes.error.detail); return }
    setActiveTicker(t)
    setCard(cardRes.data)
    setEntry(entryRes.data)
    setFilings(filingsRes.data)  // soft-fail on filings
    // Fix: patch snapshot immediately so _snapshot_had_flags() works at creation time
    if (cardRes.data && entryRes.data) {
      void coach.patchEntry(entryRes.data.id, t, {
        data_card_snapshot: cardRes.data as unknown as Record<string, unknown>,
      })
    }
    // Seed target price with a +20% over spot — easy to override.
    if (cardRes.data?.spot_price) {
      setTargetPrice(Number((cardRes.data.spot_price * 1.2).toFixed(2)))
    }
  }, [ticker, coach])

  // ---- autosave on every screen advance ----
  const persistAnswers = useCallback(async (next: Answers) => {
    if (!entry || !activeTicker) return
    const merged = { ...answers, ...next }
    setAnswers(merged)

    // Compose q3_upside from the granular fields when at least one is set.
    const market = merged.q3_market?.trim()
    const moat = merged.q3_moat?.trim()
    const whyNow = merged.q3_why_now?.trim()
    let q3_upside = merged.q3_upside ?? null
    if (market || moat || whyNow) {
      q3_upside = [
        market ? `Market: ${market}` : null,
        moat ? `Moat: ${moat}` : null,
        whyNow ? `Why now: ${whyNow}` : null,
      ].filter(Boolean).join('\n\n')
    }

    const patched = { ...merged, q3_upside }
    const res = await coach.patchEntry(entry.id, activeTicker, { answers: patched })
    if (res.data) setEntry(res.data)
  }, [entry, activeTicker, answers, coach])

  const computePath = useCallback(async () => {
    if (!activeTicker || !targetPrice || targetPrice <= 0) return
    setPathError(null)
    const res = await coach.fetchPathToTarget(activeTicker, targetPrice)
    if (res.error) { setPathError(res.error.detail); return }
    if (res.data) setPathResult(res.data)
  }, [activeTicker, targetPrice, coach])

  // Seed Fair Price defaults from Path-to-Target once (when pathResult arrives)
  useEffect(() => {
    if (!pathResult || guidedSeeded) return
    const hist = pathResult.historical_growth_pct ?? 0
    setGrowthBear(0)
    setGrowthBase(hist)
    setGrowthBull(Math.min(hist * 1.5, 0.50))
    setPeBear(pathResult.peer_multiple_low)
    setPeBase(
      pathResult.current_multiple
      ?? (pathResult.peer_multiple_low + pathResult.peer_multiple_high) / 2,
    )
    setPeBull(pathResult.peer_multiple_high)
    setGuidedSeeded(true)
  }, [pathResult, guidedSeeded])

  const computeGuidedVal = useCallback(async () => {
    if (!activeTicker || !card) return
    setGuidedValError(null)
    const eps = deriveFairEps(card, pathResult)
    if (eps == null || eps <= 0) {
      setGuidedValError(
        'No positive earnings found — this appears to be a pre-profit company. '
        + 'Size it as an option premium instead.',
      )
      return
    }
    const req: GuidedValuationInput = {
      current_eps: eps,
      growth_bear: growthBear,
      growth_base: growthBase,
      growth_bull: growthBull,
      years: 5,
      pe_bear: peBear || 10,
      pe_base: peBase || 15,
      pe_bull: peBull || 20,
      required_return: requiredReturn,
      spot_price: card.spot_price,
      required_mos: requiredMos,
    }
    const res = await coach.guidedValuation(req)
    if (res.error) { setGuidedValError(res.error.detail); return }
    if (res.data) setGuidedValResult(res.data)
  }, [
    activeTicker, card, pathResult,
    growthBear, growthBase, growthBull,
    peBear, peBase, peBull,
    requiredReturn, requiredMos, coach,
  ])

  const advance = useCallback(() => {
    setScreenIdx(i => (Math.min(i + 1, SCREENS.length - 1) as ScreenIdx))
  }, [])
  const back = useCallback(() => {
    setScreenIdx(i => (Math.max(i - 1, 0) as ScreenIdx))
  }, [])

  const finalize = useCallback(async () => {
    if (!entry || !activeTicker) return
    // Front-end basic gates — backend re-validates via assert_completable().
    if (!userCall || !stomach || finalDollars <= 0) {
      setError('Please pick a call, a stomach answer, and a final size.')
      return
    }
    if (!sellTarget || sellTarget <= 0) {
      setError('Pick a sell target so you know when to take profit.')
      return
    }
    if ((bailOutTrigger ?? '').trim().length < 20) {
      setError('Write a specific bail-out trigger (at least 20 characters).')
      return
    }
    if ((answers.q9_bear_case ?? '').trim().length < 30) {
      setError('Write a bear case (at least 30 characters) before completing.')
      return
    }
    if (!commitmentAck) {
      setError('Check the commitment box before completing.')
      return
    }
    setError(null)
    const guidedSave: GuidedValuationSave | null = guidedValResult
      ? {
          current_eps: guidedValResult.inputs_used.current_eps ?? null,
          growth_bear: growthBear, growth_base: growthBase, growth_bull: growthBull,
          years: 5,
          pe_bear: peBear, pe_base: peBase, pe_bull: peBull,
          required_return: requiredReturn,
          required_mos: requiredMos,
          fair_bear: guidedValResult.bear,
          fair_base: guidedValResult.base,
          fair_bull: guidedValResult.bull,
          spot_at_time: guidedValResult.spot ?? null,
          margin_of_safety: guidedValResult.margin_of_safety,
          buy_at_or_below: guidedValResult.buy_at_or_below,
        }
      : null
    const patch = await coach.patchEntry(entry.id, activeTicker, {
      valuation: {
        user_call: userCall,
        reasoning: answers.q3_why_now ?? null,
        guided: guidedSave,
      },
      sizing: {
        planned_dollars: plannedDollars,
        stomach_answer: stomach,
        final_dollars: finalDollars,
        portfolio_pct_estimate: portfolioPct > 0 ? portfolioPct : null,
        sell_target: sellTarget,
        add_more_price: addMorePrice > 0 ? addMorePrice : null,
        bail_out_trigger: bailOutTrigger,
        commitment_acknowledged: commitmentAck,
      },
    })
    if (patch.error) { setError(patch.error.detail); return }
    const done = await coach.completeEntry(entry.id, activeTicker)
    if (done.error) { setError(done.error.detail); return }
    if (done.data) { setEntry(done.data); setCompleted(true) }
  }, [
    entry, activeTicker, userCall, stomach, finalDollars,
    sellTarget, bailOutTrigger, commitmentAck,
    answers.q3_why_now, answers.q9_bear_case,
    plannedDollars, portfolioPct, addMorePrice,
    growthBear, growthBase, growthBull,
    peBear, peBase, peBull,
    requiredReturn, requiredMos, guidedValResult, coach,
  ])

  // ---- render ----
  if (!activeTicker || !card) {
    return (
      <div className="dd-shell">
        <h2 className="dd-title">DD Coach</h2>
        <p className="dd-subtitle">
          A plain-English wizard that walks you through buying a stock —
          no finance jargon, two safety checks, one fair-value range.
        </p>
        <div className="dd-start">
          <input
            type="text"
            value={ticker}
            onChange={e => setTicker(e.target.value)}
            placeholder="Ticker (e.g. MSFT, NBIS, IONQ)"
            className="dd-input"
            onKeyDown={e => { if (e.key === 'Enter') void startEntry() }}
            maxLength={10}
          />
          <button
            className="btn-primary"
            onClick={() => void startEntry()}
            disabled={coach.loading || !ticker.trim()}
          >
            {coach.loading ? 'Starting…' : 'Start due diligence'}
          </button>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </div>
    )
  }

  return (
    <div className="dd-shell">
      <header className="dd-shell-header">
        <h2 className="dd-title">DD Coach — {activeTicker}</h2>
        {filings && <FilingsBar filings={filings} />}
        <StepStrip current={screenIdx} />
      </header>

      {error && <div className="error-banner">{error}</div>}

      {completed ? (
        <CompletedSummary entry={entry} card={card} />
      ) : (
        <ScreenBody
          idx={screenIdx}
          ticker={activeTicker}
          card={card}
          answers={answers}
          onAnswers={persistAnswers}
          userCall={userCall}
          setUserCall={setUserCall}
          plannedDollars={plannedDollars}
          setPlannedDollars={setPlannedDollars}
          stomach={stomach}
          setStomach={setStomach}
          finalDollars={finalDollars}
          setFinalDollars={setFinalDollars}
          portfolioPct={portfolioPct}
          setPortfolioPct={setPortfolioPct}
          sellTarget={sellTarget}
          setSellTarget={setSellTarget}
          addMorePrice={addMorePrice}
          setAddMorePrice={setAddMorePrice}
          bailOutTrigger={bailOutTrigger}
          setBailOutTrigger={setBailOutTrigger}
          commitmentAck={commitmentAck}
          setCommitmentAck={setCommitmentAck}
          targetPrice={targetPrice}
          setTargetPrice={setTargetPrice}
          pathResult={pathResult}
          pathError={pathError}
          onComputePath={computePath}
          pathLoading={coach.loading}
          growthBear={growthBear}
          setGrowthBear={setGrowthBear}
          growthBase={growthBase}
          setGrowthBase={setGrowthBase}
          growthBull={growthBull}
          setGrowthBull={setGrowthBull}
          peBear={peBear}
          setPeBear={setPeBear}
          peBase={peBase}
          setPeBase={setPeBase}
          peBull={peBull}
          setPeBull={setPeBull}
          requiredReturn={requiredReturn}
          setRequiredReturn={setRequiredReturn}
          requiredMos={requiredMos}
          setRequiredMos={setRequiredMos}
          guidedValResult={guidedValResult}
          guidedValError={guidedValError}
          onComputeGuidedVal={computeGuidedVal}
        />
      )}

      {!completed && (
        <footer className="dd-nav">
          <button className="btn-secondary" onClick={back} disabled={screenIdx === 0}>Back</button>
          {screenIdx < SCREENS.length - 1 ? (
            <button className="btn-primary" onClick={advance}>Next</button>
          ) : (
            <button className="btn-primary" onClick={() => void finalize()} disabled={coach.loading}>
              Save thesis & complete
            </button>
          )}
        </footer>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StepStrip({ current }: { current: ScreenIdx }) {
  return (
    <ol className="dd-steps">
      {SCREENS.map((label, i) => (
        <li
          key={label}
          className={`dd-step${i === current ? ' dd-step-active' : ''}${i < current ? ' dd-step-done' : ''}`}
        >
          <span className="dd-step-n">{i + 1}</span>
          <span className="dd-step-label">{label}</span>
        </li>
      ))}
    </ol>
  )
}

function FilingsBar({ filings }: { filings: FilingLinks }) {
  // Persistent reference toolbar. The 10-K is the source of truth for almost
  // every screen — keep it one click away throughout the wizard.
  return (
    <nav className="dd-filings-bar" aria-label="SEC filings">
      <span className="dd-filings-label">SEC filings:</span>
      <a href={filings.latest_10k} target="_blank" rel="noopener noreferrer">10-K (annual)</a>
      <a href={filings.latest_10q} target="_blank" rel="noopener noreferrer">10-Q (quarterly)</a>
      <a href={filings.latest_8k} target="_blank" rel="noopener noreferrer">8-K (events)</a>
      <a href={filings.form4_insider} target="_blank" rel="noopener noreferrer">Insider (Form 4)</a>
      <a href={filings.proxy_def14a} target="_blank" rel="noopener noreferrer">Proxy</a>
    </nav>
  )
}

interface ScreenBodyProps {
  idx: ScreenIdx
  ticker: string
  card: DataCard
  answers: Answers
  onAnswers: (next: Answers) => Promise<void> | void
  userCall: UserCall | null
  setUserCall: (v: UserCall) => void
  plannedDollars: number
  setPlannedDollars: (n: number) => void
  stomach: StomachAnswer | null
  setStomach: (v: StomachAnswer) => void
  finalDollars: number
  setFinalDollars: (n: number) => void
  portfolioPct: number
  setPortfolioPct: (n: number) => void
  sellTarget: number
  setSellTarget: (n: number) => void
  addMorePrice: number
  setAddMorePrice: (n: number) => void
  bailOutTrigger: string
  setBailOutTrigger: (v: string) => void
  commitmentAck: boolean
  setCommitmentAck: (v: boolean) => void
  targetPrice: number
  setTargetPrice: (n: number) => void
  pathResult: PathToTarget | null
  pathError: string | null
  onComputePath: () => Promise<void> | void
  pathLoading: boolean
  // V3 guided valuation — Fair Price screen
  growthBear: number
  setGrowthBear: (n: number) => void
  growthBase: number
  setGrowthBase: (n: number) => void
  growthBull: number
  setGrowthBull: (n: number) => void
  peBear: number
  setPeBear: (n: number) => void
  peBase: number
  setPeBase: (n: number) => void
  peBull: number
  setPeBull: (n: number) => void
  requiredReturn: number
  setRequiredReturn: (n: number) => void
  requiredMos: number
  setRequiredMos: (n: number) => void
  guidedValResult: GuidedValuationResult | null
  guidedValError: string | null
  onComputeGuidedVal: () => Promise<void> | void
}

function ScreenBody(p: ScreenBodyProps) {
  switch (p.idx) {
    case 0: return <>
      <Screen1
        card={p.card}
        q1={p.answers.q1_business ?? ''}
        setQ1={v => p.onAnswers({ q1_business: v })}
        flagResponse={p.answers.q1_flag_response ?? null}
        setFlagResponse={fr => p.onAnswers({ q1_flag_response: fr })}
      />
      <AIAssistPanel
        ticker={p.ticker}
        insightType="business_summary"
        title="AI take: what this company actually does"
        subtitle="plain-English summary from the latest 10-K"
      />
    </>
    case 1: return <>
      <SimpleTextScreen
        heading="What They Sell"
        prompt="How does this company actually make money? Name the top product or service, and who pays for it."
        example="They rent GPU compute by the hour to AI startups. About 40% of revenue comes from one customer."
        value={p.answers.q2_revenue_model ?? ''}
        onChange={v => p.onAnswers({ q2_revenue_model: v })}
      />
      <AIAssistPanel
        ticker={p.ticker}
        insightType="mda_summary"
        title="AI take: latest revenue & margin drivers"
        subtitle="Management's Discussion and Analysis (MD&A) from the most recent 10-Q"
      />
    </>
    case 2: return <SimpleTextScreen
      heading="The Market"
      prompt="How big is the pie they're chasing? How much of it do they have today?"
      example="Cloud GPU compute is a ~$50B market in 2026 and growing fast. They have a tiny sliver — maybe 1% — so there's room to grow."
      value={p.answers.q3_market ?? ''}
      onChange={v => p.onAnswers({ q3_market: v })}
    />
    case 3: return <SimpleTextScreen
      heading="The Moat"
      prompt="Why can't a bigger competitor just copy this tomorrow? Pick the one that fits best and write a sentence."
      example="Switching cost — once a customer wires their AI training pipeline into a specific provider, moving is expensive and risky."
      value={p.answers.q3_moat ?? ''}
      onChange={v => p.onAnswers({ q3_moat: v })}
    />
    case 4: return <>
      <Screen5Leadership
        value={p.answers.q5_leadership ?? null}
        onChange={lead => p.onAnswers({ q5_leadership: lead })}
      />
      <AIAssistPanel
        ticker={p.ticker}
        insightType="leadership"
        title="AI take: leadership & compensation"
        subtitle="latest DEF 14A proxy + recent Form 4 cadence"
      />
    </>
    case 5: return <Screen6PathToTarget
      card={p.card}
      targetPrice={p.targetPrice}
      setTargetPrice={p.setTargetPrice}
      result={p.pathResult}
      error={p.pathError}
      loading={p.pathLoading}
      onCompute={p.onComputePath}
    />
    case 6: return <>
      <Screen7Risks
        value={p.answers.q4_risks ?? ''}
        onChange={v => p.onAnswers({ q4_risks: v })}
      />
      <AIAssistPanel
        ticker={p.ticker}
        insightType="risk_diff"
        title="AI take: risks you need to understand"
        subtitle="new this year, materially expanded, and ongoing"
      />
    </>
    case 7: return <SimpleTextScreen
      heading="Why Now"
      prompt="What has to happen in the next 12 months for this to work? If you can't name a catalyst, it isn't 'now' — it's 'maybe someday.'"
      example="The next earnings call should show their AI cloud business crossing $1B annual run-rate. If it does, the stock re-rates."
      value={p.answers.q3_why_now ?? ''}
      onChange={v => p.onAnswers({ q3_why_now: v })}
    />
    case 8: return <>
      <Screen9BearCase
        value={p.answers.q9_bear_case ?? ''}
        onChange={v => p.onAnswers({ q9_bear_case: v })}
      />
      <AIAssistPanel
        ticker={p.ticker}
        insightType="bear_scaffold"
        title="AI take: three plausible –50% scenarios"
        subtitle="stress-tests for your thesis, not predictions"
      />
    </>
    case 9: return <ScreenFairPrice
      card={p.card}
      pathResult={p.pathResult}
      growthBear={p.growthBear}
      setGrowthBear={p.setGrowthBear}
      growthBase={p.growthBase}
      setGrowthBase={p.setGrowthBase}
      growthBull={p.growthBull}
      setGrowthBull={p.setGrowthBull}
      peBear={p.peBear}
      setPeBear={p.setPeBear}
      peBase={p.peBase}
      setPeBase={p.setPeBase}
      peBull={p.peBull}
      setPeBull={p.setPeBull}
      requiredReturn={p.requiredReturn}
      setRequiredReturn={p.setRequiredReturn}
      requiredMos={p.requiredMos}
      setRequiredMos={p.setRequiredMos}
      guidedValResult={p.guidedValResult}
      guidedValError={p.guidedValError}
      loading={p.pathLoading}
      onCompute={p.onComputeGuidedVal}
    />
    case 10: return <Screen10Decision
      card={p.card}
      pathResult={p.pathResult}
      guidedValResult={p.guidedValResult}
      userCall={p.userCall}
      setUserCall={p.setUserCall}
      plannedDollars={p.plannedDollars}
      setPlannedDollars={p.setPlannedDollars}
      stomach={p.stomach}
      setStomach={p.setStomach}
      finalDollars={p.finalDollars}
      setFinalDollars={p.setFinalDollars}
      portfolioPct={p.portfolioPct}
      setPortfolioPct={p.setPortfolioPct}
      sellTarget={p.sellTarget}
      setSellTarget={p.setSellTarget}
      addMorePrice={p.addMorePrice}
      setAddMorePrice={p.setAddMorePrice}
      bailOutTrigger={p.bailOutTrigger}
      setBailOutTrigger={p.setBailOutTrigger}
      commitmentAck={p.commitmentAck}
      setCommitmentAck={p.setCommitmentAck}
    />
  }
}

// ---- Screen 1 ----

function Screen1({ card, q1, setQ1, flagResponse, setFlagResponse }: {
  card: DataCard
  q1: string
  setQ1: (v: string) => void
  flagResponse: import('../../types/ddCoach').FlagResponse | null
  setFlagResponse: (fr: import('../../types/ddCoach').FlagResponse) => void
}) {
  const hasFlags = card.flags.reasons.length > 0
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">The Business</h3>
      <p className="dd-screen-prompt">
        Read the snapshot below. Then write one or two sentences in your own
        words about what this company does. If you can't explain it to a friend
        without jargon, you don't understand it well enough yet.
      </p>
      <DataCardPanel card={card} />
      {hasFlags && (
        <RedFlagResponsePanel
          reasons={card.flags.reasons}
          value={flagResponse}
          onChange={setFlagResponse}
        />
      )}
      <Textarea
        value={q1}
        onChange={setQ1}
        placeholder="In your own words: what does this company do?"
      />
    </div>
  )
}

function RedFlagResponsePanel({ reasons, value, onChange }: {
  reasons: string[]
  value: import('../../types/ddCoach').FlagResponse | null
  onChange: (fr: import('../../types/ddCoach').FlagResponse) => void
}) {
  const ack = value?.acknowledgment ?? null
  return (
    <div className="dd-red-flag-block">
      <h4 className="dd-red-flag-heading">
        The data card flagged something — you must react before continuing
      </h4>
      <ul className="dd-red-flag-list">
        {reasons.map(r => <li key={r}>{r}</li>)}
      </ul>
      <p className="dd-screen-prompt">How does this change your view?</p>
      <RadioRow<FlagAcknowledgment>
        value={ack}
        onChange={v => onChange({ acknowledgment: v, note: value?.note ?? null })}
        options={[
          { value: 'accounted', label: "I've factored this in — my thesis still holds" },
          { value: 'changes_view', label: "It changes my view — I'll size smaller or wait" },
          { value: 'explained', label: "I read the data card explanations and understand why" },
        ]}
      />
      <Textarea
        value={value?.note ?? ''}
        onChange={v => onChange({ acknowledgment: ack ?? 'explained', note: v })}
        placeholder="Optional: one sentence on how this affects your decision."
      />
    </div>
  )
}

// ---- Simple text screens (2, 3, 4, 7) ----

function SimpleTextScreen({ heading, prompt, example, value, onChange }: {
  heading: string
  prompt: string
  example: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">{heading}</h3>
      <p className="dd-screen-prompt">{prompt}</p>
      <Textarea value={value} onChange={onChange} placeholder="Your answer…" />
      <p className="dd-example"><strong>Example:</strong> {example}</p>
    </div>
  )
}

// ---- Screen 5 — Leadership ----

function Screen5Leadership({ value, onChange }: {
  value: LeadershipCheck | null
  onChange: (v: LeadershipCheck) => void
}) {
  const v: LeadershipCheck = value ?? {
    who: '',
    insider_activity: null,
    comp_structure: null,
    concerns: '',
  }
  const patch = (next: Partial<LeadershipCheck>) => onChange({ ...v, ...next })

  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">Leadership</h3>
      <p className="dd-screen-prompt">
        Who's driving the bus? You don't need a biography — just enough to
        decide whether you trust them with your money for the next few years.
        Check the proxy (link in the toolbar) for comp and the Form 4 link
        for insider buying/selling.
      </p>

      <div className="dd-leadership-grid">
        <div className="dd-section">
          <h4>Who runs it?</h4>
          <Textarea
            value={v.who ?? ''}
            onChange={s => patch({ who: s })}
            placeholder="CEO name, how long in role, founder or hired?"
          />
        </div>

        <div className="dd-section">
          <h4>Insider activity (last 6–12 months)</h4>
          <RadioRow<InsiderActivity>
            value={v.insider_activity ?? null}
            onChange={s => patch({ insider_activity: s })}
            options={[
              { value: 'heavy_buy', label: 'Heavy insider buying' },
              { value: 'light_buy', label: 'Some insider buying' },
              { value: 'quiet', label: 'Quiet — no notable activity' },
              { value: 'light_sell', label: 'Some insider selling' },
              { value: 'heavy_sell', label: 'Heavy insider selling' },
              { value: 'unknown', label: "Don't know / couldn't tell" },
            ]}
          />
        </div>

        <div className="dd-section">
          <h4>How is the CEO paid? (proxy / DEF 14A)</h4>
          <RadioRow<CompStructure>
            value={v.comp_structure ?? null}
            onChange={s => patch({ comp_structure: s })}
            options={[
              { value: 'revenue', label: 'Mostly tied to revenue growth' },
              { value: 'profit', label: 'Mostly tied to profit / margins' },
              { value: 'stock', label: 'Mostly tied to stock price / TSR' },
              { value: 'salary', label: 'Mostly salary' },
              { value: 'unknown', label: "Don't know" },
            ]}
          />
        </div>

        <div className="dd-section">
          <h4>Concerns? (optional)</h4>
          <Textarea
            value={v.concerns ?? ''}
            onChange={s => patch({ concerns: s })}
            placeholder="Anything that bothers you — turnover, dual-class, related-party deals?"
          />
        </div>
      </div>
    </div>
  )
}

// ---- Screen 6 — Path to Target ----

function Screen6PathToTarget({
  card, targetPrice, setTargetPrice, result, error, loading, onCompute,
}: {
  card: DataCard
  targetPrice: number
  setTargetPrice: (n: number) => void
  result: PathToTarget | null
  error: string | null
  loading: boolean
  onCompute: () => Promise<void> | void
}) {
  const spot = card.spot_price ?? null

  // Quick-pick buttons set a +X% target above spot.
  const quickPick = (mult: number) => {
    if (spot == null) return
    setTargetPrice(Number((spot * mult).toFixed(2)))
  }

  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">Path to Target</h3>
      <p className="dd-screen-prompt">
        Pick a price you think the stock could reach. We'll show you the three
        ways it can get there — and how realistic each path is — so you know
        what you're really betting on.
      </p>

      <div className="dd-form">
        <div className="dd-field">
          <span className="dd-field-label">
            Today's price: {spot != null ? `$${spot.toFixed(2)}` : '—'}
          </span>
        </div>
        <NumberField
          label="Your target price ($)"
          value={targetPrice}
          onChange={setTargetPrice}
        />
        {spot != null && (
          <div className="dd-target-buttons">
            <button type="button" className="btn-secondary" onClick={() => quickPick(1.2)}>+20%</button>
            <button type="button" className="btn-secondary" onClick={() => quickPick(1.5)}>+50%</button>
            <button type="button" className="btn-secondary" onClick={() => quickPick(2.0)}>2×</button>
            <button type="button" className="btn-secondary" onClick={() => quickPick(3.0)}>3×</button>
          </div>
        )}
        <div className="dd-form-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => void onCompute()}
            disabled={loading || !targetPrice || targetPrice <= 0}
          >
            {loading ? 'Computing…' : 'Show me the paths'}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && <PathToTargetResult result={result} />}

      <p className="dd-glossary">
        <strong>Glossary:</strong> "Multiple" = how much investors pay for
        every $1 of yearly cash the company throws off.
      </p>
    </div>
  )
}

function PathToTargetResult({ result }: { result: PathToTarget }) {
  const r = result
  return (
    <div className="dd-result">
      <h4>
        From ${r.spot?.toFixed(2) ?? '—'} to ${r.target.toFixed(2)} —{' '}
        {pctFmt(r.target_return_pct)} return
      </h4>
      <p className="dd-screen-prompt">
        We're measuring against per-share{' '}
        <strong>
          {r.cash_basis === 'earnings' ? 'earnings'
            : r.cash_basis === 'fcf' ? 'free cash flow'
            : 'cash (n/a — not profitable yet)'}
        </strong>
        {r.current_multiple != null && (
          <> — today the market pays <strong>{multFmt(r.current_multiple)}</strong> per $1 of that cash.</>
        )}
        {' '}
        {r.peer_label} typically trade {r.peer_multiple_low.toFixed(0)}–{r.peer_multiple_high.toFixed(0)}×
        {r.historical_growth_pct != null && (
          <> and this company has been growing revenue ~{pctFmt(r.historical_growth_pct)} per year.</>
        )}
      </p>
      <div className="dd-paths">
        <PathCard
          title="Path A — Lemonade-stand grows"
          subtitle="The company grows fast enough to earn its way to your target."
          path={r.path_a_growth_only}
        />
        <PathCard
          title="Path B — Neighborhood gets trendy"
          subtitle="The market revalues the same cash flows — same scoops, more dollars per scoop."
          path={r.path_b_multiple_only}
        />
        <PathCard
          title="Path C — A bit of both"
          subtitle="Half from growth, half from re-rating."
          path={r.path_c_mixed}
        />
      </div>
      {r.notes.length > 0 && (
        <ul className="dd-notes">
          {r.notes.map(n => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  )
}

function PathCard({ title, subtitle, path }: {
  title: string
  subtitle: string
  path: PathResult
}) {
  const cls = path.realism ? `dd-path-realism-${path.realism}` : ''
  return (
    <div className={`dd-path-card ${cls}`}>
      <h5 className="dd-path-title">{title}</h5>
      <p className="dd-path-subtitle">{subtitle}</p>
      {!path.applicable ? (
        <p className="dd-path-na">Not applicable — {path.note}</p>
      ) : (
        <>
          <ul className="dd-path-requirements">
            {path.required_growth_pct != null && (
              <li>Required cash growth: <strong>{pctFmt(path.required_growth_pct)} / yr</strong></li>
            )}
            {path.required_multiple != null && (
              <li>Required multiple: <strong>{multFmt(path.required_multiple)}</strong></li>
            )}
          </ul>
          <p className="dd-path-note">{path.note}</p>
          {path.realism && (
            <p className="dd-path-realism">{REALISM_LABEL[path.realism]}</p>
          )}
        </>
      )}
    </div>
  )
}

// ---- Screen 7 — Risks + filings links ----

function Screen7Risks({ value, onChange }: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">The Risks</h3>
      <p className="dd-screen-prompt">
        Open the latest 10-K (link in the toolbar above), jump to "Risk
        Factors," and write down the three that worry you most — in your own
        words.
      </p>
      <TenKSkimGuide />
      <Textarea value={value} onChange={onChange} placeholder="Top three risks, in your own words…" />
    </div>
  )
}

// ---- 10-K Skim Guide ----------------------------------------------------
// A 10-K is ~200 pages. ~30 minutes on the right seven sections covers 90%
// of what matters. Everything else is boilerplate.

interface SkimRow {
  item: string
  section: string
  look_for: string
  minutes: string
}

const SKIM_ROWS: SkimRow[] = [
  { item: 'Item 1',  section: 'Business',           look_for: 'What they sell, segments, top customers, geography',         minutes: '5 min' },
  { item: 'Item 1A', section: 'Risk Factors',       look_for: 'Diff vs. last year’s 10-K — only the new risks',   minutes: '5 min' },
  { item: 'Item 3',  section: 'Legal Proceedings',  look_for: 'Anything material (lawsuits, investigations)',                minutes: '1 min' },
  { item: 'Item 7',  section: "Management's Discussion and Analysis (MD&A)", look_for: 'Revenue bridge, margin commentary, liquidity',                minutes: '10 min' },
  { item: 'Item 8',  section: 'Cash Flow Statement', look_for: 'CFO vs. Net Income, CapEx, buybacks vs. stock-based comp',   minutes: '3 min' },
  { item: 'Item 8',  section: 'Notes (Debt, Segments, Related Parties)', look_for: 'Debt maturities, true segment profit, anything weird', minutes: '5 min' },
  { item: 'Item 9A', section: 'Controls',           look_for: 'Search for “material weakness” — if present, stop', minutes: '30 sec' },
]

function TenKSkimGuide() {
  return (
    <details className="dd-skim">
      <summary className="dd-skim-summary">
        Where to skim the 10-K — ~30 min covers 90% of it
      </summary>
      <table className="dd-skim-table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Section</th>
            <th>What to look for</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {SKIM_ROWS.map(r => (
            <tr key={`${r.item}-${r.section}`}>
              <td className="dd-skim-item">{r.item}</td>
              <td>{r.section}</td>
              <td>{r.look_for}</td>
              <td className="dd-skim-time">{r.minutes}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="dd-skim-skip">
        <strong>Skip:</strong> cover page, Item 2 Properties, Item 4 Mine Safety,
        forward-looking-statements boilerplate, exhibits index.
      </p>
    </details>
  )
}

// ---- Screen 9 — Bear case ----

function Screen9BearCase({ value, onChange }: {
  value: string
  onChange: (v: string) => void
}) {
  const count = value.trim().length
  const enough = count >= 30
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">Bear Case</h3>
      <p className="dd-screen-prompt">
        Argue against yourself. If this investment loses 50% over the next two
        years, what was the most likely reason? Steelman it — write the version
        a smart short-seller would say.
      </p>
      <Textarea
        value={value}
        onChange={onChange}
        placeholder="The most likely way this turns into a 50% loser…"
      />
      <p className={`dd-bear-case ${enough ? '' : 'dd-bear-case-short'}`}>
        {count} / 30 characters {enough ? '✓' : '(write a real sentence)'}
      </p>
    </div>
  )
}

// ---- Small number inputs for Fair Price screen ----

function PctInput({ label, value, onChange }: {
  label: string
  value: number      // stored as decimal, e.g. 0.10 = 10 %
  onChange: (v: number) => void
}) {
  return (
    <label className="dd-fp-input-group">
      <span>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <input
          type="number"
          className="dd-input"
          style={{ width: 80 }}
          value={value !== 0 ? (value * 100).toFixed(1) : ''}
          onChange={e => onChange((parseFloat(e.target.value) || 0) / 100)}
          min={-50}
          max={200}
          step={0.5}
        />
        <span style={{ color: '#94a3b8', fontSize: 13 }}>%/yr</span>
      </div>
    </label>
  )
}

function MultInput({ label, value, onChange }: {
  label: string
  value: number      // raw P/E multiple
  onChange: (v: number) => void
}) {
  return (
    <label className="dd-fp-input-group">
      <span>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <input
          type="number"
          className="dd-input"
          style={{ width: 80 }}
          value={value || ''}
          onChange={e => onChange(parseFloat(e.target.value) || 0)}
          min={1}
          max={500}
          step={1}
        />
        <span style={{ color: '#94a3b8', fontSize: 13 }}>×</span>
      </div>
    </label>
  )
}

// ---- Screen 9 — Fair Price (V3) ----

interface ScreenFairPriceProps {
  card: DataCard
  pathResult: PathToTarget | null
  growthBear: number; setGrowthBear: (n: number) => void
  growthBase: number; setGrowthBase: (n: number) => void
  growthBull: number; setGrowthBull: (n: number) => void
  peBear: number; setPeBear: (n: number) => void
  peBase: number; setPeBase: (n: number) => void
  peBull: number; setPeBull: (n: number) => void
  requiredReturn: number; setRequiredReturn: (n: number) => void
  requiredMos: number; setRequiredMos: (n: number) => void
  guidedValResult: GuidedValuationResult | null
  guidedValError: string | null
  loading: boolean
  onCompute: () => Promise<void> | void
}

function ScreenFairPrice(p: ScreenFairPriceProps) {
  const eps = deriveFairEps(p.card, p.pathResult)
  const spot = p.card.spot_price
  const isPreProfit = eps == null || eps <= 0

  // Inline equation preview (before hitting compute)
  const equationPreview = !isPreProfit && p.peBear > 0 && p.peBase > 0 ? (
    <div className="dd-fp-formula">
      <strong>How the math works:</strong><br />
      {'  '}Projected earnings in 5 yrs = ${eps?.toFixed(2)} × (1 + growth%)⁵<br />
      {'  '}Future price = projected earnings × exit P/E<br />
      {'  '}Fair value today = future price ÷ (1 + {(p.requiredReturn * 100).toFixed(0)}%/yr)⁵
    </div>
  ) : null

  // Live buy-at-or-below when result is available
  const buyAtOrBelow = p.guidedValResult?.base != null
    ? p.guidedValResult.base * (1 - p.requiredMos)
    : null

  return (
    <div className="dd-screen dd-fp-screen">
      <h3 className="dd-screen-heading">Fair Price</h3>
      <p className="dd-screen-prompt">
        You've studied the business, the risks, and the bull case. Now: what's it actually worth?
        This screen walks you through the "<strong>5-year owner</strong>" model — every assumption is yours.
        There's no black box.
      </p>

      {/* Step 1 — Anchor */}
      <div className="dd-fp-anchor">
        <div className="dd-fp-anchor-label">Today's earnings anchor</div>
        {isPreProfit ? (
          <>
            <div className="dd-fp-anchor-value" style={{ fontSize: 18, color: '#fbbf24' }}>
              Pre-profit company
            </div>
            <p className="dd-fp-anchor-sub">
              Traditional valuation doesn't apply — no positive earnings to project forward.
              Size this as an option premium: only invest what you can afford to lose entirely.
            </p>
          </>
        ) : (
          <>
            <div className="dd-fp-anchor-value">${eps!.toFixed(2)}</div>
            <p className="dd-fp-anchor-sub">
              <FinanceTerm termKey="fcf_per_share" labelOverride="earnings per share" />
              {' '}— this is the company's real cash profit per share last year.
              {spot && ` Today's price is $${spot.toFixed(2)}.`}
            </p>
          </>
        )}
      </div>

      {!isPreProfit && (
        <>
          {/* Step 2 — Growth assumptions */}
          <div className="dd-fp-section">
            <h4>
              Step 1 — How fast will earnings grow?{' '}
              <FinanceTerm termKey="growth_rate" />
            </h4>
            <p className="dd-screen-prompt" style={{ marginBottom: 12 }}>
              This is YOUR assumption — not a prediction. Bear = pessimistic, Base = your best guess, Bull = upside.
              {p.pathResult && ` (Pre-filled from historical growth: ${pctFmt(p.pathResult.historical_growth_pct, 1)}/yr)`}
            </p>
            <div className="dd-fp-inputs">
              <PctInput label="🐻 Bear (worst case)" value={p.growthBear} onChange={p.setGrowthBear} />
              <PctInput label="📊 Base (most likely)" value={p.growthBase} onChange={p.setGrowthBase} />
              <PctInput label="🚀 Bull (best case)" value={p.growthBull} onChange={p.setGrowthBull} />
            </div>
          </div>

          {/* Step 3 — Exit multiple */}
          <div className="dd-fp-section">
            <h4>
              Step 2 — What will investors pay per $1 earned in 5 years?{' '}
              <FinanceTerm termKey="pe_ratio" />
            </h4>
            <p className="dd-screen-prompt" style={{ marginBottom: 12 }}>
              The P/E multiple is how many dollars the market pays for $1 of annual profit.
              {p.pathResult && ` (Current: ${multFmt(p.pathResult.current_multiple)}, Peers: ${multFmt(p.pathResult.peer_multiple_low)}–${multFmt(p.pathResult.peer_multiple_high)})`}
            </p>
            <div className="dd-fp-inputs">
              <MultInput label="🐻 Bear exit P/E" value={p.peBear} onChange={p.setPeBear} />
              <MultInput label="📊 Base exit P/E" value={p.peBase} onChange={p.setPeBase} />
              <MultInput label="🚀 Bull exit P/E" value={p.peBull} onChange={p.setPeBull} />
            </div>
          </div>

          {/* Step 4 — Required return + equation */}
          <div className="dd-fp-section">
            <h4>
              Step 3 — What annual return do you require?{' '}
              <FinanceTerm termKey="required_return" />
            </h4>
            <p className="dd-screen-prompt" style={{ marginBottom: 12 }}>
              12%/yr is a common benchmark (roughly what a diversified stock portfolio earns long-term).
              Higher = more conservative fair value.
            </p>
            <div className="dd-fp-mos-row">
              <label style={{ color: '#94a3b8', fontSize: 13 }}>Required annual return</label>
              <PctInput
                label=""
                value={p.requiredReturn}
                onChange={p.setRequiredReturn}
              />
            </div>
            {equationPreview}
          </div>

          {/* Compute button */}
          <button
            type="button"
            className="dd-fp-compute-btn"
            disabled={p.loading || p.peBear <= 0 || p.peBase <= 0 || p.peBull <= 0}
            onClick={() => void p.onCompute()}
          >
            {p.loading ? 'Computing…' : 'Calculate my fair value range'}
          </button>

          {p.guidedValError && (
            <div className="dd-fp-error">{p.guidedValError}</div>
          )}

          {/* Results */}
          {p.guidedValResult && (
            <div className="dd-fp-result">
              <h4 style={{ marginBottom: 12, color: '#94a3b8', fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Your fair value range
              </h4>
              <div className="dd-fp-result-grid">
                <div className="dd-fp-scenario dd-fp-scenario-bear">
                  <div className="dd-fp-scenario-label">🐻 Bear</div>
                  <div className="dd-fp-scenario-price">${p.guidedValResult.bear.toFixed(2)}</div>
                  {spot && <div className="dd-fp-scenario-vs">
                    {p.guidedValResult.bear >= spot ? '+' : ''}{pctFmt((p.guidedValResult.bear - spot) / spot, 0)} vs today
                  </div>}
                </div>
                <div className="dd-fp-scenario dd-fp-scenario-base">
                  <div className="dd-fp-scenario-label">📊 Base</div>
                  <div className="dd-fp-scenario-price">${p.guidedValResult.base.toFixed(2)}</div>
                  {spot && <div className="dd-fp-scenario-vs">
                    {p.guidedValResult.base >= spot ? '+' : ''}{pctFmt((p.guidedValResult.base - spot) / spot, 0)} vs today
                  </div>}
                </div>
                <div className="dd-fp-scenario dd-fp-scenario-bull">
                  <div className="dd-fp-scenario-label">🚀 Bull</div>
                  <div className="dd-fp-scenario-price">${p.guidedValResult.bull.toFixed(2)}</div>
                  {spot && <div className="dd-fp-scenario-vs">
                    {p.guidedValResult.bull >= spot ? '+' : ''}{pctFmt((p.guidedValResult.bull - spot) / spot, 0)} vs today
                  </div>}
                </div>
              </div>

              <div className="dd-fp-gate">
                {p.guidedValResult.margin_of_safety != null && (
                  <div className="dd-fp-gate-row" style={{ marginBottom: 10 }}>
                    <span className="dd-fp-gate-label">
                      <FinanceTerm termKey="margin_of_safety" /> on base case
                    </span>
                    <span className={`dd-fp-gate-value ${p.guidedValResult.margin_of_safety >= 0 ? 'mos-positive' : 'mos-negative'}`}>
                      {pctFmt(p.guidedValResult.margin_of_safety, 1)}
                    </span>
                  </div>
                )}

                <div className="dd-fp-gate-row" style={{ marginBottom: 14 }}>
                  <span className="dd-fp-gate-label">
                    Your minimum <FinanceTerm termKey="margin_of_safety" /> <FinanceTerm termKey="scenarios" labelOverride="buffer" />
                  </span>
                  <PctInput label="" value={p.requiredMos} onChange={p.setRequiredMos} />
                </div>

                {buyAtOrBelow != null && (
                  <div className="dd-fp-gate-row">
                    <span className="dd-fp-gate-label">
                      Your price gate — only buy at or below:
                    </span>
                    <span className="dd-fp-gate-buy">
                      ${buyAtOrBelow.toFixed(2)}
                      {spot && buyAtOrBelow < spot && (
                        <span style={{ color: '#94a3b8', fontSize: 12, fontWeight: 400 }}>
                          {' '}(${(spot - buyAtOrBelow).toFixed(2)} below today's ${spot.toFixed(2)})
                        </span>
                      )}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          {!p.guidedValResult && (
            <p className="dd-fp-anchor-sub" style={{ textAlign: 'center', padding: '8px 0' }}>
              Fill in your assumptions above and hit <strong>Calculate</strong> to see your fair value.
              You can adjust the numbers and recalculate as many times as you like.
            </p>
          )}
        </>
      )}
    </div>
  )
}

// ---- Screen 10 — Decision + plan-pre-commit ----

function Screen10Decision({
  card, pathResult, guidedValResult, userCall, setUserCall,
  plannedDollars, setPlannedDollars, stomach, setStomach,
  finalDollars, setFinalDollars,
  portfolioPct, setPortfolioPct,
  sellTarget, setSellTarget,
  addMorePrice, setAddMorePrice,
  bailOutTrigger, setBailOutTrigger,
  commitmentAck, setCommitmentAck,
}: {
  card: DataCard
  pathResult: PathToTarget | null
  guidedValResult: GuidedValuationResult | null
  userCall: UserCall | null
  setUserCall: (v: UserCall) => void
  plannedDollars: number
  setPlannedDollars: (n: number) => void
  stomach: StomachAnswer | null
  setStomach: (v: StomachAnswer) => void
  finalDollars: number
  setFinalDollars: (n: number) => void
  portfolioPct: number
  setPortfolioPct: (n: number) => void
  sellTarget: number
  setSellTarget: (n: number) => void
  addMorePrice: number
  setAddMorePrice: (n: number) => void
  bailOutTrigger: string
  setBailOutTrigger: (v: string) => void
  commitmentAck: boolean
  setCommitmentAck: (v: boolean) => void
}) {
  // Derive a suggested call from MoS (V3) with fallback to path realism.
  const suggestedCall: UserCall | null = useMemo(() => {
    if (guidedValResult?.margin_of_safety != null) {
      const mos = guidedValResult.margin_of_safety
      if (mos >= 0.15) return 'cheap'
      if (mos >= -0.05) return 'fair'
      if (mos >= -0.20) return 'expensive_worth_it'
      return 'cannot_value'
    }
    // Fallback: path realism (pre-V3 behaviour)
    const mixed = pathResult?.path_c_mixed
    if (!mixed?.realism) return null
    switch (mixed.realism) {
      case 'easy': return 'cheap'
      case 'plausible': return 'fair'
      case 'stretch': return 'expensive_worth_it'
      case 'unrealistic': return 'cannot_value'
    }
  }, [guidedValResult, pathResult])

  useEffect(() => {
    if (userCall == null && suggestedCall != null) setUserCall(suggestedCall)
  }, [userCall, suggestedCall, setUserCall])

  const portfolioWarn = portfolioPct > 5

  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">Decision &amp; Plan</h3>

      {/* Fair value summary from Fair Price screen */}
      {guidedValResult && (
        <div className="dd-fp-result" style={{ marginBottom: 4 }}>
          <h4 style={{ marginBottom: 10, color: '#94a3b8', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Your fair value (from Fair Price screen)
          </h4>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ color: '#f87171', fontSize: 13 }}>Bear: ${guidedValResult.bear.toFixed(2)}</span>
            <span style={{ color: '#60a5fa', fontSize: 15, fontWeight: 600 }}>Base: ${guidedValResult.base.toFixed(2)}</span>
            <span style={{ color: '#4ade80', fontSize: 13 }}>Bull: ${guidedValResult.bull.toFixed(2)}</span>
            {guidedValResult.margin_of_safety != null && (
              <span style={{ color: guidedValResult.margin_of_safety >= 0 ? '#4ade80' : '#f87171', fontSize: 13 }}>
                MoS: {pctFmt(guidedValResult.margin_of_safety, 1)}
              </span>
            )}
            {guidedValResult.buy_at_or_below != null && (
              <span style={{ color: '#fbbf24', fontSize: 13, fontWeight: 600 }}>
                Buy ≤ ${guidedValResult.buy_at_or_below.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="dd-section">
        <h4>1. What's your call?</h4>
        <RadioRow<UserCall>
          value={userCall}
          onChange={setUserCall}
          options={[
            { value: 'cheap', label: "Cheap — there's a margin of safety" },
            { value: 'fair', label: 'Fairly priced — pay full price for a good business' },
            { value: 'expensive_worth_it', label: 'Expensive but worth it — paying up for quality' },
            { value: 'cannot_value', label: "Can't put a number on it — pure speculation" },
          ]}
        />
      </div>

      <div className="dd-section">
        <h4>2. Position size</h4>
        <NumberField
          label="Planned dollars"
          value={plannedDollars}
          onChange={setPlannedDollars}
        />
        <NumberField
          label="As % of your total portfolio (optional, advisory)"
          value={portfolioPct}
          onChange={setPortfolioPct}
        />
        {portfolioWarn && (
          <div className="dd-portfolio-warning">
            That's a concentrated bet ({portfolioPct.toFixed(1)}% of your portfolio).
            Make sure your conviction matches the size.
          </div>
        )}
      </div>

      <div className="dd-section">
        <h4>3. Stomach test</h4>
        <p className="dd-screen-prompt">
          If this stock dropped 50% next week and you read about it in the news,
          would you still hold — or buy more?
        </p>
        <RadioRow<StomachAnswer>
          value={stomach}
          onChange={setStomach}
          options={[
            { value: 'yes', label: "Yes — I'd add more" },
            { value: 'unsure', label: "I'd hold but it would hurt" },
            { value: 'no', label: "No — I'd panic-sell" },
          ]}
        />
        {stomach === 'no' && (
          <div className="dd-callout dd-callout-warn">
            That's a signal that your planned size is too large. Cut the final size below.
          </div>
        )}
      </div>

      <div className="dd-section">
        <h4>4. Final dollars (this is what you actually buy)</h4>
        <NumberField
          label="Final dollars"
          value={finalDollars}
          onChange={setFinalDollars}
        />
      </div>

      <div className="dd-section dd-plan-block">
        <h4>5. Your plan — written BEFORE you buy</h4>
        <p className="dd-screen-prompt">
          The single biggest reason retail investors lose money is no plan.
          Decide now — when you're calm — what would make you sell, add, or bail.
        </p>

        <div className="dd-plan-field">
          <NumberField
            label={`Sell target — take profit when price hits $${sellTarget || '…'}`}
            value={sellTarget}
            onChange={setSellTarget}
          />
          {card.spot_price && sellTarget > 0 && (
            <p className="dd-field-hint">
              That's {pctFmt((sellTarget / card.spot_price) - 1, 0)} above today's price.
            </p>
          )}
        </div>

        <div className="dd-plan-field">
          <NumberField
            label="Add-more price (optional) — buy more if it dips to this price"
            value={addMorePrice}
            onChange={setAddMorePrice}
          />
        </div>

        <div className="dd-plan-field">
          <span className="dd-field-label">
            Bail-out trigger — what specific bad news would make you sell at a loss?
          </span>
          <Textarea
            value={bailOutTrigger}
            onChange={setBailOutTrigger}
            placeholder="Example: 'Two consecutive quarters of AI-revenue decline' or 'CEO departs'."
          />
          <p className="dd-field-hint">
            {bailOutTrigger.trim().length} / 20 characters minimum
          </p>
        </div>

        <label className="dd-commitment">
          <input
            type="checkbox"
            checked={commitmentAck}
            onChange={e => setCommitmentAck(e.target.checked)}
          />
          <span>
            I commit to this plan. If I ever revisit it, it's to update with new
            facts — not to talk myself out of selling.
          </span>
        </label>
      </div>
    </div>
  )
}

// ---- Completed summary ----

function CompletedSummary({ entry, card }: { entry: DDEntry | null; card: DataCard }) {
  if (!entry) return null
  const guided = entry.valuation.guided
  return (
    <div className="dd-complete">
      <h3>Thesis saved for {card.ticker}</h3>
      <p className="dd-screen-prompt">
        Your DD entry has been recorded. Revisit it before adding to the position
        or trimming.
      </p>
      <dl className="dd-summary">
        <dt>Final size</dt>
        <dd>${(entry.sizing.final_dollars ?? 0).toLocaleString()}</dd>
        <dt>Your call</dt>
        <dd>{entry.valuation.user_call ?? '—'}</dd>
        <dt>Stomach test</dt>
        <dd>{entry.sizing.stomach_answer ?? '—'}</dd>
        {guided?.fair_base != null && (
          <>
            <dt>Fair value range</dt>
            <dd>
              ${guided.fair_bear?.toFixed(2) ?? '—'} — ${guided.fair_base.toFixed(2)} — ${guided.fair_bull?.toFixed(2) ?? '—'}
            </dd>
            {guided.margin_of_safety != null && (
              <>
                <dt>Margin of safety (base)</dt>
                <dd style={{ color: guided.margin_of_safety >= 0 ? '#4ade80' : '#f87171' }}>
                  {pctFmt(guided.margin_of_safety, 1)}
                </dd>
              </>
            )}
            {guided.buy_at_or_below != null && (
              <>
                <dt>Buy at or below</dt>
                <dd style={{ color: '#fbbf24' }}>${guided.buy_at_or_below.toFixed(2)}</dd>
              </>
            )}
          </>
        )}
        {entry.sizing.sell_target != null && entry.sizing.sell_target > 0 && (
          <>
            <dt>Sell target</dt>
            <dd>${entry.sizing.sell_target.toLocaleString()}</dd>
          </>
        )}
        {entry.sizing.bail_out_trigger && (
          <>
            <dt>Bail-out trigger</dt>
            <dd>{entry.sizing.bail_out_trigger}</dd>
          </>
        )}
      </dl>
    </div>
  )
}

// ---- Small inputs ----

function Textarea({ value, onChange, placeholder }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  // The whole point of DD Coach is that the user has to *think* and write the
  // answer themselves. Block paste, drop, drag, and the context menu so they
  // can't shortcut the reflection with copied text from elsewhere.
  return (
    <textarea
      className="dd-textarea"
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={e => onChange(e.target.value)}
      onPaste={e => e.preventDefault()}
      onDrop={e => e.preventDefault()}
      onDragOver={e => e.preventDefault()}
      onContextMenu={e => e.preventDefault()}
      autoComplete="off"
      autoCorrect="off"
      spellCheck={false}
      placeholder={placeholder}
      rows={4}
    />
  )
}

function NumberField({ label, value, onChange }: {
  label: string
  value: number
  onChange: (v: number) => void
}) {
  return (
    <label className="dd-field">
      <span className="dd-field-label">{label}</span>
      <input
        type="number"
        className="dd-input"
        value={value || ''}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
      />
    </label>
  )
}

function RadioRow<T extends string>({ value, onChange, options }: {
  value: T | null
  onChange: (v: T) => void
  options: { value: T; label: string }[]
}) {
  return (
    <div className="dd-radio-row">
      {options.map(opt => (
        <label
          key={opt.value}
          className={`dd-radio${value === opt.value ? ' dd-radio-active' : ''}`}
        >
          <input
            type="radio"
            checked={value === opt.value}
            onChange={() => onChange(opt.value)}
          />
          <span>{opt.label}</span>
        </label>
      ))}
    </div>
  )
}
