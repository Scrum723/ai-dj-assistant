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
