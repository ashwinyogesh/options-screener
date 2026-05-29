/**
 * NarrativeReadingGuide — persistent collapsible banner above the ACS tables.
 *
 * Explains how to combine ACS (conviction strength) with lifecycle stage
 * (trajectory) when researching a ticker. Starts open; user can collapse it.
 */

export function NarrativeReadingGuide() {
  return (
    <details className="reading-guide" open>
      <summary className="reading-guide-summary">
        <span className="reading-guide-tagline">
          ACS is a snapshot — stage is a trajectory.
        </span>
        <span className="reading-guide-chevron">▾</span>
      </summary>

      <div className="reading-guide-body">
        <div className="reading-guide-tables">

          {/* ── Stage guide ─────────────────────────────────────────────── */}
          <div className="reading-guide-section">
            <p className="reading-guide-label">Stage first — are you early or late?</p>
            <table className="reading-guide-table">
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>Meaning</th>
                  <th>Options posture</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><span className="rg-stage rg-stage--1">1 — Early signal</span></td>
                  <td>Scattered mentions, not yet a formed narrative</td>
                  <td className="rg-posture rg-posture--watch">Small size · watch</td>
                </tr>
                <tr>
                  <td><span className="rg-stage rg-stage--2">2 — Forming</span></td>
                  <td>Recurring discussion with analytical backing</td>
                  <td className="rg-posture rg-posture--entry">Good entry window</td>
                </tr>
                <tr>
                  <td><span className="rg-stage rg-stage--3">3 — Growing</span></td>
                  <td>More people joining each week, peak score window</td>
                  <td className="rg-posture rg-posture--entry">Good entry window · premium elevated</td>
                </tr>
                <tr>
                  <td><span className="rg-stage rg-stage--5">5 — Crowded</span></td>
                  <td>Mainstream coverage, most upside priced in</td>
                  <td className="rg-posture rg-posture--skip">Skip</td>
                </tr>
                <tr>
                  <td><span className="rg-stage rg-stage--6">6 — Fading</span></td>
                  <td>Momentum declining, exit signals appearing</td>
                  <td className="rg-posture rg-posture--skip">Skip</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* ── ACS × Stage matrix ──────────────────────────────────────── */}
          <div className="reading-guide-section">
            <p className="reading-guide-label">Then ACS — how strong is the signal at that stage?</p>
            <table className="reading-guide-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Stage 2 (Forming)</th>
                  <th>Stage 3 (Growing)</th>
                  <th>Stage 5+</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>High ACS</strong></td>
                  <td className="rg-cell rg-cell--best">Best risk/reward. Thesis building fast with real conviction.</td>
                  <td className="rg-cell rg-cell--ok">Still a good entry window, but premium is elevated. Size accordingly.</td>
                  <td className="rg-cell rg-cell--trap">Trap — score still high from recent peak but momentum turning.</td>
                </tr>
                <tr>
                  <td><strong>Low ACS</strong></td>
                  <td className="rg-cell rg-cell--watch">Watch list — thesis forming, not yet coherent.</td>
                  <td className="rg-cell rg-cell--noise">Thesis peaked but thin — noise, not narrative.</td>
                  <td className="rg-cell rg-cell--skip">Skip.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Practical rule ──────────────────────────────────────────────── */}
        <div className="reading-guide-rule">
          <strong>Practical rule:</strong>
          <span className="rg-rule-item rg-rule--go">
            Stage 2 + ACS ≥ 50 + positive slope → research it seriously.
          </span>
          <span className="rg-rule-sep">·</span>
          <span className="rg-rule-item rg-rule--confirm">
            Stage 3 + ACS ≥ 65 → good entry, expect elevated premium.
          </span>
          <span className="rg-rule-sep">·</span>
          <span className="rg-rule-item rg-rule--avoid">
            Stage 5+ at any ACS → don't open new positions.
          </span>
        </div>
      </div>
    </details>
  )
}
