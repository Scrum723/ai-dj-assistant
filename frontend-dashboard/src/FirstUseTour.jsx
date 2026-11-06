import React, { useEffect, useLayoutEffect, useMemo, useState } from 'react';

export const TOUR_STORAGE_KEY = 'djbotbotty.boothTour.v1';

const STEPS = [
  {
    id: 'welcome',
    tab: 'dj',
    title: 'Welcome to DJ Bot Botty',
    body: 'This quick tour walks the booth once. You can skip anytime and replay later with the “?” button.',
    target: null,
  },
  {
    id: 'booth',
    tab: 'dj',
    title: 'Booth',
    body: 'Decks, library, crossfader, and next-track picks live here. On this PC MIDI runs in sim mode until you use the Mac app with rekordbox.',
    target: 'tour-tab-dj',
  },
  {
    id: 'dj-mode',
    tab: 'dj',
    title: 'Chief live / DJ MODE',
    body: 'Flip DJ MODE when you are mixing. Chief stays quiet so you can focus, then turn it back for chat energy.',
    target: 'tour-dj-mode',
  },
  {
    id: 'host',
    tab: 'host',
    title: 'Co-host Chief',
    body: 'Talk to Chief, try demo crowd, and watch mood. Generative replies need GEMINI_API_KEY or XAI_API_KEY in your user .env.',
    target: 'tour-tab-host',
  },
  {
    id: 'room',
    tab: 'room',
    title: 'Room',
    body: 'The merged community room — every platform chat in one place. Join with a nick to hang out with viewers.',
    target: 'tour-tab-room',
  },
  {
    id: 'fan',
    tab: 'fan',
    title: 'Requests',
    body: 'Fan track requests land here so you can load them to a deck when you are ready.',
    target: 'tour-tab-fan',
  },
  {
    id: 'visualizer',
    tab: 'visualizer',
    title: 'Visualizer',
    body: 'Storm Cellar controls and Now Playing. Open the full visualizer page for OBS / Restream capture.',
    target: 'tour-tab-visualizer',
  },
  {
    id: 'settings',
    tab: 'settings',
    title: 'Settings',
    body: 'Persona, voice, vibe, and community adapters. Change Chief’s name and presets without restarting.',
    target: 'tour-tab-settings',
  },
  {
    id: 'done',
    tab: 'dj',
    title: 'You are set',
    body: 'Explore freely. Replay this tour anytime with the purple “?” in the corner.',
    target: null,
  },
];

function readDone() {
  try {
    return localStorage.getItem(TOUR_STORAGE_KEY) === 'done';
  } catch {
    return false;
  }
}

function writeDone() {
  try {
    localStorage.setItem(TOUR_STORAGE_KEY, 'done');
  } catch {
    /* private mode */
  }
}

