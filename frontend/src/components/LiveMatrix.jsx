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
  instrument: { color: '#e2e8f0', fontFamily: 'monospace', fontSize: '0.72rem' },
  interp:     { color: '#475569', fontSize: '0.65rem', marginTop: '2px' },
  prob:       { color: '#c084fc' },
  payout:     { color: '#34d399', fontSize: '0.75rem' },
  reasoning:  { color: '#94a3b8', fontSize: '0.72rem', marginTop: '4px', lineHeight: 1.4 },
  empty:      { color: '#475569', padding: '2rem', textAlign: 'center' },
  methodBadge: { fontSize: '0.6rem', fontWeight: 700, padding: '1px 5px', borderRadius: '3px', marginLeft: '6px', verticalAlign: 'middle' },
  callBadge:   { fontSize: '0.6rem', fontWeight: 700, padding: '1px 6px', borderRadius: '3px', background: '#1d4ed8', color: '#bfdbfe' },
  putBadge:    { fontSize: '0.6rem', fontWeight: 700, padding: '1px 6px', borderRadius: '3px', background: '#7c2d12', color: '#fdba74' },
};

function OptionTypeBadge({ optionType }) {
  const isPut = optionType === 'P';
  return (
    <span style={isPut ? styles.putBadge : styles.callBadge}>
      {isPut ? 'PUT ↓' : 'CALL ↑'}
    </span>
  );
}

function InstrumentCell({ s }) {
  const method = s.interp_method || 'T2-only';
  const badgeColor = method === 'interpolated' ? { background: '#1e3a5f', color: '#93c5fd' }
                   : method === 'T1-only'       ? { background: '#713f12', color: '#fde68a' }
                                                : { background: '#1e293b', color: '#94a3b8' };
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
        <OptionTypeBadge optionType={s.option_type} />
      </div>
      <div style={styles.instrument}>{s.instrument_t1 || 'N/A'}</div>
      <div style={styles.instrument}>↕ {s.instrument_t2 || 'N/A'}</div>
      <span style={{ ...styles.methodBadge, ...badgeColor }}>
        {method === 'interpolated' ? `w=${s.interp_weight_w?.toFixed(2)}` : method}
      </span>
    </div>
  );
}

export default function LiveMatrix({ signals, totalScanned = 0 }) {
  const rows = signals || [];

  if (rows.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.title}>LIVE MATRIX</div>
        <div style={styles.empty}>No alpha signals detected. Scanner running...</div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.title}>
        LIVE MATRIX — {rows.length} ALPHA SIGNAL{rows.length !== 1 ? 'S' : ''}
        <span style={{ color: '#475569', fontSize: '0.75rem', marginLeft: '1rem' }}>
          ({totalScanned} total scanned)
        </span>
      </div>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>T1 / T2 INSTRUMENTS</th>
            <th style={styles.th}>σ INTERP</th>
            <th style={styles.th}>t_poly (days)</th>
            <th style={styles.th}>PROFESSIONAL % (N(d₂))</th>
            <th style={styles.th}>PM PRICE (¢)</th>
            <th style={styles.th}>EDGE</th>
            <th style={styles.th}>REC</th>
            <th style={styles.th}>PAYOUT</th>
            <th style={styles.th}>MARKET / REASONING</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={i} style={{ background: i % 2 === 0 ? '#0f172a' : '#0a0e1a' }}>
              <td style={styles.td}><InstrumentCell s={s} /></td>
              <td style={{ ...styles.td, ...styles.prob }}>
                {s.sigma_interp != null ? (Number(s.sigma_interp) * 100).toFixed(1) + '%' : '—'}
              </td>
              <td style={styles.td}>
                {s.t_poly_days != null ? `${Number(s.t_poly_days).toFixed(1)}d` : '—'}
              </td>
              <td style={{ ...styles.td, ...styles.prob }}>
                {s.deribit_prob != null ? `${(Number(s.deribit_prob) * 100).toFixed(2)}%` : '—'}
              </td>
              <td style={styles.td}>
                {s.polymarket_price != null ? `${(Number(s.polymarket_price) * 100).toFixed(1)}¢` : '—'}
              </td>
              <td style={{ ...styles.td, ...((s.edge_pct ?? 0) >= 0 ? styles.edgePos : styles.edgeNeg) }}>
                {s.edge_pct != null ? `${Number(s.edge_pct).toFixed(1)}%` : '—'}
              </td>
              <td style={styles.td}>
                <span style={s.direction === 'BUY' ? styles.buy : styles.sell}>
                  {s.direction === 'BUY' ? 'BUY YES' : 'SELL YES'}
                </span>
              </td>
              <td style={{ ...styles.td, ...styles.payout }}>
                {s.payout_ratio ? `${s.payout_ratio}x` : '—'}
              </td>
              <td style={styles.td}>
                <div style={{ color: '#e2e8f0', fontSize: '0.75rem' }}>{s.polymarket_question}</div>
                <div style={styles.reasoning}>{s.reasoning?.split('\n')[3]}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
