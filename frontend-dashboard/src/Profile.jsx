import { useCallback, useEffect, useState } from 'react';
import { API } from './api.js';

const PERK_LABELS = {
  stream_badge: 'Stream badge',
  room_pass: 'Room pass',
  request_priority: 'Request priority',
  early_chat: 'Early chat',
  room_mod: 'Room mod',
  operator: 'Operator',
};

export default function Profile({ onBack }) {
  const [user, setUser] = useState(null);
  const [links, setLinks] = useState([]);
  const [code, setCode] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [retention, setRetention] = useState({ visits: 0, streak: 0, badges: [] });
  const [optIn, setOptIn] = useState(false);
  const [notifyMsg, setNotifyMsg] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const res = await fetch(`${API}/me/profile`, { credentials: 'include' });
      if (!res.ok) {
        setErr('Sign in to view your booth pass.');
        setUser(null);
        return;
      }
      const data = await res.json();
      setUser(data.user);
      setLinks(data.linked_handles || []);
      setRetention({
        visits: data.visits || 0,
        streak: data.streak || 0,
        badges: data.badges || [],
      });
      setOptIn(!!data.user?.email_opt_in);
    } catch {
      setErr('Could not load profile.');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const mintCode = async () => {
    setBusy(true);
    setErr('');
    try {
      const res = await fetch(`${API}/me/link-code`, { method: 'POST', credentials: 'include' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErr(data.detail || 'Could not mint link code');
        return;
      }
      setCode(data);
    } catch {
      setErr('Could not mint link code');
    } finally {
      setBusy(false);
    }
  };

  const unlink = async (platform, handle) => {
    setBusy(true);
    try {
      const res = await fetch(`${API}/me/unlink`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform, handle }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) setLinks(data.linked_handles || []);
    } finally {
      setBusy(false);
    }
  };

  const saveNotify = async (value) => {
    setBusy(true);
    setNotifyMsg('');
    try {
      const res = await fetch(`${API}/me/notify`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email_opt_in: value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setNotifyMsg(data.detail || 'Could not save preference');
        return;
      }
      setOptIn(!!data.email_opt_in);
      setNotifyMsg('Saved (alerts not sent yet — preference stored only).');
      if (data.user) setUser(data.user);
    } catch {
      setNotifyMsg('Could not save preference');
    } finally {
      setBusy(false);
    }
  };

  if (!user && !err) {
    return <div style={wrap}><p style={{ color: '#9ca3af' }}>Loading profile…</p></div>;
  }

  const perks = user?.perks || {};

  return (
    <div style={wrap}>
      <div style={card}>
        {onBack ? (
          <button type="button" onClick={onBack} style={ghost}>← Back</button>
        ) : null}
        <p style={kicker}>BOOTH PASS</p>
        <h2 style={{ margin: '4px 0 8px' }}>{user?.display_name || 'Fan'}</h2>
        <p style={{ color: '#9ca3af', margin: 0 }}>{user?.email || 'No email on file'}</p>
        <p style={{ color: '#c4b5fd', fontSize: 13 }}>Provider · {user?.provider || 'email'}</p>
        {err && <p style={{ color: '#fca5a5' }}>{typeof err === 'string' ? err : JSON.stringify(err)}</p>}

        <h3 style={{ marginTop: 20, marginBottom: 8 }}>Retention</h3>
        <p style={{ color: '#fde68a', margin: '0 0 8px' }}>
          {retention.streak > 0 ? `🔥 ${retention.streak}-day streak` : 'No streak yet'}
          {' · '}
          {retention.visits || 0} visit{(retention.visits || 0) === 1 ? '' : 's'}
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {(retention.badges || []).length === 0 ? (
            <span style={{ color: '#6b7280', fontSize: 13 }}>Badges at 3 / 10 / 25 visits across linked platforms</span>
          ) : (
            (retention.badges || []).map((b) => (
              <span key={b} style={{
                padding: '4px 10px', borderRadius: 999, background: '#312e81',
                border: '1px solid #818cf8', fontSize: 12, fontWeight: 700, color: '#c4b5fd',
              }}>{b}</span>
            ))
          )}
        </div>

        <h3 style={{ marginTop: 20, marginBottom: 8 }}>Going-live alerts</h3>
        <label style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#e5e7eb', fontSize: 14 }}>
          <input
            type="checkbox"
            checked={optIn}
            disabled={busy}
            onChange={(e) => saveNotify(e.target.checked)}
          />
          Email me when Charles goes live (stored only — no mail yet)
        </label>
        {notifyMsg ? <p style={{ color: '#9ca3af', fontSize: 13 }}>{notifyMsg}</p> : null}

        <h3 style={{ marginTop: 20, marginBottom: 8 }}>Perks</h3>
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
          {Object.keys(PERK_LABELS).map((key) => (
            <li key={key} style={perkRow}>
              <span>{PERK_LABELS[key]}</span>
              <strong style={{ color: perks[key] ? '#6ee7b7' : '#6b7280' }}>
                {perks[key] ? 'on' : 'off'}
              </strong>
            </li>
          ))}
        </ul>
        {perks.notes ? <p style={{ color: '#9ca3af', fontSize: 13 }}>{perks.notes}</p> : null}

        <h3 style={{ marginTop: 24, marginBottom: 8 }}>Linked platforms</h3>
        {links.length === 0 ? (
          <p style={{ color: '#9ca3af' }}>None yet. Mint a code and type <code>!link CODE</code> in Twitch/YouTube chat.</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 8 }}>
            {links.map((l) => (
              <li key={`${l.platform}:${l.handle}`} style={perkRow}>
                <span>
                  <strong style={{ textTransform: 'capitalize' }}>{l.platform}</strong> · {l.handle}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => unlink(l.platform, l.handle)}
                  style={tiny}
                >
                  Unlink
                </button>
              </li>
            ))}
          </ul>
        )}

        <h3 style={{ marginTop: 24, marginBottom: 8 }}>Link code</h3>
        <p style={{ color: '#9ca3af', fontSize: 14, marginTop: 0 }}>
          Codes expire in 10 minutes. Say <code>!link ABC123</code> from any connected chat.
        </p>
        <button type="button" disabled={busy} onClick={mintCode} style={primary}>
          {busy ? 'Working…' : 'Get link code'}
        </button>
        {code ? (
          <div style={codeBox}>
            <div style={{ letterSpacing: 4, fontSize: 28, fontWeight: 800 }}>{code.code}</div>
            <div style={{ color: '#9ca3af', fontSize: 12, marginTop: 6 }}>
              Expires {new Date(code.expires_at * 1000).toLocaleTimeString()}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const wrap = { display: 'flex', justifyContent: 'center' };
const card = {
  width: 'min(520px, 100%)',
  background: '#161022',
  border: '1px solid #7c3aed',
  borderRadius: 16,
  padding: 28,
};
const kicker = { letterSpacing: 3, fontSize: 12, color: '#a78bfa', fontWeight: 700, margin: 0 };
const primary = {
  padding: '12px 16px',
  borderRadius: 8,
  border: 0,
  background: '#7c3aed',
  color: '#fff',
  fontWeight: 700,
  cursor: 'pointer',
};
const ghost = {
  padding: '8px 12px',
  borderRadius: 8,
  border: '1px solid #4c1d95',
  background: 'transparent',
  color: '#c4b5fd',
  cursor: 'pointer',
  marginBottom: 12,
};
const tiny = { ...ghost, padding: '4px 10px', fontSize: 12, marginBottom: 0 };
const perkRow = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '8px 10px',
  background: '#0b0614',
  borderRadius: 8,
  border: '1px solid #2e1065',
};
const codeBox = {
  marginTop: 14,
  padding: 16,
  borderRadius: 12,
  background: '#0b0614',
  border: '1px dashed #a78bfa',
  textAlign: 'center',
};
