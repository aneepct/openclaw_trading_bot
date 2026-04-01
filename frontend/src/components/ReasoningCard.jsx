import React, { useState } from 'react';

const styles = {
  container: { marginBottom: '2rem' },
  title: { fontSize: '1.1rem', color: '#7dd3fc', marginBottom: '1rem', letterSpacing: '0.1em' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: '1rem' },
  card: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '1rem', cursor: 'pointer' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' },
  badgeRow: { display: 'flex', gap: '6px', alignItems: 'center' },
  badge:     { padding: '2px 8px', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 700 },
  buyBadge:  { background: '#14532d', color: '#4ade80' },
  sellBadge: { background: '#450a0a', color: '#f87171' },
  question: { color: '#e2e8f0', fontSize: '0.84rem', marginBottom: '0.75rem', lineHeight: 1.5, fontWeight: 600 },
  stats: { display: 'flex', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.75rem' },
  stat: { textAlign: 'center', minWidth: '60px' },
  statLabel: { color: '#475569', fontSize: '0.6rem', letterSpacing: '0.05em' },
  statValue: { color: '#e2e8f0', fontSize: '0.85rem', fontWeight: 700 },
  edgeValue: { color: '#fbbf24', fontSize: '0.85rem', fontWeight: 700 },
  payoutValue: { color: '#34d399', fontSize: '0.85rem', fontWeight: 700 },
  infoRow: { display: 'flex', gap: '1rem', marginBottom: '0.75rem', fontSize: '0.72rem', color: '#94a3b8', flexWrap: 'wrap' },
  reasoning: { color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.6, borderTop: '1px solid #1e293b', paddingTop: '0.75rem', whiteSpace: 'pre-line' },
  reasoningFull: { color: '#cbd5e1' },
  aiGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem', marginTop: '0.75rem' },
  agentBox: { background: '#020617', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem' },
  agentTitle: { color: '#7dd3fc', fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em', marginBottom: '0.35rem' },
  agentMeta: { color: '#38bdf8', fontSize: '0.7rem', marginBottom: '0.35rem' },
  agentAction: { fontSize: '0.72rem', fontWeight: 700 },
  buyText: { color: '#4ade80' },
  sellText: { color: '#f87171' },
  holdText: { color: '#fbbf24' },
  agentText: { color: '#94a3b8', fontSize: '0.72rem', lineHeight: 1.5 },
  empty: { color: '#475569', padding: '2rem', textAlign: 'center' },
};

function getActionStyle(action) {
  const value = String(action || '').toUpperCase();
  if (value.includes('NO') || value.includes('SELL')) return styles.sellText;
  if (value.includes('HOLD')) return styles.holdText;
  return styles.buyText;
}

function ProviderTake({ label, analysis }) {
  if (!analysis) return null;

  return (
    <div style={styles.agentBox}>
      <div style={styles.agentTitle}>{label}</div>
      <div style={styles.agentMeta}>
        <span style={{ ...styles.agentAction, ...getActionStyle(analysis.action) }}>
          {analysis.action}
        </span>
        {analysis.conviction ? ` · ${analysis.conviction}` : ''}
        {analysis.fair_value_pct != null ? ` · ${Number(analysis.fair_value_pct).toFixed(1)}%` : ''}
      </div>
      {analysis.rationale && <div style={styles.agentText}>{analysis.rationale}</div>}
      {analysis.risk && <div style={{ ...styles.agentText, marginTop: '0.35rem', color: '#64748b' }}>Risk: {analysis.risk}</div>}
    </div>
  );
}

function SignalCard({ signal: s }) {
  const [expanded, setExpanded] = useState(false);
  const isBuy = s.direction === 'BUY';
  const providerAnalyses = s.provider_analyses || {};
  const leadRationale = providerAnalyses.openai?.rationale
    || providerAnalyses.grok?.rationale
    || s.structural_insight
    || s.reasoning;

  return (
    <div style={styles.card} onClick={() => setExpanded(e => !e)}>
      <div style={styles.header}>
        <div />
        <div style={styles.badgeRow}>
          <span style={{ ...styles.badge, ...(isBuy ? styles.buyBadge : styles.sellBadge) }}>
            {s.recommended_action || s.direction}
          </span>
        </div>
      </div>

      <div style={styles.question}>{s.polymarket_question}</div>

      <div style={styles.infoRow}>
        <span>Expiry: {s.instrument_t1_expiry ? new Date(s.instrument_t1_expiry).toLocaleDateString() : '—'}</span>
        <span>Conviction: {s.conviction || '—'}</span>
        <span>
          {s.spot_price && s.strike ? `$${Number(s.spot_price).toLocaleString()} spot / $${Number(s.strike).toLocaleString()} strike` : '—'}
        </span>
      </div>

      <div style={styles.stats}>
        <div style={styles.stat}>
          <div style={styles.statLabel}>FAIR VALUE</div>
          <div style={styles.statValue}>{(s.deribit_prob * 100).toFixed(2)}%</div>
        </div>
        <div style={styles.stat}>
          <div style={styles.statLabel}>MARKET PRICE</div>
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
      </div>

      <div style={{ ...styles.reasoning, ...(expanded ? styles.reasoningFull : {}) }}>
        {expanded ? leadRationale : (leadRationale || '').split('\n').slice(0, 2).join(' ')}
        {!expanded && <span style={{ color: '#334155' }}> [click to expand]</span>}
      </div>

      {(providerAnalyses.openai || providerAnalyses.grok || providerAnalyses.gemini) && (
        <div style={styles.aiGrid}>
          <ProviderTake label="OPENAI TAKE" analysis={providerAnalyses.openai} />
          <ProviderTake label="GROK TAKE" analysis={providerAnalyses.grok} />
          <ProviderTake label="GEMINI TAKE" analysis={providerAnalyses.gemini} />
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
        <div style={styles.title}>AI REVIEWS</div>
        <div style={styles.empty}>
          No current opportunities were approved for AI review.
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>AI REVIEWS</div>
      <div style={styles.grid}>
        {alphaSignals.map((s, i) => <SignalCard key={i} signal={s} />)}
      </div>
    </div>
  );
}