export function resetBoothTour() {
  try {
    localStorage.removeItem(TOUR_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * First-use booth tour. Auto-starts once; call onReplay from the help button.
 */
export default function FirstUseTour({ setActiveTab, forceOpen = false, onClose }) {
  const [open, setOpen] = useState(() => forceOpen || !readDone());
  const [idx, setIdx] = useState(0);
  const [rect, setRect] = useState(null);

  const step = STEPS[idx] || STEPS[0];

  useEffect(() => {
    if (forceOpen) {
      setOpen(true);
      setIdx(0);
    }
  }, [forceOpen]);

  useEffect(() => {
    if (!open) return;
    if (step.tab) setActiveTab(step.tab);
  }, [open, step.tab, setActiveTab, idx]);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      if (!step.target) {
        setRect(null);
        return;
      }
      const el = document.querySelector(`[data-tour="${step.target}"]`);
      if (!el) {
        setRect(null);
        return;
      }
      const r = el.getBoundingClientRect();
      setRect({
        top: r.top + window.scrollY - 6,
        left: r.left + window.scrollX - 6,
        width: r.width + 12,
        height: r.height + 12,
      });
      el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    };
    measure();
    const t = window.setTimeout(measure, 120);
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [open, step.target, idx]);

  const finish = () => {
    writeDone();
    setOpen(false);
    onClose?.();
  };

  const skip = () => finish();

  const next = () => {
    if (idx >= STEPS.length - 1) finish();
    else setIdx((i) => i + 1);
  };

  const back = () => setIdx((i) => Math.max(0, i - 1));

  const cardStyle = useMemo(() => {
    const base = {
      position: 'fixed',
      zIndex: 10001,
      width: 'min(380px, calc(100vw - 32px))',
      background: '#1e1b2e',
      color: '#f8fafc',
      border: '1px solid #7c3aed',
      borderRadius: 14,
      padding: '18px 18px 14px',
      boxShadow: '0 18px 50px rgba(0,0,0,0.55)',
    };
    if (rect) {
      const below = rect.top + rect.height + 14;
      const top = below + 220 > window.innerHeight + window.scrollY
        ? Math.max(12, rect.top - 200)
        : below;
      let left = rect.left;
      left = Math.min(left, window.innerWidth - 400);
      left = Math.max(12, left);
      return { ...base, top, left };
    }
    return {
      ...base,
      top: '50%',
      left: '50%',
      transform: 'translate(-50%, -50%)',
    };
  }, [rect]);

  if (!open) return null;

  const pad = 10;
  const hole = rect
    ? {
        top: Math.max(0, rect.top - pad),
        left: Math.max(0, rect.left - pad),
        width: rect.width + pad * 2,
        height: rect.height + pad * 2,
      }
    : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Booth tour"
      style={{ position: 'fixed', inset: 0, zIndex: 10000 }}
    >
      {/* dim layer with spotlight cutout */}
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
        {hole ? (
          <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0 }}>
            <defs>
              <mask id="tour-mask">
                <rect x="0" y="0" width="100%" height="100%" fill="white" />
                <rect
                  x={hole.left}
                  y={hole.top}
                  width={hole.width}
                  height={hole.height}
                  rx="10"
                  fill="black"
                />
              </mask>
            </defs>
            <rect
              x="0"
              y="0"
              width="100%"
              height="100%"
              fill="rgba(2,6,23,0.72)"
              mask="url(#tour-mask)"
            />
          </svg>
        ) : (
          <div style={{ position: 'absolute', inset: 0, background: 'rgba(2,6,23,0.72)' }} />
        )}
        {hole && (
          <div
            style={{
              position: 'absolute',
              top: hole.top,
              left: hole.left,
              width: hole.width,
              height: hole.height,
              borderRadius: 10,
              boxShadow: '0 0 0 2px #a78bfa, 0 0 24px rgba(124,58,237,0.65)',
              pointerEvents: 'none',
            }}
          />
        )}
      </div>

      <div style={cardStyle}>
        <div style={{ fontSize: 12, color: '#c4b5fd', fontWeight: 700, letterSpacing: 0.4, marginBottom: 6 }}>
          STEP {idx + 1} / {STEPS.length}
        </div>
        <h2 style={{ margin: '0 0 8px', fontSize: '1.15rem' }}>{step.title}</h2>
        <p style={{ margin: '0 0 16px', lineHeight: 1.45, color: '#e2e8f0', fontSize: 14 }}>{step.body}</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <button type="button" onClick={skip} style={btnGhost}>
            Skip tour
          </button>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" onClick={back} disabled={idx === 0} style={btnGhost}>
              Back
            </button>
            <button type="button" onClick={next} style={btnPrimary}>
              {idx >= STEPS.length - 1 ? 'Finish' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const btnPrimary = {
  background: '#7c3aed',
  color: '#fff',
  border: 'none',
  borderRadius: 8,
  padding: '8px 14px',
  fontWeight: 700,
  cursor: 'pointer',
};

const btnGhost = {
  background: 'transparent',
  color: '#cbd5e1',
  border: '1px solid #475569',
  borderRadius: 8,
  padding: '8px 14px',
  fontWeight: 600,
  cursor: 'pointer',
};
