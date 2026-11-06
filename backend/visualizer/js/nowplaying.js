(() => {
  /* Minimal now-playing for visualizer-v2 (no upstream nowplaying.js).
     Prefer SSE /api/nowplaying/stream; fall back to 2s poll of /api/nowplaying. */
  const Q = new URLSearchParams(location.search);
  const LS_KEY = "stormcellar.np";
  const underVis = (location.pathname || "").indexOf("/visualizer") === 0;
  const pollUrl =
    Q.get("nowplaying") ||
    localStorage.getItem(LS_KEY) ||
    (underVis ? "/api/nowplaying" : "");
  const streamUrl = underVis && !Q.get("nowplaying") ? "/api/nowplaying/stream" : "";

  let current = null;
  const listeners = [];

  function norm(raw) {
    if (!raw || typeof raw !== "object") return null;
    let d = raw;
    if (raw.track && typeof raw.track === "object") d = raw.track;
    const artist = (d.artist || "").toString().trim() || null;
    const title = (d.title || d.name || d.song || "").toString().trim() || null;
    if (!artist && !title) return null;
    return {
      artist,
      title,
      bpm: d.bpm != null && d.bpm !== "" ? Number(d.bpm) : null,
      key: d.key || d.camelot || null,
      source: raw.source || d.source || null,
      art: d.art || d.coverurl || d.cover || null,
      track_id: d.track_id != null ? d.track_id : d.id,
      started_at: d.started_at != null ? d.started_at : null,
    };
  }

  function apply(raw) {
    const n = norm(raw);
    const key = n ? `${(n.artist || "").toLowerCase()}|${(n.title || "").toLowerCase()}` : "";
    const prev = current
      ? `${(current.artist || "").toLowerCase()}|${(current.title || "").toLowerCase()}`
      : "";
    if (key === prev && n && current) {
      current = Object.assign({}, current, n);
      return;
    }
    current = n;
    paint();
    listeners.forEach((fn) => {
      try {
        fn(current);
      } catch (e) {}
    });
  }

  function paint() {
    const badge = document.getElementById("badge");
    if (!badge) return;
    if (!current || (!current.artist && !current.title)) {
      badge.textContent = "";
      return;
    }
    const bits = [current.artist, current.title].filter(Boolean);
    let line = bits.join(" — ");
    if (current.bpm) line += ` · ${Math.round(current.bpm)} BPM`;
    if (current.key) line += ` · ${current.key}`;
    badge.textContent = line;
  }

  function poll() {
    if (!pollUrl) return;
    fetch(pollUrl, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j) apply(j);
      })
      .catch(() => {});
  }

  function startSse() {
    if (!streamUrl || typeof EventSource === "undefined") return false;
    try {
      const es = new EventSource(streamUrl);
      es.addEventListener("track_change", (ev) => {
        try {
          apply(JSON.parse(ev.data || "{}"));
        } catch (e) {}
      });
      es.onerror = () => {
        es.close();
        startPoll();
      };
      return true;
    } catch (e) {
      return false;
    }
  }

  let pollTimer = null;
  function startPoll() {
    if (pollTimer || !pollUrl) return;
    poll();
    pollTimer = setInterval(poll, 2000);
  }

  window.NowPlaying = {
    get: () => current,
    onChange: (fn) => listeners.push(fn),
    pollUrl,
    streamUrl,
  };

  if (!startSse()) startPoll();
})();
