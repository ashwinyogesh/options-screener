import { useCallback, useEffect, useMemo, useState } from 'react'
import { DataCardPanel } from './DataCardPanel'
import { useDdCoach } from '../../hooks/useDdCoach'
import type {
  Answers,
  DataCard,
  DDEntry,
  FilingLinks,
  MaturityDiscountInputs,
  MultipleBasedInputs,
  StomachAnswer,
  UserCall,
  ValuationMethod,
  ValuationOutput,
  ValuationRequest,
} from '../../types/ddCoach'

// ---------------------------------------------------------------------------
// Screen registry — labels are user-facing copy (no finance jargon).
// ---------------------------------------------------------------------------

const SCREENS = [
  'The Business',
  'What They Sell',
  'The Market',
  'The Moat',
  'The Numbers',
  'The Risks',
  'Why Now',
  'Decision',
] as const

type ScreenIdx = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7

// ---------------------------------------------------------------------------
// Helpers — local rules that mirror backend valuation_service.select_method.
// Used only to drive the UI; the backend remains authoritative for results.
// ---------------------------------------------------------------------------

function autoSelectMethod(card: DataCard | null): ValuationMethod {
  if (!card) return 'optionality'
  const fcfs = card.fcf_3yr.map(p => p.value).filter((v): v is number => v != null)
  const profitable3yr = fcfs.length >= 3 && fcfs.every(v => v > 0)
  if (profitable3yr) return 'multiple_based'
  const latestRev = card.revenue_3yr[card.revenue_3yr.length - 1]?.value ?? null
  const gms = card.growth_lens?.gross_margin_3yr.map(p => p.value).filter((v): v is number => v != null) ?? []
  const gmImproving = gms.length >= 2 && gms[gms.length - 1] > gms[0] + 0.02
  if (latestRev != null && latestRev > 50_000_000 && gmImproving) return 'maturity_discount'
  return 'optionality'
}

