import React, { useState, useEffect, useCallback, useRef } from 'react';
import LiveMatrix from './components/LiveMatrix';
// import ReasoningCards from './components/ReasoningCard';
import Leaderboard from './components/Leaderboard';
import LoadingScreen from './components/LoadingScreen';
import { isPolymarketMarketRow } from './polymarketFilters';

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
  scanOverlay: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '70vh', gap: '1.5rem' },
  scanSpinner: { width: '48px', height: '48px', border: '3px solid #1e293b', borderTop: '3px solid #7dd3fc', borderRadius: '50%', animation: 'spin 1s linear infinite' },
  scanText: { color: '#7dd3fc', fontSize: '1rem', letterSpacing: '0.2em', fontFamily: "'Courier New', monospace" },
  scanSub: { color: '#475569', fontSize: '0.72rem', letterSpacing: '0.1em' },
  promptWrap: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '1rem' },
  promptTitle: { color: '#7dd3fc', fontSize: '0.9rem', marginBottom: '0.75rem', letterSpacing: '0.08em' },
  promptText: {
    width: '100%',
    minHeight: '240px',
    background: '#020617',
    color: '#cbd5e1',
    border: '1px solid #334155',
    borderRadius: '6px',
    padding: '0.75rem',
    fontFamily: "'Courier New', monospace",
    fontSize: '0.8rem',
    resize: 'vertical',
    outline: 'none',
    boxSizing: 'border-box',
  },
  promptActions: { display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.75rem' },
  promptHint: { color: '#64748b', fontSize: '0.72rem' },
  saveBtn: {
    background: '#1e293b',
    border: '1px solid #334155',
    color: '#94a3b8',
    padding: '6px 14px',
    borderRadius: '4px',
    cursor: 'pointer',
    fontSize: '0.75rem',
    letterSpacing: '0.05em',
  },
};

const TABS = ['MATRIX', /* 'REASONING', */ 'LEADERBOARD', 'SYSTEM PROMPT'];

