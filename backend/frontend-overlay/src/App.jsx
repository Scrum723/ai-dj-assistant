import { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

const DEV = import.meta.env.DEV;
const API = DEV ? 'http://127.0.0.1:8000' : '';
const WS_URL = DEV ? 'ws://127.0.0.1:8000/ws' : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

const VIBE_GLOW = {
  night: '#7c3aed',
  storm: '#38bdf8',
  club: '#db2777',
  chill: '#14b8a6',
  weather: '#f59e0b',
};

function shortTitle(name = '') {
  return name.replace(/\s*-\s*\d{1,2}:\d{1,2}:\d{2}.*$/, '').replace(/\.[^.]+$/, '') || name;
}

export default function App() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [currentMessage, setCurrentMessage] = useState('Halo is in the booth...');
  const [target, setTarget] = useState(null);
  const [mood, setMood] = useState('listening');
  const [state, setState] = useState(null);
  const lastSpeakId = useRef(0);
  const audioRef = useRef(null);

  const finish = () => {
    setIsSpeaking(false);
    setMood('listening');
  };

  const speak = (text, nextTarget, nextMood, audioUrl) => {
    if (!text) return;
    setIsSpeaking(true);
    setCurrentMessage(text);
    setTarget(nextTarget || null);
    setMood(nextMood || 'talk');
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (audioUrl) {
      const audio = new Audio(`${API}${audioUrl}`);
      audioRef.current = audio;
      audio.onended = finish;
      audio.onerror = () => browserSpeak(text);
      audio.play().catch(() => browserSpeak(text));
      return;
    }
    browserSpeak(text);
  };

  const browserSpeak = (text) => {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.pitch = 1.02;
      utterance.rate = 1.0;
      utterance.onend = finish;
      utterance.onerror = finish;
      window.speechSynthesis.speak(utterance);
    } else {
      setTimeout(finish, Math.min(12000, 3500 + text.split(' ').length * 350));
    }
  };

  useEffect(() => {
    let ws;
    let closed = false;
    const connect = () => {
      if (closed) return;
      ws = new WebSocket(WS_URL);
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'state') setState(msg.payload);
        if (msg.type === 'speak' && msg.text && msg.id !== lastSpeakId.current) {
          lastSpeakId.current = msg.id;
          speak(msg.text, msg.target, msg.mood, msg.audio_url);
        } else if (
          msg.type === 'state' &&
          msg.payload?.speak_id > lastSpeakId.current &&
          msg.payload.speak_text
        ) {
          lastSpeakId.current = msg.payload.speak_id;
          speak(msg.payload.speak_text, msg.payload.speak_target, msg.payload.speak_mood, msg.payload.speak_audio);
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

  const agent = state?.agent || {};
  const chat = agent.chat || state?.chat || [];
  const track = state?.current_track;
  const vibe = agent.vibe || state?.settings?.environment?.vibe || 'night';
  const glow = VIBE_GLOW[vibe] || '#7c3aed';
  const liveMood = isSpeaking ? (mood || 'speaking') : (agent.dj_mode ? 'quiet' : (agent.mood || 'listening'));
  const distraction = (state?.settings?.environment?.distraction ?? 30) / 100;
  const haloName = agent.name || 'Halo';

  return (
    <div style={{
      width: '100vw', height: '100vh',
      display: 'grid',
      gridTemplateColumns: '340px 1fr',
      gap: 24,
      padding: 28,
      boxSizing: 'border-box',
      background: 'transparent',
      fontFamily: 'system-ui, sans-serif',
      color: 'white'
    }}>
      <div style={{
        background: 'rgba(0,0,0,0.55)',
        border: `1px solid ${glow}73`,
        borderRadius: 18,
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0
      }}>
        <div style={{ fontSize: 13, letterSpacing: 1.5, color: glow, fontWeight: 700, marginBottom: 10 }}>
          LIVE CHAT
        </div>
        <div style={{ overflow: 'hidden', flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gap: 8 }}>
          {chat.slice(-10).map((line, i) => (
            <div key={`${line.ts}-${i}`} style={{ fontSize: 15, lineHeight: 1.35 }}>
              <span style={{ color: line.author === haloName ? '#c4b5fd' : '#6ee7b7', fontWeight: 700 }}>
                {line.author}
              </span>
              <span style={{ color: '#e5e7eb' }}> {line.message}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ display: 'flex', gap: 10, marginBottom: 18, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Chip color={glow}>{liveMood.toUpperCase()}</Chip>
          <Chip color="#10b981">{agent.viewer_count || 0} in the room</Chip>
          {agent.demo && <Chip color="#f59e0b">DEMO CROWD</Chip>}
          {agent.dj_mode && <Chip color="#f59e0b">DJ MODE</Chip>}
          {target && <Chip color="#38bdf8">talking to {target}</Chip>}
        </div>

        <motion.div
          animate={{
            scale: isSpeaking ? [1, 1.08, 1.03, 1.1, 1] : 1,
            y: isSpeaking ? [0, -8, 0, -4, 0] : [0, 3 + distraction * 6, 0],
          }}
          transition={{ repeat: Infinity, duration: isSpeaking ? 0.28 : 3 + (1 - distraction) * 2, ease: 'easeInOut' }}
          style={{
            width: 220, height: 220,
            background: liveMood === 'hype' ? '#db2777' : glow,
            borderRadius: '50%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: isSpeaking ? `0 0 ${40 + distraction * 50}px ${glow}` : `0 0 24px ${glow}`,
            opacity: agent.dj_mode ? 0.45 : 1,
          }}
        >
          <div style={{ color: 'white', fontWeight: 800, letterSpacing: 3, marginBottom: 14, fontSize: 14 }}>{haloName.toUpperCase()}</div>
          <div style={{ display: 'flex', gap: 36, marginBottom: 16 }}>
            <motion.div animate={{ height: isSpeaking ? 12 : 34 }} style={{ width: 22, background: 'white', borderRadius: 12 }} />
            <motion.div animate={{ height: isSpeaking ? 12 : 34 }} style={{ width: 22, background: 'white', borderRadius: 12 }} />
          </div>
          <motion.div
            animate={{
              width: isSpeaking ? [28, 58, 36, 64, 28] : 28,
              height: isSpeaking ? [8, 34, 16, 42, 8] : 8,
              borderRadius: isSpeaking ? ['20%', '50%', '30%', '50%', '20%'] : '50px'
            }}
            transition={{ repeat: isSpeaking ? Infinity : 0, duration: 0.35 }}
            style={{ background: 'white' }}
          />
        </motion.div>

        <AnimatePresence mode="wait">
          <motion.div
            key={currentMessage}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            style={{
              marginTop: 28,
              padding: '16px 28px',
              background: 'rgba(0,0,0,0.82)',
              borderRadius: 16,
              fontSize: '1.65rem',
              fontWeight: 700,
              textAlign: 'center',
              maxWidth: '90%',
              border: `2px solid ${glow}`,
              lineHeight: 1.25
            }}
          >
            {agent.dj_mode ? `${haloName} is quiet — you're on the decks.` : currentMessage}
          </motion.div>
        </AnimatePresence>

        <div style={{
          marginTop: 22,
          padding: '10px 18px',
          background: 'rgba(0,0,0,0.6)',
          borderRadius: 999,
          fontSize: 14,
          color: '#d1d5db',
          border: '1px solid #374151'
        }}>
          {track ? `NOW PLAYING · ${shortTitle(track.filename)} · ${Number(track.bpm || 0).toFixed(0)} BPM` : 'Waiting on a track from the booth'}
        </div>
      </div>
    </div>
  );
}

function Chip({ children, color }) {
  return (
    <span style={{
      padding: '6px 12px',
      borderRadius: 999,
      background: 'rgba(0,0,0,0.65)',
      border: `1px solid ${color}`,
      color: 'white',
      fontSize: 12,
      fontWeight: 700,
      letterSpacing: 0.4
    }}>
      {children}
    </span>
  );
}
