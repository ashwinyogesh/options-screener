import { useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { useSupplyChain } from '../hooks/useSupplyChain'
import type { CompanyNode, NodeSource, SupplyChainData } from '../types/supplyChain'

// ---------------------------------------------------------------- Layout ---
const FOCAL_X = 600
const FOCAL_Y_TOP = 60
const SUPPLIER_X = 100
const CUSTOMER_X = 1100
const NODE_VSPACE = 78
const COMP_HSPACE = 180
const LANE_GAP = 60       // vertical gap between segment lanes
const LANE_HEADER_H = 28  // segment label band height

// Source -> color/style
const SOURCE_COLORS: Record<NodeSource, string> = {
  '10-K': '#94a3b8',     // gray
  '8-K': '#60a5fa',      // blue
  industry: '#fbbf24',   // amber
}

function edgeStrokeFor(source: NodeSource): { stroke: string; dash?: string } {
  if (source === 'industry') return { stroke: '#fbbf24', dash: '5 4' }
  if (source === '8-K') return { stroke: '#60a5fa' }
  return { stroke: '#64748b' }
}

interface BuiltGraph {
  nodes: Node[]
  edges: Edge[]
  height: number
}

function groupBySegment(items: CompanyNode[], segments: string[]): Map<string, CompanyNode[]> {
  const map = new Map<string, CompanyNode[]>()
  for (const seg of segments) map.set(seg, [])
  map.set('Cross-segment', [])
  for (const it of items) {
    const key = it.segment && segments.includes(it.segment) ? it.segment : 'Cross-segment'
    map.get(key)!.push(it)
  }
  // Drop empty lanes
  for (const [k, v] of map) if (v.length === 0) map.delete(k)
  return map
}

function buildGraph(d: SupplyChainData | null, kind: 'sup' | 'cus', segments: string[]): {
  laneInfo: { segment: string; yStart: number; yEnd: number }[]
  laneCount: number
  totalH: number
  byLane: Map<string, CompanyNode[]>
} {
  if (!d) return { laneInfo: [], laneCount: 0, totalH: 0, byLane: new Map() }
  const list = kind === 'sup' ? d.suppliers : d.customers
  const useSegments = segments.length >= 2
  const byLane = useSegments
    ? groupBySegment(list, segments)
    : new Map([['__all__', list]])
  let y = FOCAL_Y_TOP + 80
  const laneInfo: { segment: string; yStart: number; yEnd: number }[] = []
  for (const [seg, items] of byLane) {
    const yStart = y
    const headerOffset = useSegments ? LANE_HEADER_H : 0
    const h = headerOffset + Math.max(items.length, 1) * NODE_VSPACE
    laneInfo.push({ segment: seg, yStart, yEnd: yStart + h })
    y += h + LANE_GAP
  }
  return { laneInfo, laneCount: byLane.size, totalH: y, byLane }
}

function makeNodes(d: SupplyChainData): BuiltGraph {
  const segments = d.segments ?? []
  const useSegments = segments.length >= 2
  const nodes: Node[] = []
  const edges: Edge[] = []

  const supLayout = buildGraph(d, 'sup', segments)
  const cusLayout = buildGraph(d, 'cus', segments)

  // Focal node centered between the taller of the two columns
  const maxLaneH = Math.max(supLayout.totalH, cusLayout.totalH, 300)
  const focalY = FOCAL_Y_TOP + maxLaneH / 2 - 40

  nodes.push({
    id: 'focal',
    position: { x: FOCAL_X, y: focalY },
    data: {
      label: (
        <div style={{ textAlign: 'center', padding: '6px 8px' }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#fbbf24' }}>{d.ticker}</div>
          <div style={{ fontSize: 11, color: '#cbd5e1', maxWidth: 160 }}>{d.company_name}</div>
        </div>
      ),
    },
    style: {
      background: '#1e293b',
      border: '2px solid #fbbf24',
      borderRadius: 8,
      width: 180,
    },
    draggable: false,
    selectable: false,
  })

  // Segment lane labels (only if multi-segment)
  if (useSegments) {
    for (const lane of supLayout.laneInfo) {
      if (lane.segment === '__all__') continue
      nodes.push({
        id: `lane-sup-${lane.segment}`,
        position: { x: SUPPLIER_X - 10, y: lane.yStart },
        data: {
          label: (
            <div style={{ fontSize: 10, fontWeight: 600, color: '#94a3b8', letterSpacing: 0.5, textTransform: 'uppercase' }}>
              {lane.segment === 'Cross-segment' ? 'Cross-segment' : lane.segment}
            </div>
          ),
        },
        style: {
          background: 'transparent',
          border: 'none',
          width: 200,
        },
        draggable: false,
        selectable: false,
      })
    }
    for (const lane of cusLayout.laneInfo) {
      if (lane.segment === '__all__') continue
      nodes.push({
        id: `lane-cus-${lane.segment}`,
        position: { x: CUSTOMER_X - 10, y: lane.yStart },
        data: {
          label: (
            <div style={{ fontSize: 10, fontWeight: 600, color: '#94a3b8', letterSpacing: 0.5, textTransform: 'uppercase' }}>
              {lane.segment === 'Cross-segment' ? 'Cross-segment' : lane.segment}
            </div>
          ),
        },
        style: { background: 'transparent', border: 'none', width: 200 },
        draggable: false,
        selectable: false,
      })
    }
  }

  // Suppliers — iterate lanes, place items vertically per lane.
  // Use the original index in d.suppliers for the node id so the click handler
  // resolves the correct row even after segment grouping reorders them.
  for (const lane of supLayout.laneInfo) {
    const items = supLayout.byLane.get(lane.segment) ?? []
    const headerOffset = useSegments ? LANE_HEADER_H : 0
    items.forEach((s, i) => {
      const origIdx = d.suppliers.indexOf(s)
      const id = `sup-${origIdx}`
      const y = lane.yStart + headerOffset + i * NODE_VSPACE
      nodes.push({
        id,
        position: { x: SUPPLIER_X, y },
        data: { label: nodeLabel(s, 'supplier') },
        style: nodeStyle('#60a5fa', s.source),
      })
      const stroke = edgeStrokeFor(s.source)
      edges.push({
        id: `e-${id}`,
        source: id,
        target: 'focal',
        animated: false,
        style: {
          stroke: stroke.stroke,
          strokeWidth: 1.2,
          ...(stroke.dash ? { strokeDasharray: stroke.dash } : {}),
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke.stroke },
      })
    })
  }

  // Customers — same pattern
  for (const lane of cusLayout.laneInfo) {
    const items = cusLayout.byLane.get(lane.segment) ?? []
    const headerOffset = useSegments ? LANE_HEADER_H : 0
    items.forEach((c, i) => {
      const origIdx = d.customers.indexOf(c)
      const id = `cus-${origIdx}`
      const y = lane.yStart + headerOffset + i * NODE_VSPACE
      nodes.push({
        id,
        position: { x: CUSTOMER_X, y },
        data: { label: nodeLabel(c, 'customer') },
        style: nodeStyle('#4ade80', c.source),
      })
      const stroke = edgeStrokeFor(c.source)
      edges.push({
        id: `e-${id}`,
        source: 'focal',
        target: id,
        animated: false,
        style: {
          stroke: stroke.stroke,
          strokeWidth: 1.2,
          ...(stroke.dash ? { strokeDasharray: stroke.dash } : {}),
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke.stroke },
      })
    })
  }

  // Competitors — bottom horizontal row
  const competitorY = Math.max(supLayout.totalH, cusLayout.totalH) + 40
  const compStartX =
    FOCAL_X - ((d.competitors.length - 1) * COMP_HSPACE) / 2 + 90 - 75
  d.competitors.forEach((c, i) => {
    const id = `comp-${i}`
    nodes.push({
      id,
      position: { x: compStartX + i * COMP_HSPACE, y: competitorY },
      data: { label: nodeLabel(c, 'competitor') },
      style: nodeStyle('#f87171', c.source),
    })
    const stroke = edgeStrokeFor(c.source)
    edges.push({
      id: `e-${id}`,
      source: 'focal',
      target: id,
      animated: false,
      style: {
        stroke: stroke.stroke === '#64748b' ? '#f87171' : stroke.stroke,
        strokeWidth: 1,
        strokeDasharray: stroke.dash ?? '4 4',
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: stroke.stroke === '#64748b' ? '#f87171' : stroke.stroke,
      },
    })
  })

  return { nodes, edges, height: competitorY + 100 }
}

function SourceBadge({ source }: { source: NodeSource }) {
  const color = SOURCE_COLORS[source]
  return (
    <span
      style={{
        display: 'inline-block',
        fontSize: 8,
        fontWeight: 700,
        padding: '1px 4px',
        borderRadius: 3,
        border: `1px solid ${color}`,
        color,
        marginLeft: 6,
        letterSpacing: 0.3,
        textTransform: 'uppercase',
        verticalAlign: 'middle',
      }}
      title={
        source === '10-K'
          ? 'Disclosed in 10-K filing'
          : source === '8-K'
            ? 'Disclosed in 8-K filing'
            : 'Inferred from public industry knowledge'
      }
    >
      {source === 'industry' ? 'inf' : source}
    </span>
  )
}

function nodeLabel(c: CompanyNode, kind: 'supplier' | 'customer' | 'competitor') {
  const pct = kind === 'supplier' ? c.cost_pct : kind === 'customer' ? c.revenue_pct : null
  return (
    <div style={{ padding: '4px 6px', textAlign: 'left' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#f1f5f9', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
        <span>
          {c.ticker ? `${c.ticker} ` : ''}
          <span style={{ fontWeight: 400, color: '#94a3b8' }}>{c.name}</span>
        </span>
        <SourceBadge source={c.source} />
      </div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
        {c.relationship}
        {pct != null && <span style={{ color: '#fbbf24' }}> · {pct.toFixed(1)}%</span>}
      </div>
    </div>
  )
}

function nodeStyle(borderColor: string, source: NodeSource) {
  // Inferred nodes get a dashed border to match dashed edges
  const isInferred = source === 'industry'
  return {
    background: '#0f172a',
    border: `1px ${isInferred ? 'dashed' : 'solid'} ${borderColor}`,
    borderRadius: 6,
    width: 200,
    padding: 0,
  }
}

// ------------------------------------------------------------- Component ---
export function SupplyChainView() {
  const [ticker, setTicker] = useState('AAPL')
  const [enrichIndustry, setEnrichIndustry] = useState(true)
  const { data, loading, error, fetchTicker } = useSupplyChain()
  const [selected, setSelected] = useState<CompanyNode | null>(null)

  const { nodes, edges } = useMemo(
    () => (data ? makeNodes(data) : { nodes: [] as Node[], edges: [] as Edge[], height: 0 }),
    [data],
  )

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (ticker.trim()) {
      setSelected(null)
      fetchTicker(ticker.trim().toUpperCase(), false, enrichIndustry)
    }
  }

  function handleNodeClick(_: unknown, node: Node) {
    if (node.id === 'focal' || node.id.startsWith('lane-') || !data) {
      setSelected(null)
      return
    }
    const [kind, idxStr] = node.id.split('-')
    const idx = parseInt(idxStr, 10)
    const list =
      kind === 'sup' ? data.suppliers : kind === 'cus' ? data.customers : data.competitors
    setSelected(list[idx] ?? null)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="text"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Ticker (e.g. AAPL)"
          className="chip-input"
          style={{ width: 160, padding: '8px 12px', fontSize: 14 }}
          disabled={loading}
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !ticker.trim()}>
          {loading ? 'Extracting…' : '🔗 Build Graph'}
        </button>
        {data && (
          <button
            type="button"
            className="btn"
            onClick={() => fetchTicker(ticker, true, enrichIndustry)}
            disabled={loading}
            style={{ background: '#334155', color: '#cbd5e1' }}
            title="Re-extract from filing (skip cache)"
          >
            ↻ Refresh
          </button>
        )}
        <label
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 12,
            color: '#cbd5e1',
            padding: '4px 10px',
            background: '#1e293b',
            borderRadius: 6,
            cursor: 'pointer',
          }}
          title="Augment filing-derived graph with publicly-known relationships using LLM industry knowledge"
        >
          <input
            type="checkbox"
            checked={enrichIndustry}
            onChange={(e) => setEnrichIndustry(e.target.checked)}
            disabled={loading}
          />
          Include industry knowledge
        </label>
        {data && (
          <span style={{ fontSize: 12, color: '#94a3b8' }}>
            {data.company_name} · 10-K filed {data.filing_date}
            {data.eight_k_count > 0 && (
              <span title={`8-K dates: ${data.eight_k_dates.join(', ')}`}> · +{data.eight_k_count} 8-K{data.eight_k_count > 1 ? 's' : ''}</span>
            )}
            {data.enrichment_used?.includes('industry') && (
              <span style={{ color: '#fbbf24' }}> · +industry</span>
            )}
            {data.cached && <span style={{ color: '#4ade80' }}> · cached</span>}
          </span>
        )}
      </form>

      {error && (
        <div style={{ padding: '10px 14px', background: '#7f1d1d', color: '#fecaca', borderRadius: 6 }}>
          ⚠ {error}
        </div>
      )}

      {data?.summary && (
        <div style={{ padding: '10px 14px', background: '#1e293b', borderRadius: 6, fontSize: 13, color: '#cbd5e1', borderLeft: '3px solid #fbbf24' }}>
          {data.summary}
        </div>
      )}

      {data?.concentration_note && (
        <div
          style={{
            padding: '8px 12px',
            background: '#0f172a',
            border: '1px dashed #475569',
            borderRadius: 6,
            fontSize: 12,
            color: '#94a3b8',
          }}
          title="Customer/supplier concentration disclosure from the 10-K"
        >
          <strong style={{ color: '#cbd5e1' }}>Concentration:</strong> {data.concentration_note}
        </div>
      )}

      {data && (
        <div style={{ display: 'flex', gap: 10, height: 'calc(100vh - 320px)', minHeight: 600 }}>
          <div style={{ flex: 1, background: '#020617', borderRadius: 8, border: '1px solid #1e293b', position: 'relative' }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodeClick={handleNodeClick}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.2}
              maxZoom={2}
            >
              <Background color="#1e293b" gap={20} />
              <Controls style={{ background: '#1e293b', border: '1px solid #334155' }} />
            </ReactFlow>

            {/* Legend */}
            <div
              style={{
                position: 'absolute',
                top: 10,
                right: 10,
                padding: '8px 10px',
                background: 'rgba(15, 23, 42, 0.92)',
                border: '1px solid #334155',
                borderRadius: 6,
                fontSize: 10,
                color: '#cbd5e1',
                lineHeight: 1.6,
                zIndex: 10,
                minWidth: 150,
              }}
            >
              <div style={{ fontWeight: 700, marginBottom: 4, color: '#f1f5f9' }}>Sources</div>
              <div><SourceBadge source="10-K" /> from 10-K filing</div>
              <div><SourceBadge source="8-K" /> from 8-K (recent)</div>
              <div><SourceBadge source="industry" /> inferred (LLM)</div>
              <div style={{ borderTop: '1px solid #334155', marginTop: 5, paddingTop: 4, fontSize: 9, color: '#64748b' }}>
                Solid edges = disclosed<br />
                Dashed edges = inferred
              </div>
            </div>
          </div>

          {selected && (
            <div style={{ width: 320, padding: 14, background: '#0f172a', borderRadius: 8, border: '1px solid #334155', overflowY: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                <div>
                  {selected.ticker && (
                    <div style={{ fontSize: 16, fontWeight: 700, color: '#fbbf24' }}>{selected.ticker}</div>
                  )}
                  <div style={{ fontSize: 13, color: '#cbd5e1' }}>{selected.name}</div>
                  <div style={{ marginTop: 4 }}>
                    <SourceBadge source={selected.source} />
                    {selected.segment && (
                      <span style={{ fontSize: 10, color: '#94a3b8', marginLeft: 6 }}>
                        · {selected.segment}
                      </span>
                    )}
                    {selected.confidence != null && (
                      <span style={{ fontSize: 10, color: '#fbbf24', marginLeft: 6 }}>
                        · conf {(selected.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => setSelected(null)}
                  style={{ background: 'transparent', color: '#94a3b8', border: 'none', cursor: 'pointer', fontSize: 18 }}
                  aria-label="Close"
                >
                  ×
                </button>
              </div>
              <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
                <strong style={{ color: '#cbd5e1' }}>Relationship:</strong> {selected.relationship}
              </div>
              {selected.revenue_pct != null && (
                <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                  <strong style={{ color: '#cbd5e1' }}>% of {data.ticker} revenue:</strong>{' '}
                  <span style={{ color: '#fbbf24' }}>{selected.revenue_pct.toFixed(1)}%</span>
                </div>
              )}
              {selected.cost_pct != null && (
                <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                  <strong style={{ color: '#cbd5e1' }}>% of {data.ticker} costs:</strong>{' '}
                  <span style={{ color: '#fbbf24' }}>{selected.cost_pct.toFixed(1)}%</span>
                </div>
              )}
              {selected.notes && (
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 10, lineHeight: 1.5 }}>
                  {selected.notes}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {!data && !loading && !error && (
        <div style={{ padding: 30, textAlign: 'center', color: '#64748b', fontSize: 14 }}>
          Enter a ticker and click <strong>Build Graph</strong>. Relationships are extracted from the latest 10-K filing using GPT-4.1, then optionally augmented with publicly-known industry partnerships — directionally correct, not financially precise.
        </div>
      )}
    </div>
  )
}
