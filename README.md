# DJ Bot Botty

The full booth: MIDI, decks, rekordbox virtual port, room, overlay, and viewer passes.

**Halo** is the LLM buddy in chat — not the product name.

This is a **standalone Mac app** that talks to rekordbox over a virtual MIDI port. It does **not** install into Pioneer DJ / rekordbox as a plugin.

Live web: `https://djbotbotty.com` (Railway). MIDI stays on the Mac.

It has four pieces:

1. **Brain** (`backend`) — FastAPI + virtual MIDI port `AI_DJ_Virtual_Port`
2. **Dashboard** — DJ controls, library, fan requests (`http://127.0.0.1:5173`)
3. **Overlay** — OBS browser source avatar (`http://127.0.0.1:5174`)
4. **Booth pass** — signup/login so livestream viewers keep a DJ Bot Botty profile (Halo is the chat LLM)

## Desktop app (private, one process)

Booth, request queue, room, overlay, and Halo run as **one agent**.

```bash
cd "/Users/charlesclottin/Music/PioneerDJ/AI_DJ_Assistant"
python3 start.py
```

Or build a double-clickable Mac app (stays on this machine):

```bash
chmod +x make_app.sh && ./make_app.sh
open Halo.app
```

- Booth: `http://127.0.0.1:8000/booth/`
- Viewer room: `http://127.0.0.1:8000/booth/?tab=room`
- OBS overlay: `http://127.0.0.1:8000/overlay/`
- **Halo live / DJ MODE** in the top bar: DJ mode keeps Halo quiet so you can mix.
- Settings: voice (macOS free, or ElevenLabs if you paste a key), persona, what Halo may do, vibe, distraction, eco/balanced/show.

ElevenLabs is **optional**. macOS `say` (Samantha/Ava) is the default and is already a real voice. Add an ElevenLabs key in Settings only if you want a custom studio voice.

Eco mode = fewest API calls, no demo crowd, longer quiet gaps.

Use `python3`, not `python`. Double-click **Start AI DJ Assistant.command** or **Halo.app**.

## Editing in Antigravity IDE

Yes — **Antigravity** is the editor. Open this folder, not `dist`:

`/Users/charlesclottin/Music/PioneerDJ/AI_DJ_Assistant`

Or double-click `Halo.code-workspace`.

Edit source files:

- Dashboard: `frontend-dashboard/src/App.jsx`
- Room / settings: `frontend-dashboard/src/Room.jsx`, `Settings.jsx`
- Overlay: `frontend-overlay/src/App.jsx`
- Halo brain: `backend/cohost.py`, `backend/main.py`

`frontend-dashboard/dist` is the **built copy**. Changing it does nothing useful and gets wiped on the next build. After you change `src`, run:

```bash
cd "/Users/charlesclottin/Music/PioneerDJ/AI_DJ_Assistant"
python3 start.py
```

Do not type `PioneerDJ` in the terminal — that is a folder name, not a command.

## rekordbox MIDI (required for actual decks)

1. Keep `start.py` running so the MIDI port stays alive.
2. Open **Audio MIDI Setup** if you want to confirm Core MIDI is happy.
3. In rekordbox: MIDI / controller settings → enable **AI_DJ_Virtual_Port**.
4. MIDI-learn these CCs:

| CC | Control |
|----|---------|
| 10 | Deck A play/pause |
| 11 | Deck B play/pause |
| 12 | Crossfader |
| 20–22 | Deck A EQ low / mid / hi |
| 23–25 | Deck B EQ low / mid / hi |

Until those are mapped, Play in the dashboard only updates this app. It cannot move rekordbox faders by magic.

## Library

Tracks already analyzed live in `backend/library.db` (your *Finished Songs* folder). To rescan:

```bash
cd "/Users/charlesclottin/Music/PioneerDJ/AI_DJ_Assistant"
.venv/bin/python backend/audio_analyzer.py "/Users/charlesclottin/Desktop/Finished Songs"
```

## Co-host (Halo)

Halo starts with the app. The overlay is her face: she greets people, answers chat, takes requests, and talks during silence so it is never stuck on one sentence.

