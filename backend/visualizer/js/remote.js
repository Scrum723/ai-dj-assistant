(() => {
  /* V4 remote control: /ws/visualizer → apply vis_cmd via window.__vis; report vis_status. */
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const wsUrl = `${proto}://${location.host}/ws/visualizer`;
  let ws = null;
  let retryMs = 1000;
  let frames = 0;
  let fps = 0;
  let lastFpsAt = performance.now();

  function tickFps() {
    frames++;
    const now = performance.now();
    if (now - lastFpsAt >= 1000) {
      fps = Math.round((frames * 1000) / (now - lastFpsAt));
      frames = 0;
      lastFpsAt = now;
    }
    requestAnimationFrame(tickFps);
  }
  requestAnimationFrame(tickFps);

  function inputLabel() {
    const sel = document.getElementById("dev");
    if (!sel || !sel.value) {
      const st = document.getElementById("status");
      return (st && st.textContent) || "none";
    }
    const opt = sel.options[sel.selectedIndex];
    return (opt && opt.textContent) || "input";
  }

  function sceneList() {
    try {
      if (typeof SCENES !== "undefined" && Array.isArray(SCENES)) {
        return SCENES.map((s, i) => ({ i, name: s.name }));
      }
    } catch (e) {}
    return [];
  }

  function statusPayload() {
    const V = window.__vis;
    const cur = V && V.cur;
    return {
      type: "vis_status",
      scene: cur ? cur.idx : null,
      mode: V && V.getMode ? V.getMode() : null,
      fps,
      input: inputLabel(),
      scenes: sceneList(),
      songSync: !!(V && V.songSync),
    };
  }

  function applyCmd(msg) {
    const V = window.__vis;
    if (!V || !msg || msg.type !== "vis_cmd") return;
    if (typeof V.applyRemote === "function") V.applyRemote(msg);
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      setTimeout(connect, retryMs);
      return;
    }
    ws.onopen = () => {
      retryMs = 1000;
      try {
        ws.send(JSON.stringify(statusPayload()));
      } catch (e) {}
    };
    ws.onmessage = (ev) => {
      try {
        applyCmd(JSON.parse(ev.data));
      } catch (e) {}
    };
    ws.onclose = () => {
      ws = null;
      setTimeout(connect, retryMs);
      retryMs = Math.min(15000, retryMs * 1.5);
    };
    ws.onerror = () => {
      try {
        ws && ws.close();
      } catch (e) {}
    };
  }

  setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(statusPayload()));
    } catch (e) {}
  }, 5000);

  function boot() {
    if (window.__vis) connect();
    else setTimeout(boot, 50);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
