import React, { useCallback, useEffect, useState } from 'react';
import { Play, Pause, FastForward, Music, Radio, Smartphone, Wifi, WifiOff, Bot, Users } from 'lucide-react';
import { api, wsUrl } from './api.js';
import Settings from './Settings.jsx';
import Room from './Room.jsx';
import StreamLinks, { openAllStreams } from './StreamLinks.jsx';

const VIBES = {
  night: '#121212',
  storm: '#0b1220',
  club: '#1a0820',
  chill: '#0c1412',
  weather: '#10141c',
};

export default function App() {
  const [activeTab, setActiveTab] = useState(() => new URLSearchParams(window.location.search).get('tab') || 'dj');
  const [state, setState] = useState(null);
  const [tracks, setTracks] = useState([]);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const nextState = await api('/state');
      setState(nextState);
      setError('');
    } catch (err) {
      setError('Cannot reach DJ Bot Botty on port 8000. Run start.py first.');
    }
  }, []);

  useEffect(() => {
    refresh();
    api('/tracks').then((nextTracks) => {
      setTracks(Array.isArray(nextTracks) ? nextTracks : nextTracks.tracks || []);
    }).catch(() => {});
    const timer = setInterval(refresh, 20000);
    let ws;
    let closed = false;
    const connect = () => {
      if (closed) return;
      ws = new WebSocket(wsUrl());
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'state') setState(msg.payload);
      };
      ws.onclose = () => {
        if (!closed) setTimeout(connect, 2500);
      };
    };
    connect();
    return () => {
      closed = true;
      clearInterval(timer);
      if (ws) ws.close();
    };
  }, [refresh]);

  const vibe = state?.settings?.environment?.vibe || state?.agent?.vibe || 'night';
  const djMode = !!state?.agent?.dj_mode;
  const toggleDj = async () => {
    await api('/agent/dj', { method: 'POST', body: JSON.stringify({ enabled: !djMode }) });
    refresh();
  };

  useEffect(() => {
    const listen = state?.settings?.halo?.listen_mic && !djMode;
    if (!listen || !('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) return;
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (ev) => {
      const said = ev.results[ev.results.length - 1][0].transcript;
      if (said) api('/chat', { method: 'POST', body: JSON.stringify({ author: 'Charles', message: said, platform: 'mic' }) });
    };
    try { rec.start(); } catch { /* mic permission or already started */ }
    return () => { try { rec.stop(); } catch { /* ignore */ } };
  }, [state?.settings?.halo?.listen_mic, djMode]);

  return (
    <div style={{ backgroundColor: VIBES[vibe] || '#121212', color: '#fff', minHeight: '100vh', fontFamily: 'system-ui, sans-serif' }}>
      <nav style={{ display: 'flex', padding: '20px', backgroundColor: '#1e1e1e', borderBottom: '2px solid #7c3aed', justifyContent: 'space-between', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
          <Radio color="#7c3aed" size={32} />
          <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 'bold', letterSpacing: '1px' }}>DJ BOT BOTTY</h1>
        </div>
        <StatusPills state={state} error={error} />
        <div style={{ display: 'flex', gap: '15px' }}>
          <button onClick={toggleDj} style={navBtnStyle(true, djMode ? '#f59e0b' : '#374151')}>
            {djMode ? 'DJ MODE — Halo quiet' : 'Halo (LLM) live'}
          </button>
          <button onClick={() => openAllStreams(state?.settings)} style={navBtnStyle(true, '#ff0033')}>
            Open all streams
          </button>
          <button onClick={() => setActiveTab('dj')} style={navBtnStyle(activeTab === 'dj', '#7c3aed')}>Booth</button>
          <button onClick={() => setActiveTab('host')} style={navBtnStyle(activeTab === 'host', '#db2777')}>{state?.agent?.name || 'Halo'}</button>
          <button onClick={() => setActiveTab('room')} style={navBtnStyle(activeTab === 'room', '#10b981')}>Room</button>
          <button onClick={() => setActiveTab('fan')} style={navBtnStyle(activeTab === 'fan', '#059669')}>Requests</button>
          <button onClick={() => setActiveTab('settings')} style={navBtnStyle(activeTab === 'settings', '#6366f1')}>Settings</button>
        </div>
      </nav>

      <div style={{ padding: '30px', maxWidth: '1200px', margin: '0 auto' }}>
        {error && <div style={{ ...cardStyle, borderLeft: '4px solid #ef4444', marginBottom: 20 }}>{error}</div>}
        {activeTab === 'dj' && <DJControls state={state} tracks={tracks} refresh={refresh} />}
        {activeTab === 'host' && <CohostPanel state={state} refresh={refresh} />}
        {activeTab === 'room' && <Room state={state} refresh={refresh} />}
        {activeTab === 'fan' && <FanHub refresh={refresh} />}
        {activeTab === 'settings' && <Settings state={state} refresh={refresh} />}
      </div>
    </div>
  );
}

function StatusPills({ state, error }) {
  const midiOk = state?.midi_connected;
  const apiOk = !!state && !error;
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
      <Pill ok={apiOk} label={apiOk ? 'API online' : 'API offline'} />
      <Pill ok={midiOk} label={midiOk ? `MIDI: ${state.midi_port}` : 'MIDI offline'} />
      <Pill ok={!!state?.agent?.running && !state?.agent?.dj_mode} label={state?.agent?.dj_mode ? 'DJ mode' : (state?.agent?.running ? `${state.agent.name || 'Halo'} ${state.agent.mood || 'live'}` : 'Halo offline')} />
      <Pill ok={(state?.settings?.environment?.energy_mode || 'balanced') === 'eco'} label={state?.settings?.environment?.energy_mode || 'balanced'} />
    </div>
  );
}

