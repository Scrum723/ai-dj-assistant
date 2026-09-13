const PLATFORMS = [
  { key: 'youtube', label: 'YouTube', color: '#ff0033' },
  { key: 'x', label: 'X', color: '#e5e7eb' },
  { key: 'facebook', label: 'Facebook', color: '#1877f2' },
  { key: 'tiktok', label: 'TikTok', color: '#25f4ee' },
  { key: 'rumble', label: 'Rumble', color: '#85c742' },
];

export function streamList(settings) {
  const streams = settings?.streams || {};
  return PLATFORMS.map((p) => ({ ...p, url: (streams[p.key] || '').trim() })).filter((p) => p.url);
}

export function openAllStreams(settings) {
  const links = streamList(settings);
  links.forEach((p) => {
    window.open(p.url, `halo-${p.key}`, 'noopener,noreferrer');
  });
  return links.length;
}

export default function StreamLinks({ settings, compact = false }) {
  const links = streamList(settings);

  if (!links.length) {
    return (
      <p style={{ color: '#9ca3af', fontSize: 13 }}>
        Add stream URLs in Settings to open YouTube, X, Facebook, TikTok, and Rumble at once.
      </p>
    );
  }

  return (
    <div>
      <button
        onClick={() => openAllStreams(settings)}
        style={{
          width: '100%',
          padding: compact ? '12px 16px' : '16px 18px',
          border: 'none',
          borderRadius: 10,
          background: '#7c3aed',
          color: 'white',
          fontWeight: 800,
          fontSize: compact ? 15 : 17,
          cursor: 'pointer',
          letterSpacing: 0.3,
        }}
      >
        Open all {links.length} streams
      </button>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
        {links.map((p) => (
          <a
            key={p.key}
            href={p.url}
            target="_blank"
            rel="noreferrer"
            style={{
              padding: '8px 12px',
              borderRadius: 999,
              border: `1px solid ${p.color}`,
              color: 'white',
              textDecoration: 'none',
              fontWeight: 700,
              fontSize: 13,
              background: 'rgba(0,0,0,0.35)',
            }}
          >
            {p.label}
          </a>
        ))}
      </div>
      <p style={{ color: '#9ca3af', fontSize: 12, marginTop: 10 }}>
        Your browser may ask to allow pop-ups once. Allow them so all five tabs open together.
      </p>
    </div>
  );
}

export { PLATFORMS };
