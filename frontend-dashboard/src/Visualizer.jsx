import { useCallback, useEffect, useRef, useState } from 'react';
import { API, api } from './api.js';

const MODES = ['story', 'shuffle', 'loop', 'rando'];
const CARD_STYLES = ['default', 'minimal', 'bold', 'neon'];

const card = {
  backgroundColor: '#1e1e1e',
  padding: 24,
  borderRadius: 12,
  border: '1px solid #2d2d2d',
  boxShadow: '0 4px 6px rgba(0,0,0,0.3)',
};
const h = {
  margin: '0 0 16px',
  fontSize: '1.2rem',
  color: '#e5e7eb',
  borderBottom: '1px solid #374151',
  paddingBottom: 12,
};
const lab = { display: 'block', color: '#d1d5db', fontSize: 13, marginBottom: 12 };
const inp = {
  width: '100%',
  marginTop: 6,
  padding: 10,
  borderRadius: 8,
  border: '1px solid #374151',
  background: '#111827',
  color: 'white',
  boxSizing: 'border-box',
};
const btn = (active, color = '#7c3aed') => ({
  padding: '8px 14px',
  borderRadius: 8,
  border: 'none',
  backgroundColor: active ? color : '#333',
  color: 'white',
  cursor: 'pointer',
  fontWeight: 700,
  fontSize: 13,
});

function visualizerOpenUrl() {
  if (API) return `${API}/visualizer/`;
  return `${window.location.origin}/visualizer/`;
}