export default function App() {
  const [tab, setTab] = useState('MATRIX');
  const [signals, setSignals] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);
  const [agentSummary, setAgentSummary] = useState(null);
  const agentSummaryRef = useRef(null);
  const [totalScanned, setTotalScanned] = useState(0);
  const [lastScan, setLastScan] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [promptProvider, setPromptProvider] = useState('openai');
  const [prompts, setPrompts] = useState({ openai: '', gemini: '', grok: '' });
  const [promptSavedAt, setPromptSavedAt] = useState({ openai: '', gemini: '', grok: '' });
  const [promptSaving, setPromptSaving] = useState(false);

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

  /** Load matrix + leaderboard only — called on the poll interval. Does NOT call AI providers.
   *  Accepts an optional agentOverride so runFullRefresh can pass the fresh agent
   *  directly without waiting for React state to flush (stale closure fix). */
  const loadFromApi = useCallback(async (agentOverride) => {
    setError(null);
    try {
      const [matrixRes, lbRes] = await Promise.all([
        fetch(`${API}/matrix`),
        fetch(`${API}/leaderboard`),
      ]);
      if (!matrixRes.ok) throw new Error(`Backend error: ${matrixRes.status}`);
      const matrix = await matrixRes.json();
      const lb = lbRes.ok ? await lbRes.json() : { entries: [] };
      const polyOnly = (matrix.signals || []).filter(isPolymarketMarketRow);
      const effectiveAgent = agentOverride !== undefined ? agentOverride : agentSummaryRef.current;
      setSignals(attachAgentAnalysis(polyOnly, effectiveAgent));
      setLeaderboard((lb.entries || []).filter(isPolymarketMarketRow));
      setTotalScanned(polyOnly.length);
      setLastScan(new Date().toLocaleTimeString());
    } catch (e) {
      setError(`Cannot reach backend at ${API}. (${e.message})`);
    } finally {
      setInitialLoading(false);
    }
  }, []);  // stable — reads agentSummary via ref to avoid interval restarts

  /**
   * Full refresh: live scan + CSV export + AI summary from all providers.
   * Only called when user clicks REFRESH — not on the 30s poll.
   */
  const runFullRefresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const scanRes = await fetch(`${API}/scan`, { method: 'POST' });
      if (!scanRes.ok) throw new Error(`Scan failed: ${scanRes.status}`);
      const csvRes = await fetch(`${API}/refresh/csv`, { method: 'POST' });
      if (!csvRes.ok) throw new Error(`CSV refresh failed: ${csvRes.status}`);
      // Call AI providers only on manual refresh
      const agentRes = await fetch(`${API}/agent/summary?limit=22`);
      const agent = agentRes.ok ? await agentRes.json() : null;
      agentSummaryRef.current = agent;
      setAgentSummary(agent);
      await loadFromApi(agent);
    } catch (e) {
      setError(`Cannot reach backend at ${API}. (${e.message})`);
    } finally {
      setLoading(false);
    }
  }, [loadFromApi]);

  useEffect(() => {
    // Load matrix immediately, then fetch AI summary and re-attach in background
    loadFromApi();
    fetch(`${API}/agent/summary?limit=22`)
      .then(r => r.ok ? r.json() : null)
      .then(agent => {
        if (agent) {
          agentSummaryRef.current = agent;
          setAgentSummary(agent);
          loadFromApi(agent);
        }
      })
      .catch(() => {});

    // Auto-poll matrix every 2 minutes
    const matrixInterval = setInterval(loadFromApi, 120000);

    // Full refresh (scan + CSV + AI) every 1 hour
    const fullInterval = setInterval(runFullRefresh, 3600000);

    return () => {
      clearInterval(matrixInterval);
      clearInterval(fullInterval);
    };
  }, [loadFromApi, runFullRefresh]);

  useEffect(() => {
    const loadPrompts = async () => {
      for (const provider of ['openai', 'gemini', 'grok']) {
        try {
          const res = await fetch(`${API}/agent/system-prompt/${provider}`);
          if (!res.ok) throw new Error(`Prompt fetch failed: ${res.status}`);
          const data = await res.json();
          setPrompts(p => ({ ...p, [provider]: data.prompt || '' }));
        } catch (e) {
          setError(`Cannot load ${provider} prompt from backend. (${e.message})`);
        }
      }
    };
    loadPrompts();
  }, []);

  const saveSystemPrompt = async () => {
    setPromptSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API}/agent/system-prompt/${promptProvider}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompts[promptProvider] }),
      });
      if (!res.ok) throw new Error(`Prompt save failed: ${res.status}`);
      setPromptSavedAt(p => ({ ...p, [promptProvider]: new Date().toLocaleTimeString() }));
    } catch (e) {
      setError(`Cannot save ${promptProvider} prompt to backend. (${e.message})`);
    } finally {
      setPromptSaving(false);
    }
  };

  return (
    <div style={styles.app}>
      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      <div style={styles.header}>
        <div>
          <div style={styles.logo}>OPEN CLAW</div>
          <div style={styles.subtitle}>Polymarket markets · AI-assisted edge vs fair value</div>
        </div>
        <div style={styles.status}>
          <div style={{ ...styles.dot, ...(error ? styles.dotError : {}) }} />
          <span style={styles.statusText}>{error ? 'OFFLINE' : 'LIVE'}</span>
        </div>
      </div>

      {error && <div style={styles.error}>{error}</div>}

      {initialLoading ? (
        <LoadingScreen />
      ) : (
      <>
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
      {/* {tab === 'REASONING' && <ReasoningCards signals={signals} />} */}
      {tab === 'LEADERBOARD' && <Leaderboard entries={leaderboard} />}
      {tab === 'SYSTEM PROMPT' && (
        <div style={styles.promptWrap}>
          <div style={{ display: 'flex', gap: '1px', marginBottom: '1rem', background: '#1e293b', borderRadius: '6px', padding: '4px', width: 'fit-content' }}>
            {['openai', 'gemini', 'grok'].map(p => (
              <button
                key={p}
                onClick={() => setPromptProvider(p)}
                style={{
                  ...styles.tab,
                  ...(promptProvider === p ? styles.tabActive : {}),
                  fontSize: '0.75rem',
                  padding: '6px 16px',
                }}
              >
                {p === 'openai' ? 'OPENAI' : p === 'gemini' ? 'GEMINI' : 'GROK'}
              </button>
            ))}
          </div>
          <textarea
            style={styles.promptText}
            value={prompts[promptProvider]}
            onChange={(e) => setPrompts(prev => ({ ...prev, [promptProvider]: e.target.value }))}
            placeholder={`Write the ${promptProvider.toUpperCase()} system prompt here.`}
          />
          <div style={styles.promptActions}>
            <button style={styles.saveBtn} onClick={saveSystemPrompt} disabled={promptSaving}>
              {promptSaving ? 'SAVING...' : 'SAVE PROMPT'}
            </button>
            <span style={styles.promptHint}>
              {promptSavedAt[promptProvider] ? `Saved at ${promptSavedAt[promptProvider]}` : 'Not saved yet'}
            </span>
          </div>
        </div>
      )}
      </>
      )}
    </div>
  );
}
