import React, { useState } from 'react';

const styles = {
  container: { marginBottom: '2rem' },
  title: { fontSize: '1.1rem', color: '#7dd3fc', marginBottom: '1rem', letterSpacing: '0.1em' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: '1rem' },
  card: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '1rem', cursor: 'pointer' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' },
  instruments: { fontFamily: 'monospace', fontSize: '0.75rem' },
  t1: { color: '#7dd3fc', fontWeight: 700 },
  t2: { color: '#818cf8', fontWeight: 700 },
  arrow: { color: '#475569' },
  badgeRow: { display: 'flex', gap: '6px', alignItems: 'center' },
  badge:     { padding: '2px 8px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 700 },
  buyBadge:  { background: '#14532d', color: '#4ade80' },
  sellBadge: { background: '#450a0a', color: '#f87171' },
  callBadge: { background: '#1d4ed8', color: '#bfdbfe', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 700 },
  putBadge:  { background: '#7c2d12', color: '#fdba74', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 700 },
  question: { color: '#94a3b8', fontSize: '0.75rem', marginBottom: '0.75rem', lineHeight: 1.4 },
  stats: { display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.75rem' },
  stat: { textAlign: 'center', minWidth: '60px' },
  statLabel: { color: '#475569', fontSize: '0.6rem', letterSpacing: '0.05em' },
  statValue: { color: '#e2e8f0', fontSize: '0.85rem', fontWeight: 700 },
  edgeValue: { color: '#fbbf24', fontSize: '0.85rem', fontWeight: 700 },
  payoutValue: { color: '#34d399', fontSize: '0.85rem', fontWeight: 700 },
  interpRow: { display: 'flex', gap: '1rem', marginBottom: '0.5rem', fontSize: '0.7rem', color: '#475569' },
  interpLabel: { color: '#334155' },
  interpValue: { color: '#64748b' },
  interpNotice: { marginBottom: '0.75rem', fontSize: '0.72rem', padding: '0.5rem 0.75rem', borderRadius: '6px' },
  interpNoticeHigh: { background: '#052e16', color: '#86efac', border: '1px solid #166534' },
  interpNoticeReduced: { background: '#451a03', color: '#fcd34d', border: '1px solid #92400e' },
  reasoning: { color: '#64748b', fontSize: '0.72rem', lineHeight: 1.5, borderTop: '1px solid #1e293b', paddingTop: '0.5rem', whiteSpace: 'pre-line' },
  reasoningFull: { color: '#94a3b8' },
  agentBox: { marginTop: '0.75rem', background: '#020617', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem' },
  agentTitle: { color: '#7dd3fc', fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', marginBottom: '0.35rem' },
  agentMeta: { color: '#38bdf8', fontSize: '0.7rem', marginBottom: '0.35rem' },
  agentText: { color: '#94a3b8', fontSize: '0.72rem', lineHeight: 1.5 },
  empty: { color: '#475569', padding: '2rem', textAlign: 'center' },
};

function SignalCard({ signal: s }) {
  const [expanded, setExpanded] = useState(false);
  const isBuy = s.direction === 'BUY';
  const isPut = s.option_type === 'P';
  const noticeStyle = s.interp_confidence === 'high' ? styles.interpNoticeHigh : styles.interpNoticeReduced;
  const noticeText = s.interp_note || (s.interp_confidence === 'high'
    ? 'Two-expiry interpolation available.'
    : 'Single-expiry fallback used.');

  return (
    <div style={styles.card} onClick={() => setExpanded(e => !e)}>

      {/* Header: T1/T2 instruments + badges */}
      <div style={styles.header}>
        <div style={styles.instruments}>
          <div><span style={styles.t1}>T1: {s.instrument_t1 || 'N/A'}</span></div>
          <div><span style={styles.arrow}>↕ </span><span style={styles.t2}>T2: {s.instrument_t2 || 'N/A'}</span></div>
        </div>
        <div style={styles.badgeRow}>
          {/* CALL/PUT badge */}
          <span style={isPut ? styles.putBadge : styles.callBadge}>
            {isPut ? 'PUT ↓' : 'CALL ↑'}
          </span>
          {/* BUY/SELL badge */}
          <span style={{ ...styles.badge, ...(isBuy ? styles.buyBadge : styles.sellBadge) }}>
            {s.direction}
          </span>
        </div>
      </div>

      {/* Polymarket question */}
      <div style={styles.question}>{s.polymarket_question}</div>

      <div style={{ ...styles.interpNotice, ...noticeStyle }}>
        {noticeText}
      </div>

      {/* Interpolation info */}
      <div style={styles.interpRow}>
        <span><span style={styles.interpLabel}>method: </span>{s.interp_method || '—'}</span>
        <span><span style={styles.interpLabel}>w: </span>{s.interp_weight_w?.toFixed(3) ?? '—'}</span>
        <span><span style={styles.interpLabel}>σ_interp: </span>{s.sigma_interp ? (s.sigma_interp * 100).toFixed(1) + '%' : '—'}</span>
        <span><span style={styles.interpLabel}>t_poly: </span>{s.t_poly_days?.toFixed(1)}d</span>
      </div>

      {/* Stats */}
      <div style={styles.stats}>
        <div style={styles.stat}>
          <div style={styles.statLabel}>DERIBIT PROB</div>
          <div style={styles.statValue}>{(s.deribit_prob * 100).toFixed(2)}%</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>POLY PRICE</div>
          <div style={styles.statValue}>{(s.polymarket_price * 100).toFixed(2)}%</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>EDGE</div>
          <div style={styles.edgeValue}>{s.abs_edge_pct?.toFixed(1)}%</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>PAYOUT</div>
          <div style={styles.payoutValue}>{s.payout_ratio ? `${s.payout_ratio}x` : '—'}</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>LIQUIDITY</div>
          <div style={styles.statValue}>${s.liquidity_usd ? s.liquidity_usd.toLocaleString() : '—'}</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>OPTION</div>
          <div style={{ ...styles.statValue, color: isPut ? '#fdba74' : '#bfdbfe' }}>
            {isPut ? 'PUT' : 'CALL'}
          </div>
        </div>
      </div>

      {/* Reasoning */}
      <div style={{ ...styles.reasoning, ...(expanded ? styles.reasoningFull : {}) }}>
        {expanded ? s.reasoning : s.reasoning?.split('\n').slice(2).join(' ')}
        {!expanded && <span style={{ color: '#334155' }}> [click to expand]</span>}
      </div>

      {s.agent_analysis && (
        <div style={styles.agentBox}>
          <div style={styles.agentTitle}>AGENT TAKE</div>
          <div style={styles.agentMeta}>
            {s.agent_analysis.action}
            {s.agent_analysis.bias ? ` · ${s.agent_analysis.bias}` : ''}
            {s.agent_analysis.conviction ? ` · ${s.agent_analysis.conviction}` : ''}
            {s.agent_analysis.trade_type ? ` · ${s.agent_analysis.trade_type}` : ''}
          </div>
          {s.agent_analysis.rationale && <div style={styles.agentText}>{s.agent_analysis.rationale}</div>}
          {s.agent_analysis.risk && <div style={{ ...styles.agentText, marginTop: '0.35rem', color: '#64748b' }}>Risk: {s.agent_analysis.risk}</div>}
        </div>
      )}
    </div>
  );
}

export default function ReasoningCards({ signals }) {
  const alphaSignals = (signals || []).filter(s => s.has_alpha);

  if (alphaSignals.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.title}>REASONING CARDS</div>
        <div style={styles.empty}>No signals available yet.</div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>REASONING CARDS</div>
      <div style={styles.grid}>
        {alphaSignals.map((s, i) => <SignalCard key={i} signal={s} />)}
      </div>
    </div>
  );
}
