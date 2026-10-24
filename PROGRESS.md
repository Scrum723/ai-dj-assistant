# DJ Bot Botty — Alpha Progress

Branch: `alpha/a1-d1`
Spec: `docs/djbotbotty_full_build_prompt.md`

## Phases

- [x] A1 — Halo foundation: tests, LLM provider, per-viewer memory — pytest harness, Gemini/xAI via HALO_PROVIDER, SQLite viewer memory injected into prompts
- [x] A2 — Moderation, safety and access control — input/output filters, rate limits, kill/resume, OPERATOR_KEY auth, operator chat commands
- [x] B1 — Aggregator core + unified room v1 — ChatMessage schema, YouTube/Twitch adapters, Aggregator dedupe+moderation fan-out, /ws/room, Room+overlay badges; adapters disabled by default
- [x] B2 — Room history, presence and live-room UX — SQLite scrollback + retention, presence per platform + room sockets, Room.jsx filters/reconnect/dedupe
- [x] A3 — Halo speaks to the merged room, with cost and latency controls — Aggregator feed context + platform tags, min_reply_sec selection, /metrics + daily cap, Halo→Aggregator publish
- [x] A4 — Persona, voice and DJ mode — persona presets (hype/chill/host/custom), DJ quiet (mentions/requests/operator), operator skip rate/cooldown, TTS 4s timeout fallback, dj_mode_silent for old mute
- [x] A5 — Priority queue and Halo Waiting Room 24/7 mode — PriorityQueue + cohost pull budget/cooldown, wait acks, waiting-room idle, /state queue fields; aggregator operator skip_rate
- [x] A6 — Supporter shoutouts — ChatMessage.support + adapter fixtures (YT/Twitch/Kick/TikTok), burst thank-yous at supporter priority, overlay/room highlight, GET /community/supporters/today; amounts spoken only if halo.say_amounts
- [ ] B3 — Booth pass identity
- [ ] A7 — Regulars, recurring segments and rituals
- [ ] B4 — Adapters with official APIs (Kick, Facebook, Rumble, YouTube official)
- [ ] B5 — Experimental adapters: TikTok LIVE and X
- [ ] B6 — Unified moderation and ban sync
- [ ] B7 — Relay back to platform chats
- [ ] B8 — Retention hooks and djbotbotty.com front door
- [ ] C1 — MIDI control layer and fake deck sandbox
- [ ] C2 — Track analysis v2
- [ ] C3 — Next-track selection
- [ ] C4 — Mix engine, replays and transition scoring
- [ ] C5 — Safety fallback and manual override
- [ ] D1 — Alpha hardening and launch

## Blocked on Charles

Concrete setup checklist (no credentials invented here — create these when ready):

### Already noted
- [ ] **A4:** Listen test of macOS `say` voices and ElevenLabs in OBS

### OAuth / booth pass (B3+)
- [ ] Google OAuth app: `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` (callback URL for `/auth/google/callback`)
- [ ] Facebook OAuth app: `FACEBOOK_OAUTH_CLIENT_ID` / `FACEBOOK_OAUTH_CLIENT_SECRET`
- [ ] Apple / TikTok OAuth (optional; endpoints may return 501 until wired)

### Chat platforms (B1–B7)
- [ ] YouTube: live video id + (later) Data API key/quota for official adapter
- [ ] Twitch: channel name for anon read; bot token with chat:write if enabling relay (B7)
- [ ] Kick / Facebook Page / Rumble developer apps + tokens (B4)
- [ ] TikTok LIVE terms risk acceptance + experimental enable (B5)
- [ ] X API credentials + live post id if using experimental X adapter (B5)
- [ ] **A6:** Live test donation / Super Chat / sub on one platform

### Railway / djbotbotty.com (B8 / D1)
- [ ] Railway volume mounted at `DATA_DIR`
- [ ] Health check → `/healthz` (added in B8/D1)
- [ ] Set `OPERATOR_KEY` and `COOKIE_SECURE=true` in production
- [ ] LLM keys (`GEMINI_API_KEY` or `XAI_API_KEY`) on Railway

### Mac DJ booth (C1–C5)
- [ ] Import MIDI Learn mapping on `AI_DJ_Virtual_Port` in rekordbox (C1)
- [ ] Run analysis on `~/Desktop/Finished Songs`; export rekordbox XML (C2)
- [ ] Export rekordbox history for replay scoring (C4)
- [ ] Controller + audio loopback (e.g. BlackHole) for takeover/silence watchdog (C5)

### Alpha dress rehearsal (D1)
- [ ] Multistream with merged room, Halo, OBS overlay, kill switch

## Notes / deviations

- B1 Twitch docs (2026-10-24): official IRC requires oauth PASS/NICK; non-SSL WS removed. Adapter still uses justinfan anonymous read-only over `wss://irc-ws.chat.twitch.tv:443` per plan (community practice; EventSub preferred long-term).
- B1 YouTube: pytchat loop moved to adapter; cohost.set_youtube updates adapter channel via hook. Official liveChatMessages left as TODO.
- Session restart mid-A6: WIP stashed as `wip/a6-shoutouts`; setup gate (env/docs/blockers/gitignore) runs before restoring A6.
