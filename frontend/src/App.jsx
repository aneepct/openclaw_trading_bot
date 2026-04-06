import React, { useState, useEffect, useCallback } from 'react';
import LiveMatrix from './components/LiveMatrix';
import AgentSummary from './components/AgentSummary';
import ReasoningCards from './components/ReasoningCard';
import Leaderboard from './components/Leaderboard';

const API = (process.env.REACT_APP_API_URL || 'http://localhost:8000').replace(/\/$/, '');

const styles = {
  app: { minHeight: '100vh', background: '#0a0e1a', padding: '1.5rem', fontFamily: "'Courier New', monospace" },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem', borderBottom: '1px solid #1e293b', paddingBottom: '1rem' },
  logo: { fontSize: '1.5rem', fontWeight: 700, color: '#7dd3fc', letterSpacing: '0.2em' },
  subtitle: { color: '#475569', fontSize: '0.75rem', marginTop: '2px' },
  status: { display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.75rem' },
  dot: { width: '8px', height: '8px', borderRadius: '50%', background: '#4ade80', animation: 'pulse 2s infinite' },
  dotError: { background: '#f87171', animation: 'none' },
  statusText: { color: '#64748b' },
  tabs: { display: 'flex', gap: '1px', marginBottom: '2rem', background: '#1e293b', borderRadius: '6px', padding: '4px' },
  tab: { padding: '8px 20px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', color: '#64748b', border: 'none', background: 'transparent', letterSpacing: '0.08em' },
  tabActive: { background: '#0f172a', color: '#7dd3fc' },
  refreshBtn: { background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', padding: '6px 14px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.75rem', letterSpacing: '0.05em' },
  topBar: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' },
  lastScan: { color: '#334155', fontSize: '0.7rem' },
  error: { color: '#f87171', background: '#450a0a', padding: '1rem', borderRadius: '6px', marginBottom: '1rem', fontSize: '0.8rem' },
};

const TABS = ['MATRIX', 'REASONING', 'LEADERBOARD'];

export default function App() {
  const [tab, setTab] = useState('MATRIX');
  const [signals, setSignals] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);
  const [agentSummary, setAgentSummary] = useState(null);
  const [totalScanned, setTotalScanned] = useState(0);
  const [lastScan, setLastScan] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const attachAgentAnalysis = (rawSignals, agent) => {
    if (!rawSignals?.length) return [];

    const providerMaps = Object.fromEntries(
      Object.entries(agent?.providers || {}).map(([provider, payload]) => {
        const analyses = payload?.signal_analyses || [];
        return [provider, {
          byId: new Map(analyses.filter(a => a?.market_id).map(a => [a.market_id, a])),
          byQ:  new Map(analyses.filter(a => a?.market).map(a => [a.market, a])),
        }];
      })
    );

    const lookup = (maps, signal) =>
      maps?.byId?.get(signal.polymarket_market_id) ||
      maps?.byQ?.get(signal.polymarket_question) ||
      null;

    return rawSignals.map(signal => ({
      ...signal,
      provider_analyses: {
        openai:  lookup(providerMaps.openai,  signal),
        grok:    lookup(providerMaps.grok,    signal),
        gemini:  lookup(providerMaps.gemini,  signal),
      },
      agent_analysis: lookup(providerMaps.openai, signal),
    }));
  };

  /** Load current data from the API (GET only — used on interval and after a full scan). */
  const loadFromApi = useCallback(async () => {
    setError(null);
    try {
      const [matrixRes, lbRes] = await Promise.all([
        fetch(`${API}/matrix`),
        fetch(`${API}/leaderboard`),
      ]);
      if (!matrixRes.ok) throw new Error(`Backend error: ${matrixRes.status}`);
      const matrix = await matrixRes.json();
      const lb = lbRes.ok ? await lbRes.json() : { entries: [] };
      const agentRes = await fetch(`${API}/agent/summary`);
      await fetch(`${API}/health`);
      const agent = agentRes.ok ? await agentRes.json() : null;
      setSignals(attachAgentAnalysis(matrix.signals || [], agent));
      setLeaderboard(lb.entries || []);
      setAgentSummary(agent);
      setTotalScanned(matrix.total || 0);
      setLastScan(new Date().toLocaleTimeString());
    } catch (e) {
      setError(`Cannot reach backend at ${API}. (${e.message})`);
    }
  }, []);

  /**
   * Full refresh: live scan (Deribit + Polymarket + agent signals), CSV export snapshot, then AI summary via GET /agent/summary.
   * Use for the REFRESH button only — not on the 30s poll (that would be too heavy).
   */
  const runFullRefresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const scanRes = await fetch(`${API}/scan`, { method: 'POST' });
      if (!scanRes.ok) throw new Error(`Scan failed: ${scanRes.status}`);
      const csvRes = await fetch(`${API}/refresh/csv`, { method: 'POST' });
      if (!csvRes.ok) throw new Error(`CSV refresh failed: ${csvRes.status}`);
      await loadFromApi();
    } catch (e) {
      setError(`Cannot reach backend at ${API}. (${e.message})`);
    } finally {
      setLoading(false);
    }
  }, [loadFromApi]);

  useEffect(() => {
    loadFromApi();
    const interval = setInterval(loadFromApi, 30000);
    return () => clearInterval(interval);
  }, [loadFromApi]);

  return (
    <div style={styles.app}>
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>

      <div style={styles.header}>
        <div>
          <div style={styles.logo}>OPEN CLAW</div>
          <div style={styles.subtitle}>OpenAI market review workflow for Deribit and Polymarket</div>
        </div>
        <div style={styles.status}>
          <div style={{ ...styles.dot, ...(error ? styles.dotError : {}) }} />
          <span style={styles.statusText}>{error ? 'OFFLINE' : 'LIVE'}</span>
        </div>
      </div>

      {error && <div style={styles.error}>{error}</div>}

      <div style={styles.topBar}>
        <div style={styles.tabs}>
          {TABS.map(t => (
            <button
              key={t}
              style={{ ...styles.tab, ...(tab === t ? styles.tabActive : {}) }}
              onClick={() => setTab(t)}
            >
              {t}
              {t === 'MATRIX' && signals.length > 0 && (
                <span style={{ marginLeft: '6px', background: '#fbbf24', color: '#0a0e1a', borderRadius: '10px', padding: '1px 6px', fontSize: '0.65rem', fontWeight: 700 }}>
                  {signals.length}
                </span>
              )}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {lastScan && <span style={styles.lastScan}>Last scan: {lastScan}</span>}
          <button style={styles.refreshBtn} onClick={runFullRefresh} disabled={loading}>
            {loading ? 'SCANNING...' : '↺ REFRESH'}
          </button>
        </div>
      </div>

      {tab === 'MATRIX' && <LiveMatrix signals={signals} totalScanned={totalScanned} />}
      {tab === 'REASONING' && (
        <>
          <AgentSummary agentSummary={agentSummary} />
          <ReasoningCards signals={signals} />
        </>
      )}
      {tab === 'LEADERBOARD' && <Leaderboard entries={leaderboard} />}
    </div>
  );
}