function methodHeadline(method: ValuationMethod): string {
  switch (method) {
    case 'multiple_based':
      return "We're valuing this like a mature business — it has been making real money for years."
    case 'maturity_discount':
      return "We're imagining this company grown up in a few years, then discounting back to today."
    case 'optionality':
      return "We can't put a number on this. Treat it like an option premium — only invest what you'd lose at a poker table."
  }
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
  const [valMethod, setValMethod] = useState<ValuationMethod>('optionality')
  const [valResult, setValResult] = useState<ValuationOutput | null>(null)
  const [valError, setValError] = useState<string | null>(null)
  const [mbInputs, setMbInputs] = useState<MultipleBasedInputs>({
    forward_eps: 0,
    target_pe_low: 0,
    target_pe_mid: 0,
    target_pe_high: 0,
  })
  const [mdInputs, setMdInputs] = useState<MaturityDiscountInputs>({
    revenue_bear: 0,
    revenue_base: 0,
    revenue_bull: 0,
    mature_multiple: 10,
    shares_outstanding_today: 0,
    years_to_maturity: 4,
    dilution_pct: 0.30,
    discount_rate: 0.12,
  })
  const [userCall, setUserCall] = useState<UserCall | null>(null)
  const [plannedDollars, setPlannedDollars] = useState<number>(0)
  const [stomach, setStomach] = useState<StomachAnswer | null>(null)
  const [finalDollars, setFinalDollars] = useState<number>(0)

  // ---- start a new entry ----
  const startEntry = useCallback(async () => {
    const t = ticker.trim().toUpperCase()
    if (!t) return
    setError(null)
    setCompleted(false)
    setEntry(null); setCard(null); setFilings(null)
    setValResult(null); setValError(null); setValMethod('optionality')
    setAnswers({}); setUserCall(null); setPlannedDollars(0); setStomach(null); setFinalDollars(0)
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
    if (cardRes.data) {
      setValMethod(autoSelectMethod(cardRes.data))
      if (cardRes.data.spot_price != null) {
        setMbInputs(p => ({ ...p, spot_price: cardRes.data!.spot_price ?? null }))
        setMdInputs(p => ({ ...p, spot_price: cardRes.data!.spot_price ?? null }))
      }
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

  const computeValuation = useCallback(async () => {
    if (!card) return
    setValError(null)
    const req: ValuationRequest = {
      method: valMethod,
      spot_price: card.spot_price ?? null,
    }
    if (valMethod === 'multiple_based') req.multiple_based = mbInputs
    if (valMethod === 'maturity_discount') req.maturity_discount = mdInputs
    const res = await coach.computeValuation(req)
    if (res.error) { setValError(res.error.detail); return }
    if (res.data) {
      setValResult(res.data)
      if (entry && activeTicker) {
        const r = await coach.patchEntry(entry.id, activeTicker, {
          valuation: {
            method: res.data.method,
            inputs: res.data.inputs_used,
            result: res.data.range,
          },
        })
        if (r.data) setEntry(r.data)
      }
    }
  }, [card, valMethod, mbInputs, mdInputs, coach, entry, activeTicker])

  const advance = useCallback(() => {
    setScreenIdx(i => (Math.min(i + 1, SCREENS.length - 1) as ScreenIdx))
  }, [])
  const back = useCallback(() => {
    setScreenIdx(i => (Math.max(i - 1, 0) as ScreenIdx))
  }, [])

  const finalize = useCallback(async () => {
    if (!entry || !activeTicker || !userCall || !stomach || finalDollars <= 0) {
      setError('Please pick a call, a stomach answer, and a final size before completing.')
      return
    }
    const patch = await coach.patchEntry(entry.id, activeTicker, {
      valuation: { user_call: userCall, reasoning: answers.q3_why_now ?? null },
      sizing: { planned_dollars: plannedDollars, stomach_answer: stomach, final_dollars: finalDollars },
    })
    if (patch.error) { setError(patch.error.detail); return }
    const done = await coach.completeEntry(entry.id, activeTicker)
    if (done.error) { setError(done.error.detail); return }
    if (done.data) { setEntry(done.data); setCompleted(true) }
  }, [entry, activeTicker, userCall, stomach, finalDollars, coach, answers.q3_why_now, plannedDollars])

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
          card={card}
          answers={answers}
          onAnswers={persistAnswers}
          valMethod={valMethod}
          valResult={valResult}
          valError={valError}
          mbInputs={mbInputs}
          setMbInputs={setMbInputs}
          mdInputs={mdInputs}
          setMdInputs={setMdInputs}
          onComputeValuation={computeValuation}
          userCall={userCall}
          setUserCall={setUserCall}
          plannedDollars={plannedDollars}
          setPlannedDollars={setPlannedDollars}
          stomach={stomach}
          setStomach={setStomach}
          finalDollars={finalDollars}
          setFinalDollars={setFinalDollars}
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
  card: DataCard
  answers: Answers
  onAnswers: (next: Answers) => Promise<void> | void
  valMethod: ValuationMethod
  valResult: ValuationOutput | null
  valError: string | null
  mbInputs: MultipleBasedInputs
  setMbInputs: React.Dispatch<React.SetStateAction<MultipleBasedInputs>>
  mdInputs: MaturityDiscountInputs
  setMdInputs: React.Dispatch<React.SetStateAction<MaturityDiscountInputs>>
  onComputeValuation: () => Promise<void> | void
  userCall: UserCall | null
  setUserCall: (v: UserCall) => void
  plannedDollars: number
  setPlannedDollars: (n: number) => void
  stomach: StomachAnswer | null
  setStomach: (v: StomachAnswer) => void
  finalDollars: number
  setFinalDollars: (n: number) => void
}

function ScreenBody(p: ScreenBodyProps) {
  switch (p.idx) {
    case 0: return <Screen1 card={p.card} q1={p.answers.q1_business ?? ''} setQ1={v => p.onAnswers({ q1_business: v })} />
    case 1: return <SimpleTextScreen
      heading="What They Sell"
      prompt="How does this company actually make money? Name the top product or service, and who pays for it."
      example="They rent GPU compute by the hour to AI startups. About 40% of revenue comes from one customer."
      value={p.answers.q2_revenue_model ?? ''}
      onChange={v => p.onAnswers({ q2_revenue_model: v })}
    />
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
    case 4: return <Screen5Numbers
      card={p.card}
      method={p.valMethod}
      result={p.valResult}
      error={p.valError}
      mbInputs={p.mbInputs}
      setMbInputs={p.setMbInputs}
      mdInputs={p.mdInputs}
      setMdInputs={p.setMdInputs}
      onCompute={p.onComputeValuation}
    />
    case 5: return <Screen6Risks
      value={p.answers.q4_risks ?? ''}
      onChange={v => p.onAnswers({ q4_risks: v })}
    />
    case 6: return <SimpleTextScreen
      heading="Why Now"
      prompt="What has to happen in the next 12 months for this to work? If you can't name a catalyst, it isn't 'now' — it's 'maybe someday.'"
      example="The next earnings call should show their AI cloud business crossing $1B annual run-rate. If it does, the stock re-rates."
      value={p.answers.q3_why_now ?? ''}
      onChange={v => p.onAnswers({ q3_why_now: v })}
    />
    case 7: return <Screen8Decision
      card={p.card}
      result={p.valResult}
      userCall={p.userCall}
      setUserCall={p.setUserCall}
      plannedDollars={p.plannedDollars}
      setPlannedDollars={p.setPlannedDollars}
      stomach={p.stomach}
      setStomach={p.setStomach}
      finalDollars={p.finalDollars}
      setFinalDollars={p.setFinalDollars}
    />
  }
}

// ---- Screen 1 ----

function Screen1({ card, q1, setQ1 }: { card: DataCard; q1: string; setQ1: (v: string) => void }) {
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">The Business</h3>
      <p className="dd-screen-prompt">
        Read the snapshot below. Then write one or two sentences in your own
        words about what this company does. If you can't explain it to a friend
        without jargon, you don't understand it well enough yet.
      </p>
      <DataCardPanel card={card} />
      <Textarea
        value={q1}
        onChange={setQ1}
        placeholder="In your own words: what does this company do?"
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

// ---- Screen 5 — Valuation ----

function Screen5Numbers({
  card, method, result, error,
  mbInputs, setMbInputs, mdInputs, setMdInputs, onCompute,
}: {
  card: DataCard
  method: ValuationMethod
  result: ValuationOutput | null
  error: string | null
  mbInputs: MultipleBasedInputs
  setMbInputs: React.Dispatch<React.SetStateAction<MultipleBasedInputs>>
  mdInputs: MaturityDiscountInputs
  setMdInputs: React.Dispatch<React.SetStateAction<MaturityDiscountInputs>>
  onCompute: () => Promise<void> | void
}) {
  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">The Numbers</h3>
      <p className="dd-screen-prompt">{methodHeadline(method)}</p>

      {method === 'multiple_based' && (
        <div className="dd-form">
          <NumberField
            label="Earnings per share you expect next year"
            value={mbInputs.forward_eps}
            onChange={v => setMbInputs(p => ({ ...p, forward_eps: v }))}
          />
          <NumberField
            label="Pessimistic earnings multiple"
            value={mbInputs.target_pe_low}
            onChange={v => setMbInputs(p => ({ ...p, target_pe_low: v }))}
          />
          <NumberField
            label="Reasonable earnings multiple"
            value={mbInputs.target_pe_mid}
            onChange={v => setMbInputs(p => ({ ...p, target_pe_mid: v }))}
          />
          <NumberField
            label="Optimistic earnings multiple"
            value={mbInputs.target_pe_high}
            onChange={v => setMbInputs(p => ({ ...p, target_pe_high: v }))}
          />
        </div>
      )}

      {method === 'maturity_discount' && (
        <div className="dd-form">
          <NumberField
            label="Revenue in 4 years — pessimistic ($)"
            value={mdInputs.revenue_bear}
            onChange={v => setMdInputs(p => ({ ...p, revenue_bear: v }))}
          />
          <NumberField
            label="Revenue in 4 years — base case ($)"
            value={mdInputs.revenue_base}
            onChange={v => setMdInputs(p => ({ ...p, revenue_base: v }))}
          />
          <NumberField
            label="Revenue in 4 years — optimistic ($)"
            value={mdInputs.revenue_bull}
            onChange={v => setMdInputs(p => ({ ...p, revenue_bull: v }))}
          />
          <NumberField
            label="Mature sales multiple (e.g. 10)"
            value={mdInputs.mature_multiple}
            onChange={v => setMdInputs(p => ({ ...p, mature_multiple: v }))}
          />
          <NumberField
            label="Shares outstanding today"
            value={mdInputs.shares_outstanding_today}
            onChange={v => setMdInputs(p => ({ ...p, shares_outstanding_today: v }))}
          />
        </div>
      )}

      {method === 'optionality' && (
        <div className="dd-callout">
          This is a speculative bet — there's no honest way to put a fair-value
          number on it. Decide a position size you can lose without losing
          sleep, and move on to the next screen.
        </div>
      )}

      <div className="dd-form-actions">
        <button
          className="btn-primary"
          onClick={() => void onCompute()}
          disabled={method === 'optionality'}
        >
          Compute fair value
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <div className="dd-result">
          <h4>Fair value range (per share)</h4>
          <div className="dd-result-grid">
            <ResultCell label="Pessimistic" value={result.range.bear} />
            <ResultCell label="Base case" value={result.range.base} />
            <ResultCell label="Optimistic" value={result.range.bull} />
            <ResultCell label="Today's price" value={card.spot_price ?? result.range.spot} />
          </div>
          <p className="dd-result-rationale">{result.rationale}</p>
        </div>
      )}
    </div>
  )
}

function ResultCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="dd-result-cell">
      <div className="dd-result-label">{label}</div>
      <div className="dd-result-value">{value == null ? '—' : `$${value.toFixed(2)}`}</div>
    </div>
  )
}

