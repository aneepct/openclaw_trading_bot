import React from 'react';

const styles = {
  container: { marginBottom: '2rem' },
  title: { fontSize: '1.1rem', color: '#7dd3fc', marginBottom: '1rem', letterSpacing: '0.1em' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' },
  th: { padding: '8px 12px', textAlign: 'left', background: '#1e293b', color: '#94a3b8', borderBottom: '1px solid #334155', fontWeight: 600, letterSpacing: '0.05em' },
  td: { padding: '8px 12px', borderBottom: '1px solid #1e293b', verticalAlign: 'top' },
  buy:     { color: '#4ade80', fontWeight: 700 },
  sell:    { color: '#f87171', fontWeight: 700 },
  edgePos: { color: '#4ade80', fontWeight: 700 },
  edgeNeg: { color: '#f87171', fontWeight: 700 },
  market: { color: '#e2e8f0', fontSize: '0.78rem', lineHeight: 1.5, fontWeight: 600 },
  meta: { color: '#64748b', fontSize: '0.68rem', marginTop: '4px', lineHeight: 1.4 },
  prob:       { color: '#c084fc' },
  providerCell: { minWidth: '180px' },
  providerAction: { fontWeight: 700, fontSize: '0.72rem' },
  providerProb: { color: '#c084fc', fontSize: '0.82rem', fontWeight: 700, marginTop: '4px' },
  providerText: { color: '#94a3b8', fontSize: '0.68rem', marginTop: '4px', lineHeight: 1.4 },
  reasoning:  { color: '#94a3b8', fontSize: '0.72rem', marginTop: '4px', lineHeight: 1.4 },
  empty:      { color: '#475569', padding: '2rem', textAlign: 'center' },
};

function ProviderDecision({ label, analysis, fallbackProb }) {
  if (!analysis && fallbackProb == null) {
    return <div style={styles.meta}>No response</div>;
  }

  const action = analysis?.action || 'No action';
  const prob = analysis?.fair_value_pct;
  const rationale = analysis?.rationale;
  const actionStyle = /NO/i.test(action) || /SELL/i.test(action) ? styles.sell : styles.buy;

  return (
    <div style={styles.providerCell}>
      <div style={{ ...styles.providerAction, ...actionStyle }}>{action}</div>
      <div style={styles.providerProb}>
        {prob != null
          ? `${Number(prob).toFixed(1)}%`
          : fallbackProb != null
            ? `${Number(fallbackProb).toFixed(1)}%`
            : '—'}
      </div>
      <div style={styles.providerText}>
        {rationale ? rationale.split('\n')[0] : `${label} probability only`}
      </div>
    </div>
  );
}

export default function LiveMatrix({ signals, totalScanned = 0 }) {
  const rows = signals || [];

  if (rows.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.title}>OPENAI TRADE MATRIX</div>
        <div style={styles.empty}>
          No valid today/tomorrow Deribit matches found for OpenAI review.
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>
        OPENAI TRADE MATRIX — {rows.length} OPPORTUNITY{rows.length !== 1 ? 'IES' : 'Y'}
        <span style={{ color: '#475569', fontSize: '0.75rem', marginLeft: '1rem' }}>
          ({totalScanned} current items)
        </span>
      </div>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>MARKET</th>
            <th style={styles.th}>ACTION</th>
            <th style={styles.th}>OPENAI PROBABILITY</th>
            <th style={styles.th}>GROK PROBABILITY</th>
            <th style={styles.th}>GEMINI PROBABILITY</th>
            <th style={styles.th}>MARKET PRICE</th>
            <th style={styles.th}>EDGE</th>
            <th style={styles.th}>REASONING</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={i} style={{ background: i % 2 === 0 ? '#0f172a' : '#0a0e1a' }}>
              <td style={styles.td}>
                <div style={styles.market}>{s.polymarket_question}</div>
                <div style={styles.meta}>
                  {s.spot_price != null && s.strike != null
                    ? `$${Number(s.spot_price).toLocaleString()} spot / $${Number(s.strike).toLocaleString()} strike`
                    : ''}
                </div>
              </td>
              <td style={styles.td}>
                <span style={s.direction === 'BUY' ? styles.buy : styles.sell}>
                  {s.recommended_action || (s.direction === 'BUY' ? 'BUY YES' : 'BUY NO')}
                </span>
              </td>
              <td style={styles.td}>
                <ProviderDecision
                  label="OpenAI"
                  analysis={s.provider_analyses?.openai}
                  fallbackProb={s.deribit_prob != null ? Number(s.deribit_prob) * 100 : null}
                />
              </td>
              <td style={styles.td}>
                <ProviderDecision
                  label="Grok"
                  analysis={s.provider_analyses?.grok}
                />
              </td>
              <td style={styles.td}>
                <ProviderDecision
                  label="Gemini"
                  analysis={s.provider_analyses?.gemini}
                />
              </td>
              <td style={styles.td}>
                {s.polymarket_price != null ? `${(Number(s.polymarket_price) * 100).toFixed(1)}%` : '—'}
              </td>
              <td style={{ ...styles.td, ...((s.edge_pct ?? 0) >= 0 ? styles.edgePos : styles.edgeNeg) }}>
                {s.edge_pct != null ? `${Number(s.edge_pct).toFixed(1)}%` : '—'}
              </td>
              <td style={styles.td}>
                <div style={styles.reasoning}>{(s.agent_analysis?.rationale || s.structural_insight || s.reasoning || '').split('\n')[0]}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
