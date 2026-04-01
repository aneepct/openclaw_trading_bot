import React from 'react';

const styles = {
  wrap: { marginBottom: '1.25rem', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '1rem' },
  titleRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' },
  title: { color: '#7dd3fc', fontSize: '0.95rem', letterSpacing: '0.08em', fontWeight: 700 },
  meta: { color: '#475569', fontSize: '0.7rem' },
  summary: { color: '#cbd5e1', fontSize: '0.8rem', lineHeight: 1.6, marginBottom: '0.75rem', whiteSpace: 'pre-line' },
  insight: { color: '#fbbf24', fontSize: '0.78rem', marginBottom: '0.85rem' },
  list: { display: 'grid', gap: '0.5rem' },
  item: { background: '#020617', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem' },
  market: { color: '#e2e8f0', fontSize: '0.78rem', marginBottom: '0.25rem' },
  action: { color: '#4ade80', fontWeight: 700, fontSize: '0.72rem', letterSpacing: '0.04em' },
  rationale: { color: '#94a3b8', fontSize: '0.74rem', marginTop: '0.35rem', lineHeight: 1.5 },
  badge: { color: '#38bdf8', fontSize: '0.68rem', marginLeft: '0.4rem' },
};

export default function AgentSummary({ agentSummary }) {
  if (!agentSummary) return null;

  return (
    <div style={styles.wrap}>
      <div style={styles.titleRow}>
        <div style={styles.title}>AGENT SUMMARY</div>
        <div style={styles.meta}>
          {agentSummary.enabled ? `${agentSummary.source?.toUpperCase()} · ${agentSummary.model}` : 'SCANNER FALLBACK'}
        </div>
      </div>

      <div style={styles.summary}>{agentSummary.summary}</div>
      {agentSummary.structural_insight && (
        <div style={styles.insight}>Structural insight: {agentSummary.structural_insight}</div>
      )}

      {!!agentSummary.trades?.length && (
        <div style={styles.list}>
          {agentSummary.trades.map((trade, index) => (
            <div key={index} style={styles.item}>
              <div style={styles.market}>{trade.market}</div>
              <div style={styles.action}>
                {trade.action}
                {trade.conviction ? ` · ${trade.conviction}` : ''}
                {trade.edge_pct != null && <span style={styles.badge}>{Number(trade.edge_pct).toFixed(1)}% edge</span>}
              </div>
              {trade.rationale && <div style={styles.rationale}>{trade.rationale}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