function Pill({ ok, label }) {
  const Icon = ok ? Wifi : WifiOff;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '6px 12px', borderRadius: 999,
      backgroundColor: ok ? '#064e3b' : '#3f1d1d',
      color: ok ? '#6ee7b7' : '#fca5a5', fontSize: 13, fontWeight: 600
    }}>
      <Icon size={14} /> {label}
    </span>
  );
}

function DJControls({ state, tracks, refresh }) {
  const current = state?.current_track;
  const playing = !!state?.playing;
  const queue = state?.queue || [];
  const keys = state?.compatible_keys || [];

  const play = async () => {
    await api('/action/play', { method: 'POST', body: JSON.stringify({ deck: 1 }) });
    refresh();
  };
  const skip = async () => {
    await api('/action/skip', { method: 'POST', body: '{}' });
    refresh();
  };
  const load = async (id) => {
    await api('/action/load', { method: 'POST', body: JSON.stringify({ track_id: id }) });
    refresh();
  };
  const setAutonomy = async (value) => {
    await api('/action/autonomy', { method: 'POST', body: JSON.stringify({ value: Number(value) }) });
  };
  const setCrossfader = async (value) => {
    await api('/action/crossfade', { method: 'POST', body: JSON.stringify({ value: Number(value) }) });
  };

  const title = current?.filename || 'No track loaded';
  const bpm = current?.bpm ? Number(current.bpm).toFixed(1) : '--';
  const key = current?.key || '--';

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '30px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '30px' }}>
        <div style={cardStyle}>
          <h2 style={headerStyle}><Music size={20} /> Currently Playing (Deck A)</h2>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '20px', gap: 16, flexWrap: 'wrap' }}>
            <div>
              <h3 style={{ margin: 0, fontSize: '1.6rem', color: '#a78bfa' }}>{title}</h3>
              <p style={{ margin: '5px 0 0 0', color: '#9ca3af', fontSize: '1rem' }}>
                {current ? 'Loaded from your analyzed library' : 'Pick a track from the library or hit play'}
              </p>
            </div>
            <div style={{ textAlign: 'right' }}>
              <h1 style={{ margin: 0, fontSize: '3rem' }}>{bpm} <span style={{ fontSize: '1rem', color: '#9ca3af' }}>BPM</span></h1>
              <div style={{ display: 'inline-block', padding: '5px 15px', backgroundColor: '#7c3aed', borderRadius: '20px', fontWeight: 'bold', marginTop: '5px' }}>
                Key: {key}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '20px', marginTop: '30px', justifyContent: 'center' }}>
            <button onClick={play} style={controlBtnStyle('#7c3aed')} aria-label="Play or pause">
              {playing ? <Pause size={30} /> : <Play size={30} />}
            </button>
            <button onClick={skip} style={controlBtnStyle('#3b82f6')} aria-label="Skip">
              <FastForward size={30} />
            </button>
          </div>

          <label style={{ display: 'block', marginTop: 24, color: '#9ca3af', fontSize: 14 }}>
            Crossfader (A ← → B)
            <input
              type="range" min="0" max="1" step="0.01"
              value={state?.crossfader ?? 0}
              onChange={(e) => setCrossfader(e.target.value)}
              style={{ width: '100%', marginTop: 8 }}
            />
          </label>
        </div>

        <div style={{ ...cardStyle, borderLeft: '4px solid #f59e0b' }}>
          <h2 style={headerStyle}>AI Music Theory Guide</h2>
          <p style={{ lineHeight: '1.6', color: '#d1d5db' }}>
            {current
              ? <>The current track is in <strong>{key}</strong>. Smooth mixes usually stay in {keys.join(', ') || key}.</>
              : 'Load a track to get a harmonic mix suggestion from the analyzed library.'}
          </p>
        </div>

        <div style={cardStyle}>
          <h2 style={headerStyle}>Library ({tracks.length})</h2>
          <div style={{ maxHeight: 280, overflow: 'auto', marginTop: 16 }}>
            {tracks.length === 0 && <p style={{ color: '#9ca3af' }}>No analyzed tracks yet. Run audio_analyzer.py on your music folder.</p>}
            {tracks.map((track) => (
              <button
                key={track.id}
                onClick={() => load(track.id)}
                style={{
                  width: '100%', textAlign: 'left', padding: 12, marginBottom: 8,
                  backgroundColor: current?.id === track.id ? '#3b0764' : '#1f2937',
                  color: 'white', border: '1px solid #374151', borderRadius: 8, cursor: 'pointer'
                }}
              >
                <strong>{track.filename}</strong>
                <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 4 }}>
                  {Number(track.bpm).toFixed(1)} BPM · Key {track.key}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '30px' }}>
        <div style={cardStyle}>
          <h2 style={headerStyle}>AI Autonomy Level</h2>
          <input
            type="range" min="1" max="100"
            value={state?.autonomy ?? 75}
            onChange={(e) => setAutonomy(e.target.value)}
            style={{ width: '100%', marginTop: '20px', cursor: 'pointer' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', color: '#9ca3af', fontSize: '0.9rem', marginTop: '10px' }}>
            <span>Manual</span>
            <span>{state?.autonomy ?? 75}%</span>
            <span>Autopilot</span>
          </div>
        </div>

        <div style={{ ...cardStyle, borderLeft: '4px solid #ff0033' }}>
          <h2 style={headerStyle}>Live everywhere</h2>
          <p style={{ color: '#9ca3af', fontSize: 13, marginTop: 12 }}>
            One click opens YouTube, X, Facebook, TikTok, and Rumble together.
          </p>
          <StreamLinks settings={state?.settings} />
        </div>

        <div style={cardStyle}>
          <h2 style={headerStyle}>Live Request Queue</h2>
          <ul style={{ listStyle: 'none', padding: 0, margin: '20px 0 0 0' }}>
            {queue.length === 0 && <p style={{ color: '#9ca3af' }}>No requests yet. Use the Fan Hub or chat !request.</p>}
            {queue.map((item) => (
              <QueueItem
                key={item.id}
                song={item.filename || item.query}
                requester={item.requester}
                via={item.via}
                found={item.found}
              />
            ))}
          </ul>
        </div>

        <div style={cardStyle}>
          <h2 style={headerStyle}>rekordbox MIDI map</h2>
          <p style={{ color: '#9ca3af', fontSize: 13, lineHeight: 1.5 }}>
            This app does not install inside Pioneer DJ software. It creates a virtual MIDI port named <strong>AI_DJ_Virtual_Port</strong>. In rekordbox, open MIDI settings, enable that device, then MIDI-learn:
          </p>
          <ul style={{ color: '#d1d5db', fontSize: 13, lineHeight: 1.7 }}>
            <li>CC 10 — Deck A play/pause</li>
            <li>CC 11 — Deck B play/pause</li>
            <li>CC 12 — Crossfader</li>
            <li>CC 20–22 — Deck A EQ low/mid/hi</li>
            <li>CC 23–25 — Deck B EQ low/mid/hi</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

function CohostPanel({ state, refresh }) {
  const agent = state?.agent || {};
  const [youtube, setYoutube] = useState(agent.youtube_id || '');
  const [asName, setAsName] = useState('Charles');
  const [asMsg, setAsMsg] = useState('');

  const toggleDemo = async () => {
    await api('/agent/demo', { method: 'POST', body: JSON.stringify({ enabled: !agent.demo }) });
    refresh();
  };
  const saveYoutube = async () => {
    await api('/agent/youtube', { method: 'POST', body: JSON.stringify({ video_id: youtube.trim() }) });
    refresh();
  };
  const sendChat = async () => {
    if (!asMsg.trim()) return;
    await api('/chat', { method: 'POST', body: JSON.stringify({ author: asName || 'Fan', message: asMsg.trim(), platform: 'dashboard' }) });
    setAsMsg('');
    refresh();
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 30 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div style={{ ...cardStyle, borderLeft: '4px solid #db2777' }}>
          <h2 style={headerStyle}><Bot size={20} /> Halo — DJ Bot Botty’s LLM buddy</h2>
          <p style={{ color: '#d1d5db', lineHeight: 1.6, marginTop: 16 }}>
            Halo talks on her own: greets new people, answers questions, queues requests, and fills silence so the overlay is never stuck on one sentence. Open <strong>http://127.0.0.1:5174</strong> as an OBS Browser source (click the page once so the browser allows voice).
          </p>
          <div style={{ display: 'flex', gap: 12, marginTop: 18, flexWrap: 'wrap' }}>
            <button onClick={toggleDemo} style={{ ...navBtnStyle(true, agent.demo ? '#f59e0b' : '#374151') }}>
              {agent.demo ? 'Demo crowd ON' : 'Demo crowd OFF'}
            </button>
            <span style={{ color: '#9ca3af', alignSelf: 'center' }}>
              {agent.replies || 0} lines · {agent.greets || 0} welcomes · {agent.requests_handled || 0} requests
            </span>
          </div>
        </div>

        <div style={cardStyle}>
          <h2 style={headerStyle}>Talk to Halo (test)</h2>
          <p style={{ color: '#9ca3af', fontSize: 14 }}>Type as a viewer. Halo should answer on the overlay.</p>
          <input value={asName} onChange={(e) => setAsName(e.target.value)} placeholder="Viewer name" style={{ ...inputStyle, width: '100%' }} />
          <input
            value={asMsg}
            onChange={(e) => setAsMsg(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && sendChat()}
            placeholder="hey halo! play Mad World"
            style={{ ...inputStyle, width: '100%' }}
          />
          <button onClick={sendChat} style={{ ...navBtnStyle(true, '#db2777') }}>Send to Halo</button>
        </div>

        <div style={cardStyle}>
          <h2 style={headerStyle}>YouTube live chat</h2>
          <p style={{ color: '#9ca3af', fontSize: 14 }}>Paste the live video ID (the 11-character id in the YouTube URL). Leave blank if you are not live.</p>
          <input value={youtube} onChange={(e) => setYoutube(e.target.value)} placeholder="e.g. dQw4w9WgXcQ" style={{ ...inputStyle, width: '100%' }} />
          <button onClick={saveYoutube} style={{ ...navBtnStyle(true, '#3b82f6') }}>Listen to this live</button>
        </div>
      </div>

      <div style={cardStyle}>
        <h2 style={headerStyle}><Users size={20} /> Room ({agent.viewer_count || 0})</h2>
        <div style={{ maxHeight: 520, overflow: 'auto', marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {(agent.chat || []).length === 0 && <p style={{ color: '#9ca3af' }}>Halo will start talking in a couple of seconds...</p>}
          {(agent.chat || []).slice().reverse().map((line, i) => (
            <div key={`${line.ts}-${i}`} style={{ padding: 12, background: line.author === 'Halo' ? '#3b0764' : '#1f2937', borderRadius: 8 }}>
              <strong style={{ color: line.author === 'Halo' ? '#e9d5ff' : '#6ee7b7' }}>{line.author}</strong>
              <div style={{ color: '#e5e7eb', marginTop: 4 }}>{line.message}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function FanHub({ refresh }) {
  const [query, setQuery] = useState('');
  const [name, setName] = useState('Fan');
  const [status, setStatus] = useState('');

  const send = async () => {
    if (!query.trim()) return;
    const data = await api('/request', {
      method: 'POST',
      body: JSON.stringify({ query: query.trim(), requester: name.trim() || 'Fan', via: 'Web Hub' }),
    });
    setStatus(data.status === 'queued'
      ? `Queued ${data.item?.filename || query}`
      : `No match for "${query}" — still added to the DJ queue.`);
    setQuery('');
    refresh();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', marginTop: '40px' }}>
      <div style={{ ...cardStyle, width: '100%', maxWidth: '600px', textAlign: 'center' }}>
        <h1 style={{ color: '#10b981', margin: '0 0 10px 0' }}>Request a Song!</h1>
        <p style={{ color: '#9ca3af', marginBottom: '30px' }}>Drop your request and the AI DJ will try to mix it into the set.</p>
        <input
          type="text"
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={inputStyle}
        />
        <input
          type="text"
          placeholder="e.g. Mad World or DNB Bloodrave"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          style={inputStyle}
        />
        <button onClick={send} style={{ width: '95%', padding: '15px', backgroundColor: '#10b981', color: 'white', border: 'none', borderRadius: '8px', fontSize: '1.2rem', fontWeight: 'bold', cursor: 'pointer' }}>
          Send to DJ
        </button>
        {status && <p style={{ color: '#6ee7b7', marginTop: 16 }}>{status}</p>}
        <div style={{ margin: '40px 0', borderBottom: '1px solid #374151' }}></div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '15px', color: '#9ca3af' }}>
          <Smartphone size={32} color="#3b82f6" />
          <div style={{ textAlign: 'left' }}>
            <h3 style={{ margin: 0, color: '#fff' }}>Chat request</h3>
            <p style={{ margin: '5px 0 0 0' }}>Use <strong>!request song name</strong> in the stream bot</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function QueueItem({ song, requester, via, found }) {
  return (
    <li style={{ padding: '15px', backgroundColor: '#1f2937', marginBottom: '10px', borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
      <div>
        <h4 style={{ margin: 0, fontSize: '1.05rem' }}>{song}</h4>
        <p style={{ margin: '5px 0 0 0', fontSize: '0.85rem', color: '#9ca3af' }}>Requested by {requester}{found === false ? ' · no exact match' : ''}</p>
      </div>
      <span style={{ fontSize: '0.75rem', padding: '5px 10px', backgroundColor: '#374151', borderRadius: '15px', color: '#d1d5db' }}>{via}</span>
    </li>
  );
}

const inputStyle = {
  width: '90%', padding: '15px', borderRadius: '8px', border: '1px solid #374151',
  backgroundColor: '#111827', color: 'white', fontSize: '1.1rem', marginBottom: '16px'
};

const cardStyle = {
  backgroundColor: '#1e1e1e',
  padding: '25px',
  borderRadius: '12px',
  boxShadow: '0 4px 6px rgba(0,0,0,0.3)',
  border: '1px solid #2d2d2d'
};

const headerStyle = {
  margin: 0,
  fontSize: '1.2rem',
  color: '#e5e7eb',
  display: 'flex',
  alignItems: 'center',
  gap: '10px',
  borderBottom: '1px solid #374151',
  paddingBottom: '15px'
};

const navBtnStyle = (isActive, color) => ({
  padding: '10px 20px',
  borderRadius: '8px',
  border: 'none',
  backgroundColor: isActive ? color : '#333',
  color: 'white',
  cursor: 'pointer',
  fontWeight: 'bold'
});

const controlBtnStyle = (color) => ({
  width: '60px',
  height: '60px',
  borderRadius: '50%',
  backgroundColor: color,
  border: 'none',
  color: 'white',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
  boxShadow: '0 4px 10px rgba(0,0,0,0.5)'
});