// ---- Screen 6 — Risks + filings links ----

function Screen6Risks({ value, onChange }: {
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
  { item: 'Item 7',  section: 'MD&A',               look_for: 'Revenue bridge, margin commentary, liquidity',                minutes: '10 min' },
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

// ---- Screen 8 — Decision ----

function Screen8Decision({
  card, result, userCall, setUserCall,
  plannedDollars, setPlannedDollars, stomach, setStomach,
  finalDollars, setFinalDollars,
}: {
  card: DataCard
  result: ValuationOutput | null
  userCall: UserCall | null
  setUserCall: (v: UserCall) => void
  plannedDollars: number
  setPlannedDollars: (n: number) => void
  stomach: StomachAnswer | null
  setStomach: (v: StomachAnswer) => void
  finalDollars: number
  setFinalDollars: (n: number) => void
}) {
  const spot = card.spot_price
  const base = result?.range.base ?? null
  const calculatedCall: UserCall | null = useMemo(() => {
    if (spot == null || base == null) return null
    if (spot < base * 0.7) return 'cheap'
    if (spot < base * 1.1) return 'fair'
    return 'expensive_worth_it'
  }, [spot, base])

  useEffect(() => {
    if (userCall == null && calculatedCall != null) setUserCall(calculatedCall)
  }, [userCall, calculatedCall, setUserCall])

  return (
    <div className="dd-screen">
      <h3 className="dd-screen-heading">Decision</h3>

      <div className="dd-section">
        <h4>1. What's your call?</h4>
        <RadioRow<UserCall>
          value={userCall}
          onChange={setUserCall}
          options={[
            { value: 'cheap', label: 'Cheap — there\'s a margin of safety' },
            { value: 'fair', label: 'Fairly priced — pay full price for a good business' },
            { value: 'expensive_worth_it', label: 'Expensive but worth it — paying up for quality' },
            { value: 'cannot_value', label: 'Can\'t put a number on it — pure speculation' },
          ]}
        />
      </div>

      <div className="dd-section">
        <h4>2. How much were you planning to put in?</h4>
        <NumberField
          label="Planned dollars"
          value={plannedDollars}
          onChange={setPlannedDollars}
        />
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
            { value: 'yes', label: 'Yes — I\'d add more' },
            { value: 'unsure', label: 'I\'d hold but it would hurt' },
            { value: 'no', label: 'No — I\'d panic-sell' },
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
    </div>
  )
}

// ---- Completed summary ----

function CompletedSummary({ entry, card }: { entry: DDEntry | null; card: DataCard }) {
  if (!entry) return null
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
  return (
    <textarea
      className="dd-textarea"
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={e => onChange(e.target.value)}
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
