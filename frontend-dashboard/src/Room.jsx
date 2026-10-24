import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, API } from './api.js';
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

const FILTERS = ['all', 'youtube', 'twitch', 'room', 'halo'];

function PlatformBadge({ platform }) {
  const b = BADGE[platform] || { label: (platform || '?').slice(0, 3).toUpperCase(), color: '#6b7280' };
  return (
    <span style={{
      display: 'inline-block', marginRight: 8, padding: '2px 6px', borderRadius: 4,
      fontSize: 10, fontWeight: 800, letterSpacing: 0.6, background: b.color, color: '#0b0b0b',
    }}>{b.label}</span>
  );
}

function mergeById(existing, incoming, { prepend = false } = {}) {
  const seen = new Set(existing.map((m) => m.id).filter(Boolean));
  const added = [];
  for (const item of incoming) {
    if (!item) continue;
    if (item.id && seen.has(item.id)) continue;
    if (item.id) seen.add(item.id);
    added.push(item);
  }
  if (!added.length) return existing;
  const next = prepend ? [...added, ...existing] : [...existing, ...added];
  return next.slice(-500);
}

export default function Room({ state, refresh }) {
  const agent = state?.agent || {};
  const settings = state?.settings || {};
  const [nick, setNick] = useState(() => localStorage.getItem(nickKey()) || '');
  const [joined, setJoined] = useState(!!localStorage.getItem(nickKey()));
  const [text, setText] = useState('');
  const [mode, setMode] = useState('chat');
  const [liveChat, setLiveChat] = useState([]);
  const [filter, setFilter] = useState('all');
  const [presence, setPresence] = useState({ counts: {}, total: 0 });
  const [supporters, setSupporters] = useState([]);
  const [listeningFrom, setListeningFrom] = useState([]);
  const [currentSegment, setCurrentSegment] = useState(null);
  const [highlight, setHighlight] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [isMod, setIsMod] = useState(false);
  const [modMenuId, setModMenuId] = useState(null);
  const seenIds = useRef(new Set());
  const listRef = useRef(null);
  const stickBottom = useRef(true);
  const historyLoaded = useRef(false);
  const highlightTimer = useRef(null);

  useEffect(() => {
    if (joined && nick) localStorage.setItem(nickKey(), nick);
  }, [joined, nick]);

  const absorb = useCallback((items, { prepend = false } = {}) => {
    if (!items?.length) return;
    const fresh = [];
    for (const item of items) {
      if (!item) continue;
      if (item.id && seenIds.current.has(item.id)) continue;
      if (item.id) seenIds.current.add(item.id);
      fresh.push(item);
    }
    if (!fresh.length) return;
    setLiveChat((prev) => mergeById(prev, fresh, { prepend }));
  }, []);

  const loadHistory = useCallback(async (before) => {
    const q = new URLSearchParams({ limit: '100' });
    if (before != null) q.set('before', String(before));
    const res = await fetch(`${API}/community/history?${q}`);
    if (!res.ok) throw new Error('history failed');
    const body = await res.json();
    return body.messages || [];
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const msgs = await loadHistory();
        if (cancelled) return;
        absorb(msgs);
        historyLoaded.current = true;
        setHasMore(msgs.length >= 100);
      } catch {
        /* ws history is fallback */
      }
      try {
        const p = await fetch(`${API}/community/presence`).then((r) => r.json());
        if (!cancelled && p) setPresence(p);
      } catch {
        /* optional */
      }
      try {
        const s = await fetch(`${API}/community/supporters/today`).then((r) => r.json());
        if (!cancelled && s?.supporters) setSupporters(s.supporters);
      } catch {
        /* optional */
      }
      try {
        const lf = await fetch(`${API}/community/listening-from`).then((r) => r.json());
        if (!cancelled) {
          if (lf?.listening_from) setListeningFrom(lf.listening_from);
          if (lf?.current_segment !== undefined) setCurrentSegment(lf.current_segment);
        }
      } catch {
        /* optional */
      }
      try {
        const m = await fetch(`${API}/community/mod/me`, { credentials: 'include' }).then((r) => r.json());
        if (!cancelled && m?.is_mod) setIsMod(true);
      } catch {
        /* optional */
      }
    })();
    return () => { cancelled = true; };
  }, [absorb, loadHistory]);

  useEffect(() => {
    let ws;
    let closed = false;
    let attempt = 0;
    let timer;

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(roomWsUrl());
      ws.onopen = () => { attempt = 0; };
      ws.onmessage = (event) => {
        let msg;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg.type === 'history' && Array.isArray(msg.payload)) {
          // Merge — never wipe live transcript on reconnect
          absorb(msg.payload);
          return;
        }
        if (msg.type === 'presence' && msg.payload) {
          setPresence(msg.payload);
          return;
        }
        if (msg.type === 'chat' && msg.payload) {
          const item = msg.payload;
          if (item.id && seenIds.current.has(item.id)) return;
          if (item.id) seenIds.current.add(item.id);
          stickBottom.current = true;
          setLiveChat((prev) => mergeById(prev, [item]));
        }
        if (msg.type === 'highlight' && msg.payload) {
          setHighlight(msg.payload);
          if (highlightTimer.current) clearTimeout(highlightTimer.current);
          highlightTimer.current = setTimeout(() => setHighlight(null), 10000);
          fetch(`${API}/community/supporters/today`)
            .then((r) => r.json())
            .then((s) => { if (s?.supporters) setSupporters(s.supporters); })
            .catch(() => {});
        }
        if (msg.type === 'delete-message' && msg.payload?.id) {
          const mid = msg.payload.id;
          seenIds.current.delete(mid);
          setLiveChat((prev) => prev.filter((m) => m.id !== mid));
          setModMenuId((cur) => (cur === mid ? null : cur));
        }
      };
      ws.onclose = () => {
        if (closed) return;
        const delay = Math.min(30000, 1000 * (2 ** Math.min(attempt, 5)));
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      if (ws) ws.close();
    };
  }, [absorb]);

  const onScroll = async () => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickBottom.current = nearBottom;
    if (el.scrollTop > 40 || loadingMore || !hasMore || !liveChat.length) return;
    setLoadingMore(true);
    const oldest = liveChat[0]?.ts;
    const prevHeight = el.scrollHeight;
    try {
      const older = await loadHistory(oldest);
      if (!older.length) {
        setHasMore(false);
      } else {
        absorb(older, { prepend: true });
        setHasMore(older.length >= 100);
        requestAnimationFrame(() => {
          if (listRef.current) {
            listRef.current.scrollTop = listRef.current.scrollHeight - prevHeight;
          }
        });
      }
    } catch {
      /* ignore */
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    const el = listRef.current;
    if (el && stickBottom.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [liveChat]);

  useEffect(() => {
    if (agent?.listening_from) setListeningFrom(agent.listening_from);
    if (agent?.current_segment !== undefined) setCurrentSegment(agent.current_segment || null);
  }, [agent?.listening_from, agent?.current_segment]);

  const viewers = agent.viewers || [];
  const fallbackChat = agent.chat || [];
  const chat = liveChat.length ? liveChat : fallbackChat.map((line) => ({
    id: `${line.platform || 'room'}:${line.ts}:${line.author}`,
    platform: line.platform || 'room',
    user: { display_name: line.author, handle: line.author },
    text: line.message,
    ts: line.ts,
  }));
  const filtered = filter === 'all' ? chat : chat.filter((m) => (m.platform || 'room') === filter);
  const track = state?.current_track;
  const haloName = agent.name || 'Halo';
  const room = settings.room || {};
  const counts = presence.counts || {};

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

  const modAction = async (action, line) => {
    if (!isMod || !line) return;
    const author = line.user?.display_name || line.user?.handle || line.author || '';
    const handle = line.user?.handle || author;
    const body = {
      target_profile_id: line.profile_id ?? undefined,
      platform: line.platform || '',
      handle,
      reason: `${action} from room`,
      message_id: line.id,
      seconds: action === 'timeout' ? 600 : undefined,
    };
    try {
      if (action === 'delete') {
        await api('/community/mod/delete', { method: 'POST', body: JSON.stringify(body) });
      } else if (action === 'timeout') {
        await api('/community/mod/timeout', { method: 'POST', body: JSON.stringify(body) });
      } else if (action === 'ban') {
        await api('/community/mod/ban', { method: 'POST', body: JSON.stringify(body) });
      }
    } catch {
      /* ignore */
    }
    setModMenuId(null);
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
        <h3 style={{ margin: '8px 0 8px' }}>{title}</h3>
        {currentSegment?.name && (
          <div style={{
            marginBottom: 14, padding: '8px 10px', borderRadius: 8,
            background: '#312e81', border: '1px solid #818cf8', fontSize: 13,
          }}>
            <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: 1.2, color: '#c4b5fd' }}>SEGMENT</div>
            <div style={{ fontWeight: 700, textTransform: 'capitalize' }}>{String(currentSegment.name).replace(/_/g, ' ')}</div>
            {currentSegment.line && <div style={{ color: '#e0e7ff', marginTop: 4, fontSize: 12 }}>{currentSegment.line}</div>}
          </div>
        )}
        <div style={{ margin: '16px 0 18px' }}>
          <div style={{ color: '#c4b5fd', fontSize: 12, letterSpacing: 1.4, fontWeight: 700, marginBottom: 8 }}>WATCH LIVE</div>
          <StreamLinks settings={settings} compact />
        </div>
        <div style={{ color: '#c4b5fd', fontSize: 12, letterSpacing: 1.4, fontWeight: 700 }}>
          ONLINE ({presence.total ?? 0})
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '10px 0 14px' }}>
          {Object.entries(counts).map(([plat, n]) => (
            <span key={plat} style={countChip}>
              <PlatformBadge platform={plat} />
              {n}
            </span>
          ))}
          {!Object.keys(counts).length && <span style={{ color: '#6b7280', fontSize: 13 }}>Nobody yet</span>}
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
        <div style={{ color: '#6ee7b7', fontSize: 12, letterSpacing: 1.4, fontWeight: 700, marginTop: 18 }}>
          LISTENING FROM ({listeningFrom.length})
        </div>
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 10, maxHeight: 140, overflow: 'auto' }}>
          {listeningFrom.map((row) => (
            <li key={`${row.platform}:${row.handle}`} style={{ padding: '6px 0', borderBottom: '1px solid #2d2d2d', fontSize: 13 }}>
              <span style={{ fontWeight: 700 }}>{row.handle}</span>
              <span style={{ color: '#9ca3af' }}> · {row.where_from}</span>
            </li>
          ))}
          {!listeningFrom.length && <li style={{ color: '#6b7280', fontSize: 13 }}>Say where you&apos;re listening from in chat</li>}
        </ul>
        <div style={{ color: '#f9a8d4', fontSize: 12, letterSpacing: 1.4, fontWeight: 700, marginTop: 18 }}>
          SUPPORTERS TODAY ({supporters.length})
        </div>
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 10, maxHeight: 160, overflow: 'auto' }}>
          {supporters.map((s) => (
            <li key={s.key} style={{ padding: '8px 0', borderBottom: '1px solid #2d2d2d', fontSize: 13 }}>
              <div style={{ fontWeight: 700 }}>{s.display_name || s.handle}</div>
              <div style={{ color: '#9ca3af', fontSize: 11 }}>
                {(s.platforms || []).join(' · ') || '—'} · {s.events} event{s.events === 1 ? '' : 's'}
              </div>
            </li>
          ))}
          {!supporters.length && <li style={{ color: '#6b7280', fontSize: 13 }}>No shoutouts yet today</li>}
        </ul>
      </aside>
      <section style={{ ...card, display: 'flex', flexDirection: 'column' }}>
        {highlight && (
          <div style={{
            marginBottom: 12, padding: 12, borderRadius: 10,
            background: 'linear-gradient(135deg, #4c1d95, #9d174d)', border: '1px solid #f9a8d4',
          }}>
            <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: 1.2, color: '#fbcfe8' }}>
              SUPPORTER · {(highlight.platform || '').toUpperCase()}
            </div>
            <div style={{ fontWeight: 800, marginTop: 4 }}>{highlight.author}</div>
            {highlight.thank_you && <div style={{ marginTop: 6, fontSize: 14 }}>{highlight.thank_you}</div>}
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              style={{
                padding: '6px 10px', borderRadius: 999, border: 'none', cursor: 'pointer',
                background: filter === f ? '#7c3aed' : '#374151', color: 'white',
                fontWeight: 700, fontSize: 12, textTransform: 'uppercase',
              }}
            >
              {f}
            </button>
          ))}
        </div>
        <div
          ref={listRef}
          onScroll={onScroll}
          style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 420 }}
        >
          {loadingMore && <div style={{ color: '#9ca3af', fontSize: 12, textAlign: 'center' }}>Loading older…</div>}
          {filtered.map((line, i) => {
            const author = line.user?.display_name || line.user?.handle || line.author || 'Fan';
            const body = line.text ?? line.message ?? '';
            const platform = line.platform || 'room';
            const supportKind = line.kind && line.kind !== 'chat' ? line.kind : null;
            const supportBg = supportKind ? '#4c1d95' : (author === haloName || platform === 'halo' ? '#3b0764' : '#1f2937');
            return (
              <div key={line.id || `${line.ts}-${i}`} style={{ padding: 10, borderRadius: 8, background: supportBg, border: supportKind ? '1px solid #c084fc' : undefined, position: 'relative' }}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
                  <PlatformBadge platform={platform} />
                  <strong style={{ color: author === haloName || platform === 'halo' ? '#e9d5ff' : '#6ee7b7' }}>{author}</strong>
                  {supportKind && (
                    <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 800, color: '#f9a8d4', letterSpacing: 0.6 }}>
                      {String(supportKind).replace('_', ' ').toUpperCase()}
                    </span>
                  )}
                  {isMod && platform !== 'halo' && line.id && (
                    <button
                      type="button"
                      onClick={() => setModMenuId(modMenuId === line.id ? null : line.id)}
                      style={{ marginLeft: 'auto', ...tiny, fontSize: 11 }}
                      title="Mod actions"
                    >
                      ⋮
                    </button>
                  )}
                </div>
                <div>{body}</div>
                {isMod && modMenuId === line.id && (
                  <div style={{
                    marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6,
                    padding: 8, borderRadius: 8, background: '#111827', border: '1px solid #374151',
                  }}>
                    <button type="button" style={tiny} onClick={() => modAction('delete', line)}>Delete</button>
                    <button type="button" style={tiny} onClick={() => modAction('timeout', line)}>Timeout 10m</button>
                    <button type="button" style={{ ...tiny, background: '#7f1d1d' }} onClick={() => modAction('ban', line)}>Ban</button>
                  </div>
                )}
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
const countChip = {
  display: 'inline-flex', alignItems: 'center', gap: 2, padding: '4px 8px',
  borderRadius: 8, background: '#111827', fontSize: 13, color: '#e5e7eb',
};
