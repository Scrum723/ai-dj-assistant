# Booth-pass social login (OAuth)

Email signup/login works without this. Social buttons stay disabled until Client ID/Secret are set in `~/.env` (or the process environment).

## Env vars

```
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
FACEBOOK_OAUTH_CLIENT_ID=
FACEBOOK_OAUTH_CLIENT_SECRET=
APPLE_OAUTH_CLIENT_ID=
TIKTOK_OAUTH_CLIENT_KEY=
TIKTOK_OAUTH_CLIENT_SECRET=
```

`GET /auth/providers` reports which ones are ready (`true` only when the ID/key env is non-empty).

## Redirect URIs to register

For local booth (`http://127.0.0.1:8000`):

- Google: `http://127.0.0.1:8000/auth/google/callback`
- Facebook: `http://127.0.0.1:8000/auth/facebook/callback`
- Apple: `http://127.0.0.1:8000/auth/apple/callback`
- TikTok: `http://127.0.0.1:8000/auth/tiktok/callback`

For production (`https://djbotbotty.com`), add the same paths on that origin.

## Google (recommended first)

1. Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID (Web application).
2. Add the redirect URI above.
3. Put Client ID and Client Secret into `~/.env`.
4. Restart the booth. `/auth/providers` should show `"google": true`.

Note: `GEMINI_API_KEY` / `GOOGLE_CLOUD_PROJECT` are for the LLM only. They do **not** enable Google sign-in. OAuth needs its own Client ID + Secret.

## After keys are set

Hard-refresh `/booth/`. Social buttons become clickable links to `/auth/<provider>/start`.
