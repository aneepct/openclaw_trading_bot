import React from 'react';

const styles = {
  wrap: { marginBottom: '1.25rem' },
  titleRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' },
  title: { color: '#7dd3fc', fontSize: '0.95rem', letterSpacing: '0.08em', fontWeight: 700 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem' },
  card: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '1rem' },
  providerTitle: { color: '#e2e8f0', fontSize: '0.9rem', fontWeight: 700, letterSpacing: '0.06em' },
  meta: { color: '#475569', fontSize: '0.7rem' },
  status: { color: '#38bdf8', fontSize: '0.68rem', marginTop: '0.4rem', marginBottom: '0.75rem' },
  summary: { color: '#cbd5e1', fontSize: '0.8rem', lineHeight: 1.6, marginBottom: '0.75rem', whiteSpace: 'pre-line' },
  insight: { color: '#fbbf24', fontSize: '0.78rem', marginBottom: '0.85rem' },
  list: { display: 'grid', gap: '0.5rem' },
  item: { background: '#020617', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem' },
  market: { color: '#e2e8f0', fontSize: '0.78rem', marginBottom: '0.25rem' },
  action: { fontWeight: 700, fontSize: '0.72rem', letterSpacing: '0.04em' },
  buy: { color: '#4ade80' },
  sell: { color: '#f87171' },
  hold: { color: '#fbbf24' },
  rationale: { color: '#94a3b8', fontSize: '0.74rem', marginTop: '0.35rem', lineHeight: 1.5 },
  badge: { color: '#38bdf8', fontSize: '0.68rem', marginLeft: '0.4rem' },
  emptyNote: { color: '#64748b', fontSize: '0.78rem', lineHeight: 1.5 },
};

function getActionStyle(action) {
  const value = String(action || '').toUpperCase();
  if (value.includes('NO') || value.includes('SELL')) return styles.sell;
  if (value.includes('HOLD')) return styles.hold;
  return styles.buy;
}

function ProviderCard({ name, data }) {
  const label = name.toUpperCase();
  const hasTrades = !!data?.trades?.length;

  return (
    <div style={styles.card}>
      <div style={styles.titleRow}>
        <div style={styles.providerTitle}>{label}</div>
        <div style={styles.meta}>{data?.model || 'Not configured'}</div>
      </div>
      <div style={styles.status}>
        {data?.enabled ? 'Live provider response' : (data?.status || 'Unavailable').replace(/_/g, ' ')}
      </div>
      <div style={styles.summary}>{data?.summary || `${label} has no response yet.`}</div>
      {data?.structural_insight && (
        <div style={styles.insight}>Key view: {data.structural_insight}</div>
      )}
      {hasTrades ? (
        <div style={styles.list}>
          {data.trades.map((trade, index) => (
            <div key={index} style={styles.item}>
              <div style={styles.market}>{trade.market}</div>
              <div style={{ ...styles.action, ...getActionStyle(trade.action) }}>
                {trade.action}
                {trade.conviction ? ` · ${trade.conviction}` : ''}
                {trade.edge_pct != null && <span style={styles.badge}>{Number(trade.edge_pct).toFixed(1)}% edge</span>}
              </div>
              {trade.rationale && <div style={styles.rationale}>{trade.rationale}</div>}
            </div>
          ))}
        </div>
      ) : (
        <div style={styles.emptyNote}>
          {data?.enabled
            ? `${label} did not return any trade ideas for the current set.`
            : `${label} output will appear here when this provider is configured and valid signals exist.`}
        </div>
      )}
    </div>
  );
}

export default function AgentSummary({ agentSummary }) {
  if (!agentSummary) return null;
  const providers = agentSummary.providers || {
    openai: {
      enabled: agentSummary.enabled,
      model: agentSummary.model,
      summary: agentSummary.summary,
      structural_insight: agentSummary.structural_insight,
      trades: agentSummary.trades,
      status: agentSummary.enabled ? 'ok' : 'unavailable',
    },
  };
  const orderedProviders = ['openai', 'grok', 'gemini'].filter(name => providers[name]);

  return (
    <div style={styles.wrap}>
      <div style={styles.titleRow}>
        <div style={styles.title}>AI PROVIDER COMPARISON</div>
        <div style={styles.meta}>OpenAI, Grok, Gemini</div>
      </div>
      <div style={styles.grid}>
        {orderedProviders.map(name => (
          <ProviderCard key={name} name={name} data={providers[name]} />
        ))}
      </div>
    </div>
  );
}
