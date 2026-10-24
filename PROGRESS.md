# DJ Bot Botty — Alpha Progress

Branch: `alpha/a1-d1`
Spec: `docs/djbotbotty_full_build_prompt.md`

## Phases

- [x] A1 — Halo foundation: tests, LLM provider, per-viewer memory — pytest harness, Gemini/xAI via HALO_PROVIDER, SQLite viewer memory injected into prompts
- [x] A2 — Moderation, safety and access control — input/output filters, rate limits, kill/resume, OPERATOR_KEY auth, operator chat commands
- [x] B1 — Aggregator core + unified room v1 — ChatMessage schema, YouTube/Twitch adapters, Aggregator dedupe+moderation fan-out, /ws/room, Room+overlay badges; adapters disabled by default
- [x] B2 — Room history, presence and live-room UX — SQLite scrollback + retention, presence per platform + room sockets, Room.jsx filters/reconnect/dedupe
- [ ] A3 — Halo speaks to the merged room, with cost and latency controls
- [ ] A4 — Persona, voice and DJ mode
- [ ] A5 — Priority queue and Halo Waiting Room 24/7 mode
- [ ] A6 — Supporter shoutouts
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

_(none yet)_

## Notes / deviations

- B1 Twitch docs (2026-10-24): official IRC requires oauth PASS/NICK; non-SSL WS removed. Adapter still uses justinfan anonymous read-only over `wss://irc-ws.chat.twitch.tv:443` per plan (community practice; EventSub preferred long-term).
- B1 YouTube: pytchat loop moved to adapter; cohost.set_youtube updates adapter channel via hook. Official liveChatMessages left as TODO.
