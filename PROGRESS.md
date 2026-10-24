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
- [x] B3 — Booth pass identity — Google/Facebook OAuth callbacks (signed state, mocked tests), apple/tiktok 501; identity.py linked_handles + !link CODE; Aggregator profile_id; memory merge by profile_id; Profile.jsx; request_priority perk
- [x] A7 — Regulars, recurring segments and rituals — greeting SM (first/returning/milestone once per show), where_from + favourite_genres memory, segments.py scheduler + Room listening-from/segment UI, community in-jokes ≤1/15m; chat-path request_priority
- [x] B4 — Adapters with official APIs (Kick, Facebook, Rumble, YouTube official) — webhooks+sig, Graph poll, Rumble poll, YT liveChatMessages+quota/pytchat fallback; Settings health; fixtures
- [x] B5 — Experimental adapters: TikTok LIVE and X — optional TikTokLive + X replies poll; experimental flags; auto-disable 5/10min; README Platform reality; Settings warning
- [x] B6 — Unified moderation and ban sync — community/mod bans by profile or platform+handle, Aggregator drop + delete-message, POST /community/mod/{ban,timeout,unban,delete}, push_bans opt-in, Room mod menu, mod_log; empty SUPPORT_KINDS through Aggregator
- [x] B7 — Relay back to platform chats — community/relay rules+token-bucket+echo map; send() Twitch/YT official/Kick/FB; Aggregator loop drop; off by default
- [x] B8 — Retention hooks and djbotbotty.com front door — shows/next, streaks+badges 3/10/25, /me/notify store-only, /healthz without mido
- [x] C1 — MIDI control layer and fake deck sandbox — per-deck MIDI_MAP, DJController.send gates (kill/automation/takeover), non-blocking play tap, DJ_BACKEND=sim FakeMidiOut+DeckSimulator, /action/{eq,filter,sync,panic,load,takeover}, dashboard MANUAL TAKEOVER
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
- [ ] **B3:** Google OAuth app — create in Google Cloud Console; set `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`; authorized redirect URI = `{origin}/auth/google/callback` (do not invent credentials here)
- [ ] **B3:** Facebook OAuth app — Meta developer app; set `FACEBOOK_OAUTH_CLIENT_ID` / `FACEBOOK_OAUTH_CLIENT_SECRET`; redirect `{origin}/auth/facebook/callback`
- [ ] Apple / TikTok OAuth (optional; `/auth/{apple,tiktok}/callback` return 501 until implemented)
- [ ] Optional: `OAUTH_STATE_SECRET` or `SESSION_SECRET` for CSRF state HMAC (falls back to OPERATOR_KEY / dev default)

### Chat platforms (B1–B7)
- [ ] YouTube: live video id + (later) Data API key/quota for official adapter
- [ ] Twitch: channel name for anon read; bot token with chat:write if enabling relay (B7)
- [ ] **B4 blocker:** Kick developer app + webhook URL + OAuth token (chat:write if send); Facebook Page access token + live video id; Rumble livestream API URL from rumble.com/account/livestream-api; YouTube Data API key + quota for youtube_official
- [ ] TikTok LIVE terms risk acceptance + experimental enable (B5)
- [ ] X API credentials + live post id if using experimental X adapter (B5)
- [ ] **A6:** Live test donation / Super Chat / sub on one platform

### Railway / djbotbotty.com (B8 / D1)
- [ ] Railway volume mounted at `DATA_DIR`
- [x] Health check → `/healthz` (B8)
- [ ] Set `OPERATOR_KEY` and `COOKIE_SECURE=true` in production
- [ ] LLM keys (`GEMINI_API_KEY` or `XAI_API_KEY`) on Railway

### Mac DJ booth (C1–C5)
- [ ] **C1 blocker:** Import MIDI Learn mapping on `AI_DJ_Virtual_Port` in rekordbox (see docs/rekordbox-mapping.md)
- [ ] Run analysis on `~/Desktop/Finished Songs`; export rekordbox XML (C2)
- [ ] Export rekordbox history for replay scoring (C4)
- [ ] Controller + audio loopback (e.g. BlackHole) for takeover/silence watchdog (C5)

### Alpha dress rehearsal (D1)
- [ ] Multistream with merged room, Halo, OBS overlay, kill switch

## Notes / deviations

- B3: Google/Facebook callbacks mocked in tests; live staging login needs Charles OAuth apps. Apple/TikTok callback 501 by design.
- B1 Twitch docs (2026-10-24): official IRC requires oauth PASS/NICK; non-SSL WS removed. Adapter still uses justinfan anonymous read-only over `wss://irc-ws.chat.twitch.tv:443` per plan (community practice; EventSub preferred long-term).
- B1 YouTube: pytchat loop moved to adapter; cohost.set_youtube updates adapter channel via hook. Official liveChatMessages left as TODO.
- B4 docs (2026-10-24): Kick webhook RSA over messageId.timestamp.body (docs.kick.com/events/webhook-security); Facebook Graph v26 live-video comments; Rumble livestream API JSON (support article 2025-11-20); YT liveChatMessages.list quota 5/call + pollingIntervalMillis. Dead adapters isolated; Charles tokens logged as blockers.
- B5 docs (2026-10-24): TikTokLive optional (unofficial webcast; no official LIVE chat API); X recent search conversation_id/quoted_tweet_id (docs.x.com rate limits). Both off by default; auto-disable after 5 failures / 10 min + operator alert.
- Session restart mid-A6: WIP stashed as `wip/a6-shoutouts`; setup gate (env/docs/blockers/gitignore) runs before restoring A6.
