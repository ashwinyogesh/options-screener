// React-Flow graph builder hook.
//
// Consumes a ``SupplyChainData`` payload and produces ``{ nodes, edges }``
// arrays the parent component feeds to <ReactFlow>. The pure layout math
// lives in [layout.ts](./layout.ts); this hook composes that math with the
// JSX label/style helpers in [nodes.tsx](./nodes.tsx).
import { useMemo } from 'react'
import { MarkerType, type Edge, type Node } from '@xyflow/react'

import type { SupplyChainData } from '../../types/supplyChain'
import {
  COMP_HSPACE,
  CUSTOMER_X,
  FOCAL_X,
  LANE_HEADER_H,
  NODE_VSPACE,
  SUPPLIER_X,
  buildColumnLayout,
  competitorRow,
  edgeStrokeFor,
  focalY,
} from './layout'
import { nodeLabel, nodeStyle } from './nodes'

interface BuiltGraph {
  nodes: Node[]
  edges: Edge[]
  height: number
}

const EMPTY_GRAPH: BuiltGraph = { nodes: [], edges: [], height: 0 }

function makeNodes(d: SupplyChainData): BuiltGraph {
  const segments = d.segments ?? []
  const nodes: Node[] = []
  const edges: Edge[] = []

  const supLayout = buildColumnLayout(d.suppliers, segments)
  const cusLayout = buildColumnLayout(d.customers, segments)
  const useSegments = supLayout.useSegments

  // Focal node centred between the taller column.
  nodes.push({
    id: 'focal',
    position: { x: FOCAL_X, y: focalY(supLayout.totalH, cusLayout.totalH) },
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

  // Segment lane headers (only when multi-segment).
  if (useSegments) {
    const renderLaneHeader = (
      idPrefix: 'sup' | 'cus',
      x: number,
      lanes: typeof supLayout.laneInfo,
    ) => {
      for (const lane of lanes) {
        if (lane.segment === '__all__') continue
        nodes.push({
          id: `lane-${idPrefix}-${lane.segment}`,
          position: { x: x - 10, y: lane.yStart },
          data: {
            label: (
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: '#94a3b8',
                  letterSpacing: 0.5,
                  textTransform: 'uppercase',
                }}
              >
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
    renderLaneHeader('sup', SUPPLIER_X, supLayout.laneInfo)
    renderLaneHeader('cus', CUSTOMER_X, cusLayout.laneInfo)
  }

  // Suppliers — node id uses the ORIGINAL index in d.suppliers so the
  // click handler resolves the correct row even after segment grouping
  // reorders them.
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

  // Customers — same pattern.
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

  // Competitors — bottom horizontal row.
  const compRow = competitorRow(d, supLayout.totalH, cusLayout.totalH)
  d.competitors.forEach((c, i) => {
    const id = `comp-${i}`
    nodes.push({
      id,
      position: { x: compRow.startX + i * COMP_HSPACE, y: compRow.y },
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

  return { nodes, edges, height: compRow.y + 100 }
}

export function useSupplyChainGraph(data: SupplyChainData | null): BuiltGraph {
  return useMemo(() => (data ? makeNodes(data) : EMPTY_GRAPH), [data])
}
