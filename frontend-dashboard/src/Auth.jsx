import { useEffect, useState } from 'react';
import { API } from './api.js';

const SOCIAL = [
  { id: 'google', label: 'Google' },
  { id: 'apple', label: 'Apple' },
  { id: 'facebook', label: 'Facebook' },
  { id: 'tiktok', label: 'TikTok' },
];

export default function AuthGate({ children }) {
  const [user, setUser] = useState(undefined);
  const [step, setStep] = useState('land');

  const load = async () => {
    try {
      const res = await fetch(`${API}/auth/me`, { credentials: 'include' });
      const data = await res.json();
      setUser(data.user || null);
    } catch {
      setUser(null);
    }
  };

  useEffect(() => { load(); }, []);

  if (user === undefined) {
    return <div style={shell}><p style={{ color: '#9ca3af' }}>Opening DJ Bot Botty…</p></div>;
  }
  if (!user) {
    return (
      <div style={shell}>
        {step === 'land' ? <Landing onSignup={() => setStep('signup')} onLogin={() => setStep('login')} /> : null}
        {step === 'signup' ? <SignupForm onBack={() => setStep('land')} onDone={(u) => setUser(u)} /> : null}
        {step === 'login' ? <LoginForm onBack={() => setStep('land')} onDone={(u) => setUser(u)} /> : null}
      </div>
    );
  }

  return (
    <div>
      <div style={bar}>
        <span>DJ Bot Botty pass · {user.display_name}</span>
        <button
          type="button"
          onClick={async () => {
            await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' });
            setUser(null);
            setStep('land');
          }}
          style={tiny}
        >
          Sign out
        </button>
      </div>
      {children}
    </div>
  );
}

function Landing({ onSignup, onLogin }) {
  return (
    <div style={card}>
      <p style={kicker}>THE FULL BOOTH</p>
      <h1 style={{ margin: '8px 0 12px', fontSize: 36 }}>DJ Bot Botty</h1>
      <p style={{ color: '#c4b5fd', lineHeight: 1.6 }}>
        Decks, MIDI, room, overlay, and live chat. <strong>Halo</strong> is the LLM buddy in the booth — not the whole program.
      </p>
      <p style={{ color: '#9ca3af', fontSize: 14 }}>
        Create a pass once. Next livestream you walk in with your profile and the extra perks we add over time.
      </p>
      <button type="button" onClick={onSignup} style={primary}>Create your booth pass</button>
      <button type="button" onClick={onLogin} style={ghost}>I already have a pass</button>
    </div>
  );
}

function SignupForm({ onBack, onDone }) {
  const [display_name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [ready, setReady] = useState({});

  useEffect(() => {
    fetch(`${API}/auth/providers`).then((r) => r.json()).then((d) => setReady(d.providers || {})).catch(() => {});
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    const res = await fetch(`${API}/auth/signup`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name, email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(data.detail || 'Could not create pass');
      return;
    }
    onDone(data.user);
  };

  return (
    <form onSubmit={submit} style={card}>
      <button type="button" onClick={onBack} style={ghost}>← Back</button>
      <h2>Sign up for DJ Bot Botty</h2>
      <p style={{ color: '#9ca3af' }}>Halo will know your name in the room. MIDI and decks stay on the booth machine.</p>
      <input required minLength={2} placeholder="Display name" value={display_name} onChange={(e) => setName(e.target.value)} style={inp} />
      <input required type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} style={inp} />
      <input required minLength={8} type="password" placeholder="Password (8+)" value={password} onChange={(e) => setPassword(e.target.value)} style={inp} />
      {err && <p style={{ color: '#fca5a5' }}>{typeof err === 'string' ? err : JSON.stringify(err)}</p>}
      <button type="submit" style={primary}>Create pass</button>
      <div style={{ marginTop: 18, color: '#9ca3af', fontSize: 13 }}>Or continue with</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 }}>
        {SOCIAL.map((p) => (
          <a key={p.id} href={`${API}/auth/${p.id}/start`} style={social}>
            {p.label}{ready[p.id] ? '' : ' · set keys'}
          </a>
        ))}
      </div>
    </form>
  );
}

function LoginForm({ onBack, onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setErr(data.detail || 'Login failed');
      return;
    }
    onDone(data.user);
  };
  return (
    <form onSubmit={submit} style={card}>
      <button type="button" onClick={onBack} style={ghost}>← Back</button>
      <h2>Sign in</h2>
      <input required type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} style={inp} />
      <input required type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} style={inp} />
      {err && <p style={{ color: '#fca5a5' }}>{typeof err === 'string' ? err : JSON.stringify(err)}</p>}
      <button type="submit" style={primary}>Enter booth</button>
    </form>
  );
}

const shell = { minHeight: '100vh', background: '#0b0614', color: '#fff', display: 'grid', placeItems: 'center', fontFamily: 'system-ui, sans-serif', padding: 24 };
const card = { width: 'min(440px, 100%)', background: '#161022', border: '1px solid #7c3aed', borderRadius: 16, padding: 28, display: 'flex', flexDirection: 'column', gap: 12 };
const kicker = { letterSpacing: 3, fontSize: 12, color: '#a78bfa', fontWeight: 700, margin: 0 };
const inp = { padding: 12, borderRadius: 8, border: '1px solid #4c1d95', background: '#0b0614', color: '#fff' };
const primary = { padding: '12px 16px', borderRadius: 8, border: 0, background: '#7c3aed', color: '#fff', fontWeight: 700, cursor: 'pointer' };
const ghost = { padding: '10px 12px', borderRadius: 8, border: '1px solid #4c1d95', background: 'transparent', color: '#c4b5fd', cursor: 'pointer' };
const social = { ...ghost, textAlign: 'center', textDecoration: 'none', fontSize: 13 };
const bar = { display: 'flex', justifyContent: 'space-between', padding: '8px 20px', background: '#140c22', color: '#c4b5fd', fontSize: 13 };
const tiny = { ...ghost, padding: '4px 10px', fontSize: 12 };
