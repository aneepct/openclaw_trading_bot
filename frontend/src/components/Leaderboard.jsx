import React from 'react';

const styles = {
  container: { marginBottom: '2rem' },
  title: { fontSize: '1.1rem', color: '#7dd3fc', marginBottom: '1rem', letterSpacing: '0.1em' },
  list: { display: 'flex', flexDirection: 'column', gap: '0.5rem' },
  row: { display: 'flex', alignItems: 'center', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem 1rem', gap: '1rem' },
  rank: { fontSize: '1.2rem', width: '2rem', textAlign: 'center' },
  info: { flex: 1 },
  questionText: { color: '#e2e8f0', fontSize: '0.78rem', marginTop: '3px', lineHeight: 1.4 },
  metaRow: { display: 'flex', gap: '12px', alignItems: 'center', marginTop: '6px', color: '#64748b', fontSize: '0.7rem', flexWrap: 'wrap' },
  right: { textAlign: 'right' },
  edge: { color: '#fbbf24', fontWeight: 700, fontSize: '1rem' },
  payout: { color: '#34d399', fontSize: '0.7rem', marginTop: '2px' },
  direction: { fontSize: '0.7rem' },
  buy:  { color: '#4ade80' },
  sell: { color: '#f87171' },
  empty: { color: '#475569', padding: '2rem', textAlign: 'center' },
};

const RANK_ICONS = ['1', '2', '3', '4', '5'];

export default function Leaderboard({ entries }) {
  if (!entries || entries.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.title}>TOP SIGNALS</div>
        <div style={styles.empty}>No entries yet. Leaderboard populates as signals are detected.</div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>TOP SIGNALS — {entries.length} ACTIVE</div>
      <div style={styles.list}>
        {entries.map((e, i) => {
          return (
            <div key={i} style={styles.row}>
              <div style={styles.rank}>{RANK_ICONS[i] || `#${e.rank}`}</div>
              <div style={styles.info}>
                {e.polymarket_question && (
                  <div style={styles.questionText}>{e.polymarket_question}</div>
                )}
                <div style={styles.metaRow}>
                  <span>Action: {e.recommended_action || e.direction}</span>
                  <span>Price: {e.polymarket_price != null ? `${(Number(e.polymarket_price) * 100).toFixed(1)}%` : '—'}</span>
                  <span>Fair: {e.deribit_prob != null ? `${(Number(e.deribit_prob) * 100).toFixed(1)}%` : '—'}</span>
                </div>
              </div>
              <div style={styles.right}>
                <div style={styles.edge}>{e.abs_edge_pct?.toFixed(1)}%</div>
                <div style={{ ...styles.direction, ...(e.direction === 'BUY' ? styles.buy : styles.sell) }}>
                  {e.recommended_action || e.direction}
                </div>
                <div style={styles.payout}>
                  {e.payout_ratio ? `${e.payout_ratio}x upside` : ''}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
