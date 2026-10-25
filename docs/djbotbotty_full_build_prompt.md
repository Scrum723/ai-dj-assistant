# DJ Bot Botty — Full Build Prompt (Phase A1 → Alpha)

You are a coding agent working in the `ai-dj-assistant` repo (DJ Bot Botty, owner Charles Clottin, https://github.com/Scrum723/ai-dj-assistant). Execute the whole build plan below, phase by phase, in order, until Alpha.

## 1. Mission and repo context

DJ Bot Botty is a standalone Mac app plus a cloud site (djbotbotty.com on Railway):
- `backend/` — FastAPI app (`main.py`). `cohost.py` is **Halo**, the LLM chat co-host (regex classify + Gemini, canned fallbacks, in-memory viewers, YouTube chat via pytchat). `voice.py` (ElevenLabs → macOS `say` → browser speech), `auth.py` (email signup/login, perks, OAuth *start* routes but no callbacks), `settings.py`, `sentinel.py` (honeypots + a kill flag that nothing checks), `trainer.py` (idle Halo notes), `stream_bot.py` (YouTube forwarder), `dj_controller.py` (virtual MIDI port `AI_DJ_Virtual_Port` → rekordbox: play, crossfader, EQ only), `audio_analyzer.py` (librosa BPM/key/energy into `library.db`).
- `frontend-dashboard/` — React dashboard (`App.jsx`, `Room.jsx`, `Settings.jsx`, `Auth.jsx`, `StreamLinks.jsx`).
- `backend/frontend-overlay/` — OBS overlay (listens on `/ws`).
- Known gaps: no tests or CI, no auth on control endpoints, kill switch unwired, only YouTube chat read, no cross-platform identity, no deck load/sync/filter, no mix loop.

**Vision.** Charles multistreams video (Restream/OBS multi-RTMP handles video). DJ Bot Botty merges **all platform chats into one community room** where everyone hangs out together, with Halo hosting that room and each platform's chat. Priority: Halo chat and community first (Charles DJs by hand for now), DJ automation later, built and graded in a simulator before touching rekordbox.

## 2. Global rules (every phase)

- Use `python3`. Do not break existing features: `/chat`, `/request`, `/action/*`, `/ws`, `/room`, `/settings`, auth signup/login, Halo replies, voice, overlay. Keep existing endpoint shapes; only add fields.
- **MIDI stays on the Mac.** Never import `mido`/`rtmidi` at module top level in new code. The cloud build (`requirements-cloud.txt`, Railway) must boot with no MIDI, no librosa, and every chat adapter disabled.
- No secrets in code or tests; read keys from env and document new env vars in README.
- Tests: pytest under `tests/`, fully offline (mock LLM, TTS, every platform, MIDI). `python3 -m pytest -q` must pass after every phase; `npm run build` must pass for the dashboard and overlay when you touch them.
- **One small PR or commit per phase**, touching only the listed files (create new ones as listed). No drive-by refactors.
- **Platform APIs change.** Before writing any chat adapter, read that platform's current developer docs (auth, endpoints, rate limits, posting ability) and record the doc URL and date in the adapter docstring. If reality differs from this plan, follow the docs and note it in PROGRESS.md.
- **Never fake what you can't verify.** If a phase needs Charles's Mac, rekordbox, a controller, audio loopback, OAuth apps, platform tokens or a live stream, build and test everything possible with mocks/simulators, then list the remaining step under "Blocked on Charles" in PROGRESS.md and move on. Do not invent credentials, payloads you haven't seen documented, or test results.
- Safety defaults: DJ automation off, relay off, experimental adapters off, operator auth on.

## 3. Execution loop

1. Create `PROGRESS.md` at the repo root with a checklist of all 21 phases in the order below, plus sections "Blocked on Charles" and "Notes / deviations".
2. For each phase in order:
   1. Read the phase spec and the files it names.
   2. Implement the smallest change that meets the acceptance criteria.
   3. Write the listed tests; run `python3 -m pytest -q` (and frontend builds if touched). Fix until green. Never skip or delete existing tests to pass.
   4. Commit with message `<phase id>: <phase name>` (or open one PR per phase if working with PRs).
   5. Tick the phase in PROGRESS.md with a one-line summary; add any blockers and deviations.
   6. Continue to the next phase. Stop only if a later phase cannot be built at all without an earlier blocked item; then report.
3. At the end, run the full suite, fill in the Alpha checklist (section 5) with true status, and print a final report: phases done, tests count, blockers for Charles.

## 4. Phases (in execution order)

### 1. A1 — Halo foundation: tests, LLM provider, per-viewer memory (Halo chat)

**Goal:** Make Halo reliable and testable, and let it remember people between messages and shows.

**Scope**
- pytest harness with fake LLM and fake snapshot
- LLM provider interface: Gemini (current) and xAI, chosen by HALO_PROVIDER
- viewer memory in SQLite keyed by platform+handle (later by profile_id)
- inject a 1–2 line viewer memory into the prompt

**Files:** backend/cohost.py, new backend/llm.py, new backend/memory.py, tests/, requirements*.txt (pytest only), README

**Instructions**

Context: backend/cohost.py has CohostAgent with regex _classify, _compose calling Gemini directly (12s cooldown, 75s backoff on 429), and an in-memory viewers dict lost on restart. There are no tests.
Do:
1. Create tests/ with conftest.py that builds a CohostAgent with stub callables (get_snapshot, search_tracks, enqueue_request, speak, note_chat, get_autonomy, get_settings) and no API key.
2. backend/llm.py: generate(system, prompt, max_tokens) -> str with GeminiProvider (move the existing google.genai call unchanged) and XaiProvider (OpenAI-compatible HTTP, XAI_API_KEY, XAI_MODEL). Select via HALO_PROVIDER (default gemini). Keep cooldown/backoff.
3. backend/memory.py: SQLite viewers(platform, handle, profile_id NULL, first_seen, last_seen, visits, last_request, note) in DATA_DIR/halo.db (default backend/). touch(platform, handle), get(platform, handle), remember_request(...).
4. cohost.ingest calls memory.touch; _compose adds one line like "Viewer memory: 5th visit, last asked for X"; a known viewer on a new day classifies as "returning" with its own fallback lines.
5. Tests for all of the above.

**Acceptance criteria**
- [ ] Halo replies identically when no key is set (fallback lines)
- [ ] Returning viewer is greeted as returning; visit count survives restarts
- [ ] Provider switch works via env with no code change

**Tests:** classify cases, request parsing, fallback path, memory upsert/lookup, prompt includes memory, provider selection.

**Needs Charles (log as blocker if unavailable):** None.

---

### 2. A2 — Moderation, safety and access control (Halo chat)

**Goal:** Nothing Halo says or does can embarrass Charles, and only he can drive the booth.

**Scope**
- input filter: blocklist, links, caps/char flood, per-user rate limit
- output filter on LLM text
- wire sentinel kill switch into Halo and /action/*
- operator auth on control endpoints

**Files:** new backend/moderation.py, backend/cohost.py, backend/main.py, backend/sentinel.py, frontend-dashboard/src/App.jsx (api helper), tests/

**Instructions**

Context: backend/sentinel.py sets _kill but is_killed() is never called. /action/*, /settings, /agent/* in backend/main.py have no auth.
Do:
1. backend/moderation.py: check_input(author, message) -> (ok, reason) with blocklist (settings moderation.blocklist + defaults), links, caps/char flood, per-author token bucket; check_output(text) -> cleaned or None. Design it to take a ChatMessage-like dict so the community room (B track) can reuse it.
2. cohost: drop failing input; filter output, fallback on None.
3. sentinel: POST /sentinel/resume. cohost._say and every /action/* no-op with {"status":"killed"} when is_killed().
4. require_operator dependency: header X-Operator-Key == env OPERATOR_KEY, or session user with perks.operator. Apply to /action/*, POST /settings, /agent/*, /sentinel/*. If OPERATOR_KEY unset and client is 127.0.0.1, allow. Dashboard api() sends the key from localStorage.
5. Operator chat commands: !mute <name>, !unmute <name>, !halo quiet, !halo on.
6. TestClient tests.

**Acceptance criteria**
- [ ] Blocked words never reach the LLM or TTS
- [ ] 10 msgs in 10 s from one user get throttled
- [ ] Kill stops speech within one tick; /sentinel/resume restores it
- [ ] Anonymous POST /action/play is 401 in cloud; localhost still works

**Tests:** filter, rate limiter, kill gating, auth dependency on protected routes.

**Needs Charles (log as blocker if unavailable):** None.

---

### 3. B1 — Aggregator core + unified room v1 (YouTube, Twitch, web room) (Community room)

**Goal:** One merged chat stream: YouTube and Twitch messages plus web-room messages appear together, with platform badges, in the booth room and overlay.

**Scope**
- ChatMessage schema and ChatAdapter base class
- Aggregator: starts adapters, dedupes, fans out to Halo, room and overlay
- move the pytchat YouTube loop into an adapter; add Twitch (read-only, anonymous IRC)
- room websocket channel /ws/room broadcasting ChatMessage
- Room.jsx and overlay show platform badges

**Files:** new backend/community/{schema.py, aggregator.py, adapters/base.py, adapters/youtube.py, adapters/twitch.py}, backend/cohost.py, backend/main.py, backend/stream_bot.py, frontend-dashboard/src/Room.jsx, backend/frontend-overlay/src/App.jsx, tests/

**Instructions**

Context: today YouTube chat is read with pytchat inside cohost._start_youtube (and backend/stream_bot.py forwards to /agent/youtube). Room.jsx posts to /chat. The overlay listens on /ws.
Common message schema (backend/community/schema.py):
ChatMessage {
  id: str            # "<platform>:<platform_msg_id>" (dedupe key)
  platform: str      # youtube | twitch | kick | tiktok | x | facebook | rumble | room | halo
  channel: str       # which stream/channel it came from
  user: { platform_user_id, handle, display_name, avatar_url?, badges: [str], is_mod, is_owner }
  profile_id: int?   # booth-pass profile when linked
  text: str          # plain text, emotes replaced by :name: tokens
  emotes: [{ name, url, start, end }]
  kind: str          # chat | super_chat | gift | follow | system
  ts: float          # epoch seconds (UTC)
  raw?: dict         # optional, never sent to clients
}
Do:
1. backend/community/schema.py: the dataclass/pydantic model above plus to_client() that drops raw.
2. adapters/base.py: class ChatAdapter: name, start(emit), stop(), can_send=False, send(text) raises NotImplementedError, status() -> {connected, error, last_msg_ts}.
3. adapters/youtube.py: move the pytchat loop here (behaviour unchanged) mapping to ChatMessage. Leave a TODO for the official liveChatMessages API.
4. adapters/twitch.py: anonymous read via wss://irc-ws.chat.twitch.tv (justinfan nick), parse IRCv3 tags (display-name, badges, emotes, id).
5. aggregator.py: Aggregator with register(adapter), ingest(ChatMessage) used by adapters AND by POST /chat (platform=room); dedupe by id (LRU), run moderation.check_input, then fan out to (a) cohost.ingest, (b) /ws/room subscribers, (c) the existing state chat used by the overlay. Keep the last 200 messages in memory.
6. settings.community.adapters.{youtube,twitch}: {enabled, channel}. GET /community/status lists adapter status.
7. Room.jsx: subscribe to /ws/room, render a badge per platform. Overlay: show badge text.
8. Tests: fake adapters emitting messages, dedupe, moderation drop, websocket receives broadcast.

**Acceptance criteria**
- [ ] A YouTube and a Twitch message both show in the room within 2 s, each with its badge
- [ ] Room posts reach Halo and the overlay as platform=room
- [ ] Duplicate platform ids are dropped
- [ ] Cloud boots with all adapters disabled

**Tests:** schema validation, aggregator dedupe and fan-out, fake IRC parsing, websocket broadcast with TestClient.

**Needs Charles (log as blocker if unavailable):** None (staging with a test Twitch channel and YouTube live id).

---

### 4. B2 — Room history, presence and live-room UX (Community room)

**Goal:** The room feels like a place: scrollback, who is here, and which platform they came from.

**Scope**
- persist messages to SQLite with retention
- GET /community/history?before= for scrollback
- presence: active in last 5 min per platform; web-room users counted by socket
- room UI: platform filter chips, online count by platform, reconnect logic

**Files:** backend/community/aggregator.py, new backend/community/store.py, backend/main.py, frontend-dashboard/src/Room.jsx, tests/

**Instructions**

Context: B1 added Aggregator, ChatMessage, /ws/room.
Do:
1. backend/community/store.py: messages table (id PK, platform, channel, handle, profile_id, text, emotes_json, kind, ts) in DATA_DIR/community.db, with retention days from settings community.retention_days (default 30).
2. Aggregator persists each accepted message. GET /community/history?before=<ts>&limit=100 (public, no raw).
3. Presence: last_seen per (platform, handle); web sockets count as platform=room. GET /community/presence and a presence event on /ws/room every 10 s.
4. Room.jsx: load history on open, infinite scroll up, platform filter chips, online counts, exponential reconnect, dedupe by id.
5. Tests.

**Acceptance criteria**
- [ ] Reloading the room shows the last 100 messages
- [ ] Presence shows counts per platform and updates within 10 s
- [ ] Room reconnects after a dropped socket without duplicates

**Tests:** store insert/query/retention, presence expiry on fake clock, history pagination.

**Needs Charles (log as blocker if unavailable):** None.

---

### 5. A3 — Halo speaks to the merged room, with cost and latency controls (Halo chat)

**Goal:** Halo treats everyone as one audience, knows where each person came from, and stays fast and cheap.

**Scope**
- Halo consumes the Aggregator feed instead of its own YouTube loop
- prompt context: recent merged chat with platform tags, crowd summary by platform
- reply selection: batch busy chat, prioritise mentions/requests/new people
- metrics: latency p50/p95, calls today, cost estimate, daily cap

**Files:** backend/cohost.py, backend/community/aggregator.py, backend/main.py (/metrics), tests/

**Instructions**

Context: B1/B2 provide the Aggregator. cohost still has _start_youtube.
Do:
1. Remove cohost's own YouTube start (keep set_youtube as a thin wrapper that updates the youtube adapter settings).
2. Halo receives ChatMessage dicts; recent context lines look like "[twitch] Kim: ..." and include a one-line crowd summary ("12 here: 6 YouTube, 4 Twitch, 2 room").
3. Under load, buffer messages and pick one to answer every N seconds (settings halo.min_reply_sec, default 8): mentions > requests > first-time > questions > rest. Allow a combined shout-out for several new people.
4. Wrap LLM calls with timing and token estimates; GET /metrics (operator) returns calls_today, p50_ms, p95_ms, est_cost_usd (HALO_COST_PER_1K). HALO_DAILY_CALL_CAP switches to fallbacks.
5. Halo replies are published back into the Aggregator as platform=halo so the room shows them.
6. Tests.

**Acceptance criteria**
- [ ] Halo can greet 'Kim from Twitch' and 'Raj on YouTube' in one line
- [ ] At 60 msgs/min Halo replies at most every 8 s, choosing mentions first
- [ ] Over HALO_DAILY_CALL_CAP Halo uses fallbacks
- [ ] /metrics shows p50/p95 and est. cost

**Tests:** selection ordering under load, prompt contains platform tags, cap enforcement, metrics math.

**Needs Charles (log as blocker if unavailable):** None.

---

### 6. A4 — Persona, voice and DJ mode (Halo chat)

**Goal:** Halo sounds like Charles's booth partner and behaves right while Charles is mixing.

**Scope**
- persona presets (hype, chill, host, custom)
- DJ mode becomes quiet, not mute
- TTS timeouts and fallback chain
- operator messages get priority

**Files:** backend/cohost.py, backend/main.py (speech_worker), backend/voice.py, backend/settings.py, frontend-dashboard/src/Settings.jsx, tests/

**Instructions**

Context: PERSONA constant in cohost.py, overridable by settings halo.persona. DJ mode (halo.dj_mode, POST /agent/dj) mutes speech entirely in main.speech_worker. voice.synthesize has no timeout.
Do:
1. settings halo.persona_preset in {hype, chill, host, custom}; Settings.jsx select plus custom text.
2. DJ mode = quiet: no idle lines or demo fans; reply only to mentions, requests and the operator; max 1 sentence. Keep halo.dj_mode_silent for the old behaviour.
3. Operator (settings halo.operator_handles, one per platform) skips rate limit/cooldown, may get 2-3 sentences.
4. voice.synthesize: 4 s timeout per provider, then fall through; None lets the overlay use browser speech.
5. Tests.

**Acceptance criteria**
- [ ] In DJ mode Halo answers a mention within 3 s and is silent otherwise
- [ ] TTS failure never blocks the speech queue over 4 s
- [ ] Persona change applies without restart

**Tests:** DJ mode gating, operator priority, persona selection, TTS timeout fallback.

**Needs Charles (log as blocker if unavailable):** Listen test of macOS say voices and ElevenLabs in OBS.

---

### 7. A5 — Priority queue and 'Halo Waiting Room' 24/7 mode (Halo chat)

**Goal:** Copy YallBot's best idea: an always-on room host with a strict priority order, so Halo is useful even when Charles is offline.

**Scope**
- one priority queue for everything Halo might say: operator > safety/mod > paid/supporter > requests > mentions > first-timers > regulars > idle
- waiting-room mode when Charles is offline: show schedule, now-playing or last-set recap, 'going live soon' callouts, low-frequency replies
- honest wait messages ('busy, you're #4') instead of silence
- cooldown per person and global reply budget per minute

**Files:** new backend/halo_queue.py, backend/cohost.py, backend/settings.py, backend/main.py, tests/

**Instructions**

Context: YallBot (Ryan Hall, Y'all's 24/7 bot) states that tornado warnings jump the queue, other items are announced by severity, Super Chats get a response every time, members get occasional replies, and active warnings may delay personal requests. Halo should copy this pattern for a DJ room. A3 added a reply buffer; this replaces it with a proper queue.
Do:
1. backend/halo_queue.py: PriorityQueue of HaloTask {priority, created_ts, expires_ts, kind, target, platform, payload}. Priorities from settings halo.priorities with defaults: operator 0, safety 1, supporter 2, request 3, mention 4, first_timer 5, regular 6, idle 9. Expire low-priority tasks after halo.task_ttl_sec (default 120).
2. cohost pulls from the queue at most every halo.min_reply_sec and within halo.max_replies_per_min; per-user cooldown halo.user_cooldown_sec.
3. When a task waits > 20 s, Halo may post one short "got you, you're #N" ack (no LLM call).
4. Waiting-room mode (settings halo.live=false or no stream active): idle lines become schedule/next-show/last-set info; replies slower; never say Charles is live.
5. Expose queue length and positions in /state.
6. Tests for all rules.

**Acceptance criteria**
- [ ] Paid/supporter events are always answered, even under load
- [ ] Queue position is visible in /state and the room
- [ ] Offline mode replies at most once per 30 s per person and never claims Charles is live
- [ ] Lower-priority items expire after 2 min instead of piling up

**Tests:** ordering under mixed load, expiry, per-user cooldown, offline-mode gating on a fake clock.

**Needs Charles (log as blocker if unavailable):** None.

---

### 8. A6 — Supporter shoutouts: tips, Super Chats, subs, gifts, follows (Halo chat)

**Goal:** Every supporter on every platform gets a fast, personal, platform-correct thank-you, and the room sees it.

**Scope**
- normalize paid/support events from each adapter into kind=super_chat|gift|member|follow with amount and currency
- shoutout templates per platform norm (short on TikTok/Kick, fuller on YouTube) with optional LLM personalization
- on-screen overlay card and room highlight
- daily supporter wall; no amounts read aloud unless Charles enables it

**Files:** backend/community/schema.py, adapters (youtube, twitch, kick, tiktok), backend/cohost.py, backend/frontend-overlay/src/App.jsx, frontend-dashboard/src/Room.jsx, tests/

**Instructions**

Context: B1 defines ChatMessage.kind (chat | super_chat | gift | follow | system). A5 gives Halo a priority queue with a supporter tier.
Do:
1. Extend ChatMessage with support {amount, currency, tier, count}. Map from fixtures: YouTube superChatDetails/memberMilestone, Twitch USERNOTICE sub/resub/subgift and bits, Kick subscription/gift events, TikTok gift (experimental adapter). Leave unsupported fields null.
2. Supporter events go into A5's queue at supporter priority. Group bursts from the same person within 10 s.
3. Templates per platform in settings halo.thanks.{platform}; LLM may personalize using viewer memory but must fall back to the template within 3 s.
4. Publish a highlight event to the overlay and room; GET /community/supporters/today.
5. Never read amounts aloud unless settings halo.say_amounts is true.
6. Tests.

**Acceptance criteria**
- [ ] A fixture Super Chat, Twitch sub, Kick gift and TikTok gift each produce one thank-you within 5 s
- [ ] Burst of 20 gifts collapses into one grouped thank-you
- [ ] Supporter wall lists today's supporters by profile across platforms

**Tests:** event mapping fixtures per platform, burst grouping, template selection, overlay payload.

**Needs Charles (log as blocker if unavailable):** Live check with a real test donation on one platform.

---

### 9. B3 — Booth pass identity: one person, one profile across platforms (Community room)

**Goal:** A viewer who watches on YouTube today and TikTok tomorrow is the same person in the room and to Halo.

**Scope**
- finish OAuth callbacks (Google first)
- linked_handles table; link by one-time code typed in any platform chat
- Aggregator attaches profile_id and display name
- profile page with linked platforms and perks; request priority perk

**Files:** backend/auth.py, backend/memory.py, backend/community/{aggregator.py, identity.py}, backend/main.py, frontend-dashboard/src/{Auth.jsx, Profile.jsx}, tests/

**Instructions**

Context: backend/auth.py has users/sessions, email signup/login, perks JSON, and /auth/{provider}/start pointing at /auth/{provider}/callback which does not exist.
Do:
1. GET /auth/google/callback (httpx code exchange, signed state against CSRF, upsert user, set cookie, redirect /room). Same for facebook; apple/tiktok return 501 with a message.
2. backend/community/identity.py: linked_handles(user_id, platform, platform_user_id, handle, linked_at). POST /me/link-code -> 6 chars, 10 min. Aggregator recognises "!link CODE" from any platform and links it; Halo confirms.
3. Aggregator resolves profile_id for each message; room messages from a signed-in socket carry the user.
4. memory keys by profile_id when present and merges history.
5. Profile.jsx: name, perks, linked platforms with unlink, link-code button. enqueue_request honours perks.request_priority.
6. Tests.

**Acceptance criteria**
- [ ] Google login completes end to end on staging
- [ ] Typing !link ABC123 on Twitch links that handle; next message shows the profile name
- [ ] Memory merges visits across linked handles
- [ ] Room users are automatically their profile

**Tests:** callback with mocked token exchange and signed state, link-code expiry, identity resolution, memory merge.

**Needs Charles (log as blocker if unavailable):** None (Charles creates the OAuth apps).

---

### 10. A7 — Regulars, recurring segments and rituals (Halo chat)

**Goal:** Turn first-timers into regulars with welcomes, remembered details, community names and recurring interactive segments.

**Scope**
- welcome flow: first-timer gets a short welcome and one 'how to join in' tip; second visit gets 'welcome back'
- regular recognition: visit milestones, 'where are you listening from' map, remembered favourite genres
- recurring segments on a schedule: roll call, 'request hour', 'guess the BPM', 'track ID' after each mix, last-call
- community name and in-jokes stored in settings and used sparingly

**Files:** new backend/segments.py, backend/memory.py, backend/cohost.py, backend/settings.py, frontend-dashboard/src/Room.jsx, tests/

**Instructions**

Context: memory (A1/B3) keys viewers by profile across platforms. Ryan Hall, Y'all built a named community ("Y'all Squad") with its own Discord, merch using community sayings, and a nonprofit; Halo should help Charles build the same sense of belonging in the merged room.
Do:
1. Greeting state machine per profile: first_time -> returning -> milestone (5/10/25 visits). Each fires once per show.
2. memory stores where_from (only if the person says it) and favourite_genres (from requests). Halo may mention them.
3. backend/segments.py: scheduler with segments defined in settings halo.segments [{name, every_min|at_minute, enabled, dj_mode_ok}]. Built-ins: roll_call, request_hour, guess_the_bpm (answer from now-playing bpm), track_id (announce after a transition), last_call.
4. settings community.name and community.in_jokes [str]; Halo uses one at most every 15 min.
5. Room.jsx shows a "listening from" list and the current segment.
6. Tests.

**Acceptance criteria**
- [ ] First-timer, returning and milestone greetings fire once each
- [ ] Segments run on schedule, pause in DJ mode except roll call and track ID
- [ ] Room shows a live 'where from' list by city/country (opt-in text only)
- [ ] In-jokes appear at most once per 15 min

**Tests:** greeting state machine, segment scheduler on fake clock, DJ-mode gating, frequency caps.

**Needs Charles (log as blocker if unavailable):** None.

---

### 11. B4 — Adapters with official APIs: Kick, Facebook, Rumble (+ YouTube official) (Community room)

**Goal:** Read chat from the remaining platforms that offer a sanctioned way to do it.

**Scope**
- Kick adapter via Kick's public developer API (events/webhooks)
- Facebook Live comments via Graph API for Charles's Page
- Rumble via its livestream API key JSON feed (polling)
- optional YouTube official liveChatMessages adapter with quota guard
- per-adapter health in /community/status and dashboard

**Files:** backend/community/adapters/{kick.py, facebook.py, rumble.py, youtube_official.py}, backend/settings.py, backend/main.py (webhook routes), frontend-dashboard/src/Settings.jsx, tests/

**Instructions**

Context: B1 provides ChatAdapter and Aggregator. Check each platform's CURRENT docs first and record the date in the docstring.
Do:
1. adapters/kick.py: Kick public API (OAuth app, chat message events via webhook/subscription). Add POST /community/webhooks/kick with signature verification. Mark can_send per docs.
2. adapters/facebook.py: Graph API live_video comments for a Page (PAGE_ACCESS_TOKEN, live video id), polling or streaming endpoint; respect rate limits.
3. adapters/rumble.py: poll the Rumble livestream API URL (RUMBLE_LIVESTREAM_API_URL) for chat messages; dedupe by id; read-only.
4. adapters/youtube_official.py: liveChatMessages.list with pollingIntervalMillis and a daily quota counter; fall back to the pytchat adapter if quota runs out.
5. Exponential backoff on errors/429 for all. Status exposed in /community/status; Settings.jsx shows enable toggles and health.
6. Tests use saved JSON fixtures; no network.

**Acceptance criteria**
- [ ] Each adapter maps a recorded sample payload to ChatMessage
- [ ] Polling adapters back off on 429 and respect configured intervals
- [ ] A dead adapter shows red in status and never blocks others

**Tests:** fixture payloads per platform, backoff on fake 429, webhook signature check (Kick), quota counter (YouTube).

**Needs Charles (log as blocker if unavailable):** None; needs Charles's developer apps and tokens for each platform.

---

### 12. B5 — Experimental adapters: TikTok LIVE and X (Community room)

**Goal:** Bring in the platforms without an official live-chat API, clearly flagged as best-effort.

**Scope**
- TikTok LIVE via an unofficial webcast client behind a feature flag
- X: no public live-chat API; optional reader for replies to the live post, or manual paste
- loud 'experimental' labelling and auto-disable on repeated failure

**Files:** backend/community/adapters/{tiktok.py, x.py}, backend/settings.py, README, tests/

**Instructions**

Context: TikTok has no official API to read LIVE chat for general developers; community libraries (e.g. TikTokLive for Python) read the webcast feed unofficially and can break or violate terms. X has no public API for live broadcast chat.
Do:
1. adapters/tiktok.py: optional dependency, guarded import, settings community.adapters.tiktok.{enabled:false, username}. Map comments/gifts/joins to ChatMessage kinds. can_send=False.
2. adapters/x.py: if X API credentials and a post id are set, poll replies/quotes to that post as chat (respect X API tier rate limits); otherwise disabled. can_send only if the tier allows posting.
3. Auto-disable after 5 failures in 10 minutes; publish an operator alert.
4. README section "Platform reality" with the table from the plan.
5. Tests with fixtures; never hit the network.

**Acceptance criteria**
- [ ] Disabled by default; enabling shows an experimental warning in Settings
- [ ] Failure 5 times in 10 min auto-disables and alerts the operator
- [ ] No credentials stored beyond what the user enters

**Tests:** fixture mapping, auto-disable logic, flag gating.

**Needs Charles (log as blocker if unavailable):** Live check during a real TikTok LIVE; Charles accepts the terms risk.

---

### 13. B6 — Unified moderation and ban sync (Community room)

**Goal:** One mod action covers a person everywhere they show up.

**Scope**
- room-level bans/timeouts by profile or platform handle
- mods: Charles plus appointed room mods
- optional push of bans to platforms that allow it (Twitch, YouTube, Kick)
- audit log

**Files:** backend/moderation.py, new backend/community/mod.py, backend/main.py, frontend-dashboard/src/Room.jsx (mod menu), tests/

**Instructions**

Do:
1. backend/community/mod.py: bans(target_profile_id|platform+handle, until, reason, by). Aggregator drops messages from banned targets; delete-message events broadcast on /ws/room.
2. Mod roles: perks.room_mod or operator. POST /community/mod/{ban,timeout,unban,delete} (require mod).
3. Optional push: adapters with can_moderate implement ban(handle, seconds); call them when settings community.push_bans is true.
4. Room.jsx: mod menu on a message for mods.
5. mod_log table and GET /community/mod/log (operator).
6. Tests.

**Acceptance criteria**
- [ ] Banning a linked profile hides their messages from every platform in the room
- [ ] Timeout expires on schedule
- [ ] Push-to-platform only runs when enabled and credentials exist
- [ ] Every action logged with who and why

**Tests:** ban by profile hides all linked handles, timeout expiry, push adapters mocked, audit rows.

**Needs Charles (log as blocker if unavailable):** None; platform mod scopes needed to test pushes.

---

### 14. B7 — Relay back to platform chats (Community room)

**Goal:** Optionally echo room messages and Halo replies into platform chats where the API allows, so the conversation feels shared.

**Scope**
- send() on adapters that allow posting (Twitch, YouTube official, Kick, Facebook Page)
- relay rules: Halo replies on/off per platform, room messages prefixed with name
- loop prevention and per-platform rate limits

**Files:** backend/community/{aggregator.py, relay.py}, adapters with can_send, backend/settings.py, tests/

**Instructions**

Do:
1. backend/community/relay.py: rules from settings community.relay.{platform}: {halo: bool, room: bool, max_per_min}. Format room messages as "[room] Name: text" and Halo as plain text.
2. Implement send() for Twitch (authenticated IRC with TWITCH_BOT_TOKEN), YouTube official (liveChatMessages.insert, quota aware), Kick (chat send endpoint if available in current docs), Facebook (comment as Page). Others can_send=False.
3. Mark relayed messages with an id map so the echo coming back from the platform is dropped by the Aggregator.
4. Token-bucket per platform. Off by default.
5. Tests with mocked senders.

**Acceptance criteria**
- [ ] Relayed messages never re-enter as new messages (no loops)
- [ ] Per-platform send rate is capped below the platform limit
- [ ] Off by default; enabled per platform

**Tests:** loop prevention by tagging own messages, rate cap, rule matrix.

**Needs Charles (log as blocker if unavailable):** None; needs bot accounts/tokens with chat-write scope.

---

### 15. B8 — Retention hooks and djbotbotty.com front door (Community room)

**Goal:** Give the merged community reasons to come back, and make the Railway site the home of the room.

**Scope**
- show schedule and 'next live' banner
- streaks and badges per profile across platforms
- opt-in for going-live alerts (stored only)
- /healthz and Railway docs

**Files:** backend/main.py, backend/memory.py, backend/settings.py, frontend-dashboard/src/{Room.jsx, StreamLinks.jsx}, backend/frontend-overlay/src/App.jsx, Dockerfile/nixpacks.toml, README

**Instructions**

Do:
1. settings.shows [{start_iso, title}]; GET /shows/next; Room.jsx and overlay show "Next live" when offline, with StreamLinks to every platform.
2. Streaks and badges computed per profile (falling back to platform handle) across all linked platforms; exposed in /me and on room messages.
3. POST /me/notify {email_opt_in}. No sending in this PR.
4. GET /healthz {ok, midi, llm, adapters}. Railway health check points at it.
5. Tests.

**Acceptance criteria**
- [ ] Room shows next show and the viewer's streak
- [ ] Badges at 3/10/25 visits count all linked platforms
- [ ] /healthz is 200 on Railway with no MIDI

**Tests:** streak math, badge thresholds, healthz without mido.

**Needs Charles (log as blocker if unavailable):** None.

---

### 16. C1 — MIDI control layer and fake deck sandbox (DJ automation)

**Goal:** Every deck command goes through one safe, testable layer, with a simulated rekordbox for development.

**Scope**
- full mapping for decks 1/2
- one send gate: kill switch, automation flag, manual takeover
- FakeMidiOut and DeckSimulator
- endpoints for EQ, filter, sync, panic, real load
- non-blocking play tap

**Files:** backend/dj_controller.py, new backend/sim/deck_sim.py, backend/main.py, frontend-dashboard/src/App.jsx, new docs/rekordbox-mapping.md, tests/

**Instructions**

Context: backend/dj_controller.py maps only play A/B (CC10/11), crossfader (CC12) and EQ (CC20-25), all on channel 0. deck_play_pause uses time.sleep(0.08) in a sync request. set_eq has no endpoint. /action/load and /action/skip never reach rekordbox.
Do:
1. Per-deck MIDI_MAP (deck 1 ch 0, deck 2 ch 1): play, cue, sync, load, volume, filter, eq_low/mid/hi, browse_up/down, crossfader. Document as a MIDI Learn table in docs/rekordbox-mapping.md.
2. DJController.send(command, deck, value, source) is the only path; refuses on sentinel.is_killed(); source="auto" also refused when dj.automation is false or dj.manual_takeover is true.
3. Non-blocking play tap.
4. backend/sim/deck_sim.py: FakeMidiOut + DeckSimulator (loaded track, playing, position on injectable clock, bpm, pitch, eq, filter, volume, crossfader). DJ_BACKEND=sim.
5. Operator endpoints /action/eq, /action/filter, /action/sync, /action/panic, /action/load (browse+load to deck). Dashboard deck A/B, EQ, filter, MANUAL TAKEOVER.
6. Tests against the simulator.

**Acceptance criteria**
- [ ] DJ_BACKEND=sim runs the app on Linux
- [ ] Panic resets EQ/filter and centres on the playing deck
- [ ] With takeover on, no automated command is sent
- [ ] Bad deck/band returns 422

**Tests:** message bytes, gate behaviour, simulator state, endpoint validation.

**Needs Charles (log as blocker if unavailable):** Import the mapping in rekordbox MIDI Learn on AI_DJ_Virtual_Port.

---

### 17. C2 — Track analysis v2 (DJ automation)

**Goal:** Accurate BPM, key, beat grid, phrases and energy.

**Scope**
- full-track tempo with half/double correction, beats, downbeats, phrases
- major/minor key with Camelot
- energy curve, intro/outro
- rekordbox XML import
- DB migration

**Files:** backend/audio_analyzer.py, new backend/rekordbox_xml.py, backend/main.py, tests/

**Instructions**

Context: backend/audio_analyzer.py reads the first 120 s and stores bpm, key (strongest pitch class), mean RMS energy.
Do:
1. ALTER TABLE migration: camelot, mode, beats_json, downbeats_json, phrases_json, energy_curve_json, intro_sec, outro_sec, duration_sec, source.
2. Full-track analysis: tempo folded into 70-180, beats/downbeats, 16/32-bar phrases, Krumhansl major/minor key, Camelot, 8-bar energy curve, intro/outro thresholds.
3. backend/rekordbox_xml.py parses collection XML (AverageBpm, TEMPO, Tonality, POSITION_MARK) and overwrites matches with source='rekordbox'.
4. CLI --reanalyze, --rekordbox-xml PATH.
5. Tests generate WAVs with numpy in tmp_path.

**Acceptance criteria**
- [ ] Click fixtures at 120/128/174 BPM within 0.5 BPM
- [ ] All 24 Camelot codes correct
- [ ] XML import fills grid/key
- [ ] Old library.db migrates

**Tests:** synthesised audio, Camelot table, XML fixture, migration.

**Needs Charles (log as blocker if unavailable):** Run on ~/Desktop/Finished Songs; export rekordbox XML.

---

### 18. C3 — Next-track selection (DJ automation)

**Goal:** Pick the next track by BPM, key and energy while honoring community requests.

**Scope**
- scorer with BPM, Camelot, energy, recency, request bonus
- energy plan presets
- plain-word reasons for dashboard and Halo

**Files:** new backend/selector.py, backend/main.py (/dj/next), frontend-dashboard/src/App.jsx, tests/

**Instructions**

Context: main.compatible_keys exists but is unused.
Do:
1. backend/selector.py: score(current, candidate, ctx) with settings dj.weights: bpm_fit (half/double), camelot_fit, energy_fit vs plan, recency penalty, request bonus (requests may come from any platform via the Aggregator). pick(..., k=3) with reasons.
2. Plans warmup/build/peak/cooldown.
3. GET /dj/next; dashboard shows top 3 with "load to other deck".
4. Halo may announce the reason (respect DJ mode).
5. Tests on a 30-track fixture.

**Acceptance criteria**
- [ ] No repeats within last N
- [ ] Fitting requests win
- [ ] Top 3 with reasons on dashboard

**Tests:** deterministic scoring on fixtures.

**Needs Charles (log as blocker if unavailable):** None.

---

### 19. C4 — Mix engine, replays and transition scoring (DJ automation)

**Goal:** Phrase-aligned automatic transitions with EQ and filter, developed and graded in the sandbox.

**Scope**
- mixer state machine
- templates: bass-swap blend, filter out, phrase cut
- recorded-set replay
- transition scorer
- CLI report

**Files:** new backend/mixer.py, backend/sim/{replay.py, score.py, run.py}, backend/main.py, tests/

**Instructions**

Context: C1 gives DJController.send and DeckSimulator; C2 track structure; C3 selector.pick.
Do:
1. backend/mixer.py: async Mixer with injectable clock: IDLE -> PLAYING -> PREPARING -> TRANSITIONING -> PLAYING. Start on the phrase at or after outro minus 32 bars. Templates as timed steps in bars.
2. Position from start timestamp + bpm; expose drift.
3. sim/replay.py: rekordbox history export or JSON [{track_id, start_sec}].
4. sim/score.py: beat_offset_ms, phrase_aligned, bpm_delta_pct, camelot_ok, loudness_overlap, bass_clash_sec; 0-100.
5. sim/run.py CLI table + JSON.
6. POST /dj/automation (operator); default off; all sends source="auto".
7. Tests under 5 s.

**Acceptance criteria**
- [ ] 10-track sim set with no silence, transitions within 1 beat of a phrase
- [ ] Per-transition scores and a grade
- [ ] Automation off by default

**Tests:** fake-clock end-to-end set, scorer cases, replay parser.

**Needs Charles (log as blocker if unavailable):** Export rekordbox history playlists.

---

### 20. C5 — Safety fallback and manual override (on the Mac) (DJ automation)

**Goal:** No dead air, and the bot always yields to Charles.

**Scope**
- MIDI input listener: human touch = takeover
- silence watchdog via loopback
- mixer heartbeat
- action log

**Files:** backend/dj_controller.py, backend/mixer.py, new backend/watchdog.py, backend/main.py, tests/

**Instructions**

Do:
1. Optional MIDI input listener (guarded import) on a configured port; foreign CC/notes set manual_takeover for dj.takeover_minutes and pause the Mixer.
2. backend/watchdog.py: level source (sim or sounddevice RMS). Silence > 3 s with automation on -> play backup deck, crossfader to it.
3. Heartbeat; no tick for 2 s -> panic + alert in /state.
4. Log every send() to dj_actions.jsonl.
5. Tests; Mac-only code skipped in CI.

**Acceptance criteria**
- [ ] Fader move stops automation within 200 ms
- [ ] 3 s silence triggers recovery
- [ ] Loop stall triggers panic and alert

**Tests:** simulated input, silence, stall.

**Needs Charles (log as blocker if unavailable):** Required: controller, audio loopback (e.g. BlackHole), real rekordbox.

---

### 21. D1 — Alpha hardening and launch (Alpha launch)

**Goal:** Ship a stable Alpha: tested, deployed, monitored, with a rollback.

**Scope**
- CI for pytest and both frontends
- structured logs, error reporting, /metrics, /healthz incl. adapter health
- rate limits, Secure cookies, CORS from env
- DB volume and backups
- checklist and rollback

**Files:** .github/workflows/ci.yml, backend/main.py, backend/auth.py, Dockerfile/nixpacks.toml, README, docs/alpha-checklist.md

**Instructions**

Do:
1. .github/workflows/ci.yml: python3.11 + requirements-cloud.txt + pytest; node 20 builds for frontend-dashboard and backend/frontend-overlay.
2. JSON logs with request id; optional SENTRY_DSN; alert hook when an adapter is down > 2 min.
3. IP rate limits on /chat, /request, /auth/*, /ws/room connects; COOKIE_SECURE true when RAILWAY_ENVIRONMENT set; CORS from env.
4. DATA_DIR for all DBs (Railway volume) and daily sqlite backup.
5. docs/alpha-checklist.md and README rollback section.
6. No behaviour changes beyond these.

**Acceptance criteria**
- [ ] CI green
- [ ] Railway deploys from main with health check
- [ ] Rollback tested once
- [ ] Adapter outages alert the operator

**Tests:** full suite; staging smoke of /healthz, /chat, /ws/room.

**Needs Charles (log as blocker if unavailable):** Dress-rehearsal multistream with merged room, Halo, OBS overlay, kill switch.

---

## 5. Alpha launch checklist (fill in truthfully at the end)

- [ ] CI green: pytest plus dashboard and overlay builds
- [ ] Operator auth on every control endpoint; kill switch and resume tested
- [ ] Moderation filter and rate limits on; blocklist reviewed by Charles
- [ ] Merged room live on djbotbotty.com with YouTube, Twitch, Kick, Facebook, Rumble (TikTok/X experimental, off by default)
- [ ] Booth pass linking tested across at least two platforms
- [ ] Unified bans hide a person everywhere; mod log works
- [ ] Halo answers the merged room within budget; priority queue, supporter thanks and segments working; daily LLM cap visible
- [ ] Relay off by default; tested on one platform
- [ ] DJ automation off by default; simulator transition scores logged (target: average 80+ over 3 replayed sets, zero silences)
- [ ] Railway: /healthz with adapter status, DB volume, backups, Secure cookies
- [ ] Rollback steps documented and tested once
- [ ] "Blocked on Charles" list in PROGRESS.md is complete and specific
