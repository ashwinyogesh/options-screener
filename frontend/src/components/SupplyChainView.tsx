// Top-level shell for the supply-chain visualization tab.
//
// Layout, node-building, legend, metadata bar, and detail panel all live
// under [components/SupplyChain/](./SupplyChain/). This file owns the
// input form + ``selected``-row state + ReactFlow canvas wiring only.
import { useState } from 'react'
import { ReactFlow, Background, Controls, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { useSupplyChain } from '../hooks/useSupplyChain'
import type { CompanyNode } from '../types/supplyChain'
import { Legend } from './SupplyChain/Legend'
import { MetadataBar } from './SupplyChain/MetadataBar'
import { NodeDetailPanel } from './SupplyChain/NodeDetailPanel'
import { useSupplyChainGraph } from './SupplyChain/useSupplyChainGraph'

export function SupplyChainView() {
  const [ticker, setTicker] = useState('AAPL')
  const [enrichIndustry, setEnrichIndustry] = useState(true)
  const { data, loading, error, fetchTicker } = useSupplyChain()
  const [selected, setSelected] = useState<CompanyNode | null>(null)

  const { nodes, edges } = useSupplyChainGraph(data)

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
      <form
        onSubmit={handleSubmit}
        style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}
      >
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
        {data && <MetadataBar data={data} />}
      </form>

      {error && (
        <div
          style={{
            padding: '10px 14px',
            background: '#7f1d1d',
            color: '#fecaca',
            borderRadius: 6,
          }}
        >
          ⚠ {error}
        </div>
      )}

      {data?.summary && (
        <div
          style={{
            padding: '10px 14px',
            background: '#1e293b',
            borderRadius: 6,
            fontSize: 13,
            color: '#cbd5e1',
            borderLeft: '3px solid #fbbf24',
          }}
        >
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
        <div
          style={{
            display: 'flex',
            gap: 10,
            height: 'calc(100vh - 320px)',
            minHeight: 600,
          }}
        >
          <div
            style={{
              flex: 1,
              background: '#020617',
              borderRadius: 8,
              border: '1px solid #1e293b',
              position: 'relative',
            }}
          >
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
            <Legend />
          </div>

          {selected && (
            <NodeDetailPanel
              selected={selected}
              focalTicker={data.ticker}
              onClose={() => setSelected(null)}
            />
          )}
        </div>
      )}

      {!data && !loading && !error && (
        <div style={{ padding: 30, textAlign: 'center', color: '#64748b', fontSize: 14 }}>
          Enter a ticker and click <strong>Build Graph</strong>. Relationships are extracted from
          the latest 10-K filing using GPT-4.1, then optionally augmented with publicly-known
          industry partnerships — directionally correct, not financially precise.
        </div>
      )}
    </div>
  )
}
