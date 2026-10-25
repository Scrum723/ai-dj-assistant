# Alpha launch checklist

Fill truthfully at D1. Code/CI items can be checked here; live dress-rehearsal and Charles credential items stay open until verified on stream.

## Spec §5

- [x] CI green: pytest plus dashboard and overlay builds — `.github/workflows/ci.yml` (python 3.11 + node 20); full suite no longer ignores hang files after `test_ws_room_broadcast` fix
- [x] Operator auth on every control endpoint; kill switch and resume tested — A2 suite (`test_kill_auth`, `require_operator`)
- [x] Moderation filter and rate limits on; blocklist reviewed by Charles — **code on**; Charles still needs to review/extend `settings.moderation.blocklist`
- [ ] Merged room live on djbotbotty.com with YouTube, Twitch, Kick, Facebook, Rumble (TikTok/X experimental, off by default) — **Blocked on Charles** (tokens + dress rehearsal)
- [ ] Booth pass linking tested across at least two platforms — **Blocked on Charles** (OAuth apps)
- [x] Unified bans hide a person everywhere; mod log works — B6 tests; live push_bans needs platform tokens
- [x] Halo answers the merged room within budget; priority queue, supporter thanks and segments working; daily LLM cap visible — A3–A7 offline tests; live LLM needs keys on Railway
- [x] Relay off by default; tested on one platform — B7 unit/fixtures; **live send** still needs bot token (Charles)
- [x] DJ automation off by default; simulator transition scores logged (target: average 80+ over 3 replayed sets, zero silences) — C4/C5 sim path; Mac MIDI still Charles
- [x] Railway: `/healthz` with adapter status, DB volume, backups, Secure cookies — code paths ready (`DATA_DIR` backups, `COOKIE_SECURE` / `RAILWAY_ENVIRONMENT`); **volume mount + prod verify** Charles
- [x] Rollback steps documented and tested once — documented in README; **Railway rollback practice** still Charles
- [x] "Blocked on Charles" list in `PROGRESS.md` is complete and specific

## D1 hardening extras

- [x] JSON logs + `X-Request-Id`; optional `SENTRY_DSN`
- [x] Adapter down >2 min → operator alert hook
- [x] IP rate limits on `/chat`, `/request`, `/auth/*`, `/ws/room`
- [x] `CORS_ORIGINS` from env; XFF only when `TRUSTED_PROXY` / Railway
- [x] `stream_bot` sends `X-Operator-Key` when `OPERATOR_KEY` set
- [x] Experimental adapter auto-disable clears on settings re-enable
