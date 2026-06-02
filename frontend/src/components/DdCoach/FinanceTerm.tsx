/**
 * FinanceTerm — inline glossary component.
 *
 * Renders "term [ⓘ]" where the ⓘ opens a popover with:
 *   1. A plain-English definition
 *   2. Why this specific metric matters in THIS context
 *   3. What you'll see it called in the news (the vocabulary bridge)
 *
 * Usage:
 *   <FinanceTerm termKey="pe_ratio" />
 *   <FinanceTerm termKey="margin_of_safety" labelOverride="25% safety cushion" />
 */

import { useEffect, useRef, useState } from 'react'

// ---------------------------------------------------------------------------
// Term definitions
// ---------------------------------------------------------------------------

interface TermDef {
  label: string           // default display text (the "term" part)
  plain: string           // one-sentence plain-English definition
  whyHere: string         // why this number matters in THIS screen's context
  newsAlias: string       // "You'll see this called X in the news"
}

const FINANCE_TERMS: Record<string, TermDef> = {
  fcf_per_share: {
    label: 'earnings per share',
    plain:
      'This is your slice of the company\'s real cash profit — '
      + 'after the company pays all its bills, what\'s left per share you own.',
    whyHere:
      'We use this as the starting point for your valuation. '
      + 'A higher number means the company generates more cash for each share. '
      + 'If this is $5, the company earned $5 for every share outstanding last year.',
    newsAlias:
      'You\'ll see this called "EPS", "FCF per share", or "earnings per share" in headlines.',
  },
  pe_ratio: {
    label: 'P/E ratio',
    plain:
      'How many dollars investors currently pay for every $1 the company earns. '
      + 'A P/E of 20 means you\'re paying $20 for $1 of annual profit.',
    whyHere:
      'We use it as your "exit multiple" — the price-to-earnings ratio the stock '
      + 'might trade at in 5 years when you plan to sell. '
      + 'Lower is cheaper; higher means the market believes in faster future growth.',
    newsAlias:
      'You\'ll see this called "price-to-earnings", "P/E", or "earnings multiple" in news.',
  },
  growth_rate: {
    label: 'growth rate',
    plain:
      'How fast the company\'s earnings are growing each year, expressed as a percentage. '
      + '10%/yr means earnings double roughly every 7 years.',
    whyHere:
      'This is YOUR assumption — not a fact. You\'re deciding how fast you think the '
      + 'company will grow. Bear = pessimistic, Base = most likely, Bull = optimistic. '
      + 'The bigger your disagreement with the market, the more edge (or risk) you have.',
    newsAlias:
      'You\'ll see this called "earnings growth", "EPS CAGR", or "revenue growth" in analyst reports.',
  },
  required_return: {
    label: 'required return',
    plain:
      'The annual gain you demand before an investment is worth the risk to you. '
      + '12%/yr means you won\'t invest unless the stock can plausibly double in ~6 years.',
    whyHere:
      'This is your personal hurdle rate — it accounts for inflation, opportunity cost, '
      + 'and the fact that you could lose money. We use it to discount the future price '
      + 'back to what it\'s worth to you TODAY. Higher hurdle = more conservative fair value.',
    newsAlias:
      'You\'ll see this called "discount rate", "hurdle rate", or "cost of equity" in finance.',
  },
  margin_of_safety: {
    label: 'margin of safety',
    plain:
      'Your cushion for being wrong — the percentage below fair value you insist on paying. '
      + '25% margin of safety on a $100 fair value means you only buy at $75 or less.',
    whyHere:
      'Even good analysis is imperfect. Markets are noisy. This buffer protects you if '
      + 'your growth estimate turns out too optimistic. Without it, you need everything '
      + 'to go right just to break even.',
    newsAlias:
      'You\'ll see this called "margin of safety" (Ben Graham coined it) or '
      + '"discount to intrinsic value" in value investing writing.',
  },
  scenarios: {
    label: 'bear / base / bull',
    plain:
      'Three possible futures: Bear = things go worse than expected. '
      + 'Base = roughly what you expect. Bull = things go better than hoped.',
    whyHere:
      'No one can predict the future. By modeling all three, you see a range of fair values '
      + 'instead of a single number — and you buy only when even the BASE case gives you '
      + 'enough margin of safety.',
    newsAlias:
      'You\'ll see these called "downside/base/upside case" or "bear/bull scenarios" '
      + 'in analyst price targets.',
  },
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface FinanceTermProps {
  termKey: keyof typeof FINANCE_TERMS
  labelOverride?: string   // override the default display label
}

export function FinanceTerm({ termKey, labelOverride }: FinanceTermProps) {
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  const def = FINANCE_TERMS[termKey]
  if (!def) return null

  const displayLabel = labelOverride ?? def.label

  // Close when clicking outside
  useEffect(() => {
    if (!open) return
    function handleClickOutside(e: MouseEvent) {
      if (
        popoverRef.current
        && !popoverRef.current.contains(e.target as Node)
        && buttonRef.current
        && !buttonRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  return (
    <span className="ft-wrapper">
      <span className="ft-term">{displayLabel}</span>
      {' '}
      <button
        ref={buttonRef}
        type="button"
        className="ft-icon"
        aria-label={`What is ${displayLabel}?`}
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
      >
        ⓘ
      </button>
      {open && (
        <div ref={popoverRef} className="ft-popover" role="dialog" aria-label={`${displayLabel} definition`}>
          <button
            type="button"
            className="ft-popover-close"
            aria-label="Close"
            onClick={() => setOpen(false)}
          >
            ×
          </button>
          <p className="ft-popover-plain">{def.plain}</p>
          <p className="ft-popover-context">
            <strong>Why it matters here:</strong> {def.whyHere}
          </p>
          <p className="ft-popover-news">
            <em>{def.newsAlias}</em>
          </p>
        </div>
      )}
    </span>
  )
}