1. Open [http://127.0.0.1:5174](http://127.0.0.1:5174) (OBS Browser source, 1920×1080, click once to allow voice).
2. Dashboard tab **Co-host Halo**:
   - **Demo crowd ON** — fake viewers so you can test without going live
   - **Talk to Halo** — type as a viewer
   - **YouTube live chat** — paste a live video ID when you are actually streaming
3. Turn demo OFF once real chat is connected.

`GEMINI_API_KEY` in `~/.env` makes replies generative. Without it, Halo still talks using local lines.

### Halo LLM / memory env vars

| Var | Default | Meaning |
|-----|---------|---------|
| `HALO_PROVIDER` | `gemini` | LLM backend: `gemini` or `xai` (alias `grok`) |
| `GEMINI_API_KEY` | — | Required when `HALO_PROVIDER=gemini` for generative replies |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini model id |
| `XAI_API_KEY` | — | Required when `HALO_PROVIDER=xai` |
| `XAI_MODEL` | `grok-2-latest` | xAI model id |
| `HALO_COST_PER_1K` | `0.0001` | Estimated USD per 1k tokens for `/metrics` |
| `HALO_DAILY_CALL_CAP` | unset | When set, Halo uses fallbacks after this many LLM calls/day |
| `DATA_DIR` | `backend/` | Directory for `halo.db`, `community.db`, `users.db`, `library.db`; daily copies under `$DATA_DIR/backups/YYYY-MM-DD/` |
| `OPERATOR_KEY` | unset | Shared secret for booth control. Send as header `X-Operator-Key`. Required in cloud; when unset, `127.0.0.1` may call `/action/*`, `POST /settings`, `/agent/*`, `/sentinel/*` without a key. Dashboard reads the key from `localStorage.operatorKey`. |
| `YOUTUBE_LIVE_VIDEO_ID` | unset | Optional default YouTube live id (adapters still off until enabled in settings) |
| `ELEVENLABS_API_KEY` | unset | Optional TTS; falls through to macOS `say` / browser speech |
| `COOKIE_SECURE` | — | Set `true` on Railway / HTTPS (also auto when `RAILWAY_ENVIRONMENT` is set) |
| `CORS_ORIGINS` | localhost + djbotbotty.com | Comma-separated browser origins |
| `TRUSTED_PROXY` | unset | Set `1` to honor `X-Forwarded-For` (auto on Railway). Without it, XFF is ignored so clients cannot spoof localhost for operator auth. |
| `LOG_FORMAT` | plain / `json` on Railway | Structured JSON logs with `request_id` |
| `SENTRY_DSN` | unset | Optional Sentry error reporting |
| `X_BEARER_TOKEN` | unset | Optional X API bearer for experimental replies adapter |
| `X_POST_ID` | unset | Optional root post id for X replies polling |
| `RUMBLE_LIVESTREAM_API_URL` | unset | Rumble livestream API URL (embeds key) |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | unset | Facebook Page token for Live comments |

### Railway / djbotbotty.com

- Health check path: **`/healthz`** — returns `{ok, midi, llm, adapters}`. Always HTTP 200; `midi` is `false` on cloud (no mido). Point Railway health at `/healthz`.
- Mount a volume at `DATA_DIR` for SQLite DBs. On boot the app copies DBs once per UTC day into `$DATA_DIR/backups/YYYY-MM-DD/` (see note below).
- Set `OPERATOR_KEY`, `COOKIE_SECURE=true` (or rely on `RAILWAY_ENVIRONMENT`), and an LLM key (`GEMINI_API_KEY` or `XAI_API_KEY`).
- Optional: `CORS_ORIGINS`, `SENTRY_DSN`, `LOG_FORMAT=json`.
- IP rate limits apply to `/chat`, `/request`, `/auth/*`, and `/ws/room` connects.
- Show schedule: `settings.shows` as `[{start_iso, title}]`; room/overlay show **Next live** via `GET /shows/next`.

#### DATA_DIR backups

Boot runs `ops.maybe_daily_backup()`: if `$DATA_DIR/backups/<UTC-day>/.done` is missing, it copies `halo.db`, `community.db`, `users.db`, `identity.db`, `library.db`, and jsonl logs into that folder. For off-box retention, sync `$DATA_DIR/backups/` with Railway volume snapshots or an external job — the app does not upload backups itself.

#### Rollback

1. In Railway → service → **Deployments**, open the last known-good deploy → **Rollback** (or redeploy the previous Git SHA on `main`).
2. Confirm health: `GET https://djbotbotty.com/healthz` returns `ok: true`.
3. If a bad migration corrupted SQLite, stop the service, restore files from `$DATA_DIR/backups/<day>/` over the live DB paths, then restart.
4. Keep `OPERATOR_KEY` / OAuth secrets unchanged across rollbacks unless the deploy that set them is the one you are undoing.
5. Document the rollback time and SHA in `PROGRESS.md` Notes when you practice it (Alpha checklist: test once).

Switch providers with `HALO_PROVIDER` only — no code change. Viewer memory persists in `$DATA_DIR/halo.db`; room history in `$DATA_DIR/community.db`. Full template: `.env.example`. Alpha ops checklist: `docs/alpha-checklist.md`.

### Platform reality

How chat is read today (adapters stay **off** until enabled in Settings):

| Platform | Read chat | Send | Notes |
|----------|-----------|------|-------|
| YouTube (pytchat) | Unofficial (pytchat) | No | Default YT path; no quota |
| YouTube official | Data API `liveChatMessages` | Later (quota) | Daily quota guard; falls back to pytchat |
| Twitch | Anonymous IRC | Later (bot token) | Read-only justinfan |
| Kick | Events webhooks | If access token | Signature-verified |
| Facebook Live | Graph API comments | Later (Page) | Needs Page token + live video id |
| Rumble | Livestream API JSON poll | No | Read-only |
| TikTok LIVE | **Experimental** unofficial webcast (`TikTokLive` optional) | No | Off by default; terms risk; auto-disable 5 fails / 10 min |
| X | **Experimental** replies/quotes to a post | Only if `can_post` | No live-chat API; needs bearer + post id |

Experimental adapters show a warning in Settings when enabled and auto-disable after 5 failures in 10 minutes (operator alert on `/community/status` and room).

**Local run (this PC):** use `.venv\Scripts\python.exe` and `python -m pytest -q` from the repo root. Cloud boot uses `requirements-cloud.txt` (no MIDI / no librosa).

### Moderation / kill switch

Halo drops blocked words, links, caps/char floods, and rate-limited spam before they reach the LLM or TTS. Operator chat commands (from `halo.operator_handles` or the dashboard): `!mute <name>`, `!unmute <name>`, `!halo quiet`, `!halo on`. `POST /sentinel/kill-ours` freezes speech and `/action/*` (returns `{"status":"killed"}`); `POST /sentinel/resume` clears it.

Forward chat from another bot (Twitch bridge, etc.):

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"author":"Fan","message":"hey halo play Mad World","platform":"twitch"}'
```

## OBS overlay

Add a **Browser** source: `http://127.0.0.1:5174`  
Width 1920, height 1080, **Shutdown source when not visible** off.  
The page is transparent. Press `s` or `t` in that window to test the mouth/TTS.

## URLs

- Dashboard: http://127.0.0.1:5173
- Overlay: http://127.0.0.1:5174
- API: http://127.0.0.1:8000
