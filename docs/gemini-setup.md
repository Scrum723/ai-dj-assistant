# Chief + Gemini setup

Chief’s generative chat uses **Google Gemini** by default (`CHIEF_PROVIDER=gemini`). Without a key, Chief still talks using local canned lines.

## Env (put in `~/.env` — never commit real keys)

| Var | Example / default | Role |
|-----|-------------------|------|
| `CHIEF_PROVIDER` | `gemini` | Select Gemini (or `xai` / `grok`) |
| `GEMINI_API_KEY` | *(secret)* | Required for generative replies |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Model id (also available: `gemini-3.8-flash`, `gemini-flash-latest`, …) |
| `GOOGLE_CLOUD_PROJECT` | project number or id | Optional; used by other Google tooling. **Not** a substitute for `GEMINI_API_KEY`. |

Legacy `HALO_PROVIDER` still works; `CHIEF_*` wins when both are set.

## Verified on Windows booth (2026-11-06)

- Provider: Gemini (`GeminiProvider available True`)
- Model: `gemini-3.6-flash`
- Project number on this machine: `352688721186`
- Key source: Google AI Studio / Gemini API key in `C:\Users\cclot\.env`

### Important: Gemini 3.x thinking tokens

Gemini 3.x models can spend output budget on internal “thinking.” A low `max_output_tokens` (e.g. 120) often returns **empty text** with `finish_reason=MAX_TOKENS`.

`backend/llm.py` therefore:

- Floors output tokens at **512**
- Sets `thinking_budget=0` when the SDK supports it
- Falls back to stitching candidate text parts if `.text` is empty

Do not lower the floor without re-testing live replies.

## Quick check

```powershell
cd C:\Users\cclot\ai-dj-assistant\.worktrees\chief-visualizer
$env:PYTHONPATH = "backend"
python -c "from dotenv import load_dotenv; load_dotenv(r'$env:USERPROFILE\.env'); from llm import get_provider; p=get_provider(); print(type(p).__name__, p.available); print(repr(p.generate('You are Chief.','Say hi in 5 words.', max_tokens=120)))"
```

Expect: `GeminiProvider True` and a non-empty reply string.

In the booth: Co-host → Send a test line → Chief should answer in the Room panel within a few seconds. Overlay (`/overlay/`) speaks via browser TTS on Windows.

## Not the same as Google sign-in

`GEMINI_API_KEY` does **not** enable Google booth-pass OAuth. Social login needs `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` — see `docs/oauth-setup.md`.
