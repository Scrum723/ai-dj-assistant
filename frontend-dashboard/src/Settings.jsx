import { useEffect, useState } from 'react';
import { api } from './api.js';
import { PLATFORMS } from './StreamLinks.jsx';

const CAPS = [
  ['greet', 'Greet new people'],
  ['requests', 'Take song requests'],
  ['questions', 'Answer questions'],
  ['idle_talk', 'Talk during quiet stretches'],
  ['hype', 'Hype the chat'],
  ['introduce_fans', 'Introduce fans to each other'],
  ['midi', 'Allow MIDI deck takeover'],
];

export default function Settings({ state, refresh }) {
  const [form, setForm] = useState(null);
  const [voices, setVoices] = useState([]);
  const [saved, setSaved] = useState('');
  const [eleven, setEleven] = useState('');

  useEffect(() => {
    setForm(state?.settings ? structuredClone(state.settings) : null);
  }, [state?.settings]);

  useEffect(() => {
    api('/voices').then((d) => setVoices(d.macos || [])).catch(() => {});
  }, []);

  if (!form) return <p style={{ color: '#9ca3af' }}>Loading settings…</p>;

  const halo = form.halo;
  const env = form.environment;
  const setHalo = (patch) => setForm({ ...form, halo: { ...halo, ...patch } });
  const setEnv = (patch) => setForm({ ...form, environment: { ...env, ...patch } });
  const setCap = (key, value) => setHalo({ capabilities: { ...halo.capabilities, [key]: value } });

  const save = async () => {
    const payload = { ...form };
    if (eleven.trim()) payload.halo = { ...payload.halo, elevenlabs_key: eleven.trim() };
    await api('/settings', { method: 'POST', body: JSON.stringify(payload) });
    setEleven('');
    setSaved('Saved. Voice and energy take effect on the next line Halo speaks.');
    refresh();
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      <section style={card}>
        <h2 style={h}>Halo (LLM buddy)</h2>
        <label style={lab}>Name
          <input value={halo.name} onChange={(e) => setHalo({ name: e.target.value })} style={inp} />
        </label>
        <label style={lab}>Persona
          <textarea value={halo.persona} onChange={(e) => setHalo({ persona: e.target.value })} style={{ ...inp, minHeight: 120 }} />
        </label>
        <label style={lab}>Voice engine
          <select value={halo.voice_provider} onChange={(e) => setHalo({ voice_provider: e.target.value })} style={inp}>
            <option value="macos">macOS (free, always on this Mac)</option>
            <option value="elevenlabs">ElevenLabs (best, needs API key)</option>
            <option value="browser">Browser voice (fallback)</option>
          </select>
        </label>
        {halo.voice_provider === 'macos' && (
          <label style={lab}>macOS voice
            <select value={halo.voice_id} onChange={(e) => setHalo({ voice_id: e.target.value })} style={inp}>
              {voices.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        )}
        {halo.voice_provider === 'elevenlabs' && (
          <>
            <p style={{ color: '#9ca3af', fontSize: 13 }}>
              Optional. You do not need ElevenLabs for Halo to talk — macOS Samantha/Ava is already much better than the old browser voice.
              Add a key only if you want a custom studio voice. {halo.elevenlabs_key_set ? 'A key is already saved.' : 'No key saved yet.'}
            </p>
            <label style={lab}>ElevenLabs API key
              <input type="password" value={eleven} onChange={(e) => setEleven(e.target.value)} placeholder="xi-..." style={inp} />
            </label>
            <label style={lab}>Voice ID
              <input value={halo.elevenlabs_voice_id} onChange={(e) => setHalo({ elevenlabs_voice_id: e.target.value })} style={inp} />
            </label>
          </>
        )}
        <h3 style={{ ...h, fontSize: 16, marginTop: 18 }}>What Halo is allowed to do</h3>
        {CAPS.map(([key, label]) => (
          <label key={key} style={{ display: 'flex', gap: 8, margin: '8px 0', color: '#e5e7eb' }}>
            <input type="checkbox" checked={!!halo.capabilities?.[key]} onChange={(e) => setCap(key, e.target.checked)} />
            {label}
          </label>
        ))}
      </section>

      <section style={card}>
        <h2 style={h}>Environment & efficiency</h2>
        <label style={lab}>Energy mode
          <select value={env.energy_mode} onChange={(e) => setEnv({ energy_mode: e.target.value })} style={inp}>
            <option value="eco">Eco — quiet, local lines, least CPU/API</option>
            <option value="balanced">Balanced — social, still gentle on quota</option>
            <option value="show">Show — more chatter, more presence</option>
          </select>
        </label>
        <label style={lab}>Room vibe
          <select value={env.vibe} onChange={(e) => setEnv({ vibe: e.target.value })} style={inp}>
            <option value="night">Night booth</option>
            <option value="storm">Storm</option>
            <option value="club">Club</option>
            <option value="chill">Chill</option>
            <option value="weather">Weather desk</option>
          </select>
        </label>
        <label style={lab}>Distraction {env.distraction}
          <input type="range" min="0" max="100" value={env.distraction} onChange={(e) => setEnv({ distraction: Number(e.target.value) })} style={{ width: '100%' }} />
          <span style={{ color: '#9ca3af', fontSize: 12 }}>Low = Halo stays out of the way. High = more idle talk and overlay motion.</span>
        </label>
        <label style={lab}>Autonomy {form.autonomy}
          <input type="range" min="1" max="100" value={form.autonomy} onChange={(e) => setForm({ ...form, autonomy: Number(e.target.value) })} style={{ width: '100%' }} />
        </label>
        <label style={{ display: 'flex', gap: 8, margin: '12px 0', color: '#e5e7eb' }}>
          <input type="checkbox" checked={!!form.demo} onChange={(e) => setForm({ ...form, demo: e.target.checked })} />
          Demo crowd (off when you are actually live)
        </label>
        <label style={{ display: 'flex', gap: 8, margin: '12px 0', color: '#e5e7eb' }}>
          <input type="checkbox" checked={!!halo.listen_mic} onChange={(e) => setHalo({ listen_mic: e.target.checked })} />
          Halo may listen to this Mac’s mic (off in DJ mode)
        </label>
        <label style={lab}>YouTube live video ID
          <input value={form.youtube_id || ''} onChange={(e) => setForm({ ...form, youtube_id: e.target.value })} style={inp} />
        </label>
        <h3 style={{ ...h, fontSize: 16, marginTop: 18 }}>Stream links (open all at once)</h3>
        {PLATFORMS.map((p) => (
          <label key={p.key} style={lab}>{p.label}
            <input
              value={(form.streams || {})[p.key] || ''}
              onChange={(e) => setForm({ ...form, streams: { ...(form.streams || {}), [p.key]: e.target.value } })}
              placeholder={`https://…`}
              style={inp}
            />
          </label>
        ))}
        <button onClick={save} style={btn}>Save settings</button>
        {saved && <p style={{ color: '#6ee7b7' }}>{saved}</p>}
      </section>
    </div>
  );
}

const card = { background: '#1e1e1e', padding: 24, borderRadius: 12, border: '1px solid #2d2d2d' };
const h = { margin: '0 0 16px', fontSize: 20 };
const lab = { display: 'block', color: '#d1d5db', fontSize: 13, marginBottom: 12 };
const inp = { width: '100%', marginTop: 6, padding: 10, borderRadius: 8, border: '1px solid #374151', background: '#111827', color: 'white', boxSizing: 'border-box' };
const btn = { marginTop: 12, padding: '12px 18px', border: 'none', borderRadius: 8, background: '#7c3aed', color: 'white', fontWeight: 700, cursor: 'pointer' };
