import React, { useState, useEffect } from 'react';

const STEPS = [
  { label: 'Connecting to backend',       duration: 600 },
  { label: 'Fetching Deribit order books', duration: 900 },
  { label: 'Loading Polymarket markets',  duration: 700 },
  { label: 'Computing edge signals',      duration: 800 },
  { label: 'Initialising AI layer',       duration: 500 },
];

export default function LoadingScreen() {
  const [stepIndex, setStepIndex] = useState(0);
  const [dots, setDots]           = useState('');
  const [barPct, setBarPct]       = useState(0);

  // Advance through steps
  useEffect(() => {
    if (stepIndex >= STEPS.length) return;
    const t = setTimeout(() => setStepIndex(i => i + 1), STEPS[stepIndex].duration);
    return () => clearTimeout(t);
  }, [stepIndex]);

  // Animate progress bar smoothly toward target %
  useEffect(() => {
    const target = stepIndex >= STEPS.length ? 100 : Math.round((stepIndex / STEPS.length) * 92);
    const id = setInterval(() => {
      setBarPct(p => {
        if (p >= target) { clearInterval(id); return target; }
        return p + 1;
      });
    }, 12);
    return () => clearInterval(id);
  }, [stepIndex]);

  // Blinking dots on the current step label
  useEffect(() => {
    const id = setInterval(() => setDots(d => d.length >= 3 ? '' : d + '.'), 400);
    return () => clearInterval(id);
  }, []);

  const currentLabel = stepIndex < STEPS.length ? STEPS[stepIndex].label : 'Ready';

  return (
    <div style={s.overlay}>
      <style>{`
        @keyframes ocPulse  { 0%,100%{opacity:1} 50%{opacity:0.35} }
        @keyframes ocScan   { 0%{transform:translateY(-100%)} 100%{transform:translateY(400%)} }
        @keyframes ocFadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        @keyframes ocBlink  { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes ocGlow   { 0%,100%{box-shadow:0 0 8px #38bdf855} 50%{box-shadow:0 0 22px #38bdf8cc} }
      `}</style>

      {/* Brand */}
      <div style={s.brand}>
        <div style={s.logoWrap}>
          <span style={s.logoBracket}>[</span>
          <span style={s.logoText}>OPEN CLAW</span>
          <span style={s.logoBracket}>]</span>
        </div>
        <div style={s.tagline}>Deribit · Polymarket · AI Edge Scanner</div>
      </div>

      {/* Scan grid decoration */}
      <div style={s.gridBox}>
        <div style={s.scanLine} />
        {Array.from({ length: 4 }).map((_, r) => (
          <div key={r} style={s.gridRow}>
            {Array.from({ length: 8 }).map((_, c) => (
              <div
                key={c}
                style={{
                  ...s.gridCell,
                  opacity: Math.random() > 0.55 ? 0.18 : 0.06,
                  animationDelay: `${(r * 8 + c) * 120}ms`,
                }}
              />
            ))}
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div style={s.barWrap}>
        <div style={{ ...s.barFill, width: `${barPct}%` }} />
      </div>
      <div style={s.barPct}>{barPct}%</div>

      {/* Step list */}
      <div style={s.stepList}>
        {STEPS.map((step, i) => {
          const done    = i < stepIndex;
          const active  = i === stepIndex;
          const pending = i > stepIndex;
          return (
            <div key={i} style={{ ...s.stepRow, opacity: pending ? 0.28 : 1 }}>
              <span style={{ ...s.stepIcon, color: done ? '#4ade80' : active ? '#38bdf8' : '#334155' }}>
                {done ? '✓' : active ? '›' : '·'}
              </span>
              <span style={{ ...s.stepLabel, color: done ? '#4ade80' : active ? '#e2e8f0' : '#475569' }}>
                {step.label}
                {active && <span style={s.dotAnim}>{dots}</span>}
              </span>
            </div>
          );
        })}
      </div>

      {/* Live status line */}
      <div style={s.statusLine}>
        <span style={s.cursor}>▋</span>
        <span style={s.statusText}>{currentLabel}{stepIndex < STEPS.length ? dots : ''}</span>
      </div>
    </div>
  );
}

const s = {
  overlay: {
    position: 'fixed', inset: 0,
    background: '#0a0e1a',
    display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center',
    gap: '1.4rem',
    fontFamily: "'Courier New', monospace",
    zIndex: 9999,
  },
  brand: {
    textAlign: 'center',
    animation: 'ocFadeIn 0.6s ease both',
  },
  logoWrap: {
    fontSize: '2.4rem', fontWeight: 900,
    letterSpacing: '0.22em', lineHeight: 1,
    marginBottom: '0.3rem',
  },
  logoBracket: { color: '#334155' },
  logoText: {
    color: '#7dd3fc',
    textShadow: '0 0 24px #38bdf888',
    animation: 'ocPulse 3s ease-in-out infinite',
  },
  tagline: {
    color: '#334155', fontSize: '0.7rem',
    letterSpacing: '0.18em',
  },
  gridBox: {
    position: 'relative', overflow: 'hidden',
    width: '340px', borderRadius: '6px',
    border: '1px solid #1e293b',
    background: '#020617',
    padding: '0.5rem',
  },
  scanLine: {
    position: 'absolute', left: 0, right: 0,
    height: '2px',
    background: 'linear-gradient(90deg,transparent,#38bdf855,transparent)',
    animation: 'ocScan 2.4s linear infinite',
  },
  gridRow: { display: 'flex', gap: '4px', marginBottom: '4px' },
  gridCell: {
    flex: 1, height: '14px', borderRadius: '2px',
    background: '#38bdf8',
    animation: 'ocPulse 2s ease-in-out infinite',
  },
  barWrap: {
    width: '340px', height: '4px',
    background: '#1e293b', borderRadius: '9999px',
    overflow: 'hidden',
  },
  barFill: {
    height: '100%', borderRadius: '9999px',
    background: 'linear-gradient(90deg,#1d4ed8,#38bdf8)',
    transition: 'width 0.12s linear',
    animation: 'ocGlow 2s ease-in-out infinite',
  },
  barPct: {
    color: '#334155', fontSize: '0.65rem',
    letterSpacing: '0.12em',
    marginTop: '-0.6rem',
    alignSelf: 'flex-end',
    marginRight: 'calc(50% - 170px)',
  },
  stepList: {
    display: 'flex', flexDirection: 'column',
    gap: '0.35rem', width: '340px',
  },
  stepRow: {
    display: 'flex', alignItems: 'center', gap: '0.6rem',
    fontSize: '0.74rem', transition: 'opacity 0.3s',
  },
  stepIcon: { width: '14px', textAlign: 'center', fontWeight: 700 },
  stepLabel: { letterSpacing: '0.04em' },
  dotAnim: { color: '#38bdf8' },
  statusLine: {
    display: 'flex', alignItems: 'center', gap: '6px',
    color: '#1e40af', fontSize: '0.68rem',
    letterSpacing: '0.08em',
  },
  cursor: {
    color: '#38bdf8',
    animation: 'ocBlink 1s step-end infinite',
  },
  statusText: { color: '#1e40af' },
};
