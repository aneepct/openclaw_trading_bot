import React from 'react';

const styles = {
  container: { marginBottom: '2rem' },
  title: { fontSize: '1.1rem', color: '#7dd3fc', marginBottom: '1rem', letterSpacing: '0.1em' },
  list: { display: 'flex', flexDirection: 'column', gap: '0.5rem' },
  row: { display: 'flex', alignItems: 'center', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '6px', padding: '0.75rem 1rem', gap: '1rem' },
  rank: { fontSize: '1.2rem', width: '2rem', textAlign: 'center' },
  info: { flex: 1 },
  instruments: { fontFamily: 'monospace', fontSize: '0.75rem' },
  t1: { color: '#7dd3fc', fontWeight: 700 },
  t2: { color: '#818cf8' },
  questionText: { color: '#64748b', fontSize: '0.68rem', marginTop: '3px' },
  metaRow: { display: 'flex', gap: '8px', alignItems: 'center', marginTop: '4px' },
  liquidity: { color: '#64748b', fontSize: '0.7rem' },
  callBadge: { fontSize: '0.6rem', fontWeight: 700, padding: '1px 5px', borderRadius: '3px', background: '#1d4ed8', color: '#bfdbfe' },
  putBadge:  { fontSize: '0.6rem', fontWeight: 700, padding: '1px 5px', borderRadius: '3px', background: '#7c2d12', color: '#fdba74' },
  right: { textAlign: 'right' },
  edge: { color: '#fbbf24', fontWeight: 700, fontSize: '1rem' },
  payout: { color: '#34d399', fontSize: '0.7rem', marginTop: '2px' },
  direction: { fontSize: '0.7rem' },
  buy:  { color: '#4ade80' },
  sell: { color: '#f87171' },
  empty: { color: '#475569', padding: '2rem', textAlign: 'center' },
};

const RANK_ICONS = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣'];

export default function Leaderboard({ entries }) {
  if (!entries || entries.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.title}>24H ALPHA LEADERBOARD</div>
        <div style={styles.empty}>No entries yet. Leaderboard populates as signals are detected.</div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>24H ALPHA LEADERBOARD — TOP {entries.length}</div>
      <div style={styles.list}>
        {entries.map((e, i) => {
          const isPut = e.option_type === 'P';
          return (
            <div key={i} style={styles.row}>
              <div style={styles.rank}>{RANK_ICONS[i] || `#${e.rank}`}</div>

              {/* T1/T2 pair + option type + liquidity */}
              <div style={styles.info}>
                <div style={styles.instruments}>
                  <span style={styles.t1}>{e.instrument_t1 || 'N/A'}</span>
                  <span style={styles.t2}> ↕ {e.instrument_t2 || 'N/A'}</span>
                </div>
                <div style={styles.metaRow}>
                  <span style={isPut ? styles.putBadge : styles.callBadge}>
                    {isPut ? 'PUT ↓' : 'CALL ↑'}
                  </span>
                  <span style={styles.liquidity}>
                    Liq: ${e.liquidity_usd ? e.liquidity_usd.toLocaleString() : '—'}
                  </span>
                </div>
                {e.polymarket_question && (
                  <div style={styles.questionText}>{e.polymarket_question}</div>
                )}
              </div>

              {/* Edge + direction + payout */}
              <div style={styles.right}>
                <div style={styles.edge}>{e.abs_edge_pct?.toFixed(1)}%</div>
                <div style={{ ...styles.direction, ...(e.direction === 'BUY' ? styles.buy : styles.sell) }}>
                  {e.direction}
                </div>
                <div style={styles.payout}>
                  {e.payout_ratio ? `${e.payout_ratio}x payout` : ''}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