export default function Visualizer() {
  const [snap, setSnap] = useState(null);
  const [np, setNp] = useState(null);
  const [history, setHistory] = useState([]);
  const [manualArtist, setManualArtist] = useState('');
  const [manualTitle, setManualTitle] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const debounceRef = useRef({});

  const refreshVis = useCallback(async () => {
    try {
      const data = await api('/api/visualizer');
      setSnap(data);
      setErr('');
    } catch (e) {
      setErr('Cannot reach visualizer API');
    }
  }, []);

  const refreshNp = useCallback(async () => {
    try {
      const [cur, hist] = await Promise.all([
        api('/api/nowplaying'),
        api('/api/nowplaying/history'),
      ]);
      setNp(cur && (cur.title || cur.artist) ? cur : null);
      setHistory(Array.isArray(hist?.tracks) ? hist.tracks.slice().reverse() : []);
    } catch {
      /* optional while backend offline */
    }
  }, []);

  useEffect(() => {
    refreshVis();
    refreshNp();
    const id = setInterval(() => {
      refreshVis();
      refreshNp();
    }, 4000);
    return () => clearInterval(id);
  }, [refreshVis, refreshNp]);

  const state = snap?.state || {};
  const status = snap?.status || {};
  const scenes = Array.isArray(status.scenes) ? status.scenes : [];
  const clients = snap?.clients ?? 0;

  const send = async (body) => {
    setBusy(true);
    try {
      const data = await api('/api/visualizer', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setSnap({
        state: data.state || data.cmd || state,
        clients: data.clients ?? clients,
        status: data.status ?? status,
      });
      setErr('');
    } catch (e) {
      setErr(e.message || 'Command failed');
    } finally {
      setBusy(false);
    }
  };

  const sendDebounced = (key, body, ms = 80) => {
    if (debounceRef.current[key]) clearTimeout(debounceRef.current[key]);
    debounceRef.current[key] = setTimeout(() => send(body), ms);
  };

  const setStrobeSafe = async (want) => {
    if (!want) {
      const ok = window.confirm(
        'Turn off strobe-safe? This may enable intense flashing. Confirm only if the room is ready.',
      );
      if (!ok) return;
      await send({ strobeSafe: false, confirm: true });
      return;
    }
    await send({ strobeSafe: true });
  };

  const setManual = async () => {
    if (!manualTitle.trim() && !manualArtist.trim()) return;
    try {
      await api('/api/nowplaying', {
        method: 'POST',
        body: JSON.stringify({
          artist: manualArtist.trim(),
          title: manualTitle.trim() || manualArtist.trim(),
        }),
      });
      setManualArtist('');
      setManualTitle('');
      refreshNp();
    } catch (e) {
      setErr(e.message || 'Manual now-playing failed');
    }
  };

  const clearManual = async () => {
    try {
      await api('/api/nowplaying', { method: 'DELETE' });
      refreshNp();
    } catch (e) {
      setErr(e.message || 'Clear failed');
    }
  };

  const sceneValue =
    state.scene === 'R' || state.mode === 'rando'
      ? 'R'
      : String(state.scene ?? status.scene ?? 0);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 24 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <section style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <h2 style={{ ...h, border: 'none', padding: 0, margin: 0 }}>Storm Cellar visualizer</h2>
            <a
              href={visualizerOpenUrl()}
              target="_blank"
              rel="noreferrer"
              style={{ ...btn(true, '#2563eb'), textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
            >
              Open visualizer
            </a>
          </div>
          <p style={{ color: '#9ca3af', fontSize: 13, marginTop: 12 }}>
            Clients: <strong style={{ color: '#e5e7eb' }}>{clients}</strong>
            {' · '}FPS: <strong style={{ color: '#e5e7eb' }}>{status.fps ?? '—'}</strong>
            {' · '}Input: <strong style={{ color: '#e5e7eb' }}>{status.input || '—'}</strong>
            {busy ? ' · sending…' : ''}
          </p>
          {err && <p style={{ color: '#f87171', marginTop: 8 }}>{err}</p>}

          <label style={{ ...lab, marginTop: 16 }}>
            Scene
            <select
              value={sceneValue}
              onChange={(e) => {
                const v = e.target.value;
                if (v === 'R') send({ scene: 'R', mode: 'rando' });
                else send({ scene: Number(v) });
              }}
              style={inp}
            >
              {scenes.length === 0 && (
                <option value={sceneValue}>
                  {state.mode === 'rando' ? 'Rando' : `Scene ${sceneValue}`} (open visualizer for names)
                </option>
              )}
              {scenes.map((s) => (
                <option key={s.i} value={String(s.i)}>
                  {s.i}. {s.name}
                </option>
              ))}
              <option value="R">R — Rando</option>
            </select>
          </label>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            <button type="button" style={btn(false)} onClick={() => send({ prev: true })}>
              Prev
            </button>
            <button type="button" style={btn(false)} onClick={() => send({ next: true })}>
              Next
            </button>
          </div>

          <div style={{ marginTop: 16 }}>
            <div style={{ color: '#9ca3af', fontSize: 13, marginBottom: 8 }}>Mode</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {MODES.map((m) => (
                <button
                  key={m}
                  type="button"
                  style={btn(state.mode === m, '#7c3aed')}
                  onClick={() => send({ mode: m })}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          <label style={{ ...lab, marginTop: 18 }}>
            Reactivity {Number(state.reactivity ?? 1.2).toFixed(2)}
            <input
              type="range"
              min="0"
              max="3"
              step="0.05"
              value={state.reactivity ?? 1.2}
              onChange={(e) => {
                const reactivity = Number(e.target.value);
                setSnap((s) => ({
                  ...(s || {}),
                  state: { ...(s?.state || {}), reactivity },
                }));
                sendDebounced('reactivity', { reactivity });
              }}
              style={{ width: '100%', marginTop: 8 }}
            />
          </label>
          <label style={lab}>
            Sensitivity {Number(state.sens ?? 1).toFixed(2)}
            <input
              type="range"
              min="0.3"
              max="3"
              step="0.05"
              value={state.sens ?? 1}
              onChange={(e) => {
                const sens = Number(e.target.value);
                setSnap((s) => ({
                  ...(s || {}),
                  state: { ...(s?.state || {}), sens },
                }));
                sendDebounced('sens', { sens });
              }}
              style={{ width: '100%', marginTop: 8 }}
            />
          </label>
          <label style={lab}>
            Scene length {Number(state.sceneMinutes ?? 4).toFixed(1)} min
            <input
              type="range"
              min="0.5"
              max="15"
              step="0.5"
              value={state.sceneMinutes ?? 4}
              onChange={(e) => {
                const sceneMinutes = Number(e.target.value);
                setSnap((s) => ({
                  ...(s || {}),
                  state: { ...(s?.state || {}), sceneMinutes },
                }));
                sendDebounced('sceneMinutes', { sceneMinutes });
              }}
              style={{ width: '100%', marginTop: 8 }}
            />
          </label>
        </section>

        <section style={card}>
          <h2 style={h}>Toggles</h2>
          <Toggle
            label="Auto-gain"
            checked={!!state.autogain}
            onChange={(v) => send({ autogain: v })}
          />
          <Toggle
            label="FX"
            checked={!!state.fx}
            onChange={(v) => send({ fx: v })}
          />
          <Toggle
            label="Projection"
            checked={!!state.projection}
            onChange={(v) => send({ projection: v })}
          />
          <Toggle
            label="Strobe-safe"
            checked={state.strobeSafe !== false}
            onChange={(v) => setStrobeSafe(v)}
          />
          <Toggle
            label="Title cards"
            checked={state.titleCards !== false}
            onChange={(v) => send({ titleCards: v })}
          />
          <Toggle
            label="Now-playing card"
            checked={state.nowPlayingCard !== false}
            onChange={(v) => send({ nowPlayingCard: v })}
          />
          <label style={lab}>
            Card style
            <select
              value={state.cardstyle || 'default'}
              onChange={(e) => send({ cardstyle: e.target.value })}
              style={inp}
            >
              {CARD_STYLES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <Toggle
            label="Song-synced scenes"
            checked={!!state.songSync}
            onChange={(v) => send({ songSync: v })}
          />
          <Toggle
            label="Debug"
            checked={!!state.debug}
            onChange={(v) => send({ debug: v })}
          />
        </section>
      </div>

      <section style={card}>
        <h2 style={h}>Now Playing</h2>
        <div
          style={{
            padding: 14,
            background: '#111827',
            borderRadius: 10,
            border: '1px solid #374151',
            marginBottom: 16,
          }}
        >
          {np ? (
            <>
              <div style={{ fontSize: 18, fontWeight: 700, color: '#a78bfa' }}>
                {np.title || 'Untitled'}
              </div>
              <div style={{ color: '#d1d5db', marginTop: 4 }}>{np.artist || '—'}</div>
              <div style={{ color: '#9ca3af', fontSize: 12, marginTop: 8 }}>
                {[
                  np.bpm != null ? `${Number(np.bpm).toFixed(0)} BPM` : null,
                  np.key || null,
                  np.source || null,
                ]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </div>
            </>
          ) : (
            <p style={{ color: '#9ca3af', margin: 0 }}>No track yet</p>
          )}
        </div>

        <h3 style={{ ...h, fontSize: 15 }}>Manual set</h3>
        <label style={lab}>
          Artist
          <input value={manualArtist} onChange={(e) => setManualArtist(e.target.value)} style={inp} />
        </label>
        <label style={lab}>
          Title
          <input
            value={manualTitle}
            onChange={(e) => setManualTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && setManual()}
            style={inp}
          />
        </label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" style={btn(true, '#7c3aed')} onClick={setManual}>
            Set now playing
          </button>
          <button type="button" style={btn(false, '#374151')} onClick={clearManual}>
            Clear
          </button>
        </div>

        <h3 style={{ ...h, fontSize: 15, marginTop: 24 }}>History</h3>
        <div style={{ maxHeight: 360, overflow: 'auto' }}>
          {history.length === 0 && <p style={{ color: '#9ca3af' }}>No history yet.</p>}
          {history.map((t, i) => (
            <div
              key={`${t.started_at || i}-${t.title}-${t.artist}`}
              style={{
                padding: 10,
                marginBottom: 8,
                background: '#1f2937',
                borderRadius: 8,
                border: '1px solid #374151',
                fontSize: 13,
              }}
            >
              <strong>{t.title || 'Untitled'}</strong>
              <div style={{ color: '#9ca3af', marginTop: 2 }}>
                {t.artist || '—'}
                {t.source ? ` · ${t.source}` : ''}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Toggle({ label, checked, onChange }) {
  return (
    <label style={{ display: 'flex', gap: 8, margin: '10px 0', color: '#e5e7eb', alignItems: 'center' }}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
