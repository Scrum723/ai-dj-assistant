import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api.js';
import StreamLinks from './StreamLinks.jsx';

function nickKey() {
  return 'halodj.room.nick';
}

function roomWsUrl() {
  const host = import.meta.env.DEV ? '127.0.0.1:8000' : window.location.host;
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${host}/ws/room`;
}

const BADGE = {
  youtube: { label: 'YT', color: '#ef4444' },
  twitch: { label: 'TW', color: '#a855f7' },
  kick: { label: 'KK', color: '#22c55e' },
  tiktok: { label: 'TT', color: '#ec4899' },
  x: { label: 'X', color: '#9ca3af' },
  facebook: { label: 'FB', color: '#3b82f6' },
  rumble: { label: 'RB', color: '#f59e0b' },
  room: { label: 'ROOM', color: '#10b981' },
  halo: { label: 'HALO', color: '#c4b5fd' },
};

function PlatformBadge({ platform }) {
  const b = BADGE[platform] || { label: (platform || '?').slice(0, 3).toUpperCase(), color: '#6b7280' };
  return (
    <span style={{
      display: 'inline-block', marginRight: 8, padding: '2px 6px', borderRadius: 4,
      fontSize: 10, fontWeight: 800, letterSpacing: 0.6, background: b.color, color: '#0b0b0b',
    }}>{b.label}</span>
  );
}

export default function Room({ state, refresh }) {
  const agent = state?.agent || {};
  const settings = state?.settings || {};
  const [nick, setNick] = useState(() => localStorage.getItem(nickKey()) || '');
  const [joined, setJoined] = useState(!!localStorage.getItem(nickKey()));
  const [text, setText] = useState('');
  const [mode, setMode] = useState('chat');
  const [liveChat, setLiveChat] = useState([]);
  const seenIds = useRef(new Set());

  useEffect(() => {
    if (joined && nick) localStorage.setItem(nickKey(), nick);
  }, [joined, nick]);

  useEffect(() => {
    let ws;
    let closed = false;
    const connect = () => {
      if (closed) return;
      ws = new WebSocket(roomWsUrl());
      ws.onmessage = (event) => {
        let msg;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type === 'history' && Array.isArray(msg.payload)) {
          const next = [];
          for (const item of msg.payload) {
            if (item?.id && seenIds.current.has(item.id)) continue;
            if (item?.id) seenIds.current.add(item.id);
            next.push(item);
          }
          setLiveChat(next);
          return;
        }
        if (msg.type === 'chat' && msg.payload) {
          const item = msg.payload;
          if (item.id && seenIds.current.has(item.id)) return;
          if (item.id) seenIds.current.add(item.id);
          setLiveChat((prev) => [...prev, item].slice(-200));
        }
      };
      ws.onclose = () => {
        if (!closed) setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (ws) ws.close();
    };
  }, []);

  const viewers = agent.viewers || [];
  const fallbackChat = agent.chat || [];
  const chat = liveChat.length ? liveChat : fallbackChat.map((line) => ({
    id: `${line.platform || 'room'}:${line.ts}:${line.author}`,
    platform: line.platform || 'room',
    user: { display_name: line.author, handle: line.author },
    text: line.message,
    ts: line.ts,
  }));
  const track = state?.current_track;
  const haloName = agent.name || 'Halo';
  const room = settings.room || {};

  const send = async () => {
    if (!text.trim() || !nick.trim()) return;
    const payload = text.trim();
    const message = mode === 'request' ? `play ${payload}` : mode === 'question' ? payload.replace(/\?*$/, '?') : payload;
    await api('/chat', { method: 'POST', body: JSON.stringify({ author: nick.trim(), message, platform: 'room' }) });
    setText('');
    refresh();
  };

  const wave = async (who) => {
    if (!nick) return;
    await api('/chat', { method: 'POST', body: JSON.stringify({ author: nick, message: `hey ${who}, just waving from the room`, platform: 'room' }) });
  };

  const title = useMemo(() => {
    const name = track?.filename || '';
    return name.replace(/\s*-\s*\d{1,2}:\d{1,2}:\d{2}.*$/, '').replace(/\.[^.]+$/, '') || 'Waiting on a track';
  }, [track]);

  const urls = state?.room_urls || {};
  const copy = (value) => {
    if (!value) return;
    navigator.clipboard?.writeText(value);
  };

  if (!joined) {
    return (
      <div style={{ maxWidth: 480, margin: '40px auto', ...card, textAlign: 'center' }}>
        <h1 style={{ color: '#a78bfa' }}>DJ Bot Botty room</h1>
        <p style={{ color: '#9ca3af' }}>Pick a name. Chat, request, ask {haloName} (the LLM buddy) a question, or say hey to someone already here.</p>
        <RoomUrls urls={urls} copy={copy} />
        <input value={nick} onChange={(e) => setNick(e.target.value)} placeholder="Your name" style={inp} onKeyDown={(e) => e.key === 'Enter' && nick.trim() && setJoined(true)} />
        <button onClick={() => nick.trim() && setJoined(true)} style={btn}>Join</button>
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 24, minHeight: 560 }}>
      <aside style={card}>
        <RoomUrls urls={state?.room_urls} copy={copy} />
        <div style={{ color: '#c4b5fd', fontSize: 12, letterSpacing: 1.4, fontWeight: 700, marginTop: 16 }}>NOW PLAYING</div>
        <h3 style={{ margin: '8px 0 16px' }}>{title}</h3>
        <div style={{ margin: '16px 0 18px' }}>
          <div style={{ color: '#c4b5fd', fontSize: 12, letterSpacing: 1.4, fontWeight: 700, marginBottom: 8 }}>WATCH LIVE</div>
          <StreamLinks settings={settings} compact />
        </div>
        <div style={{ color: '#c4b5fd', fontSize: 12, letterSpacing: 1.4, fontWeight: 700 }}>IN THE ROOM ({viewers.length})</div>
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 10 }}>
          {viewers.map((v) => (
            <li key={v} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #2d2d2d' }}>
              <span>{v}</span>
              {v !== nick && <button onClick={() => wave(v)} style={tiny}>wave</button>}
            </li>
          ))}
        </ul>
      </aside>
      <section style={{ ...card, display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 420 }}>
          {chat.map((line, i) => {
            const author = line.user?.display_name || line.user?.handle || line.author || 'Fan';
            const body = line.text ?? line.message ?? '';
            const platform = line.platform || 'room';
            return (
              <div key={line.id || `${line.ts}-${i}`} style={{ padding: 10, borderRadius: 8, background: author === haloName || platform === 'halo' ? '#3b0764' : '#1f2937' }}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
                  <PlatformBadge platform={platform} />
                  <strong style={{ color: author === haloName || platform === 'halo' ? '#e9d5ff' : '#6ee7b7' }}>{author}</strong>
                </div>
                <div>{body}</div>
              </div>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 8, margin: '16px 0 10px' }}>
          {room.allow_chat !== false && <ModeChip active={mode === 'chat'} onClick={() => setMode('chat')}>Chat</ModeChip>}
          {room.allow_requests !== false && <ModeChip active={mode === 'request'} onClick={() => setMode('request')}>Request</ModeChip>}
          {room.allow_questions !== false && <ModeChip active={mode === 'question'} onClick={() => setMode('question')}>Ask {haloName}</ModeChip>}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder={mode === 'request' ? 'Track name…' : mode === 'question' ? 'Ask anything…' : 'Say hey, make a friend…'}
            style={{ ...inp, margin: 0 }}
          />
          <button onClick={send} style={btn}>Send</button>
        </div>
      </section>
    </div>
  );
}

function RoomUrls({ urls, copy }) {
  const local = urls?.local || urls?.short || 'http://127.0.0.1:8000/room';
  const lan = urls?.lan || [];
  return (
    <div style={{ textAlign: 'left', margin: '12px 0 18px', padding: 12, background: '#111827', borderRadius: 8, fontSize: 13 }}>
      <div style={{ color: '#c4b5fd', fontWeight: 700, marginBottom: 8 }}>ROOM URL</div>
      <button type="button" onClick={() => copy(local)} style={urlBtn}>{local}</button>
      {lan.map((u) => (
        <button key={u} type="button" onClick={() => copy(u)} style={urlBtn}>{u} · Wi-Fi</button>
      ))}
      <p style={{ color: '#9ca3af', margin: '8px 0 0', fontSize: 12 }}>This Mac: local URL. Phones on the same Wi-Fi: the Wi-Fi URL. TikTok chat cannot be joined natively.</p>
    </div>
  );
}

function ModeChip({ active, onClick, children }) {
  return (
    <button onClick={onClick} style={{
      padding: '8px 12px', borderRadius: 999, border: 'none', cursor: 'pointer',
      background: active ? '#7c3aed' : '#374151', color: 'white', fontWeight: 700
    }}>{children}</button>
  );
}

const card = { background: '#1e1e1e', padding: 20, borderRadius: 12, border: '1px solid #2d2d2d' };
const inp = { flex: 1, width: '100%', padding: 12, borderRadius: 8, border: '1px solid #374151', background: '#111827', color: 'white', boxSizing: 'border-box', marginBottom: 12 };
const btn = { padding: '12px 18px', border: 'none', borderRadius: 8, background: '#10b981', color: 'white', fontWeight: 700, cursor: 'pointer' };
const tiny = { padding: '4px 8px', border: 'none', borderRadius: 6, background: '#374151', color: 'white', cursor: 'pointer' };
const urlBtn = {
  display: 'block', width: '100%', textAlign: 'left', marginTop: 6, padding: '8px 10px',
  border: '1px solid #374151', borderRadius: 6, background: '#1f2937', color: '#e5e7eb',
  cursor: 'pointer', fontSize: 12, wordBreak: 'break-all'
};
